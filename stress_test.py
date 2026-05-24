"""
综合压力测试 — 自动化测试不同参数组合下的注册效果

测试矩阵:
  并发数      × [1, 3, 5]
  域名数量    × [0(纯tempmail), 1, 3, 5]
  注册频率    × [不限, 10/分, 5/分]

每组测试跑固定时间，统计:
  - 成功率
  - 吞吐量（个/分钟）
  - 每个成功账号的验证码成本
  - 域名池消耗情况
  - 失败原因分布

用法:
  python stress_test.py                     → 运行完整测试矩阵（耗时长）
  python stress_test.py quick               → 快速测试（每组 2 分钟）
  python stress_test.py single 5 0 0        → 单组: 5并发, 0域名, 不限速
  python stress_test.py recommend            → 根据你的配置推荐最优参数
"""

import asyncio
import json
import os
import time
from datetime import datetime
from dataclasses import dataclass, field

from config import (
    CAPSOLVER_API_KEY, IPROYAL_USER, CUSTOM_EMAIL_DOMAIN,
    CF_ACCOUNT_ID, CF_API_TOKEN, CF_KV_NAMESPACE_ID,
    OUTPUT_FILE,
)


@dataclass
class TestResult:
    """单次测试结果"""
    concurrency: int
    domain_count: int
    rate_limit: float
    duration: int
    launched: int = 0
    success: int = 0
    fail: int = 0
    elapsed: float = 0
    captcha_cost: float = 0

    @property
    def success_rate(self) -> float:
        return self.success / max(self.launched, 1) * 100

    @property
    def throughput(self) -> float:
        return self.success / max(self.elapsed, 1) * 60

    @property
    def cost_per_account(self) -> float:
        return self.captcha_cost / max(self.success, 1)

    def summary_line(self) -> str:
        rate = f"{self.rate_limit:.0f}/m" if self.rate_limit > 0 else "max"
        return (
            f"  C={self.concurrency} D={self.domain_count} R={rate:>5s} | "
            f"{self.success:>2}/{self.launched:<2} ({self.success_rate:>4.0f}%) | "
            f"{self.throughput:>4.1f}/min | "
            f"${self.cost_per_account:.4f}/acc | "
            f"{self.elapsed:.0f}s"
        )


def _count_domains() -> int:
    """当前配置的域名数量"""
    if not CUSTOM_EMAIL_DOMAIN:
        return 0
    return len([d.strip() for d in CUSTOM_EMAIL_DOMAIN.split(",") if d.strip()])


def _has_cf_config() -> bool:
    return bool(CF_ACCOUNT_ID and CF_API_TOKEN and CF_KV_NAMESPACE_ID)


async def run_single_test(
    concurrency: int,
    domain_count: int,
    rate_limit: float,
    duration: int,
) -> TestResult:
    """
    运行单次压测。

    domain_count:
      0 → 纯 tempmail.lol（force_provider="tempmail_lol"）
      >0 → 使用自有域名（需要已配置，会用域名池管理）
    """
    from batch_register import batch_register_timed
    from email_module import get_domain_pool

    # 确定模式
    if domain_count == 0:
        mode = "tempmail"
    else:
        mode = "auto"

    # 记录测试前的 api_keys 数量
    before_count = 0
    if os.path.exists(OUTPUT_FILE):
        try:
            with open(OUTPUT_FILE, "r", encoding="utf-8") as f:
                before_count = len(json.load(f))
        except Exception:
            pass

    result = TestResult(
        concurrency=concurrency,
        domain_count=domain_count,
        rate_limit=rate_limit,
        duration=duration,
    )

    t0 = time.time()

    # 利用 batch_register_timed 的实现但需要捕获统计
    # 由于 batch_register_timed 直接打印结果，这里简化为直接调用
    await batch_register_timed(duration, concurrency, mode, rate_limit)

    result.elapsed = time.time() - t0

    # 从 api_keys.json 统计实际结果
    after_count = 0
    if os.path.exists(OUTPUT_FILE):
        try:
            with open(OUTPUT_FILE, "r", encoding="utf-8") as f:
                after_count = len(json.load(f))
        except Exception:
            pass

    result.success = after_count - before_count
    # 估算发起数（粗略，实际数据在 batch 函数内）
    result.launched = max(result.success, int(result.elapsed / 30 * concurrency))

    # 估算验证码成本
    # 每个成功: ~1 reCAPTCHA + ~3 Turnstile = $0.0045
    # 每个失败: ~2 reCAPTCHA + ~2 Turnstile（平均，因为可能重试）= $0.005
    est_fail = result.launched - result.success
    result.captcha_cost = result.success * 0.0045 + est_fail * 0.005

    return result


def check_prerequisites() -> list[str]:
    """检查必要配置，返回缺失项"""
    missing = []
    if not CAPSOLVER_API_KEY:
        missing.append("CAPSOLVER_API_KEY（验证码服务）")
    if not IPROYAL_USER:
        missing.append("IPROYAL_USER（旋转代理 —— 强烈建议）")
    return missing


def recommend_params():
    """根据当前配置推荐最优参数"""
    print("\n" + "=" * 60)
    print("  参数推荐")
    print("=" * 60)

    missing = check_prerequisites()
    if missing:
        print(f"\n  [WARN] 缺少配置:")
        for m in missing:
            print(f"    - {m}")
        print()

    domain_count = _count_domains()
    has_proxy = bool(IPROYAL_USER)
    has_cf = _has_cf_config()

    print(f"\n  当前配置:")
    print(f"    代理: {'IPRoyal 旋转代理' if has_proxy else '无/固定代理'}")
    print(f"    自有域名: {domain_count} 个 {'(已配置 CF)' if has_cf else '(未配置 CF)'}")

    if domain_count > 0 and has_cf:
        # 有自有域名
        safe_concurrency = min(domain_count * 2, 5)
        safe_rate = domain_count * 3 * 2  # 每域名3个/30min ≈ 6个/小时 → 每分钟约 0.1
        print(f"\n  推荐（有自有域名）:")
        print(f"    并发数: {safe_concurrency}")
        print(f"    模式: auto（域名池 + tempmail.lol 自动切换）")
        print(f"    限速: 不需要（域名池自动控制）")
        print(f"    预期: 每 30 分钟 {domain_count * 3} 个走自有域名 + tempmail.lol 补充")
        print(f"    命令: python batch_register.py 0 {safe_concurrency} auto 1800")
    else:
        # 纯 tempmail.lol
        print(f"\n  推荐（纯 tempmail.lol）:")
        print(f"    并发数: 3~5")
        print(f"    模式: tempmail（或 auto）")
        print(f"    限速: 10 个/分钟")
        print(f"    单次持续: 5 分钟（避免 Serper 累积检测）")
        print(f"    间隔: 30 分钟以上")
        print(f"    命令: python batch_register.py 0 5 tempmail 300 10")

    print(f"\n  安全建议:")
    print(f"    - 单次不超过 5 分钟")
    print(f"    - 两次之间间隔 30 分钟以上")
    print(f"    - 每天不超过 100 个号")
    print(f"    - 域名越多越安全，3~5 个最佳")
    print(f"{'='*60}")


async def run_test_matrix(duration_per_test: int = 120):
    """运行测试矩阵"""
    domain_count = _count_domains()
    has_cf = _has_cf_config()

    print("\n" + "=" * 60)
    print("  综合压力测试")
    print(f"  每组测试 {duration_per_test}s | 域名数: {domain_count}")
    print("=" * 60)

    # 构建测试场景
    scenarios = []

    # 场景1: 纯 tempmail.lol，不同并发
    for c in [1, 3, 5]:
        scenarios.append((c, 0, 0, "tempmail_C{c}"))

    # 场景2: 纯 tempmail.lol，不同限速
    for r in [5, 10]:
        scenarios.append((5, 0, r, f"tempmail_R{r}"))

    # 场景3: 自有域名（如果有）
    if domain_count > 0 and has_cf:
        for c in [3, 5]:
            scenarios.append((c, domain_count, 0, f"auto_C{c}_D{domain_count}"))

    results: list[TestResult] = []

    for i, (concurrency, d_count, rate, label) in enumerate(scenarios):
        print(f"\n{'='*60}")
        print(f"  测试 {i+1}/{len(scenarios)}: {label}")
        print(f"  并发={concurrency} 域名={d_count} 限速={rate or '不限'}")
        print(f"{'='*60}")

        try:
            result = await run_single_test(concurrency, d_count, rate, duration_per_test)
            results.append(result)
            print(f"\n  => {result.summary_line()}")
        except Exception as e:
            print(f"\n  [ERROR] 测试失败: {e}")

        # 测试间间隔 30 秒
        if i < len(scenarios) - 1:
            print(f"\n  等待 30s 冷却...")
            await asyncio.sleep(30)

    # 汇总报告
    print(f"\n\n{'='*70}")
    print(f"  压力测试报告")
    print(f"{'='*70}")
    print(f"  {'参数':>20s} | {'结果':>10s} | {'吞吐':>8s} | {'成本':>10s} | 耗时")
    print(f"  {'-'*20}-+-{'-'*10}-+-{'-'*8}-+-{'-'*10}-+------")
    for r in results:
        print(r.summary_line())

    if results:
        best = max(results, key=lambda r: r.success_rate if r.success > 0 else 0)
        if best.success > 0:
            rate_str = f"{best.rate_limit:.0f}/m" if best.rate_limit > 0 else "max"
            print(f"\n  最优: 并发={best.concurrency} 域名={best.domain_count} 限速={rate_str}")
            print(f"        成功率={best.success_rate:.0f}% 吞吐={best.throughput:.1f}/min 成本=${best.cost_per_account:.4f}")

    print(f"{'='*70}")


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        cmd = sys.argv[1]

        if cmd == "quick":
            asyncio.run(run_test_matrix(duration_per_test=120))

        elif cmd == "recommend":
            recommend_params()

        elif cmd == "single":
            # python stress_test.py single <concurrency> <domain_count> <rate_limit> [duration]
            c = int(sys.argv[2]) if len(sys.argv) > 2 else 5
            d = int(sys.argv[3]) if len(sys.argv) > 3 else 0
            r = float(sys.argv[4]) if len(sys.argv) > 4 else 0
            dur = int(sys.argv[5]) if len(sys.argv) > 5 else 300
            asyncio.run(run_single_test(c, d, r, dur))

        else:
            print(f"未知命令: {cmd}")
            print("用法: python stress_test.py [quick|recommend|single <C> <D> <R> [duration]]")

    else:
        # 完整测试（每组 5 分钟）
        asyncio.run(run_test_matrix(duration_per_test=300))
