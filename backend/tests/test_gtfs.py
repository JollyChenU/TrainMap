"""覆盖外部快照校验、跨日运行日历、事务回滚和刷新失败。"""

import io
import sqlite3
import zipfile
from dataclasses import replace
from datetime import date

import pytest
from fastapi.testclient import TestClient

from backend.app import main
from backend.app.gtfs import fetch_snapshot, parse_feed, station_cities
from backend.app.models import ReachabilityRequest
from backend.app.repository import RailwayRepository


def feed(tmp_path, bad=False):
    """构造十个正确车次及一个可选异常车次，保证隔离阈值有测试覆盖。"""

    count = 11 if bad else 10
    content = {
        "agency.txt": "agency_id,agency_timezone\nCR,Asia/Shanghai\n",
        "feed_info.txt": "feed_start_date,feed_end_date\n20261120,20261125\n",
        "stops.txt": "stop_id,stop_name,stop_lat,stop_lon\nA,甲站,39,116\nB,乙站,38,115\n",
        "trips.txt": "trip_id,trip_short_name,service_id\n" + "".join(f"T{i},G{i},S\n" for i in range(count)),
        "calendar_dates.txt": "service_id,date,exception_type\nS,20261120,1\n",
        "stop_times.txt": "trip_id,stop_id,stop_sequence,arrival_time,departure_time\n" + "".join(
            f"T{i},A,1,23:00:00,23:00:00\nT{i},B,2,{('26:00:00' if bad and i == 10 else '25:00:00')},25:00:00\n"
            for i in range(count)
        ),
    }
    path = tmp_path / "test.zip"
    with zipfile.ZipFile(path, "w") as archive:
        for name, value in content.items():
            archive.writestr(name, value)
    return parse_feed(path, {"A": "甲城", "B": "乙城"}, "test")


def test_calendar_and_quarantine(tmp_path):
    """次日凌晨允许到达，非运行日不能凭每日模板生成列车。"""

    snapshot = feed(tmp_path, bad=True)
    assert len(snapshot.trips) == 10
    assert snapshot.metadata["rejected_count"] == "1"
    assert snapshot.trips[0].service_dates == frozenset({date(2026, 11, 20)})
    main.repository.replace_snapshot(snapshot)
    origin = next(c.city_id for c in snapshot.cities if c.name == "甲城")
    client = TestClient(main.app)
    request = dict(origin_city_id=origin, train_types=["G"],
                   start_at="2026-11-20T22:00:00+08:00", end_at="2026-11-21T02:00:00+08:00")
    result = client.post("/api/reachability", json=request).json()
    assert result["reachable_station_count"] == 1
    main.result_store.clear()
    assert client.get(f"/api/results/{result['query_id']}").status_code == 200
    request.update(start_at="2026-11-21T22:00:00+08:00", end_at="2026-11-22T02:00:00+08:00")
    assert client.post("/api/reachability", json=request).json()["reachable_station_count"] == 0
    request.update(end_at="2026-11-26T02:00:00+08:00")
    assert client.post("/api/reachability", json=request).status_code == 422


def test_transaction_rollback(tmp_path):
    """外键插入失败后，数据库不能变成半份快照。"""

    repository = RailwayRepository(tmp_path / "rollback.db")
    snapshot = feed(tmp_path)
    snapshot.stations[0] = replace(snapshot.stations[0], city_id="missing")
    with pytest.raises(sqlite3.IntegrityError):
        repository.replace_snapshot(snapshot)
    assert repository.metadata()["source_kind"] == "demo"
    assert len(repository.list_trips()) == 7


def test_catalog_only_lists_passenger_stations(tmp_path):
    """没有任何上下客业务的站点不能出现在客运站目录中。"""

    repository = RailwayRepository(tmp_path / "passenger-only.db")
    with repository.connect() as connection:
        connection.execute(
            "INSERT INTO stations VALUES (?, ?, ?, ?, ?)",
            ("unused", "beijing", "北京货运测试站", 116.5, 39.8),
        )
    assert "北京货运测试站" not in {
        station.name for station in repository.list_stations("beijing")
    }


def test_city_coordinate_is_a_real_station(tmp_path):
    """城市代表点必须落在真实车站，不能取两个站之间的坐标均值。"""

    snapshot = feed(tmp_path)
    city = next(item for item in snapshot.cities if item.name == "甲城")
    station_points = {
        (station.longitude, station.latitude)
        for station in snapshot.stations
        if station.city_id == city.city_id
    }
    assert (city.longitude, city.latitude) in station_points


def test_refresh_failure_keeps_previous(monkeypatch):
    """下载失败应显示错误，不冒充刷新成功。"""

    before = main.repository.metadata()
    def fail(progress, existing_version=""):
        raise ValueError("测试下载失败")
    monkeypatch.setattr(main, "fetch_snapshot", fail)
    main._refresh_worker()
    assert main.refresh_job["state"] == "failed"
    assert main.repository.metadata() == before


def test_missing_reference_rejected(tmp_path):
    """缺失站点引用必须拒绝整个候选包。"""

    feed(tmp_path)
    path = tmp_path / "test.zip"
    with zipfile.ZipFile(path) as archive:
        content = {n: archive.read(n) for n in archive.namelist()}
    content["stop_times.txt"] = content["stop_times.txt"].replace(b",B,", b",MISSING,")
    with zipfile.ZipFile(path, "w") as archive:
        for name, value in content.items():
            archive.writestr(name, value)
    with pytest.raises(ValueError, match="缺失"):
        parse_feed(path, {"A": "甲", "B": "乙"}, "test")


def test_station_mapping_and_timezone():
    """城市不能通过站名猜测；混合时区输入归一化后正常校验。"""

    assert station_cities("@x|北京南|VNP|p|p|1|1|北京|")["VNP"] == "北京"
    with pytest.raises(ValueError):
        station_cities("@x|北京南|VNP|")
    request = ReachabilityRequest(origin_city_id="x", train_types=["G", "G"],
                                  start_at="2026-11-20T20:00:00",
                                  end_at="2026-11-21T04:00:00Z")
    assert request.end_at.hour == 12
    assert request.train_types == ["G"]


def test_same_version_skips_download(monkeypatch):
    """相同发布版本只读取发布索引，不下载 ZIP 或改写快照获取时间。"""

    calls = []
    def download(url, destination, limit):
        calls.append(url)
        destination.write_text(
            '<feed xmlns="http://www.w3.org/2005/Atom"><entry>'
            '<title>gtfs-20261115-091021</title></entry></feed>', encoding="utf-8"
        )
    monkeypatch.setattr("backend.app.gtfs._download", download)
    assert fetch_snapshot(lambda message: None, "gtfs-20261115-091021") is None
    assert len(calls) == 1
    assert calls[0].endswith("/releases.atom")


def test_unchanged_refresh_persists_completion(monkeypatch):
    """无新版本仍应持久化任务完成状态，且保留旧快照和历史结果。"""

    before = main.repository.metadata()
    monkeypatch.setattr(main, "fetch_snapshot", lambda progress, existing_version: None)
    main._refresh_worker()
    assert main.repository.metadata() == before
    restarted = RailwayRepository(main.repository.database_path)
    assert restarted.load_refresh_state()["state"] == "completed"


def test_old_adapter_forces_revalidation(monkeypatch):
    """旧适配器生成的快照不能仅凭发布版本相同就跳过重新校验。"""

    received = []
    monkeypatch.setattr(main, "fetch_snapshot", lambda progress, version: received.append(version))
    main._refresh_worker()
    assert received == [""]


def test_calendar_removal_and_exact_name_fallback(tmp_path):
    """日历删除例外覆盖常规开行；精确站名只能映射唯一城市。"""

    feed(tmp_path)
    path = tmp_path / "test.zip"
    with zipfile.ZipFile(path) as archive:
        content = {name: archive.read(name) for name in archive.namelist()}
    content["calendar.txt"] = (
        "service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,start_date,end_date\n"
        "S,1,1,1,1,1,1,1,20261120,20261125\n"
    ).encode()
    content["calendar_dates.txt"] = b"service_id,date,exception_type\nS,20261120,2\n"
    with zipfile.ZipFile(path, "w") as archive:
        for name, value in content.items():
            archive.writestr(name, value)
    mapping = station_cities("@x|甲站|OLD|p|p|1|1|甲城|@x|乙站|B|p|p|1|1|乙城|")
    snapshot = parse_feed(path, mapping, "test")
    assert snapshot.metadata["unknown_city_count"] == "0"
    assert date(2026, 11, 20) not in snapshot.trips[0].service_dates
    assert date(2026, 11, 21) in snapshot.trips[0].service_dates
    ambiguous = station_cities("@x|同名站|A|p|p|1|1|甲城|@x|同名站|B|p|p|1|1|乙城|")
    assert "name:同名站" not in ambiguous
