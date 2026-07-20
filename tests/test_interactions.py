"""
互动接口测试（点赞/收藏）

面试点：测试幂等性（重复操作不报错）、认证、完整业务流。
每个测试独立运行，不依赖其他测试的状态。
"""

import pytest
from httpx import AsyncClient


async def _register_and_login(client: AsyncClient, username: str) -> str:
    """注册 + 登录，返回 access_token"""
    await client.post(
        "/api/auth/register",
        json={"username": username, "email": f"{username}@test.com", "password": "test123456"},
    )
    resp = await client.post(
        "/api/auth/login", json={"username": username, "password": "test123456"}
    )
    return resp.json()["data"]["access_token"]


async def _create_article(client: AsyncClient, token: str, slug: str) -> int:
    """创建已发布文章，返回文章 ID"""
    resp = await client.post(
        "/api/articles",
        json={"title": f"文章-{slug}", "slug": slug, "content": "测试内容", "status": "published"},
        headers={"Authorization": f"Bearer {token}"},
    )
    return resp.json()["data"]["id"]


# ── 点赞 ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_like_unauthorized(async_client: AsyncClient):
    """未登录点赞 → 401"""
    response = await async_client.post("/api/articles/1/like")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_like_success(async_client: AsyncClient):
    """正常点赞 → 200"""
    token = await _register_and_login(async_client, "liker")
    article_id = await _create_article(async_client, token, "like-article")

    resp = await async_client.post(
        f"/api/articles/{article_id}/like",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["message"] == "点赞成功"


@pytest.mark.asyncio
async def test_like_idempotent(async_client: AsyncClient):
    """重复点赞 — 幂等返回成功，不报错"""
    token = await _register_and_login(async_client, "doubleliker")
    article_id = await _create_article(async_client, token, "idempotent-like")

    # 第一次点赞
    resp1 = await async_client.post(
        f"/api/articles/{article_id}/like",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp1.status_code == 200

    # 第二次点赞（幂等）
    resp2 = await async_client.post(
        f"/api/articles/{article_id}/like",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp2.status_code == 200
    # 幂等：不报错，返回"已点赞"


@pytest.mark.asyncio
async def test_unlike_success(async_client: AsyncClient):
    """取消点赞 → 200"""
    token = await _register_and_login(async_client, "unliker")
    article_id = await _create_article(async_client, token, "unlike-article")

    # 先点赞
    await async_client.post(
        f"/api/articles/{article_id}/like",
        headers={"Authorization": f"Bearer {token}"},
    )
    # 再取消
    resp = await async_client.delete(
        f"/api/articles/{article_id}/like",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["message"] == "已取消点赞"


@pytest.mark.asyncio
async def test_unlike_without_like(async_client: AsyncClient):
    """未点赞时取消 → 200（幂等）"""
    token = await _register_and_login(async_client, "neverliked")
    article_id = await _create_article(async_client, token, "never-liked")

    resp = await async_client.delete(
        f"/api/articles/{article_id}/like",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_like_nonexistent_article(async_client: AsyncClient):
    """点赞不存在的文章 → 404"""
    token = await _register_and_login(async_client, "ghostliker")
    resp = await async_client.post(
        "/api/articles/99999/like",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404


# ── 收藏 ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_favorite_unauthorized(async_client: AsyncClient):
    """未登录收藏 → 401"""
    response = await async_client.post("/api/articles/1/favorite")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_favorite_success(async_client: AsyncClient):
    """正常收藏 → 200"""
    token = await _register_and_login(async_client, "favoriter")
    article_id = await _create_article(async_client, token, "fav-article")

    resp = await async_client.post(
        f"/api/articles/{article_id}/favorite",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["message"] == "收藏成功"


@pytest.mark.asyncio
async def test_favorite_idempotent(async_client: AsyncClient):
    """重复收藏 — 幂等返回成功"""
    token = await _register_and_login(async_client, "doublefav")
    article_id = await _create_article(async_client, token, "idempotent-fav")

    await async_client.post(
        f"/api/articles/{article_id}/favorite",
        headers={"Authorization": f"Bearer {token}"},
    )
    resp = await async_client.post(
        f"/api/articles/{article_id}/favorite",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_unfavorite_success(async_client: AsyncClient):
    """取消收藏 → 200"""
    token = await _register_and_login(async_client, "unfavoriter")
    article_id = await _create_article(async_client, token, "unfav-article")

    await async_client.post(
        f"/api/articles/{article_id}/favorite",
        headers={"Authorization": f"Bearer {token}"},
    )
    resp = await async_client.delete(
        f"/api/articles/{article_id}/favorite",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200


# ── 我的收藏列表 ──────────────────────────────────


@pytest.mark.asyncio
async def test_my_favorites_unauthorized(async_client: AsyncClient):
    """未登录查看收藏 → 401"""
    response = await async_client.get("/api/users/me/favorites")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_my_favorites_success(async_client: AsyncClient):
    """查看我的收藏列表 → 200 + 收藏的文章在里面"""
    token = await _register_and_login(async_client, "favlist")
    article_id = await _create_article(async_client, token, "my-fav-article")

    # 收藏文章
    await async_client.post(
        f"/api/articles/{article_id}/favorite",
        headers={"Authorization": f"Bearer {token}"},
    )

    resp = await async_client.get(
        "/api/users/me/favorites",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["data"]["total"] == 1
    assert len(data["data"]["items"]) == 1
    assert data["data"]["items"][0]["id"] == article_id
