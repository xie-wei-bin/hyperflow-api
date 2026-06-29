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

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.exceptions import NotFoundException
from app.models.comment import Comment


async def get_article_comments(db: AsyncSession, article_id: int) -> list[dict[str, Any]]:
    """
    获取文章评论（树形结构）

    面试点：先查顶级评论（parent_id IS NULL），selectinload 预加载 replies
    然后在 Python 里递归构建树，避免对数据库多次查询
    """
    result = await db.execute(
        select(Comment)
        .options(selectinload(Comment.user), selectinload(Comment.replies))
        .where(Comment.article_id == article_id, Comment.parent_id == None)  # noqa: E711
        .order_by(Comment.created_at.desc())
    )
    comments = result.scalars().all()
    return [_build_comment_tree(c) for c in comments]


def _build_comment_tree(comment: Comment) -> dict[str, Any]:
    """
    递归构建评论树

    面试点：递归终止条件 — replies 列表为空时返回空数组
    每层递归处理: 当前评论 → 其 replies → replies 的 replies → ...
    is_deleted 的评论：内容替换但保留结构，子回复正常显示
    """
    return {
        "id": comment.id,
        "content": comment.content if not comment.is_deleted else "该评论已被删除",
        "article_id": comment.article_id,
        "user": {
            "id": comment.user.id,
            "username": comment.user.username,
            "avatar": comment.user.avatar,
        },
        "parent_id": comment.parent_id,
        "is_deleted": comment.is_deleted,
        "created_at": comment.created_at.isoformat(),
        "replies": [_build_comment_tree(reply) for reply in (comment.replies or [])],
    }


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
    """回复评论（创建子评论）"""
    # 验证被回复的评论存在
    result = await db.execute(select(Comment).where(Comment.id == parent_id))
    parent = result.scalar_one_or_none()
    if not parent:
        raise NotFoundException("父评论不存在")

    # 子评论继承父评论的 article_id
    comment = Comment(
        content=content, article_id=parent.article_id, user_id=user_id, parent_id=parent_id
    )
    db.add(comment)
    await db.flush()

    result = await db.execute(
        select(Comment).options(selectinload(Comment.user)).where(Comment.id == comment.id)
    )
    return result.scalar_one()
