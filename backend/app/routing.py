"""基于时刻表快照的分层最早到达搜索。"""

from __future__ import annotations

import heapq
from bisect import bisect_left
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from itertools import count
from zoneinfo import ZoneInfo

from .models import (
    City,
    CityResult,
    DataStatus,
    LegResult,
    ReachabilityRequest,
    ReachabilityResponse,
    Station,
    StationResult,
    Trip,
)


CHINA_TIMEZONE = ZoneInfo("Asia/Shanghai")


@dataclass(frozen=True, slots=True)
class DepartureEvent:
    """一个运行实例在某一站可以上车的发车事件。"""

    trip: Trip
    service_date: date
    stop_index: int
    departure_at: datetime


@dataclass(frozen=True, slots=True)
class Label:
    """搜索过程中不可变的最早到达标签。"""

    station_id: str
    boardings: int
    arrival_at: datetime
    legs: tuple[LegResult, ...]


def _normalize_datetime(value: datetime) -> datetime:
    """将输入时刻统一到北京时间，禁止依赖服务器本地时区。"""

    if value.tzinfo is None:
        return value.replace(tzinfo=CHINA_TIMEZONE)
    return value.astimezone(CHINA_TIMEZONE)


def _at_service_seconds(service_date: date, seconds: int) -> datetime:
    """把允许超过 24 小时的相对秒数转换为绝对北京时间。"""

    midnight = datetime.combine(service_date, time.min, tzinfo=CHINA_TIMEZONE)
    return midnight + timedelta(seconds=seconds)


def _build_departure_index(
    trips: list[Trip],
    start_at: datetime,
    end_at: datetime,
    allowed_types: set[str],
) -> dict[str, list[DepartureEvent]]:
    """展开与查询窗口相交的每日运行模板，并按站点建立发车索引。"""

    selected = [trip for trip in trips if trip.train_type in allowed_types]
    maximum_offset = max(
        (
            max(
                stop.arrival_seconds or 0,
                stop.departure_seconds or 0,
            )
            for trip in selected
            for stop in trip.stops
        ),
        default=0,
    )
    lookback_days = maximum_offset // 86400 + 1
    first_service_date = start_at.date() - timedelta(days=lookback_days)
    last_service_date = end_at.date()

    by_station: dict[str, list[DepartureEvent]] = defaultdict(list)
    current = first_service_date
    while current <= last_service_date:
        for trip in selected:
            if trip.service_dates is not None and current not in trip.service_dates:
                continue
            for stop_index, stop in enumerate(trip.stops):
                if not stop.can_board or stop.departure_seconds is None:
                    continue
                departure_at = _at_service_seconds(current, stop.departure_seconds)
                if start_at <= departure_at <= end_at:
                    by_station[stop.station_id].append(
                        DepartureEvent(
                            trip=trip,
                            service_date=current,
                            stop_index=stop_index,
                            departure_at=departure_at,
                        )
                    )
        current += timedelta(days=1)

    for events in by_station.values():
        events.sort(key=lambda event: event.departure_at)
    return by_station


def calculate_reachability(
    request: ReachabilityRequest,
    cities: list[City],
    stations: list[Station],
    trips: list[Trip],
    data_status: DataStatus,
    query_id: str,
) -> ReachabilityResponse:
    """计算满足时间、车型和同站换乘约束的所有可达车站。"""

    start_at = _normalize_datetime(request.start_at)
    end_at = _normalize_datetime(request.end_at)
    normalized_request = request.model_copy(update={"start_at": start_at, "end_at": end_at})

    city_by_id = {city.city_id: city for city in cities}
    station_by_id = {station.station_id: station for station in stations}
    origin_city = city_by_id[request.origin_city_id]
    origin_stations = [
        station for station in stations if station.city_id == request.origin_city_id
    ]
    origin_station_ids = {station.station_id for station in origin_stations}
    maximum_boardings = request.max_transfers + 1 if request.transfers_enabled else 1
    transfer_delta = timedelta(minutes=request.min_transfer_minutes)
    departure_index = _build_departure_index(
        trips,
        start_at,
        end_at,
        set(request.train_types),
    )

    labels: dict[tuple[str, int], Label] = {}
    queue: list[tuple[datetime, int, Label]] = []
    sequence = count()
    for station in origin_stations:
        label = Label(station.station_id, 0, start_at, ())
        labels[(station.station_id, 0)] = label
        heapq.heappush(queue, (start_at, next(sequence), label))

    direct_station_ids: set[str] = set()
    while queue:
        _, _, label = heapq.heappop(queue)
        if labels.get((label.station_id, label.boardings)) != label:
            continue
        if label.boardings >= maximum_boardings:
            continue

        ready_at = (
            start_at if label.boardings == 0 else label.arrival_at + transfer_delta
        )
        events = departure_index.get(label.station_id, ())
        first = bisect_left(events, ready_at, key=lambda event: event.departure_at)
        for event in events[first:]:
            if event.departure_at > end_at:
                break

            boarding_stop = event.trip.stops[event.stop_index]
            boarding_station = station_by_id[boarding_stop.station_id]
            for destination_stop in event.trip.stops[event.stop_index + 1 :]:
                if not destination_stop.can_alight or destination_stop.arrival_seconds is None:
                    continue
                arrival_at = _at_service_seconds(
                    event.service_date,
                    destination_stop.arrival_seconds,
                )
                if arrival_at > end_at:
                    break
                if arrival_at <= event.departure_at:
                    continue

                destination_station = station_by_id[destination_stop.station_id]
                next_boardings = label.boardings + 1
                leg = LegResult(
                    train_code=event.trip.display_code,
                    train_type=event.trip.train_type,
                    from_station_id=boarding_station.station_id,
                    from_station_name=boarding_station.name,
                    to_station_id=destination_station.station_id,
                    to_station_name=destination_station.name,
                    departure_at=event.departure_at,
                    arrival_at=arrival_at,
                    service_date=event.service_date,
                )
                next_label = Label(
                    station_id=destination_station.station_id,
                    boardings=next_boardings,
                    arrival_at=arrival_at,
                    legs=label.legs + (leg,),
                )
                key = (next_label.station_id, next_boardings)
                previous = labels.get(key)
                if previous is None or next_label.arrival_at < previous.arrival_at:
                    labels[key] = next_label
                    heapq.heappush(
                        queue,
                        (next_label.arrival_at, next(sequence), next_label),
                    )
                if next_boardings == 1:
                    direct_station_ids.add(destination_station.station_id)

    station_results: list[StationResult] = []
    for station in stations:
        if station.station_id in origin_station_ids:
            continue
        candidates = [labels[(station.station_id, n)] for n in range(1, maximum_boardings + 1)
                      if (station.station_id, n) in labels]
        if not candidates:
            continue
        best = min(candidates, key=lambda item: (item.arrival_at, item.boardings))
        city = city_by_id[station.city_id]
        station_results.append(
            StationResult(
                station_id=station.station_id,
                station_name=station.name,
                city_id=city.city_id,
                city_name=city.name,
                longitude=station.longitude,
                latitude=station.latitude,
                arrival_at=best.arrival_at,
                transfers=best.boardings - 1,
                direct_available=station.station_id in direct_station_ids,
                legs=list(best.legs),
            )
        )

    grouped: dict[str, list[StationResult]] = defaultdict(list)
    for station_result in station_results:
        grouped[station_result.city_id].append(station_result)

    city_results: list[CityResult] = []
    for city_id, grouped_stations in grouped.items():
        city = city_by_id[city_id]
        grouped_stations.sort(key=lambda item: (item.arrival_at, item.station_name))
        city_results.append(
            CityResult(
                city_id=city.city_id,
                city_name=city.name,
                longitude=city.longitude,
                latitude=city.latitude,
                earliest_arrival_at=grouped_stations[0].arrival_at,
                direct_available=any(item.direct_available for item in grouped_stations),
                stations=grouped_stations,
            )
        )
    city_results.sort(key=lambda item: (item.earliest_arrival_at, item.city_name))

    return ReachabilityResponse(
        query_id=query_id,
        generated_at=datetime.now(CHINA_TIMEZONE),
        origin_city_id=origin_city.city_id,
        origin_city_name=origin_city.name,
        origin_longitude=origin_city.longitude,
        origin_latitude=origin_city.latitude,
        origin_station_ids=sorted(origin_station_ids),
        data_status=data_status,
        request=normalized_request,
        cities=city_results,
        reachable_city_count=len(city_results),
        reachable_station_count=len(station_results),
    )
