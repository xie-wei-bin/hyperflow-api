"""评论接口测试"""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_get_comments_article_not_found(async_client: AsyncClient):
    """不存在的文章 → 404"""
    response = await async_client.get("/api/articles/99999/comments")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_create_comment_unauthorized(async_client: AsyncClient):
    """未登录发表评论 → 401"""
    response = await async_client.post("/api/articles/1/comments", json={"content": "测试评论"})
    assert response.status_code == 401
