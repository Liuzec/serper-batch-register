"""
模块3：单次注册流程（纯 httpx，无浏览器依赖）

API 端点 (baseURL: https://api.serper.dev):
  POST /auth/register — 注册 (需要 recaptchaToken + turnstileToken)
  POST /auth/login    — 登录 (需要 turnstileToken)
  GET  /users/api-keys — 获取 API Key 列表 (需要 session cookie)

流程 (6 步):
1. 并发: 创建临时邮箱 + 解 reCAPTCHA v2 + 解 Turnstile
2. POST /auth/register
3. 解 Turnstile → POST /auth/login
4. 轮询 Mail.tm 等待验证邮件
5. GET 验证链接激活账号
6. 重新登录 → GET /users/api-keys 获取 API Key
"""

import asyncio
import json
import os
from datetime import datetime
from faker import Faker

import httpx

from config import (
    SERPER_SIGNUP_URL, OUTPUT_FILE, TURNSTILE_SITEKEY,
    PROXY_URL, IPROYAL_USER,
)
from email_module import create_temp_email, wait_for_verification_email
from captcha_module import solve_recaptcha_v2, solve_turnstile
from proxy_module import get_proxy_url

fake = Faker()

SERPER_API_BASE = "https://api.serper.dev"

# 模拟真实浏览器的请求头
BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": "https://serper.dev",
    "Referer": "https://serper.dev/signup",
    "Content-Type": "application/json",
}


async def api_register(
    client: httpx.AsyncClient,
    form_data: dict,
    recaptcha_token: str,
    turnstile_token: str,
) -> dict:
    """POST /auth/register 注册"""
    body = {
        "firstName": form_data["firstName"],
        "lastName": form_data["lastName"],
        "email": form_data["email"],
        "password": form_data["password"],
        "recaptchaToken": recaptcha_token,
        "turnstileToken": turnstile_token,
    }
    try:
        resp = await client.post(
            f"{SERPER_API_BASE}/auth/register",
            json=body,
            headers=BROWSER_HEADERS,
        )
        text = resp.text[:500]
        try:
            data = resp.json()
        except Exception:
            data = None
        return {"ok": resp.is_success, "status": resp.status_code, "data": data, "text": text}
    except Exception as e:
        return {"error": str(e)}


async def api_login(
    client: httpx.AsyncClient,
    email: str,
    password: str,
    turnstile_token: str,
) -> dict:
    """POST /auth/login 登录，成功后 session cookie 自动保存在 client 中"""
    body = {
        "email": email,
        "password": password,
        "turnstileToken": turnstile_token,
    }
    try:
        resp = await client.post(
            f"{SERPER_API_BASE}/auth/login",
            json=body,
            headers=BROWSER_HEADERS,
        )
        text = resp.text[:500]
        try:
            data = resp.json()
        except Exception:
            data = None
        return {"ok": resp.is_success, "status": resp.status_code, "data": data, "text": text}
    except Exception as e:
        return {"error": str(e)}


async def fetch_api_key(client: httpx.AsyncClient) -> str | None:
    """GET /users/api-keys 获取 API Key（需要 session cookie）"""
    try:
        resp = await client.get(
            f"{SERPER_API_BASE}/users/api-keys",
            headers={
                "User-Agent": BROWSER_HEADERS["User-Agent"],
                "Accept": "application/json",
                "Referer": "https://serper.dev/api-keys",
            },
        )
        if resp.is_success:
            data = resp.json()
            if isinstance(data, list) and len(data) > 0:
                return data[0].get("key")
    except Exception:
        pass
    return None


_save_lock = asyncio.Lock()


async def save_result(result: dict):
    async with _save_lock:
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


async def _get_working_proxy() -> str | None:
    """获取一个能连通 serper.dev 的代理 URL（IPRoyal 旋转代理）"""
    for _try in range(5):
        candidate = get_proxy_url(lifetime="5m")
        try:
            async with httpx.AsyncClient(proxy=candidate, timeout=10) as _c:
                _r = await _c.get(
                    f"{SERPER_API_BASE}/auth/me",
                    headers={"User-Agent": BROWSER_HEADERS["User-Agent"]},
                )
                print(f"   代理 #{_try+1}: 可达")
                return candidate
        except Exception as e:
            print(f"   代理 #{_try+1}: 不可达 ({type(e).__name__})，换 IP...")
    return None


def _make_client(proxy_url: str | None) -> httpx.AsyncClient:
    """创建 httpx 客户端"""
    kwargs = {
        "timeout": httpx.Timeout(60, connect=15),
        "follow_redirects": True,
        "cookies": httpx.Cookies(),
    }
    if proxy_url:
        kwargs["proxy"] = proxy_url
    return httpx.AsyncClient(**kwargs)


async def register_single_account(force_provider: str | None = None) -> dict | None:
    """
    执行一次完整的注册流程（纯 httpx，无浏览器）

    force_provider: 强制使用指定邮箱 provider（"custom_domain" 或 "tempmail_lol"）
    """
    print("\n" + "=" * 50)
    print("开始注册新账号")
    print("=" * 50)

    first_name = fake.first_name()
    last_name = fake.last_name()
    # 随机密码：大小写 + 数字 + 特殊字符，避免所有账号同一密码成为指纹
    import random, string
    _pw_chars = string.ascii_letters + string.digits
    _pw_base = ''.join(random.choices(_pw_chars, k=12))
    _pw_special = random.choice("!@#$%&*")
    password = _pw_base + _pw_special
    use_iproyal = bool(IPROYAL_USER)

    try:
        # ---- 第1步：并发 — 创建邮箱 + 解两个验证码 ----
        print("\n[1/6] 并发: 创建邮箱 + 解验证码...")
        email_task = asyncio.create_task(create_temp_email(force_provider=force_provider))
        rc_task = asyncio.create_task(solve_recaptcha_v2(SERPER_SIGNUP_URL))
        ts_task = asyncio.create_task(solve_turnstile(SERPER_SIGNUP_URL, TURNSTILE_SITEKEY))

        try:
            email_info = await email_task
            email_address = email_info["address"]
            email_token = email_info["token"]
            email_provider = email_info["provider"]
            print(f"   邮箱: {email_address} ({email_provider})")
        except Exception as e:
            print(f"   [FAIL] 创建邮箱失败: {e}")
            rc_task.cancel()
            ts_task.cancel()
            return None

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
        print(f"   姓名: {first_name} {last_name}")

        # ---- 第2步：POST /auth/register（换 IP 重试） ----
        print("\n[2/6] POST /auth/register...")
        form_data = {
            "firstName": first_name,
            "lastName": last_name,
            "email": email_address.lower().strip(),
            "password": password,
        }

        max_ip_tries = 5 if use_iproyal else 1
        reg_ok = False
        client = None

        for ip_attempt in range(1, max_ip_tries + 1):
            # 每次尝试获取新的代理 IP
            if use_iproyal:
                print(f"   [IP {ip_attempt}/{max_ip_tries}] 获取代理...")
                proxy_url = await _get_working_proxy()
                if not proxy_url:
                    print("   代理不可用，跳过")
                    continue
            elif PROXY_URL:
                proxy_url = PROXY_URL
                if ip_attempt == 1:
                    print(f"   固定代理: {PROXY_URL}")
            else:
                proxy_url = None
                if ip_attempt == 1:
                    print("   [WARN] 无代理，直连")

            # 关闭旧 client，创建新的（新 IP）
            if client:
                await client.aclose()
            client = _make_client(proxy_url)

            # 第 2 次以上换 IP 时重新解验证码（token 可能过期）
            if ip_attempt > 1:
                print(f"   重新解验证码...")
                try:
                    rc_task = asyncio.create_task(solve_recaptcha_v2(SERPER_SIGNUP_URL))
                    ts_task = asyncio.create_task(solve_turnstile(SERPER_SIGNUP_URL, TURNSTILE_SITEKEY))
                    recaptcha_token = await rc_task
                    turnstile_token_1 = await ts_task
                except Exception as e:
                    print(f"   验证码解决失败: {e}")
                    continue

            reg_result = await api_register(client, form_data, recaptcha_token, turnstile_token_1)
            status = reg_result.get("status")
            print(f"   尝试: status={status} ok={reg_result.get('ok')}")

            if reg_result.get("ok"):
                reg_ok = True
                break

            if reg_result.get("error"):
                print(f"   请求失败: {reg_result['error']}")
                continue

            # 解析错误
            error_msg = ""
            data = reg_result.get("data")
            if data and isinstance(data, dict):
                error_msg = data.get("message", "") or data.get("error", "")
                if isinstance(error_msg, dict):
                    error_msg = error_msg.get("message", str(error_msg))
            print(f"   错误: {error_msg or reg_result.get('text', '')[:200]}")

            # 不可恢复的错误
            if status in (422, 409):
                break

            # 频率限制 → 换 IP + 等待冷却
            is_rate_limit = (
                status == 429
                or "registration failed" in error_msg.lower()
                or "too many" in error_msg.lower()
            )
            if is_rate_limit and use_iproyal:
                wait = 15 * ip_attempt  # 15s, 30s, 45s, 60s...
                print(f"   [RATE LIMIT] 换 IP + 等待 {wait}s...")
                await asyncio.sleep(wait)
                continue

            # 非旋转代理的频率限制 → 只能等
            if is_rate_limit:
                print(f"   [RATE LIMIT] 无法换 IP，等待 60 秒...")
                await asyncio.sleep(60)

        if not reg_ok:
            print("   [FAIL] 注册失败")
            if client:
                await client.aclose()
            return None

        print("   [OK] 注册成功!")

        # ---- 第3步：解 Turnstile → POST /auth/login ----
        # 从这里开始用注册成功的那个 client（同 IP）
        print("\n[3/6] 登录账号...")
        try:
            turnstile_token_2 = await solve_turnstile(SERPER_SIGNUP_URL, TURNSTILE_SITEKEY)
        except Exception as e:
            print(f"   [WARN] Turnstile 失败: {e}, 用空 token 尝试登录")
            turnstile_token_2 = ""

        login_result = await api_login(client, email_address.lower().strip(), password, turnstile_token_2)
        print(f"   响应: status={login_result.get('status')} ok={login_result.get('ok')}")

        if login_result.get("ok"):
            print("   [OK] 登录成功!")
        else:
            print(f"   [WARN] 登录可能失败: {login_result.get('text', '')[:100]}")

        # ---- 第4步：等待验证邮件 ----
        print("\n[4/6] 等待验证邮件...")
        try:
            verify_link = await wait_for_verification_email(email_token, provider=email_provider, timeout=180)
            print(f"   [OK] 验证链接: {verify_link[:60]}...")
        except TimeoutError:
            print("   [FAIL] 等待验证邮件超时")
            await client.aclose()
            return None

        # ---- 第5步：调用验证 API 激活账号 ----
        print("\n[5/6] 激活账号...")
        try:
            import re as _re
            token_match = _re.search(r'token=([a-zA-Z0-9_\-\.%]+)', verify_link)
            if not token_match:
                print(f"   [WARN] 无法从链接中提取 token: {verify_link[:80]}")
            else:
                verify_token = token_match.group(1)
                resp = await client.post(
                    f"{SERPER_API_BASE}/users/verify-email",
                    json={"token": verify_token},
                    headers=BROWSER_HEADERS,
                )
                print(f"   验证响应: {resp.status_code}")
                if resp.is_success:
                    print("   [OK] 账号已验证")
                else:
                    print(f"   [WARN] 验证可能失败: {resp.text[:200]}")
        except Exception as e:
            print(f"   [WARN] 验证请求异常: {e}")

        # ---- 第6步：重新登录 + GET /users/api-keys ----
        print("\n[6/6] 获取 API Key...")
        print("   重新登录...")
        try:
            ts_token_3 = await solve_turnstile(SERPER_SIGNUP_URL, TURNSTILE_SITEKEY)
            login_r = await api_login(client, email_address.lower().strip(), password, ts_token_3)
            print(f"   登录: status={login_r.get('status')}")
        except Exception as e:
            print(f"   [WARN] 重新登录失败: {e}")

        api_key = await fetch_api_key(client)
        await client.aclose()

        if api_key:
            print(f"   [OK] API Key: {api_key[:10]}...")
        else:
            print("   [WARN] 未能提取 API Key")

        result = {
            "email": email_address,
            "password": password,
            "api_key": api_key or "NEED_MANUAL_EXTRACT",
            "first_name": first_name,
            "last_name": last_name,
            "created_at": datetime.now().isoformat(),
        }
        await save_result(result)
        print("\n[OK] 注册完成!")
        return result

    except Exception as e:
        print(f"\n[FAIL] 注册过程出错: {e}")
        return None


if __name__ == "__main__":
    result = asyncio.run(register_single_account())
    if result:
        print(f"\n成功! API Key: {result.get('api_key', 'N/A')}")
    else:
        print("\n注册失败")
