"""
接口限流器 — slowapi 令牌桶算法

=== 面试重点 ===
Q: 令牌桶算法的原理？
A: 一个桶每秒放入 N 个令牌，每次请求取走一个令牌。
   令牌用完 → 请求被拒绝（429 Too Many Requests）
   令牌会随时间补充，不是"归零后永远不可用"。
   对比：固定窗口计数（"每分钟 5 次"）在窗口边界有突刺问题（59 秒发 5 次 + 0 秒再发 5 次）

Q: 为什么从 main.py 抽出来单独文件？
A: router 文件（auth.py）需要导入 limiter 来加 @limiter.limit 装饰器。
   如果 limiter 定义在 main.py 里 → 循环导入（main import router, router import main）
   抽成独立模块 → 两边都能 import，不循环。

Q: 分布式限流怎么实现？
A: slowapi + Redis 后端：多个服务实例共享同一个 Redis 计数器。
   Key 格式：blog:rate_limit:{ip}:{endpoint}
   每个实例 INCR key → 检查是否超限 → 共享计数，不重复
"""

from slowapi import Limiter
from slowapi.util import get_remote_address

# 全局默认 200/min，具体接口可用 @limiter.limit("5/minute") 覆盖
limiter = Limiter(key_func=get_remote_address, default_limits=["200/minute"])
