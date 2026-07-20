"""
评论服务 — 树形结构构建

=== 面试重点 ===
Q: 评论树怎么从平铺数据转成树形结构？
A: 数据库查出来是平铺的（parent_id 只存数值），在 Python 内存里递归构建树：
   1. 先查所有顶级评论（parent_id IS NULL）
   2. 递归遍历每个评论的 replies
   3. 每个 reply 继续递归它的 replies...
   时间复杂度 O(n)，n = 评论总数，比多次递归查询数据库快得多

Q: 为什么不一次查全部评论然后 in Python 分组？
A: 当前实现正是：一次性查出顶级评论，replies 通过 selectinload 预加载
   好处：2 次 SQL 查询（顶级 + 全部子回复），不会 N+1
   更好方案：一次查出所有评论 → Python dict 按 parent_id 分组 → 构建树
   当前方案已足够，数据量超过 1000 条评论时再考虑优化

Q: 软删除评论怎么处理？
A: is_deleted=True → content 显示为"该评论已被删除"，但保留结构
   不真删原因：
   1. 如果有回复，真删会导致子回复也消失（或成孤儿）
   2. 保留结构让读者知道"这里曾经有人回复过"
   3. 合规要求：用户曾发布的内容需要可追溯
"""

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.exceptions import NotFoundException
from app.models.comment import Comment


# 评论量阈值：≤ 此值用全量内存分组（不限层级），> 此值用分页 + 预加载（省内存）
_COMMENT_FLAT_THRESHOLD = 200


async def get_article_comments(db: AsyncSession, article_id: int) -> list[dict[str, Any]]:
    """
    获取文章评论（树形结构）— 按评论量自适应选策略

    少量评论（≤200）：一次全量查询 → Python 内存按 parent_id 分组构建树
    大量评论（>200）：顶级评论分页 + selectinload 预加载子回复
    """
    # 先 COUNT 决定走哪条路
    count_result = await db.execute(
        select(func.count()).select_from(Comment).where(Comment.article_id == article_id)
    )
    total = count_result.scalar() or 0

    if total <= _COMMENT_FLAT_THRESHOLD:
        return await _get_comments_flat(db, article_id)
    return await _get_comments_paginated(db, article_id)


async def _get_comments_flat(db: AsyncSession, article_id: int) -> list[dict[str, Any]]:
    """少量评论：全量查出 → 内存分组 → 不限层级"""
    result = await db.execute(
        select(Comment)
        .options(selectinload(Comment.user))
        .where(Comment.article_id == article_id)
        .order_by(Comment.created_at.asc())
    )
    all_comments = result.scalars().all()

    children: dict[int | None, list[dict[str, Any]]] = {}
    for c in all_comments:
        node = _comment_to_dict(c)
        children.setdefault(c.parent_id, []).append(node)

    def build_tree(parent_id: int | None) -> list[dict[str, Any]]:
        result_list = []
        for node in children.get(parent_id, []):
            node["replies"] = build_tree(node["id"])
            result_list.append(node)
        return result_list

    return list(reversed(build_tree(None)))


async def _get_comments_paginated(db: AsyncSession, article_id: int) -> list[dict[str, Any]]:
    """大量评论：顶级评论分页，子回复用 selectinload 预加载 3 层"""
    result = await db.execute(
        select(Comment)
        .options(
            selectinload(Comment.user),
            selectinload(Comment.replies)
            .selectinload(Comment.replies)
            .selectinload(Comment.replies),
        )
        .where(Comment.article_id == article_id, Comment.parent_id == None)  # noqa: E711
        .order_by(Comment.created_at.desc())
        .limit(50)
    )
    return [_comment_to_tree(c) for c in result.scalars().all()]


def _comment_to_dict(comment: Comment) -> dict[str, Any]:
    return {
        "id": comment.id,
        "content": comment.content if not comment.is_deleted else "该评论已被删除",
        "article_id": comment.article_id,
        "user": {"id": comment.user.id, "username": comment.user.username, "avatar": comment.user.avatar},
        "parent_id": comment.parent_id,
        "is_deleted": comment.is_deleted,
        "created_at": comment.created_at.isoformat(),
    }


def _comment_to_tree(comment: Comment) -> dict[str, Any]:
    """selectinload 路径：ORM 已预加载 replies，直接递归"""
    node = _comment_to_dict(comment)
    node["replies"] = [_comment_to_tree(r) for r in (comment.replies or [])]
    return node


async def create_comment(db: AsyncSession, article_id: int, user_id: int, content: str) -> Comment:
    """发表顶级评论"""
    comment = Comment(content=content, article_id=article_id, user_id=user_id)
    db.add(comment)
    await db.flush()
    # 重新查一次以预加载 user 关联（避免返回时 N+1）
    result = await db.execute(
        select(Comment).options(selectinload(Comment.user)).where(Comment.id == comment.id)
    )
    return result.scalar_one()


async def reply_comment(db: AsyncSession, parent_id: int, user_id: int, content: str) -> Comment:
    """回复评论（创建子评论），限制最多 3 层嵌套"""
    from app.exceptions import BadRequestException

    result = await db.execute(select(Comment).where(Comment.id == parent_id))
    parent = result.scalar_one_or_none()
    if not parent:
        raise NotFoundException("父评论不存在")

    # depth 字段存了层级：0=顶级 1=一级回复 2=二级回复，O(1) 判断无需循环查表
    if parent.depth >= 2:
        raise BadRequestException("评论嵌套已达上限（3 层），无法继续回复")

    comment = Comment(
        content=content,
        article_id=parent.article_id,
        user_id=user_id,
        parent_id=parent_id,
        depth=parent.depth + 1,  # 子评论层级 = 父评论 +1
    )
    db.add(comment)
    await db.flush()

    result = await db.execute(
        select(Comment).options(selectinload(Comment.user)).where(Comment.id == comment.id)
    )
    return result.scalar_one()
