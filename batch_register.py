"""
模块4：批量并发注册

三种运行模式:
  数量模式: python batch_register.py 10 5              → 注册 10 个，5 路并发
  限时模式: python batch_register.py 0 5 auto 300      → 5 路并发跑 300 秒
  限速模式: python batch_register.py 0 5 auto 300 20   → 限时 + 每分钟最多 20 个

邮箱模式 (mode 参数):
  auto    — 使用 .env 中的 EMAIL_MODE 配置（默认 api 轮换）
  domain  — 强制使用自建域名池
  也可指定具体 provider: 1secmail / tempmail_lol / mailgw / mailtm / guerrilla

频率控制:
  - REGISTER_INTERVAL 控制两次注册间隔
  - rate_limit 参数控制每分钟最大注册数
  - domain 模式下域名池自动限速
"""

import asyncio
import time
from datetime import datetime
from config import MAX_CONCURRENCY, OUTPUT_FILE, REGISTER_INTERVAL
from register import register_single_account
from email_module import get_domain_pool


async def batch_register(total: int, concurrency: int = MAX_CONCURRENCY, mode: str = "auto"):
    """批量并发注册（按数量）"""
    print(f"\n{'='*50}")
    print(f"批量注册启动")
    print(f"目标: {total} 个账号 | 并发: {concurrency} | 模式: {mode}")
    print(f"{'='*50}")

    _print_pool_status()

    providers = _assign_providers(total, mode)
    semaphore = asyncio.Semaphore(concurrency)
    success = 0
    fail = 0
    completed = 0

    async def worker(index: int, force_provider: str | None):
        nonlocal success, fail, completed
        if REGISTER_INTERVAL > 0:
            await asyncio.sleep(REGISTER_INTERVAL * index / concurrency)
        async with semaphore:
            tag = force_provider or "auto"
            print(f"\n--- 任务 #{index+1}/{total} 开始 [{tag}] ---")
            try:
                result = await register_single_account(force_provider=force_provider)
                if result:
                    success += 1
                else:
                    fail += 1
            except Exception as e:
                print(f"   任务 #{index+1} 异常: {e}")
                fail += 1
            finally:
                completed += 1
                print(f"   [进度] {completed}/{total} 完成 (成功 {success}, 失败 {fail})")

    tasks = [worker(i, providers[i]) for i in range(total)]
    start_time = datetime.now()
    await asyncio.gather(*tasks)
    elapsed = (datetime.now() - start_time).total_seconds()

    _print_summary(success, fail, elapsed, total)


async def batch_register_timed(
    duration: int,
    concurrency: int = MAX_CONCURRENCY,
    mode: str = "auto",
    rate_limit: float = 0,
):
    """
    批量并发注册（按时间 + 可选限速）

    参数:
        duration: 持续时间（秒）
        concurrency: 最大并发数
        mode: "auto" / "mix" / "tempmail" / "custom"
        rate_limit: 每分钟最大注册数（0=不限制）
    """
    rate_desc = f"{rate_limit:.0f}个/分" if rate_limit > 0 else "不限"
    print(f"\n{'='*60}")
    print(f"  限时注册启动")
    print(f"  时长: {duration}s ({duration/60:.0f}分钟) | 并发: {concurrency} | 模式: {mode}")
    print(f"  限速: {rate_desc} | 注册间隔: {REGISTER_INTERVAL}s")
    print(f"{'='*60}")

    _print_pool_status()

    semaphore = asyncio.Semaphore(concurrency)
    success = 0
    fail = 0
    launched = 0
    completed = 0
    start_time = time.time()
    deadline = start_time + duration
    active_tasks: set[asyncio.Task] = set()

    # 限速器：追踪每分钟注册数
    launch_timestamps: list[float] = []

    def _get_provider(index: int) -> str | None:
        if mode == "domain":
            return "custom_domain"
        elif mode in ("tempmailio", "tempmail_lol", "mailgw", "mailtm", "guerrilla"):
            return mode
        return None  # auto: 由 EMAIL_MODE 决定

    async def worker(index: int, force_provider: str | None):
        nonlocal success, fail, completed
        async with semaphore:
            tag = force_provider or "auto"
            elapsed_so_far = time.time() - start_time
            print(f"\n--- 任务 #{index+1} 开始 [{tag}] (已运行 {elapsed_so_far:.0f}s) ---")
            try:
                result = await register_single_account(force_provider=force_provider)
                if result:
                    success += 1
                else:
                    fail += 1
            except Exception as e:
                print(f"   任务 #{index+1} 异常: {e}")
                fail += 1
            finally:
                completed += 1
                remaining = max(0, deadline - time.time())
                print(f"   [进度] 完成 {completed} 个 (成功 {success}, 失败 {fail}) | 剩余 {remaining:.0f}s")

    while time.time() < deadline:
        # 清理已完成的任务
        done = {t for t in active_tasks if t.done()}
        active_tasks -= done

        # 限速检查
        if rate_limit > 0:
            now = time.time()
            launch_timestamps[:] = [t for t in launch_timestamps if now - t < 60]
            if len(launch_timestamps) >= rate_limit:
                wait = 60 - (now - launch_timestamps[0]) + 0.5
                if wait > 0 and time.time() + wait < deadline:
                    print(f"   [限速] 已达 {rate_limit:.0f}个/分，等待 {wait:.0f}s...")
                    await asyncio.sleep(min(wait, deadline - time.time()))
                    continue

        # 注册间隔
        if REGISTER_INTERVAL > 0 and launch_timestamps:
            since_last = time.time() - launch_timestamps[-1]
            if since_last < REGISTER_INTERVAL:
                await asyncio.sleep(REGISTER_INTERVAL - since_last)

        # 发起新任务
        if len(active_tasks) < concurrency:
            provider = _get_provider(launched)
            task = asyncio.create_task(worker(launched, provider))
            active_tasks.add(task)
            launch_timestamps.append(time.time())
            launched += 1
        else:
            if active_tasks:
                done_set, _ = await asyncio.wait(active_tasks, return_when=asyncio.FIRST_COMPLETED)
                active_tasks -= done_set

    # 时间到，等待已启动的任务完成
    if active_tasks:
        print(f"\n--- 时间到！等待 {len(active_tasks)} 个进行中的任务完成... ---")
        await asyncio.gather(*active_tasks)

    elapsed = time.time() - start_time
    _print_summary(success, fail, elapsed, launched)


def _assign_providers(total: int, mode: str) -> list:
    if mode == "domain":
        return ["custom_domain"] * total
    elif mode in ("tempmailio", "tempmail_lol", "mailgw", "mailtm", "guerrilla"):
        return [mode] * total
    return [None] * total  # auto: 由 EMAIL_MODE 决定


def _print_pool_status():
    pool = get_domain_pool()
    if pool:
        print(f"  {pool.summary()}")
        stats = pool.get_stats()
        for domain, s in stats.items():
            cd = f" (冷却 {s['cooldown_seconds']}s)" if s['cooldown_seconds'] > 0 else ""
            print(f"    {domain}: {s['remaining']}/{pool.max_per_window} 可用{cd}")


def _print_summary(success: int, fail: int, elapsed: float, launched: int):
    print(f"\n{'='*60}")
    print(f"  注册完成")
    print(f"  发起: {launched} | 成功: {success} | 失败: {fail}")
    print(f"  总耗时: {elapsed:.1f}s")
    if elapsed > 0 and success > 0:
        print(f"  吞吐量: {success / elapsed * 60:.1f} 个/分钟")
        print(f"  成功率: {success / max(launched, 1) * 100:.0f}%")
    print(f"  结果保存在: {OUTPUT_FILE}")

    pool = get_domain_pool()
    if pool:
        print(f"  {pool.summary()}")

    print(f"{'='*60}")


if __name__ == "__main__":
    import sys
    total = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    concurrency = int(sys.argv[2]) if len(sys.argv) > 2 else MAX_CONCURRENCY
    mode = sys.argv[3] if len(sys.argv) > 3 else "auto"
    duration = int(sys.argv[4]) if len(sys.argv) > 4 else 0
    rate_limit = float(sys.argv[5]) if len(sys.argv) > 5 else 0

    if duration > 0:
        asyncio.run(batch_register_timed(duration, concurrency, mode, rate_limit))
    else:
        asyncio.run(batch_register(total, concurrency, mode))
