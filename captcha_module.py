"""
模块2：验证码解决
使用 Capsolver API 解决:
- Google reCAPTCHA v2 invisible
- Cloudflare Turnstile

所有函数接受可选的 httpx.AsyncClient 参数以复用连接。
"""

import httpx
import asyncio
from config import CAPSOLVER_API_KEY, TURNSTILE_SITEKEY, RECAPTCHA_SITEKEY

CAPSOLVER_API_URL = "https://api.capsolver.com"


async def _poll_capsolver_result(client: httpx.AsyncClient, task_id: str, label: str, max_polls: int = 60) -> str:
    """通用轮询：等待 Capsolver 任务完成"""
    for i in range(max_polls):
        await asyncio.sleep(2)
        resp = await client.post(
            f"{CAPSOLVER_API_URL}/getTaskResult",
            json={"clientKey": CAPSOLVER_API_KEY, "taskId": task_id},
        )
        data = resp.json()
        status = data.get("status", "")
        if status == "ready":
            solution = data.get("solution", {})
            token = solution.get("token") or solution.get("gRecaptchaResponse", "")
            print(f"   [OK] {label} 已解决 ({(i+1)*2}s)")
            return token
        elif status == "failed":
            raise Exception(f"Capsolver {label} 失败: {data.get('errorDescription', '未知')}")
    raise TimeoutError(f"Capsolver {label} 超时 ({max_polls*2}s)")


async def solve_recaptcha_v2(
    website_url: str,
    website_key: str = RECAPTCHA_SITEKEY,
    client: httpx.AsyncClient | None = None,
) -> str:
    """调用 Capsolver 解 Google reCAPTCHA v2 invisible"""
    should_close = client is None
    client = client or httpx.AsyncClient(timeout=180)
    try:
        resp = await client.post(
            f"{CAPSOLVER_API_URL}/createTask",
            json={
                "clientKey": CAPSOLVER_API_KEY,
                "task": {
                    "type": "ReCaptchaV2TaskProxyLess",
                    "websiteURL": website_url,
                    "websiteKey": website_key,
                },
            },
        )
        data = resp.json()
        if data.get("errorId", 0) != 0:
            raise Exception(f"Capsolver reCAPTCHA 创建失败: {data.get('errorDescription', '未知')}")
        task_id = data["taskId"]
        print(f"   Capsolver reCAPTCHA 任务: {task_id}")
        return await _poll_capsolver_result(client, task_id, "reCAPTCHA v2", max_polls=90)
    finally:
        if should_close:
            await client.aclose()


async def solve_turnstile(
    website_url: str,
    website_key: str = TURNSTILE_SITEKEY,
    client: httpx.AsyncClient | None = None,
) -> str:
    """调用 Capsolver 解 Cloudflare Turnstile"""
    should_close = client is None
    client = client or httpx.AsyncClient(timeout=120)
    try:
        resp = await client.post(
            f"{CAPSOLVER_API_URL}/createTask",
            json={
                "clientKey": CAPSOLVER_API_KEY,
                "task": {
                    "type": "AntiTurnstileTaskProxyLess",
                    "websiteURL": website_url,
                    "websiteKey": website_key,
                },
            },
        )
        data = resp.json()
        if data.get("errorId", 0) != 0:
            raise Exception(f"Capsolver Turnstile 创建失败: {data.get('errorDescription', '未知')}")
        task_id = data["taskId"]
        print(f"   Capsolver Turnstile 任务: {task_id}")
        return await _poll_capsolver_result(client, task_id, "Turnstile")
    finally:
        if should_close:
            await client.aclose()


# ============================================
# 测试入口
# ============================================
async def test_captcha_module():
    print("=== 测试验证码模块 ===")
    print(f"   API Key: {CAPSOLVER_API_KEY[:10]}...")

    if not CAPSOLVER_API_KEY:
        print("   [WARN] 请先在 .env 文件中填写 CAPSOLVER_API_KEY")
        return

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{CAPSOLVER_API_URL}/getBalance",
            json={"clientKey": CAPSOLVER_API_KEY},
        )
        data = resp.json()
        print(f"   Capsolver 余额: ${data.get('balance', '未知')}")

    print("\n[OK] 验证码模块测试通过!")


if __name__ == "__main__":
    asyncio.run(test_captcha_module())
