"""API 与导出功能的集成测试。"""

import io
import json
import re
import zipfile

from fastapi.testclient import TestClient

from backend.app.main import app


client = TestClient(app)


def test_health_and_city_catalog() -> None:
    """服务启动后应能列出包含多个始发站的北京。"""

    assert client.get("/api/health").json()["status"] == "ok"
    response = client.get("/api/cities", params={"q": "北京"})

    assert response.status_code == 200
    assert response.json()[0]["station_count"] == 3


def test_reachability_and_offline_export() -> None:
    """完整查询结果应可导出为不依赖网络的 ZIP。"""

    query = {
        "origin_city_id": "beijing",
        "start_at": "2026-11-20T20:00:00+08:00",
        "end_at": "2026-11-21T12:00:00+08:00",
        "train_types": ["G", "D", "K"],
        "transfers_enabled": True,
        "max_transfers": 1,
        "min_transfer_minutes": 30,
    }
    response = client.post("/api/reachability", json=query)
    assert response.status_code == 200
    payload = response.json()
    assert payload["reachable_city_count"] == 9
    assert payload["data_status"]["source_kind"] == "demo"

    export = client.get(
        f"/api/results/{payload['query_id']}/export",
        params={"mode": "offline"},
    )
    assert export.status_code == 200
    assert export.headers["content-type"] == "application/zip"

    with zipfile.ZipFile(io.BytesIO(export.content)) as archive:
        assert set(archive.namelist()) == {
            "index.html",
            "export-manifest.json",
            "README.txt",
            "result.json",
        }
        html = archive.read("index.html").decode("utf-8")
        assert "杭州" in html
        assert "完全离线导出" in html
        assert "https://" not in html


def test_online_export_inlines_the_production_frontend() -> None:
    """在线 HTML 应复用正式前端且不依赖本机静态资源或后端。"""

    response = client.post(
        "/api/reachability",
        json={
            "origin_city_id": "hangzhou",
            "start_at": "2026-11-20T20:00:00+08:00",
            "end_at": "2026-11-21T12:00:00+08:00",
            "train_types": ["G", "D", "K"],
            "transfers_enabled": False,
        },
    )
    assert response.status_code == 200

    export = client.get(
        f"/api/results/{response.json()['query_id']}/export",
        params={"mode": "online"},
    )
    assert export.status_code == 200
    document = export.text
    assert "window.__TARINMAP_EXPORT_DATA__=" in document
    assert '"origin_stations"' in document
    assert "/assets/" not in document
    assert "data:text/javascript;base64," in document
    bootstrap = json.loads(re.search(
        r"window\.__TARINMAP_EXPORT_DATA__=(.*?);</script>", document,
    ).group(1))
    assert "direct_result" not in bootstrap


def test_online_export_embeds_direct_variant_only_when_transfers_are_enabled() -> None:
    """开启中转的查询应额外保存仅直达结果，供下载后离线切换。"""

    response = client.post(
        "/api/reachability",
        json={
            "origin_city_id": "beijing",
            "start_at": "2026-11-20T20:00:00+08:00",
            "end_at": "2026-11-21T12:00:00+08:00",
            "train_types": ["G", "D", "K"],
            "transfers_enabled": True,
            "max_transfers": 1,
            "min_transfer_minutes": 30,
        },
    )
    assert response.status_code == 200

    export = client.get(
        f"/api/results/{response.json()['query_id']}/export",
        params={"mode": "online"},
    )
    assert export.status_code == 200
    bootstrap = json.loads(re.search(
        r"window\.__TARINMAP_EXPORT_DATA__=(.*?);</script>", export.text,
    ).group(1))
    assert bootstrap["result"]["request"]["transfers_enabled"] is True
    assert bootstrap["direct_result"]["request"]["transfers_enabled"] is False
    assert bootstrap["direct_result"]["request"]["max_transfers"] == 0


def test_compare_calculates_individual_queries_on_one_snapshot() -> None:
    query = {
        "start_at": "2026-11-20T20:00:00+08:00",
        "end_at": "2026-11-21T12:00:00+08:00",
        "train_types": ["G", "D", "K"],
    }
    response = client.post("/api/reachability/compare", json=[
        {**query, "origin_city_id": "beijing"},
        {**query, "origin_city_id": "hangzhou", "transfers_enabled": True, "max_transfers": 1},
    ])
    assert response.status_code == 200
    first, second = response.json()
    assert [first["origin_city_id"], second["origin_city_id"]] == ["beijing", "hangzhou"]
    assert first["data_status"] == second["data_status"]
    assert first["request"]["max_transfers"] == 0
    assert second["request"]["max_transfers"] == 1


def test_invalid_window_returns_validation_error() -> None:
    """到达截止时间不晚于出发时间时应返回可理解的校验错误。"""

    response = client.post(
        "/api/reachability",
        json={
            "origin_city_id": "beijing",
            "start_at": "2026-11-21T12:00:00+08:00",
            "end_at": "2026-11-21T11:00:00+08:00",
            "train_types": ["G"],
        },
    )

    assert response.status_code == 422


def test_origin_station_must_belong_to_selected_city() -> None:
    """始发站筛选不能引用其他城市的车站。"""

    response = client.post(
        "/api/reachability",
        json={
            "origin_city_id": "beijing",
            "origin_station_ids": ["hangzhou_east"],
            "start_at": "2026-11-20T20:00:00+08:00",
            "end_at": "2026-11-21T12:00:00+08:00",
            "train_types": ["G"],
        },
    )

    assert response.status_code == 422
    assert "始发站" in response.json()["detail"]
