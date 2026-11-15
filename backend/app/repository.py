"""SQLite 数据仓库及内置演示铁路快照。"""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from collections.abc import Iterable
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path

from .models import City, Station, StopTime, Trip


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS cities (
    city_id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    longitude REAL NOT NULL,
    latitude REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS stations (
    station_id TEXT PRIMARY KEY,
    city_id TEXT NOT NULL REFERENCES cities(city_id),
    name TEXT NOT NULL,
    longitude REAL NOT NULL,
    latitude REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_stations_city ON stations(city_id);

CREATE TABLE IF NOT EXISTS trips (
    trip_id TEXT PRIMARY KEY,
    display_code TEXT NOT NULL,
    train_type TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS stop_times (
    trip_id TEXT NOT NULL REFERENCES trips(trip_id) ON DELETE CASCADE,
    sequence INTEGER NOT NULL,
    station_id TEXT NOT NULL REFERENCES stations(station_id),
    arrival_seconds INTEGER,
    departure_seconds INTEGER,
    can_board INTEGER NOT NULL DEFAULT 1,
    can_alight INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (trip_id, sequence)
);

CREATE INDEX IF NOT EXISTS idx_stop_times_station ON stop_times(station_id);

CREATE TABLE IF NOT EXISTS metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS calendars (
    trip_id TEXT PRIMARY KEY REFERENCES trips(trip_id) ON DELETE CASCADE,
    dates_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS results (
    query_id TEXT PRIMARY KEY,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS refresh_state (
    id INTEGER PRIMARY KEY CHECK(id = 1),
    payload TEXT NOT NULL
);
"""


DEMO_CITIES = (
    ("beijing", "北京", 116.4074, 39.9042),
    ("shijiazhuang", "石家庄", 114.5149, 38.0428),
    ("jinan", "济南", 117.1201, 36.6512),
    ("zhengzhou", "郑州", 113.6254, 34.7466),
    ("nanjing", "南京", 118.7969, 32.0603),
    ("shanghai", "上海", 121.4737, 31.2304),
    ("hangzhou", "杭州", 120.1551, 30.2741),
    ("wuhan", "武汉", 114.3054, 30.5931),
    ("changsha", "长沙", 112.9388, 28.2282),
    ("guangzhou", "广州", 113.2644, 23.1291),
)

DEMO_STATIONS = (
    ("beijing", "beijing", "北京站", 116.4270, 39.9028),
    ("beijing_south", "beijing", "北京南站", 116.3790, 39.8652),
    ("beijing_west", "beijing", "北京西站", 116.3212, 39.8948),
    ("shijiazhuang", "shijiazhuang", "石家庄站", 114.4848, 38.0107),
    ("jinan_west", "jinan", "济南西站", 116.8923, 36.6695),
    ("zhengzhou_east", "zhengzhou", "郑州东站", 113.7790, 34.7590),
    ("nanjing_south", "nanjing", "南京南站", 118.7977, 31.9680),
    ("shanghai_hongqiao", "shanghai", "上海虹桥站", 121.3269, 31.2005),
    ("hangzhou_east", "hangzhou", "杭州东站", 120.2120, 30.2908),
    ("wuhan", "wuhan", "武汉站", 114.4245, 30.6090),
    ("changsha_south", "changsha", "长沙南站", 113.0647, 28.1465),
    ("guangzhou_south", "guangzhou", "广州南站", 113.2695, 22.9889),
)

# 时间允许超过 24 小时，以保留跨日关系。
DEMO_TRIPS: tuple[tuple[str, str, str, tuple[tuple, ...]], ...] = (
    (
        "G101",
        "G101",
        "G",
        (
            ("beijing_south", None, 20 * 3600 + 10 * 60),
            ("jinan_west", 21 * 3600 + 42 * 60, 21 * 3600 + 47 * 60),
            ("nanjing_south", 23 * 3600 + 35 * 60, 23 * 3600 + 40 * 60),
            ("shanghai_hongqiao", 25 * 3600, None),
        ),
    ),
    (
        "G71",
        "G71",
        "G",
        (
            ("beijing_west", None, 20 * 3600 + 20 * 60),
            ("shijiazhuang", 21 * 3600 + 28 * 60, 21 * 3600 + 33 * 60),
            ("zhengzhou_east", 23 * 3600, 23 * 3600 + 5 * 60),
            ("wuhan", 25 * 3600 + 2 * 60, 25 * 3600 + 8 * 60),
            ("changsha_south", 26 * 3600 + 28 * 60, 26 * 3600 + 33 * 60),
            ("guangzhou_south", 29 * 3600 + 12 * 60, None),
        ),
    ),
    (
        "D901",
        "D901",
        "D",
        (
            ("beijing", None, 20 * 3600 + 45 * 60),
            ("shijiazhuang", 22 * 3600 + 25 * 60, 22 * 3600 + 35 * 60),
            ("zhengzhou_east", 24 * 3600 + 20 * 60, None),
        ),
    ),
    (
        "K51",
        "K51",
        "K",
        (
            ("beijing", None, 21 * 3600),
            ("shijiazhuang", 24 * 3600 + 5 * 60, 24 * 3600 + 15 * 60),
            ("zhengzhou_east", 28 * 3600 + 15 * 60, 28 * 3600 + 25 * 60),
            ("wuhan", 35 * 3600 + 30 * 60, None),
        ),
    ),
    (
        "G7311",
        "G7311",
        "G",
        (
            ("shanghai_hongqiao", None, 30 * 3600 + 30 * 60),
            ("hangzhou_east", 31 * 3600 + 18 * 60, None),
        ),
    ),
    (
        "G177",
        "G177",
        "G",
        (
            ("jinan_west", None, 22 * 3600 + 20 * 60),
            ("zhengzhou_east", 24 * 3600 + 18 * 60, 24 * 3600 + 23 * 60),
            ("wuhan", 26 * 3600 + 20 * 60, None),
        ),
    ),
    (
        "G1101",
        "G1101",
        "G",
        (
            ("wuhan", None, 26 * 3600),
            ("changsha_south", 27 * 3600 + 20 * 60, 27 * 3600 + 25 * 60),
            ("guangzhou_south", 29 * 3600 + 55 * 60, None),
        ),
    ),
)


class RailwayRepository:
    """管理铁路快照并向算法提供不可变领域对象。"""

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    @contextmanager
    def connect(self):
        """创建启用名称访问和外键约束的数据库连接。"""

        connection = sqlite3.connect(self.database_path, timeout=60)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def initialize(self) -> None:
        """创建表结构，并在空数据库中写入演示快照。"""

        with self.connect() as connection:
            connection.executescript(SCHEMA)
            count = connection.execute("SELECT COUNT(*) FROM cities").fetchone()[0]
            if count == 0:
                self._seed_demo(connection)

    def _seed_demo(self, connection: sqlite3.Connection) -> None:
        """以单个事务写入可用于端到端验证的演示数据。"""

        connection.executemany(
            "INSERT INTO cities(city_id, name, longitude, latitude) VALUES (?, ?, ?, ?)",
            DEMO_CITIES,
        )
        connection.executemany(
            """
            INSERT INTO stations(station_id, city_id, name, longitude, latitude)
            VALUES (?, ?, ?, ?, ?)
            """,
            DEMO_STATIONS,
        )
        for trip_id, code, train_type, stops in DEMO_TRIPS:
            connection.execute(
                "INSERT INTO trips(trip_id, display_code, train_type) VALUES (?, ?, ?)",
                (trip_id, code, train_type),
            )
            rows = []
            for sequence, (station_id, arrival, departure) in enumerate(stops):
                rows.append(
                    (
                        trip_id,
                        sequence,
                        station_id,
                        arrival,
                        departure,
                        int(departure is not None),
                        int(arrival is not None),
                    )
                )
            connection.executemany(
                """
                INSERT INTO stop_times(
                    trip_id, sequence, station_id, arrival_seconds, departure_seconds,
                    can_board, can_alight
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
        fetched_at = datetime.now().astimezone().isoformat()
        connection.executemany(
            "INSERT INTO metadata(key, value) VALUES (?, ?)",
            (
                ("source_kind", "demo"),
                ("source_name", "内置演示铁路快照"),
                ("coverage_status", "complete_demo"),
                ("fetched_at", fetched_at),
            ),
        )

    def list_cities(self, query: str = "") -> list[City]:
        """按城市名称搜索，并返回所有匹配项。"""

        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT city_id, name, longitude, latitude
                FROM cities
                WHERE name LIKE ?
                ORDER BY name
                """,
                (f"%{query.strip()}%",),
            ).fetchall()
        return [City(**dict(row)) for row in rows]

    def get_city(self, city_id: str) -> City | None:
        """按稳定城市标识查找城市。"""

        with self.connect() as connection:
            row = connection.execute(
                "SELECT city_id, name, longitude, latitude FROM cities WHERE city_id = ?",
                (city_id,),
            ).fetchone()
        return City(**dict(row)) if row else None

    def list_stations(self, city_id: str | None = None) -> list[Station]:
        """仅列出至少办理一次上下客业务的客运站。"""

        sql = """
            SELECT s.station_id, s.city_id, s.name, s.longitude, s.latitude
            FROM stations s
            WHERE EXISTS (
                SELECT 1 FROM stop_times st
                WHERE st.station_id = s.station_id
                  AND (st.can_board = 1 OR st.can_alight = 1)
            )
        """
        params: Iterable[str] = ()
        if city_id:
            sql += " AND s.city_id = ?"
            params = (city_id,)
        sql += " ORDER BY s.name"
        with self.connect() as connection:
            rows = connection.execute(sql, tuple(params)).fetchall()
        return [Station(**dict(row)) for row in rows]

    def list_trips(self) -> list[Trip]:
        """加载列车模板及其有序经停时刻。"""

        with self.connect() as connection:
            trip_rows = connection.execute(
                "SELECT trip_id, display_code, train_type FROM trips ORDER BY trip_id"
            ).fetchall()
            # 批量读取经停表，避免真实快照触发上万次小查询。
            stop_groups = defaultdict(list)
            for row in connection.execute("SELECT * FROM stop_times ORDER BY trip_id, sequence"):
                stop_groups[row["trip_id"]].append(StopTime(
                    row["station_id"], row["sequence"], row["arrival_seconds"],
                    row["departure_seconds"], bool(row["can_board"]), bool(row["can_alight"]),
                ))
            calendars = {
                row["trip_id"]: frozenset(date.fromisoformat(day) for day in json.loads(row["dates_json"]))
                for row in connection.execute("SELECT * FROM calendars")
            }
            trips: list[Trip] = []
            for trip_row in trip_rows:
                trips.append(
                    Trip(
                        trip_id=trip_row["trip_id"],
                        display_code=trip_row["display_code"],
                        train_type=trip_row["train_type"],
                        stops=tuple(stop_groups[trip_row["trip_id"]]),
                        service_dates=calendars.get(trip_row["trip_id"]),
                    )
                )
        return trips

    def replace_snapshot(self, snapshot) -> None:
        """以一个事务替换全部铁路表；插入失败时 SQLite 自动回滚。"""

        with self.connect() as connection:
            for table in ("calendars", "stop_times", "trips", "stations", "cities", "metadata"):
                connection.execute(f"DELETE FROM {table}")
            connection.executemany("INSERT INTO cities VALUES (?, ?, ?, ?)", [
                (c.city_id, c.name, c.longitude, c.latitude) for c in snapshot.cities
            ])
            connection.executemany("INSERT INTO stations VALUES (?, ?, ?, ?, ?)", [
                (s.station_id, s.city_id, s.name, s.longitude, s.latitude) for s in snapshot.stations
            ])
            connection.executemany("INSERT INTO trips VALUES (?, ?, ?)", [
                (t.trip_id, t.display_code, t.train_type) for t in snapshot.trips
            ])
            connection.executemany("INSERT INTO calendars VALUES (?, ?)", [
                (t.trip_id, json.dumps(sorted(d.isoformat() for d in t.service_dates)))
                for t in snapshot.trips if t.service_dates is not None
            ])
            connection.executemany("INSERT INTO stop_times VALUES (?, ?, ?, ?, ?, ?, ?)", (
                (t.trip_id, s.sequence, s.station_id, s.arrival_seconds, s.departure_seconds,
                 int(s.can_board), int(s.can_alight))
                for t in snapshot.trips for s in t.stops
            ))
            connection.executemany("INSERT INTO metadata VALUES (?, ?)", snapshot.metadata.items())

    def save_result(self, result) -> None:
        """结果连同当时快照说明持久化，重启或刷新后仍可导出。"""

        with self.connect() as connection:
            connection.execute("INSERT OR REPLACE INTO results VALUES (?, ?, ?)",
                               (result.query_id, result.model_dump_json(), result.generated_at.isoformat()))
            connection.execute("DELETE FROM results WHERE query_id NOT IN "
                               "(SELECT query_id FROM results ORDER BY created_at DESC LIMIT 100)")

    def load_result(self, query_id: str):
        """最多保留最近一百份查询结果，限制本地数据库体积。"""

        from .models import ReachabilityResponse
        with self.connect() as connection:
            row = connection.execute("SELECT payload FROM results WHERE query_id = ?", (query_id,)).fetchone()
        return ReachabilityResponse.model_validate_json(row["payload"]) if row else None

    def save_refresh_state(self, state: dict) -> None:
        """记录最近一次刷新阶段，进程中断后可向用户说明而非一直等待。"""

        with self.connect() as connection:
            connection.execute("INSERT OR REPLACE INTO refresh_state VALUES (1, ?)",
                               (json.dumps(state, ensure_ascii=False),))

    def load_refresh_state(self) -> dict:
        """恢复上次任务的最终状态。"""

        with self.connect() as connection:
            row = connection.execute("SELECT payload FROM refresh_state WHERE id = 1").fetchone()
        return json.loads(row["payload"]) if row else {"state": "idle", "message": "尚未刷新"}

    def metadata(self) -> dict[str, str]:
        """返回快照来源元数据。"""

        with self.connect() as connection:
            rows = connection.execute("SELECT key, value FROM metadata").fetchall()
        return {row["key"]: row["value"] for row in rows}

    def refresh_demo_timestamp(self) -> datetime:
        """模拟手动刷新，并更新演示快照时间。"""

        fetched_at = datetime.now().astimezone()
        with self.connect() as connection:
            connection.execute(
                "UPDATE metadata SET value = ? WHERE key = 'fetched_at'",
                (fetched_at.isoformat(),),
            )
        return fetched_at
