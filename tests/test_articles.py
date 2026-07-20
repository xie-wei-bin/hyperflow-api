"""
文章接口测试

面试点：测试认证流程后再测业务接口 —— 每个测试独立，不依赖前一个测试的数据
使用 helper 函数复用注册+登录流程，减少样板代码
"""

import pytest
from httpx import AsyncClient

# ── 辅助函数 ────────────────────────────────────


async def register_and_login(client: AsyncClient, username: str, email: str) -> str:
    """注册 + 登录，返回 access_token"""
    await client.post(
        "/api/auth/register",
        json={"username": username, "email": email, "password": "test123456"},
    )
    resp = await client.post(
        "/api/auth/login", json={"username": username, "password": "test123456"}
    )
    return resp.json()["data"]["access_token"]


async def create_article(client: AsyncClient, token: str, **overrides) -> dict:
    """创建文章，返回响应 data"""
    payload = {
        "title": "测试文章",
        "slug": f"test-slug-{hash(token) & 0xFFFF}",  # 避免 slug 冲突
        "content": "这是测试文章的内容。",
        "status": "published",
    }
    payload.update(overrides)
    resp = await client.post(
        "/api/articles", json=payload, headers={"Authorization": f"Bearer {token}"}
    )
    return resp.json()


# ── 列表 / 分页 ──────────────────────────────────


@pytest.mark.asyncio
async def test_list_articles_empty(async_client: AsyncClient):
    """空数据库 → 文章列表返回空数组"""
    response = await async_client.get("/api/articles")
    assert response.status_code == 200
    data = response.json()
    assert data["data"]["items"] == []
    assert data["data"]["total"] == 0
    assert data["data"]["page"] == 1
    assert data["data"]["total_pages"] == 1


@pytest.mark.asyncio
async def test_list_articles_with_data(async_client: AsyncClient):
    """有文章时列表返回正确数据"""
    token = await register_and_login(async_client, "writer1", "writer1@test.com")
    await create_article(async_client, token, slug="my-first-post", title="第一篇文章")
    await create_article(async_client, token, slug="my-second-post", title="第二篇文章")

    response = await async_client.get("/api/articles")
    assert response.status_code == 200
    data = response.json()
    assert data["data"]["total"] == 2
    assert len(data["data"]["items"]) == 2


@pytest.mark.asyncio
async def test_list_articles_pagination(async_client: AsyncClient):
    """分页 — page_size=1 时返回正确分页元数据"""
    token = await register_and_login(async_client, "pager", "pager@test.com")
    for i in range(3):
        await create_article(async_client, token, slug=f"page-post-{i}", title=f"文章{i}")

    response = await async_client.get("/api/articles?page=1&page_size=1")
    data = response.json()
    assert data["data"]["total"] == 3
    assert data["data"]["total_pages"] == 3
    assert data["data"]["page_size"] == 1
    assert len(data["data"]["items"]) == 1


@pytest.mark.asyncio
async def test_list_articles_page_out_of_range(async_client: AsyncClient):
    """页码超出范围 → 返回空数组"""
    response = await async_client.get("/api/articles?page=999")
    assert response.status_code == 200
    data = response.json()
    assert data["data"]["items"] == []


@pytest.mark.asyncio
async def test_list_articles_invalid_page(async_client: AsyncClient):
    """非法页码（page=0）→ 422"""
    response = await async_client.get("/api/articles?page=0")
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_list_articles_page_size_too_large(async_client: AsyncClient):
    """page_size 超过 100 → 422"""
    response = await async_client.get("/api/articles?page_size=200")
    assert response.status_code == 422


# ── 创建 ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_article_unauthorized(async_client: AsyncClient):
    """未登录创建文章 → 401"""
    response = await async_client.post(
        "/api/articles",
        json={"title": "测试", "slug": "test-unauth", "content": "内容"},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_create_article_success(async_client: AsyncClient):
    """已登录创建文章 → 201"""
    token = await register_and_login(async_client, "author1", "author1@test.com")
    data = await create_article(async_client, token, slug="hello-world", title="Hello World")
    assert data["code"] == 201
    assert data["data"]["title"] == "Hello World"
    assert data["data"]["slug"] == "hello-world"
    assert data["data"]["status"] == "published"
    assert data["data"]["author"]["username"] == "author1"


@pytest.mark.asyncio
async def test_create_article_default_draft(async_client: AsyncClient):
    """不指定 status → 默认为 draft"""
    token = await register_and_login(async_client, "drafter", "drafter@test.com")
    # 不传 status，服务端默认 draft
    resp = await async_client.post(
        "/api/articles",
        json={"title": "真草稿", "slug": "real-draft", "content": "草稿内容"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201
    assert resp.json()["data"]["status"] == "draft"


@pytest.mark.asyncio
async def test_create_article_empty_title(async_client: AsyncClient):
    """标题为空 → 422"""
    token = await register_and_login(async_client, "empty", "empty@test.com")
    resp = await async_client.post(
        "/api/articles",
        json={"title": "", "slug": "empty-title", "content": "内容"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 422


# ── 详情 ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_article_by_slug(async_client: AsyncClient):
    """通过 slug 获取文章详情 → 200"""
    token = await register_and_login(async_client, "detailer", "detailer@test.com")
    await create_article(async_client, token, slug="my-article", title="我的文章")

    response = await async_client.get("/api/articles/my-article")
    assert response.status_code == 200
    data = response.json()
    assert data["data"]["title"] == "我的文章"
    assert data["data"]["content"] == "这是测试文章的内容。"


@pytest.mark.asyncio
async def test_get_article_not_found(async_client: AsyncClient):
    """文章不存在 → 404"""
    response = await async_client.get("/api/articles/nonexistent-slug")
    assert response.status_code == 404


# ── 更新 ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_update_article_success(async_client: AsyncClient):
    """作者更新自己的文章 → 200"""
    token = await register_and_login(async_client, "updater", "updater@test.com")
    await create_article(async_client, token, slug="to-update", title="旧标题")

    resp = await async_client.put(
        "/api/articles/1",
        json={"title": "新标题"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["title"] == "新标题"


@pytest.mark.asyncio
async def test_update_article_forbidden(async_client: AsyncClient):
    """非作者更新他人文章 → 403"""
    token_a = await register_and_login(async_client, "writer_a", "writer_a@test.com")
    await create_article(async_client, token_a, slug="a-article", title="A的文章")

    token_b = await register_and_login(async_client, "writer_b", "writer_b@test.com")
    resp = await async_client.put(
        "/api/articles/1",
        json={"title": "B想改A的文章"},
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert resp.status_code == 403


# ── 删除 ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_delete_article_success(async_client: AsyncClient):
    """作者删除自己的文章（软删除）→ 200"""
    token = await register_and_login(async_client, "deleter", "deleter@test.com")
    await create_article(async_client, token, slug="to-delete", title="待删除")

    resp = await async_client.delete(
        "/api/articles/1", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 200


# ── 热门排行 ─────────────────────────────────────


@pytest.mark.asyncio
async def test_hot_articles(async_client: AsyncClient):
    """热门排行 → 200 + 返回数组"""
    response = await async_client.get("/api/articles/hot")
    assert response.status_code == 200
    assert isinstance(response.json()["data"], list)
