"""
模块2：验证码解决
使用 Capsolver API 同时解决:
- Google reCAPTCHA v2（图片选择验证码）
- Cloudflare Turnstile（人机校验）

Serper 注册页面同时使用了这两种验证码。
"""

import httpx
import asyncio
import re
from config import CAPSOLVER_API_KEY, TURNSTILE_SITEKEY

CAPSOLVER_API_URL = "https://api.capsolver.com"

# Serper signup 页面的 reCAPTCHA v2 sitekey
RECAPTCHA_SITEKEY = "6LeIQvYhAAAAAPeN8aXSjTMeCPC7qOCIEZE1_QI4"


async def _poll_capsolver_result(client, task_id: str, label: str, max_polls: int = 60) -> str:
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
            token = data["solution"]["token"] if "token" in data.get("solution", {}) else data["solution"].get("gRecaptchaResponse", "")
            print(f"   [OK] {label} 已解决 ({(i+1)*2}s)")
            return token
        elif status == "failed":
            raise Exception(f"Capsolver {label} 失败: {data.get('errorDescription', '未知')}")
    raise TimeoutError(f"Capsolver {label} 超时 ({max_polls*2}s)")


async def solve_recaptcha_v2(website_url: str, website_key: str = RECAPTCHA_SITEKEY) -> str:
    """
    调用 Capsolver 解 Google reCAPTCHA v2

    参数:
        website_url: 目标页面 URL
        website_key: reCAPTCHA v2 sitekey

    返回: g-recaptcha-response token
    """
    async with httpx.AsyncClient(timeout=180) as client:
        create_resp = await client.post(
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
        data = create_resp.json()
        if data.get("errorId", 0) != 0:
            raise Exception(f"Capsolver reCAPTCHA 创建失败: {data.get('errorDescription', '未知')}")

        task_id = data["taskId"]
        print(f"   Capsolver reCAPTCHA 任务: {task_id}")
        return await _poll_capsolver_result(client, task_id, "reCAPTCHA v2", max_polls=90)


async def solve_turnstile(website_url: str, website_key: str) -> str:
    """
    调用 Capsolver 解 Cloudflare Turnstile

    参数:
        website_url: 目标页面 URL
        website_key: Turnstile sitekey

    返回: cf-turnstile-response token
    """
    async with httpx.AsyncClient(timeout=120) as client:
        create_resp = await client.post(
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
        data = create_resp.json()
        if data.get("errorId", 0) != 0:
            raise Exception(f"Capsolver Turnstile 创建失败: {data.get('errorDescription', '未知')}")

        task_id = data["taskId"]
        print(f"   Capsolver Turnstile 任务: {task_id}")
        return await _poll_capsolver_result(client, task_id, "Turnstile")


async def get_turnstile_sitekey(page, timeout: int = 30) -> str:
    """从页面提取 Turnstile sitekey"""
    for i in range(timeout):
        sitekey = await page.evaluate("""
            () => {
                const div = document.querySelector('[data-sitekey]');
                if (div) return div.getAttribute('data-sitekey');

                const iframe = document.querySelector(
                    'iframe[src*="turnstile"], iframe[src*="challenges.cloudflare.com"]'
                );
                if (iframe) {
                    const src = iframe.getAttribute('src') || '';
                    const match = src.match(/(0x4[A-Za-z0-9_]+)/);
                    if (match) return match[1];
                }

                for (const entry of performance.getEntriesByType('resource')) {
                    const match = entry.name.match(/(0x4[A-Za-z0-9_]+)/);
                    if (match) return match[1];
                }

                const html = document.documentElement.innerHTML;
                const match = html.match(/(0x4[A-Za-z0-9_]+)/);
                if (match) return match[1];

                return null;
            }
        """)
        if sitekey:
            print(f"   Turnstile sitekey: {sitekey}")
            return sitekey
        await asyncio.sleep(1)

    if TURNSTILE_SITEKEY:
        print(f"   Turnstile sitekey (fallback): {TURNSTILE_SITEKEY}")
        return TURNSTILE_SITEKEY

    raise Exception("无法提取 Turnstile sitekey")


async def get_recaptcha_sitekey(page) -> str:
    """从页面提取 reCAPTCHA v2 sitekey"""
    sitekey = await page.evaluate("""
        () => {
            // 方法1: data-sitekey 属性
            const el = document.querySelector('.g-recaptcha[data-sitekey], [data-sitekey]');
            if (el) return el.getAttribute('data-sitekey');
            // 方法2: recaptcha iframe URL 中的 k 参数
            const iframe = document.querySelector('iframe[src*="recaptcha"]');
            if (iframe) {
                const m = iframe.src.match(/[?&]k=([^&]+)/);
                if (m) return m[1];
            }
            // 方法3: 从 HTML 中搜索
            const html = document.documentElement.innerHTML;
            const m = html.match(/["']sitekey["']\\s*:\\s*["']([^"']+)["']/);
            if (m) return m[1];
            return null;
        }
    """)
    if sitekey:
        print(f"   reCAPTCHA sitekey: {sitekey}")
        return sitekey
    # fallback
    print(f"   reCAPTCHA sitekey (fallback): {RECAPTCHA_SITEKEY}")
    return RECAPTCHA_SITEKEY


async def inject_captcha_tokens(page, recaptcha_token: str = "", turnstile_token: str = ""):
    """将验证码 token 注入页面（简洁版，避免递归遍历挂起）"""
    await page.evaluate("""
        ({recaptchaToken, turnstileToken}) => {
            // 注入 reCAPTCHA v2 token
            if (recaptchaToken) {
                // 设置所有 g-recaptcha-response 字段
                document.querySelectorAll(
                    '#g-recaptcha-response, [name="g-recaptcha-response"]'
                ).forEach(el => {
                    el.value = recaptchaToken;
                    el.style.display = 'block';
                    el.dispatchEvent(new Event('change', { bubbles: true }));
                });
            }
            // 注入 Turnstile token
            if (turnstileToken) {
                document.querySelectorAll(
                    '[name="cf-turnstile-response"]'
                ).forEach(el => {
                    el.value = turnstileToken;
                    el.dispatchEvent(new Event('change', { bubbles: true }));
                });
            }
        }
    """, {"recaptchaToken": recaptcha_token, "turnstileToken": turnstile_token})
    if recaptcha_token:
        print("   [OK] reCAPTCHA token 已注入")
    if turnstile_token:
        print("   [OK] Turnstile token 已注入")


# 保留旧接口兼容
async def inject_turnstile_token(page, token: str):
    """兼容旧接口"""
    await inject_captcha_tokens(page, turnstile_token=token)


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
