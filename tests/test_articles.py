"""文章接口测试"""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_list_articles_empty(async_client: AsyncClient):
    """空数据库 → 文章列表返回空数组"""
    response = await async_client.get("/api/articles")
    assert response.status_code == 200
    data = response.json()
    assert data["data"]["items"] == []
    assert data["data"]["total"] == 0


@pytest.mark.asyncio
async def test_create_article_unauthorized(async_client: AsyncClient):
    """未登录创建文章 → 401"""
    response = await async_client.post(
        "/api/articles",
        json={"title": "测试文章", "slug": "test-article", "content": "这是测试内容"},
    )
    assert response.status_code == 401
