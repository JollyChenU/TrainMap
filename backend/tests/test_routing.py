"""可达性算法的核心业务规则测试。"""

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from backend.app.models import DataStatus, ReachabilityRequest
from backend.app.repository import RailwayRepository
from backend.app.routing import calculate_reachability


TIMEZONE = ZoneInfo("Asia/Shanghai")


@pytest.fixture()
def repository(tmp_path: Path) -> RailwayRepository:
    """为每个用例创建独立的演示快照。"""

    return RailwayRepository(tmp_path / "test.db")


def make_request(**overrides) -> ReachabilityRequest:
    """生成覆盖典型周末夜间窗口的查询。"""

    values = {
        "origin_city_id": "beijing",
        "start_at": datetime(2026, 11, 20, 20, 0, tzinfo=TIMEZONE),
        "end_at": datetime(2026, 11, 21, 12, 0, tzinfo=TIMEZONE),
        "train_types": ["G", "D", "K"],
        "transfers_enabled": False,
        "max_transfers": 0,
        "min_transfer_minutes": 30,
    }
    values.update(overrides)
    return ReachabilityRequest(**values)


def run(repository: RailwayRepository, request: ReachabilityRequest):
    """使用固定演示状态执行一次纯算法计算。"""

    return calculate_reachability(
        request=request,
        cities=repository.list_cities(),
        stations=repository.list_stations(),
        trips=repository.list_trips(),
        data_status=DataStatus(
            source_kind="demo",
            source_name="测试快照",
            fetched_at=datetime.now(TIMEZONE),
            coverage_status="complete_demo",
            note="仅用于测试",
        ),
        query_id="test-query",
    )


def test_direct_mode_excludes_transfer_only_destination(
    repository: RailwayRepository,
) -> None:
    """关闭换乘时，不能出现只能在上海同站换乘到达的杭州。"""

    result = run(repository, make_request())
    city_names = {city.city_name for city in result.cities}

    assert "上海" in city_names
    assert "杭州" not in city_names
    assert all(station.transfers == 0 for city in result.cities for station in city.stations)


def test_same_station_transfer_reaches_hangzhou(
    repository: RailwayRepository,
) -> None:
    """开启一次换乘后，应在上海虹桥接续列车抵达杭州东。"""

    result = run(
        repository,
        make_request(transfers_enabled=True, max_transfers=1),
    )
    hangzhou = next(city for city in result.cities if city.city_name == "杭州")
    station = hangzhou.stations[0]

    assert station.transfers == 1
    assert station.direct_available is False
    assert [leg.train_code for leg in station.legs] == ["G101", "G7311"]
    assert station.legs[0].to_station_id == station.legs[1].from_station_id


def test_minimum_transfer_interval_is_enforced(
    repository: RailwayRepository,
) -> None:
    """最短换乘间隔超过实际等待时间时，不允许接续。"""

    result = run(
        repository,
        make_request(
            transfers_enabled=True,
            max_transfers=1,
            min_transfer_minutes=360,
        ),
    )

    assert "杭州" not in {city.city_name for city in result.cities}


def test_train_type_filter_applies_to_every_leg(
    repository: RailwayRepository,
) -> None:
    """只选择普速列车时，不得借用高铁完成后续行程。"""

    result = run(repository, make_request(train_types=["K"]))
    city_names = {city.city_name for city in result.cities}

    assert city_names == {"石家庄", "郑州", "武汉"}
    assert all(
        leg.train_type == "K"
        for city in result.cities
        for station in city.stations
        for leg in station.legs
    )


def test_time_boundaries_are_inclusive(repository: RailwayRepository) -> None:
    """恰好在起始时刻发车、截止时刻前到达的列车应被包含。"""

    result = run(
        repository,
        make_request(
            start_at=datetime(2026, 11, 20, 20, 10, tzinfo=TIMEZONE),
            end_at=datetime(2026, 11, 21, 1, 0, tzinfo=TIMEZONE),
            train_types=["G"],
        ),
    )

    assert "上海" in {city.city_name for city in result.cities}


def test_origin_city_is_not_counted_as_destination(
    repository: RailwayRepository,
) -> None:
    """始发城市及其其他始发站不应计入目的地统计。"""

    result = run(repository, make_request())

    assert result.origin_city_name == "北京"
    assert "北京" not in {city.city_name for city in result.cities}
    assert len(result.origin_station_ids) == 3


def test_selected_origin_stations_limit_departures(
    repository: RailwayRepository,
) -> None:
    """只选择北京西站时，不得使用北京站或北京南站的列车。"""

    result = run(
        repository,
        make_request(origin_station_ids=["beijing_west"]),
    )
    city_names = {city.city_name for city in result.cities}

    assert result.origin_station_ids == ["beijing_west"]
    assert "广州" in city_names
    assert "济南" not in city_names
