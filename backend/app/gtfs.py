"""下载和校验铁路 GTFS；所有候选数据通过校验后才能替换正在使用的快照。"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import re
import tempfile
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable

from .models import City, Station, StopTime, Trip

REPOSITORY = "https://github.com/wensimehrp/chinese-railway-gtfs"
ADAPTER_VERSION = "calendar-city-exact-v2"
STATION_URL = "https://kyfw.12306.cn/otn/resources/js/framework/station_name.js"
Progress = Callable[[str], None]


@dataclass
class Snapshot:
    """已校验的候选快照，包含可审计的隔离清单。"""

    cities: list[City]
    stations: list[Station]
    trips: list[Trip]
    metadata: dict[str, str]


def station_cities(text: str) -> dict[str, str]:
    """按 12306 明确给出的城市字段归属，不能按车站名称截断猜测城市。"""

    mapping = {}
    names: dict[str, set[str]] = defaultdict(set)
    for record in text.split("@")[1:]:
        fields = record.split("|")
        if len(fields) > 7 and fields[7].strip():
            mapping[fields[2]] = fields[7].strip()
            names[fields[1]].add(fields[7].strip())
    if not mapping:
        raise ValueError("12306 车站目录缺少城市归属字段")
    # GTFS 可能保留历史电报码，仅在站名精确且城市唯一时补齐归属。
    mapping.update({"name:" + name: next(iter(cities)) for name, cities in names.items() if len(cities) == 1})
    return mapping


def _seconds(value: str) -> int:
    """接受 GTFS 的 24:00 以上时刻，但拒绝非法分钟、秒和超长运行偏移。"""

    match = re.fullmatch(r"(\d{1,3}):([0-5]\d):([0-5]\d)", value)
    if not match:
        raise ValueError(f"非法时刻 {value!r}")
    h, m, s = map(int, match.groups())
    if h > 336:
        raise ValueError("列车时刻偏移超过 14 天")
    return h * 3600 + m * 60 + s


def parse_feed(path: Path, mapping: dict[str, str], version: str) -> Snapshot:
    """读取必需表并隔离时刻异常；不加载体积庞大的 shapes 铁路线文件。"""

    with zipfile.ZipFile(path) as archive:
        def read(name: str, required: bool = True) -> list[dict[str, str]]:
            if name not in archive.namelist():
                if required:
                    raise ValueError(f"缺少 {name}")
                return []
            entry = archive.getinfo(name)
            if entry.file_size > 32 * 1024 * 1024:
                raise ValueError(f"{name} 超出允许大小")
            with archive.open(entry) as stream:
                return list(csv.DictReader(io.TextIOWrapper(stream, encoding="utf-8-sig")))

        info_rows = read("feed_info.txt")
        if len(info_rows) != 1:
            raise ValueError("feed_info 必须只有一行")
        info = info_rows[0]
        begin = datetime.strptime(info["feed_start_date"], "%Y%m%d").date()
        end = datetime.strptime(info["feed_end_date"], "%Y%m%d").date()
        if end < begin or (end - begin).days > 730:
            raise ValueError("数据有效期非法")
        agencies = read("agency.txt")
        if not agencies or any(a["agency_timezone"] != "Asia/Shanghai" for a in agencies):
            raise ValueError("数据不是北京时间铁路时刻表")
        stop_rows, trip_rows = read("stops.txt"), read("trips.txt")
        time_rows = read("stop_times.txt")
        calendar_rows = read("calendar.txt", False)
        exception_rows = read("calendar_dates.txt", False)

    def unique(rows: list[dict], field: str) -> set[str]:
        keys = [row[field] for row in rows]
        if not keys or any(not key for key in keys) or len(keys) != len(set(keys)):
            raise ValueError(f"{field} 为空或重复")
        return set(keys)

    stop_ids = unique(stop_rows, "stop_id")
    trip_ids = unique(trip_rows, "trip_id")
    services: dict[str, set[date]] = defaultdict(set)
    weekdays = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
    for row in calendar_rows:
        first = max(begin, datetime.strptime(row["start_date"], "%Y%m%d").date())
        last = min(end, datetime.strptime(row["end_date"], "%Y%m%d").date())
        current = first
        services[row["service_id"]]
        while current <= last:
            if row[weekdays[current.weekday()]] == "1":
                services[row["service_id"]].add(current)
            current += timedelta(days=1)
    for row in exception_rows:
        day = datetime.strptime(row["date"], "%Y%m%d").date()
        if not begin <= day <= end or row["exception_type"] not in ("1", "2"):
            raise ValueError("运行日历异常")
        days = services[row["service_id"]]
        if row["exception_type"] == "1":
            days.add(day)
        else:
            days.discard(day)
    if not services:
        raise ValueError("缺少有效的运行日历")

    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in time_rows:
        if row["trip_id"] not in trip_ids or row["stop_id"] not in stop_ids:
            raise ValueError("经停表存在缺失的车次或站点引用")
        grouped[row["trip_id"]].append(row)

    stations, by_city = [], defaultdict(list)
    unknown = []
    for row in stop_rows:
        lat, lon = float(row["stop_lat"]), float(row["stop_lon"])
        if not (math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180):
            raise ValueError(f"车站 {row['stop_id']} 坐标非法")
        if not lat or not lon:
            raise ValueError(f"车站 {row['stop_id']} 坐标为零")
        name = mapping.get(row["stop_id"]) or mapping.get("name:" + row["stop_name"])
        if name is None:
            # 未知归属保持为独立站点入口，不伪造行政城市关系。
            name = f"{row['stop_name']}（归属待核验）"
            unknown.append(row["stop_id"])
        city_id = "city-" + hashlib.sha256(name.encode()).hexdigest()[:16]
        station = Station(row["stop_id"], city_id, row["stop_name"], lon, lat)
        stations.append(station)
        by_city[name].append(station)
    cities = [
        City(items[0].city_id, name,
             sum(s.longitude for s in items) / len(items),
             sum(s.latitude for s in items) / len(items))
        for name, items in by_city.items()
    ]

    trips, rejected = [], []
    for row in trip_rows:
        tid = row["trip_id"]
        if row["service_id"] not in services:
            raise ValueError(f"车次 {tid} 缺少日历引用")
        try:
            ordered = sorted(grouped[tid], key=lambda r: int(r["stop_sequence"]))
            if len(ordered) < 2:
                raise ValueError("不足两个经停站")
            previous, sequence, stops = -1, -1, []
            for i, stop in enumerate(ordered):
                arrival, departure = _seconds(stop["arrival_time"]), _seconds(stop["departure_time"])
                seq = int(stop["stop_sequence"])
                if arrival < previous or departure < arrival or seq <= sequence:
                    raise ValueError("到发时刻或站序不单调")
                pickup, dropoff = stop.get("pickup_type", "0") or "0", stop.get("drop_off_type", "0") or "0"
                stops.append(StopTime(stop["stop_id"], seq, arrival, departure,
                                      i < len(ordered) - 1 and pickup == "0",
                                      i > 0 and dropoff == "0"))
                previous, sequence = departure, seq
            code = row["trip_short_name"].strip()
            if not code:
                raise ValueError("车次名称为空")
            train_type = code[0] if code[0].isalpha() else "OTHER"
            trips.append(Trip(tid, code, train_type, tuple(stops),
                              frozenset(services[row["service_id"]])))
        except (ValueError, KeyError) as error:
            rejected.append({"trip_id": tid, "code": row.get("trip_short_name", tid), "reason": str(error)})
    if not trips or len(rejected) > len(trip_rows) * 0.1:
        raise ValueError("异常车次超过 10% 或没有有效车次，拒绝替换旧快照")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return Snapshot(cities, stations, trips, {
        "source_kind": "external",
        "adapter_version": ADAPTER_VERSION,
        "source_name": "中国铁路 GTFS / wensimehrp",
        "source_url": REPOSITORY,
        "license": "CC BY 4.0；车站坐标来源 OpenStreetMap，保留其署名及适用许可",
        "version": version,
        "sha256": digest.hexdigest(),
        "fetched_at": datetime.now().astimezone().isoformat(),
        "valid_from": begin.isoformat(),
        "valid_until": end.isoformat(),
        "coverage_status": "partial",
        "trip_count": str(len(trips)),
        "station_count": str(len(stations)),
        "rejected_count": str(len(rejected)),
        "unknown_city_count": str(len(unknown)),
        "rejected_trips": json.dumps(rejected, ensure_ascii=False),
        "unknown_city_stations": json.dumps(unknown),
    })


def _download(url: str, destination: Path, limit: int) -> None:
    """只在用户触发的数据准备过程中下载，并限制响应大小和等待时间。"""

    request = urllib.request.Request(url, headers={"User-Agent": "TarinMap/1.0"})
    with urllib.request.urlopen(request, timeout=60) as response, destination.open("wb") as stream:
        total = 0
        while chunk := response.read(1024 * 1024):
            total += len(chunk)
            if total > limit:
                raise ValueError("下载文件超过允许大小")
            stream.write(chunk)


def fetch_snapshot(progress: Progress, existing_version: str = "") -> Snapshot | None:
    """获取最新公开发布；使用临时目录，任何失败均不会污染当前数据库。"""

    with tempfile.TemporaryDirectory(prefix="tarinmap-refresh-") as temporary:
        folder = Path(temporary)
        progress("正在检查 GTFS 发布版本")
        _download(f"{REPOSITORY}/releases.atom", folder / "feed.xml", 2 * 1024 * 1024)
        root = ET.parse(folder / "feed.xml").getroot()
        title = root.find("{http://www.w3.org/2005/Atom}entry/{http://www.w3.org/2005/Atom}title")
        version = title.text if title is not None else ""
        if not re.fullmatch(r"gtfs-\d{8}-\d{6}", version or ""):
            raise ValueError("无法识别 GTFS 发布版本")
        if version == existing_version:
            progress("当前已是最新发布版本，无需重复下载")
            return None
        progress(f"正在下载 {version}（约 75 MB）")
        path = folder / "gtfs.zip"
        _download(f"{REPOSITORY}/releases/download/{version}/output_gtfs.zip", path, 200 * 1024 * 1024)
        progress("正在获取车站城市归属")
        _download(STATION_URL, folder / "stations.js", 4 * 1024 * 1024)
        mapping = station_cities((folder / "stations.js").read_text(encoding="utf-8-sig"))
        progress("正在校验时刻、运行日历和站点引用")
        return parse_feed(path, mapping, version)
