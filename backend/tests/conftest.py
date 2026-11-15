"""所有 API 测试使用临时数据库，不覆盖本机真实快照。"""

import pytest

from backend.app import main
from backend.app.repository import RailwayRepository


@pytest.fixture(autouse=True)
def isolated_api(tmp_path, monkeypatch):
    """隔离模块级仓库和内存状态。"""

    monkeypatch.setattr(main, "repository", RailwayRepository(tmp_path / "api.db"))
    monkeypatch.setattr(main, "result_store", {})
    monkeypatch.setattr(main, "refresh_job", {"state": "idle", "message": "测试"})
