"""
模块4：并发注册（在单次注册跑通后使用）

使用 asyncio.Semaphore 控制并发数，
同时运行多个注册任务，快速批量生产账号。
"""

import asyncio
from datetime import datetime
from config import MAX_CONCURRENCY, OUTPUT_FILE
from register import register_single_account


async def batch_register(total: int, concurrency: int = MAX_CONCURRENCY):
    """
    批量并发注册

    参数:
        total: 总共要注册的账号数
        concurrency: 最大并发数
    """
    print(f"\n{'='*50}")
    print(f"批量注册启动")
    print(f"目标: {total} 个账号 | 并发: {concurrency}")
    print(f"{'='*50}")

    semaphore = asyncio.Semaphore(concurrency)
    success = 0
    fail = 0
    completed = 0

    async def worker(index: int):
        nonlocal success, fail, completed
        async with semaphore:
            print(f"\n--- 任务 #{index+1}/{total} 开始 ---")
            try:
                result = await register_single_account()
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

    tasks = [worker(i) for i in range(total)]

    start_time = datetime.now()
    await asyncio.gather(*tasks)
    elapsed = (datetime.now() - start_time).total_seconds()

    print(f"\n{'='*50}")
    print(f"批量注册完成")
    print(f"成功: {success} | 失败: {fail}")
    print(f"耗时: {elapsed:.1f} 秒")
    print(f"结果保存在: {OUTPUT_FILE}")
    print(f"{'='*50}")


if __name__ == "__main__":
    import sys
    total = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    concurrency = int(sys.argv[2]) if len(sys.argv) > 2 else MAX_CONCURRENCY
    asyncio.run(batch_register(total, concurrency))
