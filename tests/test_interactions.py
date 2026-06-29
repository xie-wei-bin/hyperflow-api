"""
互动接口测试（点赞/收藏）

面试点：只测了认证失败场景（fast feedback）。
业务成功场景依赖真实数据（需先创建文章），这是集成测试的范畴。
"""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_like_unauthorized(async_client: AsyncClient):
    """未登录点赞 → 401"""
    response = await async_client.post("/api/articles/1/like")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_favorite_unauthorized(async_client: AsyncClient):
    """未登录收藏 → 401"""
    response = await async_client.post("/api/articles/1/favorite")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_my_favorites_unauthorized(async_client: AsyncClient):
    """未登录查看收藏 → 401"""
    response = await async_client.get("/api/users/me/favorites")
    assert response.status_code == 401
