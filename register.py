"""
模块3：单次注册流程
通过 JS fetch 直接 POST /auth/register + /auth/login
绕过 Cloudflare Managed Challenge 弹窗

API 端点 (baseURL: https://api.serper.dev):
  POST /auth/register — 注册 (需要 recaptchaToken + turnstileToken)
  POST /auth/login    — 登录 (需要 turnstileToken)
  GET  /auth/me       — 获取用户信息 (需要 session cookie)
  GET  /users/api-keys — 获取 API Key 列表 (需要 session cookie)

流程:
1. 创建临时邮箱
2. 打开注册页面（获取 cookies）
3. 并发解 reCAPTCHA v2 invisible + Turnstile
4. fetch POST /auth/register
5. 解第二个 Turnstile（登录用）
6. fetch POST /auth/login
7. 等待验证邮件 + 激活
8. 重新登录 + GET /users/api-keys 获取 API Key
"""

import asyncio
import json
import os
import random
from datetime import datetime
from playwright.async_api import async_playwright
from faker import Faker

from config import (
    SERPER_SIGNUP_URL, SERPER_LOGIN_URL, SERPER_DASHBOARD_URL,
    OUTPUT_FILE, BROWSER_CHANNEL, HEADLESS, TURNSTILE_SITEKEY,
)
from email_module import create_temp_email, wait_for_verification_email
from captcha_module import solve_recaptcha_v2, solve_turnstile

fake = Faker()


def random_delay() -> int:
    return random.randint(500, 1200)


async def dismiss_cookie_banner(page):
    for text in ["Reject all", "Accept all", "Accept", "Decline"]:
        btn = page.locator(f'button:has-text("{text}")').first
        try:
            if await btn.count() > 0 and await btn.is_visible():
                await btn.click()
                await page.wait_for_timeout(500)
                return
        except Exception:
            continue


async def api_register(page, form_data: dict, recaptcha_token: str, turnstile_token: str) -> dict:
    """通过 fetch POST /auth/register 注册"""
    result = await page.evaluate("""
        async ({formData, recaptchaToken, turnstileToken}) => {
            try {
                const body = {
                    firstName: formData.firstName,
                    lastName: formData.lastName,
                    email: formData.email,
                    password: formData.password,
                    recaptchaToken: recaptchaToken,
                    turnstileToken: turnstileToken,
                };
                const resp = await fetch('https://api.serper.dev/auth/register', {
                    method: 'POST',
                    credentials: 'include',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(body),
                });
                const text = await resp.text().catch(() => '');
                let data = null;
                try { data = JSON.parse(text); } catch {}
                return {ok: resp.ok, status: resp.status, data: data, text: text.slice(0, 500)};
            } catch(e) {
                return {error: e.message};
            }
        }
    """, {
        "formData": form_data,
        "recaptchaToken": recaptcha_token,
        "turnstileToken": turnstile_token,
    })
    return result


async def api_login(page, email: str, password: str, turnstile_token: str) -> dict:
    """通过 fetch POST /auth/login 登录"""
    result = await page.evaluate("""
        async ({email, password, turnstileToken}) => {
            try {
                const resp = await fetch('https://api.serper.dev/auth/login', {
                    method: 'POST',
                    credentials: 'include',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({
                        email: email,
                        password: password,
                        turnstileToken: turnstileToken,
                    }),
                });
                const text = await resp.text().catch(() => '');
                let data = null;
                try { data = JSON.parse(text); } catch {}
                return {ok: resp.ok, status: resp.status, data: data, text: text.slice(0, 500)};
            } catch(e) {
                return {error: e.message};
            }
        }
    """, {"email": email, "password": password, "turnstileToken": turnstile_token})
    return result


async def fetch_api_key(page) -> str | None:
    """通过 GET /users/api-keys 获取 API Key（需要先登录获取 session cookie）"""
    try:
        result = await page.evaluate("""
            async () => {
                try {
                    const resp = await fetch('https://api.serper.dev/users/api-keys', {
                        credentials: 'include',
                    });
                    if (resp.ok) {
                        const data = await resp.json();
                        // data 是数组: [{id, userId, key, name, revokedAt, metadata}]
                        if (Array.isArray(data) && data.length > 0) {
                            return data[0].key || null;
                        }
                    }
                    return null;
                } catch {
                    return null;
                }
            }
        """)
        return result
    except Exception:
        return None


async def extract_api_key_from_page(page) -> str | None:
    """从 API keys 页面 DOM 提取 API Key（备用方案）"""
    try:
        return await page.evaluate("""
            () => {
                for (const el of document.querySelectorAll('code, pre, .api-key, [data-api-key]')) {
                    const t = el.textContent.trim();
                    if (t.length >= 30 && t.length <= 64 && /^[a-f0-9]+$/i.test(t)) return t;
                }
                for (const el of document.querySelectorAll('input[readonly], input[type="text"]')) {
                    const v = el.value.trim();
                    if (v.length >= 30 && v.length <= 64 && /^[a-f0-9]+$/i.test(v)) return v;
                }
                const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, null);
                while (w.nextNode()) {
                    const t = w.currentNode.textContent.trim();
                    if (t.length >= 30 && t.length <= 64 && /^[a-f0-9]+$/i.test(t)) return t;
                }
                return null;
            }
        """)
    except Exception:
        return None


def save_result(result: dict):
    results = []
    if os.path.exists(OUTPUT_FILE):
        with open(OUTPUT_FILE, "r", encoding="utf-8") as f:
            try:
                results = json.load(f)
            except json.JSONDecodeError:
                results = []
    results.append(result)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print(f"   结果已保存到 {OUTPUT_FILE} (共 {len(results)} 个账号)")


async def register_single_account() -> dict | None:
    """执行一次完整的注册流程"""
    print("\n" + "=" * 50)
    print("开始注册新账号")
    print("=" * 50)

    # ---- 第1步：创建临时邮箱 ----
    print("\n[1/8] 创建临时邮箱...")
    try:
        email_info = await create_temp_email()
        email_address = email_info["address"]
        email_token = email_info["token"]
        print(f"   邮箱: {email_address}")
    except Exception as e:
        print(f"   [FAIL] 创建邮箱失败: {e}")
        return None

    first_name = fake.first_name()
    last_name = fake.last_name()
    password = "SerperPass123!"
    print(f"   姓名: {first_name} {last_name}")

    async with async_playwright() as p:
        launch_options = {
            "headless": HEADLESS,
            "args": ["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        }
        if BROWSER_CHANNEL:
            launch_options["channel"] = BROWSER_CHANNEL
        browser = await p.chromium.launch(**launch_options)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 720},
            locale="en-US",
        )
        page = await context.new_page()

        try:
            # ---- 第2步：打开注册页面（获取 Cloudflare cookies） ----
            print("\n[2/8] 打开注册页面...")
            await page.goto(SERPER_SIGNUP_URL, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(3000)
            await dismiss_cookie_banner(page)
            print("   [OK] 页面已加载")

            # ---- 第3步：并发解两个验证码 ----
            print("\n[3/8] 解验证码 (reCAPTCHA v2 invisible + Turnstile)...")
            print("   并发解两个验证码...")
            rc_task = asyncio.create_task(solve_recaptcha_v2(SERPER_SIGNUP_URL))
            ts_task = asyncio.create_task(solve_turnstile(SERPER_SIGNUP_URL, TURNSTILE_SITEKEY))

            recaptcha_token = ""
            turnstile_token_1 = ""
            try:
                recaptcha_token = await rc_task
            except Exception as e:
                print(f"   [WARN] reCAPTCHA 失败: {e}")
            try:
                turnstile_token_1 = await ts_task
            except Exception as e:
                print(f"   [WARN] Turnstile 失败: {e}")

            if not recaptcha_token:
                print("   [FAIL] reCAPTCHA 必须解决才能注册")
                return None

            print(f"   reCAPTCHA: {len(recaptcha_token)} chars")
            print(f"   Turnstile: {len(turnstile_token_1)} chars")

            # ---- 第4步：POST /auth/register（带重试） ----
            print("\n[4/8] POST /auth/register...")
            form_data = {
                "firstName": first_name,
                "lastName": last_name,
                "email": email_address.lower().strip(),
                "password": password,
            }

            max_retries = 3
            reg_ok = False
            for attempt in range(1, max_retries + 1):
                if attempt > 1:
                    print(f"\n   [RETRY {attempt}/{max_retries}] 重新解验证码...")
                    try:
                        rc_task = asyncio.create_task(solve_recaptcha_v2(SERPER_SIGNUP_URL))
                        ts_task = asyncio.create_task(solve_turnstile(SERPER_SIGNUP_URL, TURNSTILE_SITEKEY))
                        recaptcha_token = await rc_task
                        turnstile_token_1 = await ts_task
                    except Exception as e:
                        print(f"   验证码解决失败: {e}")
                        continue

                reg_result = await api_register(page, form_data, recaptcha_token, turnstile_token_1)
                print(f"   尝试 {attempt}: status={reg_result.get('status')} ok={reg_result.get('ok')}")

                if reg_result.get("error"):
                    print(f"   请求失败: {reg_result['error']}")
                    continue

                if reg_result.get("ok"):
                    reg_ok = True
                    break

                # 解析错误信息
                error_msg = ""
                data = reg_result.get("data")
                if data and isinstance(data, dict):
                    error_msg = data.get("error", "") or data.get("message", "")
                    if isinstance(error_msg, dict):
                        error_msg = error_msg.get("message", str(error_msg))
                full_text = reg_result.get('text', '')[:300]
                print(f"   错误: {error_msg or full_text}")

                # 如果是 422（缺少字段）或 409（邮箱已存在），不重试
                if reg_result.get("status") in (422, 409):
                    break

                # 如果是 429（频率限制），等待较长时间再重试
                if reg_result.get("status") == 429:
                    wait_secs = 60 * attempt  # 60s, 120s, 180s
                    print(f"   [RATE LIMIT] 等待 {wait_secs} 秒后重试...")
                    await page.wait_for_timeout(wait_secs * 1000)
                elif attempt < max_retries:
                    await page.wait_for_timeout(3000)

            if not reg_ok:
                print("   [FAIL] 注册在所有重试后均失败")
                return None

            print("   [OK] 注册成功!")

            # ---- 第5步：解第二个 Turnstile（登录用） + POST /auth/login ----
            print("\n[5/8] 登录账号...")
            try:
                turnstile_token_2 = await solve_turnstile(SERPER_SIGNUP_URL, TURNSTILE_SITEKEY)
            except Exception as e:
                print(f"   [WARN] 第二个 Turnstile 失败: {e}, 用空 token 尝试登录")
                turnstile_token_2 = ""

            login_result = await api_login(page, email_address.lower().strip(), password, turnstile_token_2)
            print(f"   响应: status={login_result.get('status')} ok={login_result.get('ok')}")

            if login_result.get("ok"):
                print("   [OK] 登录成功!")
            else:
                print(f"   [WARN] 登录可能失败: {login_result.get('text', '')[:100]}")

            # ---- 第6步：等待验证邮件 ----
            print("\n[6/8] 等待验证邮件...")
            try:
                verify_link = await wait_for_verification_email(email_token, timeout=180)
                print(f"   [OK] 验证链接: {verify_link[:60]}...")
            except TimeoutError:
                print("   [FAIL] 等待验证邮件超时")
                await page.screenshot(path="debug_no_email.png")
                return None

            # ---- 第7步：访问验证链接 ----
            print("\n[7/8] 激活账号...")
            await page.goto(verify_link, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(3000)
            print("   [OK] 账号已验证")

            # ---- 第8步：重新登录 + 提取 API Key ----
            print("\n[8/8] 获取 API Key...")

            # 验证后需要重新打开 serper.dev 页面（获取同源 cookies）
            await page.goto("https://serper.dev/dashboard", wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(2000)

            # 重新登录
            print("   重新登录...")
            try:
                ts_token_3 = await solve_turnstile(SERPER_SIGNUP_URL, TURNSTILE_SITEKEY)
                login_r = await api_login(page, email_address.lower().strip(), password, ts_token_3)
                print(f"   登录: status={login_r.get('status')}")
            except Exception as e:
                print(f"   [WARN] 重新登录失败: {e}")

            # 方法1: 通过 API 获取 API Key (推荐)
            api_key = await fetch_api_key(page)

            if not api_key:
                # 方法2: 导航到 API keys 页面提取
                print("   API 获取失败，尝试页面提取...")
                await page.goto("https://serper.dev/api-keys", wait_until="domcontentloaded", timeout=30000)
                await page.wait_for_timeout(3000)
                api_key = await extract_api_key_from_page(page)

            if api_key:
                print(f"   [OK] API Key: {api_key[:10]}...")
            else:
                print("   [WARN] 未能提取 API Key")
                await page.screenshot(path="debug_apikey.png")

            result = {
                "email": email_address,
                "password": password,
                "api_key": api_key or "NEED_MANUAL_EXTRACT",
                "first_name": first_name,
                "last_name": last_name,
                "created_at": datetime.now().isoformat(),
            }
            save_result(result)
            print("\n[OK] 注册完成!")
            return result

        except Exception as e:
            print(f"\n[FAIL] 注册过程出错: {e}")
            try:
                await page.screenshot(path="debug_error.png")
            except Exception:
                pass
            return None
        finally:
            await browser.close()


if __name__ == "__main__":
    result = asyncio.run(register_single_account())
    if result:
        print(f"\n成功! API Key: {result.get('api_key', 'N/A')}")
    else:
        print("\n注册失败，检查 debug 截图")
