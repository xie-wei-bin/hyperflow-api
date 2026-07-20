"""
认证接口测试

面试点：每个测试函数测一个场景（成功/失败/边界），断言只验证预期行为。
测试函数名 = 文档：test_register_success → 一看就知道测"注册成功"
"""

import pytest
from httpx import AsyncClient

# ── 注册 ────────────────────────────────────────


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
    assert data["data"]["email"] == "test@example.com"


@pytest.mark.asyncio
async def test_register_duplicate_username(async_client: AsyncClient):
    """重复用户名注册 → 409"""
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
async def test_register_duplicate_email(async_client: AsyncClient):
    """重复邮箱注册 → 409"""
    await async_client.post(
        "/api/auth/register",
        json={"username": "user_a", "email": "same@example.com", "password": "test123456"},
    )
    response = await async_client.post(
        "/api/auth/register",
        json={"username": "user_b", "email": "same@example.com", "password": "test123456"},
    )
    assert response.status_code == 409


@pytest.mark.asyncio
async def test_register_short_password(async_client: AsyncClient):
    """密码太短 → 422 校验失败"""
    response = await async_client.post(
        "/api/auth/register",
        json={"username": "shortpw", "email": "short@example.com", "password": "ab"},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_register_invalid_email(async_client: AsyncClient):
    """邮箱格式错误 → 422 校验失败"""
    response = await async_client.post(
        "/api/auth/register",
        json={"username": "bademail", "email": "not-an-email", "password": "test123456"},
    )
    assert response.status_code == 422


# ── 登录 ────────────────────────────────────────


@pytest.mark.asyncio
async def test_login_success(async_client: AsyncClient):
    """正常登录（用户名）→ 返回 access + refresh token"""
    await async_client.post(
        "/api/auth/register",
        json={"username": "loginuser", "email": "login@example.com", "password": "login123456"},
    )
    response = await async_client.post(
        "/api/auth/login", json={"username": "loginuser", "password": "login123456"}
    )
    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data["data"]
    assert "refresh_token" in data["data"]
    assert data["data"]["token_type"] == "bearer"


@pytest.mark.asyncio
async def test_login_with_email(async_client: AsyncClient):
    """邮箱登录 — 支持用户名或邮箱"""
    await async_client.post(
        "/api/auth/register",
        json={"username": "emailuser", "email": "emailonly@example.com", "password": "test123456"},
    )
    response = await async_client.post(
        "/api/auth/login", json={"username": "emailonly@example.com", "password": "test123456"}
    )
    assert response.status_code == 200
    assert "access_token" in response.json()["data"]


@pytest.mark.asyncio
async def test_login_wrong_password(async_client: AsyncClient):
    """密码错误 → 401"""
    await async_client.post(
        "/api/auth/register",
        json={"username": "wrongpw", "email": "wrongpw@example.com", "password": "correct123"},
    )
    response = await async_client.post(
        "/api/auth/login", json={"username": "wrongpw", "password": "wrongpassword"}
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_login_nonexistent_user(async_client: AsyncClient):
    """不存在的用户登录 → 401"""
    response = await async_client.post(
        "/api/auth/login", json={"username": "nobody", "password": "whatever123"}
    )
    assert response.status_code == 401


# ── Token 刷新 ───────────────────────────────────


@pytest.mark.asyncio
async def test_refresh_token_success(async_client: AsyncClient):
    """正常刷新 Token — 用 refresh token 换新 access token"""
    # 注册 + 登录获取 refresh token
    await async_client.post(
        "/api/auth/register",
        json={"username": "refreshuser", "email": "refresh@example.com", "password": "test123456"},
    )
    login_resp = await async_client.post(
        "/api/auth/login", json={"username": "refreshuser", "password": "test123456"}
    )
    refresh_token = login_resp.json()["data"]["refresh_token"]

    # 用 refresh token 换新的
    response = await async_client.post(
        "/api/auth/refresh", json={"refresh_token": refresh_token}
    )
    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data["data"]
    assert "refresh_token" in data["data"]


@pytest.mark.asyncio
async def test_refresh_with_access_token(async_client: AsyncClient):
    """用 access token 刷新 → 被拒绝（只能 refresh token 才能刷新）"""
    await async_client.post(
        "/api/auth/register",
        json={"username": "accref", "email": "accref@example.com", "password": "test123456"},
    )
    login_resp = await async_client.post(
        "/api/auth/login", json={"username": "accref", "password": "test123456"}
    )
    access_token = login_resp.json()["data"]["access_token"]

    response = await async_client.post(
        "/api/auth/refresh", json={"refresh_token": access_token}
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_refresh_invalid_token(async_client: AsyncClient):
    """用伪造 token 刷新 → 401"""
    response = await async_client.post(
        "/api/auth/refresh", json={"refresh_token": "not-a-valid-token"}
    )
    assert response.status_code == 401


# ── 当前用户信息 ─────────────────────────────────


@pytest.mark.asyncio
async def test_get_me_success(async_client: AsyncClient):
    """已登录获取个人信息 → 200"""
    await async_client.post(
        "/api/auth/register",
        json={"username": "meuser", "email": "me@example.com", "password": "test123456"},
    )
    login_resp = await async_client.post(
        "/api/auth/login", json={"username": "meuser", "password": "test123456"}
    )
    token = login_resp.json()["data"]["access_token"]

    response = await async_client.get(
        "/api/auth/me", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["data"]["username"] == "meuser"
    assert data["data"]["email"] == "me@example.com"
    # 查看自己时应该包含 is_active
    assert "is_active" in data["data"]


@pytest.mark.asyncio
async def test_get_me_unauthorized(async_client: AsyncClient):
    """未登录获取个人信息 → 401"""
    response = await async_client.get("/api/auth/me")
    assert response.status_code == 401


# ── 健康检查 ─────────────────────────────────────


@pytest.mark.asyncio
async def test_health_check(async_client: AsyncClient):
    """健康检查 → 200 + database/redis 字段"""
    response = await async_client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["code"] == 200
    assert "database" in data["data"]
    assert "redis" in data["data"]
