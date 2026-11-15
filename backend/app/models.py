"""铁路活动范围工具的领域模型与 API 数据结构。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field, model_validator


@dataclass(frozen=True, slots=True)
class City:
    """归一化后的城市。"""

    city_id: str
    name: str
    longitude: float
    latitude: float


@dataclass(frozen=True, slots=True)
class Station:
    """可办理客运业务的物理车站。"""

    station_id: str
    city_id: str
    name: str
    longitude: float
    latitude: float


@dataclass(frozen=True, slots=True)
class StopTime:
    """列车在某一车站的相对到发时刻，单位为始发日零点后的秒数。"""

    station_id: str
    sequence: int
    arrival_seconds: int | None
    departure_seconds: int | None
    can_board: bool = True
    can_alight: bool = True


@dataclass(frozen=True, slots=True)
class Trip:
    """列车运行模板；服务日期为空时表示每日运行。"""

    trip_id: str
    display_code: str
    train_type: str
    stops: tuple[StopTime, ...]
    service_dates: frozenset[date] | None = None


class ReachabilityRequest(BaseModel):
    """一次可达范围计算请求。"""

    origin_city_id: str
    start_at: datetime
    end_at: datetime
    train_types: list[str] = Field(min_length=1)
    transfers_enabled: bool = False
    max_transfers: int = Field(default=0, ge=0, le=4)
    min_transfer_minutes: int = Field(default=30, ge=0, le=720)

    @model_validator(mode="after")
    def validate_window(self) -> "ReachabilityRequest":
        """确保时间窗口有效，并在关闭换乘时归一化换乘次数。"""

        timezone = ZoneInfo("Asia/Shanghai")
        self.start_at = self.start_at.replace(tzinfo=timezone) if self.start_at.tzinfo is None else self.start_at.astimezone(timezone)
        self.end_at = self.end_at.replace(tzinfo=timezone) if self.end_at.tzinfo is None else self.end_at.astimezone(timezone)
        allowed = {"G", "D", "C", "K", "T", "Z", "Y", "S", "OTHER"}
        if not set(self.train_types) <= allowed:
            raise ValueError("不支持的列车类型")
        self.train_types = sorted(set(self.train_types))
        if self.end_at <= self.start_at:
            raise ValueError("最晚到达时间必须晚于最早出发时间")
        if self.end_at - self.start_at > timedelta(days=14):
            raise ValueError("单次查询时间范围不能超过 14 天")
        if not self.transfers_enabled:
            self.max_transfers = 0
        return self


class LegResult(BaseModel):
    """一段连续乘车信息。"""

    train_code: str
    train_type: str
    from_station_id: str
    from_station_name: str
    to_station_id: str
    to_station_name: str
    departure_at: datetime
    arrival_at: datetime
    service_date: date | None = None


class StationResult(BaseModel):
    """一个可达车站及其代表方案。"""

    station_id: str
    station_name: str
    city_id: str
    city_name: str
    longitude: float
    latitude: float
    arrival_at: datetime
    transfers: int
    direct_available: bool
    legs: list[LegResult]


class CityResult(BaseModel):
    """按城市聚合的可达结果。"""

    city_id: str
    city_name: str
    longitude: float
    latitude: float
    earliest_arrival_at: datetime
    direct_available: bool
    stations: list[StationResult]


class DataStatus(BaseModel):
    """当前铁路快照的来源及覆盖状态。"""

    source_kind: Literal["demo", "external"]
    source_name: str
    fetched_at: datetime
    coverage_status: Literal["complete_demo", "partial", "complete"]
    note: str
    version: str = ""
    valid_from: date | None = None
    valid_until: date | None = None
    trip_count: int = 0
    station_count: int = 0
    rejected_count: int = 0
    unknown_city_count: int = 0
    source_url: str = ""
    license: str = ""


class ReachabilityResponse(BaseModel):
    """完整计算响应，可直接用于地图展示和导出。"""

    query_id: str
    generated_at: datetime
    origin_city_id: str
    origin_city_name: str
    origin_longitude: float
    origin_latitude: float
    origin_station_ids: list[str]
    data_status: DataStatus
    request: ReachabilityRequest
    cities: list[CityResult]
    reachable_city_count: int
    reachable_station_count: int


class ExportRequest(BaseModel):
    """基于已计算结果创建导出文件。"""

    query_id: str
    mode: Literal["online", "offline"]
