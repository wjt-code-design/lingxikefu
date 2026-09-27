"""文档/schema 暴露面测试（L3 补漏，审计 M5 任务 3，2026-09-27）。

修复前：prod 只关 docs_url/redoc_url，**/openapi.json 仍在线**——匿名可枚举
全部端点与请求/响应 schema（攻击面地图）。修复后：ENV=prod 三件套同关。
边界：契约工具链（backend/scripts/generate_openapi.py、scripts/check_contracts.py、
ci.yml 契约门禁）从 app 对象直接 ``app.openapi()`` 生成 schema、不走 HTTP，
关路由不影响它们——本文件同时钉住「schema 仍可对象生成」与「dev 仍在线」两侧。
"""
from __future__ import annotations

import pytest
from app.main import _docs_exposure_kwargs, app
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _mini(env: str) -> FastAPI:
    """按指定 env 的暴露参数装配最小 app（app.main 的真实 app 在测试环境为 dev，
    prod 分支必须用同一 helper 另立实例才能实测 404）。"""
    mini = FastAPI(**_docs_exposure_kwargs(env))

    @mini.get("/ping")
    def _ping() -> dict:  # pragma: no cover - 路由注册验证用
        return {"ok": True}

    return mini


def test_prod_kwargs_close_all_three_surfaces() -> None:
    """prod 配置三件套同关（漏掉 openapi_url 即回到泄漏原样——本条专杀回退）。"""
    assert _docs_exposure_kwargs("prod") == {
        "docs_url": None,
        "redoc_url": None,
        "openapi_url": None,
    }


@pytest.mark.parametrize("env", ["dev", "test"])
def test_non_prod_keeps_exposure_defaults(env: str) -> None:
    """dev/test 不加任何关闭参数（调试面/契约工具保留）。"""
    assert _docs_exposure_kwargs(env) == {}


def test_prod_openapi_json_404_and_schema_still_generable() -> None:
    """TestClient 打 prod 装配的 app：/openapi.json、/docs、/redoc 全 404；
    业务路由不受影响；app.openapi() 仍可对象生成（契约门禁数据路径不静默）。"""
    with TestClient(_mini("prod")) as c:
        assert c.get("/openapi.json").status_code == 404
        assert c.get("/docs").status_code == 404
        assert c.get("/redoc").status_code == 404
        assert c.get("/ping").status_code == 200
    schema = _mini("prod").openapi()
    assert schema.get("openapi"), "app.openapi() 对象生成失效（契约工具链被打坏）"


def test_dev_openapi_json_served() -> None:
    """dev 装配：/openapi.json 在线（修复不扩大化到非 prod）。"""
    with TestClient(_mini("dev")) as c:
        assert c.get("/openapi.json").status_code == 200
        assert c.get("/docs").status_code == 200


def test_real_app_current_env_still_serves_schema() -> None:
    """测试环境（ENV=dev，conftest 未覆盖 ENV）：主 app 的 /openapi.json 仍 200，
    generate_openapi.py 写出的 contracts/api-schema.json 数据路径保持不红。"""
    with TestClient(app) as c:
        assert c.get("/openapi.json").status_code == 200
