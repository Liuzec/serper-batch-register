"""
域名池管理器 — 多域名轮换 + per-domain 频率控制

核心逻辑:
  每个域名在一个时间窗口内最多使用 N 次。
  超过限额的域名进入冷却期，自动切换到其他域名。
  所有域名都冷却时，fallback 到 tempmail.lol（随机域名，无此限制）。

配置参数（.env）:
  DOMAIN_MAX_PER_WINDOW=3       每个域名每个窗口最多注册 3 个
  DOMAIN_WINDOW_SECONDS=1800    窗口 30 分钟

示例:
  3 个域名，每个窗口 3 次 → 每 30 分钟最多 9 个号走自有域名
  超出后自动 fallback 到 tempmail.lol
"""

import time
import asyncio
from collections import defaultdict
from config import DOMAIN_MAX_PER_WINDOW, DOMAIN_WINDOW_SECONDS


class DomainPool:
    """
    线程安全的域名池管理器。

    用法:
        pool = DomainPool(["a.xyz", "b.xyz", "c.xyz"])
        domain = await pool.acquire()    # 获取一个可用域名（或 None）
        pool.get_stats()                 # 查看各域名使用情况
    """

    def __init__(
        self,
        domains: list[str],
        max_per_window: int = DOMAIN_MAX_PER_WINDOW,
        window_seconds: int = DOMAIN_WINDOW_SECONDS,
    ):
        self.domains = domains
        self.max_per_window = max_per_window
        self.window_seconds = window_seconds
        # {domain: [timestamp1, timestamp2, ...]}
        self._usage: dict[str, list[float]] = defaultdict(list)
        self._lock = asyncio.Lock()

    def _clean_expired(self, domain: str, now: float):
        """清除过期的使用记录"""
        cutoff = now - self.window_seconds
        self._usage[domain] = [t for t in self._usage[domain] if t > cutoff]

    def _available_count(self, domain: str, now: float) -> int:
        """某域名在当前窗口内还能用几次"""
        self._clean_expired(domain, now)
        return self.max_per_window - len(self._usage[domain])

    async def acquire(self) -> str | None:
        """
        获取一个可用域名。

        优先选剩余额度最多的域名（均匀分散注册量）。
        所有域名都达到限额时返回 None（调用方应 fallback 到 tempmail.lol）。
        """
        async with self._lock:
            now = time.time()
            best_domain = None
            best_remaining = 0

            for domain in self.domains:
                remaining = self._available_count(domain, now)
                if remaining > best_remaining:
                    best_remaining = remaining
                    best_domain = domain

            if best_domain:
                self._usage[best_domain].append(now)
                return best_domain

            return None

    def get_stats(self) -> dict:
        """获取各域名的使用统计"""
        now = time.time()
        stats = {}
        for domain in self.domains:
            self._clean_expired(domain, now)
            used = len(self._usage[domain])
            remaining = self.max_per_window - used
            if used > 0:
                oldest = min(self._usage[domain])
                cooldown = max(0, self.window_seconds - (now - oldest))
            else:
                cooldown = 0
            stats[domain] = {
                "used": used,
                "remaining": remaining,
                "cooldown_seconds": round(cooldown),
            }
        return stats

    def total_remaining(self) -> int:
        """所有域名加起来还能用几次"""
        now = time.time()
        return sum(self._available_count(d, now) for d in self.domains)

    def time_until_next_available(self) -> float:
        """距离下一个域名冷却结束的秒数（所有域名都满时）"""
        now = time.time()
        earliest_expire = float('inf')
        for domain in self.domains:
            self._clean_expired(domain, now)
            if self._usage[domain]:
                oldest = min(self._usage[domain])
                expire_at = oldest + self.window_seconds
                earliest_expire = min(earliest_expire, expire_at)
        if earliest_expire == float('inf'):
            return 0
        return max(0, earliest_expire - now)

    def summary(self) -> str:
        """一行摘要"""
        stats = self.get_stats()
        parts = []
        for domain, s in stats.items():
            parts.append(f"{domain}:{s['used']}/{self.max_per_window}")
        return f"域名池 [{' | '.join(parts)}] 窗口={self.window_seconds}s"
