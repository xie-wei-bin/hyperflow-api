"""
认证接口测试

面试点：每个测试函数测一个场景（成功/失败/边界），断言只验证预期行为。
测试函数名 = 文档：test_register_success → 一看就知道测"注册成功"
"""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_register_success(async_client: AsyncClient):
    """正常注册 → 返回 201"""
    response = await async_client.post(
        "/api/auth/register",
        json={"username": "testuser", "email": "test@example.com", "password": "test123456"},
    )
    assert response.status_code == 201
    data = response.json()
    assert data["code"] == 201
    assert data["data"]["username"] == "testuser"


@pytest.mark.asyncio
async def test_register_duplicate(async_client: AsyncClient):
    """重复注册 → 409"""
    await async_client.post(
        "/api/auth/register",
        json={"username": "duplicate", "email": "dup@example.com", "password": "test123456"},
    )
    response = await async_client.post(
        "/api/auth/register",
        json={"username": "duplicate", "email": "dup2@example.com", "password": "test123456"},
    )
    assert response.status_code == 409


@pytest.mark.asyncio
async def test_login_success(async_client: AsyncClient):
    """正常登录 → 返回 access + refresh token"""
    await async_client.post(
        "/api/auth/register",
        json={"username": "loginuser", "email": "login@example.com", "password": "login123456"},
    )
    response = await async_client.post(
        "/api/auth/login",
        json={"username": "loginuser", "password": "login123456"},
    )
    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data["data"]
    assert "refresh_token" in data["data"]


@pytest.mark.asyncio
async def test_login_wrong_password(async_client: AsyncClient):
    """密码错误 → 401"""
    await async_client.post(
        "/api/auth/register",
        json={"username": "wrongpw", "email": "wrongpw@example.com", "password": "correct123"},
    )
    response = await async_client.post(
        "/api/auth/login",
        json={"username": "wrongpw", "password": "wrongpassword"},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_health_check(async_client: AsyncClient):
    """健康检查 → 200 + database/redis 字段"""
    response = await async_client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert "database" in data["data"]
