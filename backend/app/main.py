"""铁路活动范围地图工具的 FastAPI 入口。"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path
from threading import Lock, RLock, Thread
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles

from .exporter import build_html, build_offline_zip
from .gtfs import ADAPTER_VERSION, fetch_snapshot
from .models import DataStatus, ReachabilityRequest, ReachabilityResponse
from .repository import RailwayRepository
from .routing import calculate_reachability
from .verification import verify_leg


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATABASE_PATH = Path(
    os.getenv("TARINMAP_DATABASE", PROJECT_ROOT / "backend" / "data" / "tarinmap.db")
)

app = FastAPI(
    title="铁路活动范围地图 API",
    version="0.1.0",
    description="根据铁路时刻表快照计算指定时间窗口内的可达城市与车站。",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)

repository = RailwayRepository(DATABASE_PATH)
result_store: dict[str, ReachabilityResponse] = {}
result_lock = Lock()
snapshot_lock = RLock()
job_lock = Lock()
refresh_job = repository.load_refresh_state()
if refresh_job["state"] == "running":
    refresh_job = {"state": "failed", "message": "上次刷新因服务重启中断，旧快照已保留，可重新刷新"}
    repository.save_refresh_state(refresh_job)


def current_data_status() -> DataStatus:
    """将数据库元数据转换为对外的数据状态。"""

    metadata = repository.metadata()
    return DataStatus(
        source_kind=metadata.get("source_kind", "demo"),
        source_name=metadata.get("source_name", "未知来源"),
        fetched_at=datetime.fromisoformat(metadata["fetched_at"]),
        coverage_status=metadata.get("coverage_status", "partial"),
        note=(
            "当前为内置演示快照，只用于验证产品流程和算法，不代表真实铁路时刻。"
            if metadata.get("source_kind") == "demo"
            else (
                f"参考快照，非全国完整性保证；隔离 {metadata.get('rejected_count', '0')} 个异常车次，"
                f"{metadata.get('unknown_city_count', '0')} 个站点城市归属待核验。"
            )
        ),
        **{key: metadata[key] for key in (
            "version", "valid_from", "valid_until", "trip_count", "station_count",
            "rejected_count", "unknown_city_count", "source_url", "license",
        ) if key in metadata},
    )


def query_identifier(request: ReachabilityRequest) -> str:
    """为查询参数和当前快照生成可复用的稳定标识。"""

    payload = {
        "request": request.model_dump(mode="json"),
        "snapshot": repository.metadata().get("fetched_at"),
        "algorithm": "calendar-earliest-arrival-v3",
    }
    digest = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return digest[:20]


@app.get("/api/health")
def health() -> dict[str, str]:
    """提供启动检查，不访问外部铁路服务。"""

    return {"status": "ok", "version": app.version}


@app.get("/api/cities")
def list_cities(q: str = Query(default="", max_length=40)) -> list[dict]:
    """搜索可选始发城市，并附带其客运站数量。"""

    cities = repository.list_cities(q)
    stations = repository.list_stations()
    station_counts: dict[str, int] = {}
    for station in stations:
        station_counts[station.city_id] = station_counts.get(station.city_id, 0) + 1
    return [
        {
            **asdict(city),
            "station_count": station_counts.get(city.city_id, 0),
        }
        for city in cities if station_counts.get(city.city_id, 0) > 0
    ]


@app.get("/api/cities/{city_id}/stations")
def list_city_stations(city_id: str) -> list[dict]:
    """列出所选城市中会作为起点的全部车站。"""

    if repository.get_city(city_id) is None:
        raise HTTPException(status_code=404, detail="城市不存在")
    return [asdict(station) for station in repository.list_stations(city_id)]


@app.get("/api/data-status", response_model=DataStatus)
def data_status() -> DataStatus:
    """返回当前快照来源、获取时间和参考说明。"""

    return current_data_status()


def _refresh_worker() -> None:
    """下载阶段不锁定查询，只有提交快照时与查询互斥。"""

    def progress(message: str) -> None:
        with job_lock:
            refresh_job.update(state="running", message=message)
            repository.save_refresh_state(refresh_job)
    try:
        metadata = repository.metadata()
        # 适配器升级后重新校验同一发布，避免旧城市映射永远被同版本缓存跳过。
        existing_version = metadata.get("version", "") if metadata.get("adapter_version") == ADAPTER_VERSION else ""
        candidate = fetch_snapshot(progress, existing_version)
        if candidate is not None:
            progress("正在提交快照")
            with snapshot_lock:
                repository.replace_snapshot(candidate)
                with result_lock:
                    result_store.clear()
        with job_lock:
            refresh_job.update(state="completed", message="快照已更新" if candidate else "已检查，当前为最新发布版本")
            repository.save_refresh_state(refresh_job)
    except Exception as error:
        with job_lock:
            refresh_job.update(state="failed", message=f"更新失败，原快照已保留：{error}")
            repository.save_refresh_state(refresh_job)


@app.post("/api/data-status/refresh", status_code=202)
def refresh_data_status() -> dict:
    """启动去重的后台数据准备任务，不阻塞浏览器请求。"""

    with job_lock:
        if refresh_job["state"] != "running":
            refresh_job.update(state="running", message="准备下载铁路快照")
            repository.save_refresh_state(refresh_job)
            Thread(target=_refresh_worker, daemon=True, name="railway-refresh").start()
        return dict(refresh_job)


@app.get("/api/data-status/refresh")
def refresh_progress() -> dict:
    """前端短轮询任务阶段；服务中断后可重新发起。"""

    with job_lock:
        return dict(refresh_job)


@app.get("/api/data-status/quality")
def data_quality() -> dict:
    """公开异常车次及未知城市清单，便于解释部分覆盖的来源。"""

    metadata = repository.metadata()
    return {
        "rejected_trips": json.loads(metadata.get("rejected_trips", "[]")),
        "unknown_city_stations": json.loads(metadata.get("unknown_city_stations", "[]")),
    }


@app.post("/api/reachability", response_model=ReachabilityResponse)
def reachability(request: ReachabilityRequest) -> ReachabilityResponse:
    """使用当前本地快照计算可达范围，不在计算期间访问外部服务。"""

    with snapshot_lock:
        return _calculate(request)


def _calculate(request: ReachabilityRequest) -> ReachabilityResponse:
    """整个计算读取同一版本快照，避免刷新期间混用站点和车次。"""

    status = current_data_status()
    if status.valid_from and status.valid_until:
        if request.start_at.date() < status.valid_from or request.end_at.date() > status.valid_until:
            raise HTTPException(422, f"查询时间超出快照有效期 {status.valid_from} 至 {status.valid_until}，请刷新或调整日期")
    origin = repository.get_city(request.origin_city_id)
    if origin is None:
        raise HTTPException(status_code=404, detail="始发城市不存在")
    query_id = query_identifier(request)
    with result_lock:
        cached = result_store.get(query_id)
    if cached is None:
        cached = repository.load_result(query_id)
    if cached is not None:
        return cached

    result = calculate_reachability(
        request=request,
        cities=repository.list_cities(),
        stations=repository.list_stations(),
        trips=repository.list_trips(),
        data_status=status,
        query_id=query_id,
    )
    with result_lock:
        if len(result_store) >= 100:
            result_store.pop(next(iter(result_store)))
        result_store[query_id] = result
    repository.save_result(result)
    return result


@app.get("/api/results/{query_id}", response_model=ReachabilityResponse)
def get_result(query_id: str) -> ReachabilityResponse:
    """读取持久化查询结果，支持重启后继续查看。"""

    with result_lock:
        result = result_store.get(query_id)
    result = result or repository.load_result(query_id)
    if result is None:
        raise HTTPException(status_code=404, detail="查询结果不存在或已从最近一百份记录中清理")
    return result


@app.get("/api/results/{query_id}/export")
def export_result(query_id: str, mode: str = Query(pattern="^(online|offline)$")) -> Response:
    """导出单文件 HTML 或完全离线 ZIP。"""

    with result_lock:
        result = result_store.get(query_id)
    result = result or repository.load_result(query_id)
    if result is None:
        raise HTTPException(status_code=404, detail="查询结果不存在或服务已重启")

    safe_name = f"{result.origin_city_name}_铁路可达范围"
    if mode == "online":
        content = build_html(result, offline=False)
        encoded_name = quote(f"{safe_name}.html")
        return Response(
            content=content,
            media_type="text/html; charset=utf-8",
            headers={
                "Content-Disposition": f"attachment; filename*=UTF-8''{encoded_name}"
            },
        )

    content = build_offline_zip(result)
    encoded_name = quote(f"{safe_name}_离线.zip")
    return Response(
        content=content,
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{encoded_name}"},
    )


@app.post("/api/results/{query_id}/verify")
def verify_train(query_id: str, train_code: str, service_date: date) -> dict:
    """只允许核验结果中存在的车次；失败不改变已计算快照。"""

    result = get_result(query_id)
    leg = next((leg for city in result.cities for station in city.stations for leg in station.legs
                if leg.train_code == train_code and leg.service_date == service_date), None)
    if leg is None or result.data_status.source_kind != "external":
        raise HTTPException(422, "未找到可核验的真实行程")
    try:
        return verify_leg(leg)
    except Exception as error:
        raise HTTPException(502, f"经停核验失败：{error}") from error


# 生产构建完成后由同一服务提供网页；开发时仍可使用 Vite 代理。
frontend_dist = PROJECT_ROOT / "frontend" / "dist"
if frontend_dist.exists():
    app.mount("/", StaticFiles(directory=frontend_dist, html=True), name="frontend")
