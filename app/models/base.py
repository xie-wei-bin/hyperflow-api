"""
SQLAlchemy 声明基类

=== 面试重点 ===
Q: 为什么 Base 单独一个文件，不放在 database.py 里？
A: 循环导入问题：
   database.py 需要 import models 才能知道有哪些表（Alembic 自动发现）
   models/*.py 需要 Base 才能定义表
   如果 Base 在 database.py → models import database → database import models → 💥
   拆出来 → models import base（OK）→ database import models（OK）→ 无循环

Q: DeclarativeBase vs declarative_base() 区别？
A: declarative_base() → 函数调用返回 Base 类（SQLAlchemy 1.x 旧式）
   class Base(DeclarativeBase) → 类继承（SQLAlchemy 2.0 新式，类型推导更好）
   新式的 Base 是一个真正的类，IDE 和 mypy 能正确推导子类属性
"""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """所有模型的基类，表名自动从类名推导（User → user, ArticleTag → article_tag）"""

    pass
