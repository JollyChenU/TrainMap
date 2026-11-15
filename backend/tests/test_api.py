"""API 与导出功能的集成测试。"""

import io
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
