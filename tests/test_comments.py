"""
评论接口测试

面试点：评论测试覆盖认证、权限、树形结构、软删除四个维度
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
    """创建文章，返回文章 ID"""
    resp = await client.post(
        "/api/articles",
        json={"title": slug, "slug": slug, "content": "文章内容", "status": "published"},
        headers={"Authorization": f"Bearer {token}"},
    )
    return resp.json()["data"]["id"]


# ── 获取评论 ─────────────────────────────────────


@pytest.mark.asyncio
async def test_get_comments_empty(async_client: AsyncClient):
    """文章有 0 条评论 → 返回空数组"""
    token = await _register_and_login(async_client, "emptycommenter")
    article_id = await _create_article(async_client, token, "no-comments-yet")

    response = await async_client.get(f"/api/articles/{article_id}/comments")
    assert response.status_code == 200
    assert response.json()["data"] == []


@pytest.mark.asyncio
async def test_get_comments_article_not_found(async_client: AsyncClient):
    """不存在的文章 → 404"""
    response = await async_client.get("/api/articles/99999/comments")
    assert response.status_code == 404


# ── 发表评论 ─────────────────────────────────────


@pytest.mark.asyncio
async def test_create_comment_unauthorized(async_client: AsyncClient):
    """未登录发表评论 → 401"""
    response = await async_client.post("/api/articles/1/comments", json={"content": "测试评论"})
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_create_comment_success(async_client: AsyncClient):
    """已登录发表评论 → 201"""
    token = await _register_and_login(async_client, "commenter")
    article_id = await _create_article(async_client, token, "comment-article")

    resp = await async_client.post(
        f"/api/articles/{article_id}/comments",
        json={"content": "这是一条测试评论"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["data"]["content"] == "这是一条测试评论"
    assert data["data"]["article_id"] == article_id
    assert data["data"]["parent_id"] is None
    assert "user" in data["data"]


@pytest.mark.asyncio
async def test_create_comment_article_not_found(async_client: AsyncClient):
    """在不存在文章下评论 → 404"""
    token = await _register_and_login(async_client, "ghostcommenter")
    resp = await async_client.post(
        "/api/articles/99999/comments",
        json={"content": "不存在的文章"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404


# ── 回复评论 ─────────────────────────────────────


@pytest.mark.asyncio
async def test_reply_comment_success(async_client: AsyncClient):
    """回复评论 → 201，parent_id 正确"""
    token_a = await _register_and_login(async_client, "replier_a")
    article_id = await _create_article(async_client, token_a, "reply-article")

    # 先发一条顶级评论
    comm_resp = await async_client.post(
        f"/api/articles/{article_id}/comments",
        json={"content": "顶层评论"},
        headers={"Authorization": f"Bearer {token_a}"},
    )
    comment_id = comm_resp.json()["data"]["id"]

    # 另一个用户回复
    token_b = await _register_and_login(async_client, "replier_b")
    resp = await async_client.post(
        f"/api/comments/{comment_id}/reply",
        json={"content": "回复顶层评论"},
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert resp.status_code == 201
    assert resp.json()["data"]["parent_id"] == comment_id


@pytest.mark.asyncio
async def test_reply_nonexistent_comment(async_client: AsyncClient):
    """回复不存在的评论 → 404"""
    token = await _register_and_login(async_client, "badreplier")
    resp = await async_client.post(
        "/api/comments/99999/reply",
        json={"content": "回复不存在的评论"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404


# ── 树形结构 ─────────────────────────────────────


@pytest.mark.asyncio
async def test_comment_tree_structure(async_client: AsyncClient):
    """多层级评论 → 返回树形嵌套结构"""
    token = await _register_and_login(async_client, "treebuilder")
    article_id = await _create_article(async_client, token, "tree-article")

    # 顶层评论
    top = await async_client.post(
        f"/api/articles/{article_id}/comments",
        json={"content": "第一层"},
        headers={"Authorization": f"Bearer {token}"},
    )
    top_id = top.json()["data"]["id"]

    # 回复顶层
    sub = await async_client.post(
        f"/api/comments/{top_id}/reply",
        json={"content": "第二层"},
        headers={"Authorization": f"Bearer {token}"},
    )
    sub_id = sub.json()["data"]["id"]

    # 回复子评论
    await async_client.post(
        f"/api/comments/{sub_id}/reply",
        json={"content": "第三层"},
        headers={"Authorization": f"Bearer {token}"},
    )

    # 获取树形结构
    resp = await async_client.get(f"/api/articles/{article_id}/comments")
    assert resp.status_code == 200
    tree = resp.json()["data"]
    assert len(tree) == 1  # 一个顶层
    assert tree[0]["content"] == "第一层"
    assert len(tree[0]["replies"]) == 1  # 一个回复
    assert tree[0]["replies"][0]["content"] == "第二层"
    assert len(tree[0]["replies"][0]["replies"]) == 1  # 回复的回复
    assert tree[0]["replies"][0]["replies"][0]["content"] == "第三层"


# ── 删除评论 ─────────────────────────────────────


@pytest.mark.asyncio
async def test_delete_comment_by_author(async_client: AsyncClient):
    """评论作者删除自己的评论 → 200（软删除）"""
    token = await _register_and_login(async_client, "delcommenter")
    article_id = await _create_article(async_client, token, "del-comment-article")

    resp = await async_client.post(
        f"/api/articles/{article_id}/comments",
        json={"content": "将被删除的评论"},
        headers={"Authorization": f"Bearer {token}"},
    )
    comment_id = resp.json()["data"]["id"]

    del_resp = await async_client.delete(
        f"/api/comments/{comment_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert del_resp.status_code == 200


@pytest.mark.asyncio
async def test_delete_comment_by_other_user(async_client: AsyncClient):
    """非评论作者删除他人评论 → 403"""
    token_a = await _register_and_login(async_client, "owner")
    article_id = await _create_article(async_client, token_a, "owner-article")

    resp = await async_client.post(
        f"/api/articles/{article_id}/comments",
        json={"content": "我的评论"},
        headers={"Authorization": f"Bearer {token_a}"},
    )
    comment_id = resp.json()["data"]["id"]

    token_b = await _register_and_login(async_client, "intruder")
    del_resp = await async_client.delete(
        f"/api/comments/{comment_id}",
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert del_resp.status_code == 403
