"""
模块1：临时邮箱（多 provider 自动轮换 + 双模式）

两种模式（通过 EMAIL_MODE 配置）:

  api（默认）:
    轮换使用 5 个临时邮箱 API，每次注册自动分配不同 provider
    1secmail → tempmail.lol → mail.gw → mail.tm → guerrilla
    ~40+ 个不同邮箱域名，大幅降低被 Serper 域名检测的风险
    无需额外配置，开箱即用

  domain:
    使用自建域名池 + Cloudflare Worker + KV，频率控制
    域名额度耗尽后自动 fallback 到 API 轮换
    需要自有域名 + Cloudflare 配置

对外接口:
  create_temp_email()  → {"address", "token", "provider"}
  wait_for_verification_email(token, provider, ...)  → 验证链接 URL
  get_domain_pool()    → DomainPool 实例（domain 模式专用）
"""

import httpx
import asyncio
import random
import string
import re
import quopri
from urllib.parse import quote as url_quote

from config import (
    EMAIL_MODE, MAILTM_API_BASE, MAILGW_API_BASE,
    CUSTOM_EMAIL_DOMAIN, CF_ACCOUNT_ID, CF_API_TOKEN, CF_KV_NAMESPACE_ID,
    DOMAIN_MAX_PER_WINDOW, DOMAIN_WINDOW_SECONDS,
)
from domain_pool import DomainPool


# ============================================
# 通用工具
# ============================================

BROWSER_UA = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    )
}


def _random_username(length: int = 12) -> str:
    chars = string.ascii_lowercase + string.digits
    return ''.join(random.choices(chars, k=length))


def _decode_email_body(body: str) -> str:
    """解码邮件正文：处理 quoted-printable 编码"""
    try:
        decoded = quopri.decodestring(body.encode("utf-8", errors="replace")).decode("utf-8", errors="replace")
        return decoded
    except Exception:
        body = body.replace("=3D", "=")
        body = body.replace("=\r\n", "").replace("=\n", "")
        return body


def extract_verification_link(email_body: str) -> str | None:
    """从邮件正文中提取 Serper 验证链接（自动解码 quoted-printable）"""
    body = _decode_email_body(email_body)
    pattern = r'https://serper\.dev/confirm-email\?token=[a-zA-Z0-9_\-\.%]+'
    match = re.search(pattern, body)
    if match:
        return match.group(0)
    fallback = r'https://serper\.dev/[^\s"<>\']+'
    match = re.search(fallback, body)
    if match:
        return match.group(0)
    return None


# ============================================
# 域名池（domain 模式专用，全局单例）
# ============================================

_domain_pool: DomainPool | None = None


def _get_custom_domains() -> list[str]:
    """解析自有域名列表（支持逗号分隔多个域名）"""
    return [d.strip() for d in CUSTOM_EMAIL_DOMAIN.split(",") if d.strip()]


def _custom_domain_available() -> bool:
    """检查自有域名邮箱是否已配置"""
    return bool(CUSTOM_EMAIL_DOMAIN and CF_ACCOUNT_ID and CF_API_TOKEN and CF_KV_NAMESPACE_ID)


def get_domain_pool() -> DomainPool | None:
    """获取域名池实例（domain 模式专用）"""
    global _domain_pool
    if _domain_pool is None and _custom_domain_available():
        domains = _get_custom_domains()
        _domain_pool = DomainPool(
            domains,
            max_per_window=DOMAIN_MAX_PER_WINDOW,
            window_seconds=DOMAIN_WINDOW_SECONDS,
        )
    return _domain_pool


# ============================================
# Provider: 自有域名（Cloudflare Worker + KV）
# ============================================

async def _custom_domain_create(client: httpx.AsyncClient) -> dict | None:
    """自有域名：从域名池获取可用域名，本地生成随机地址。"""
    pool = get_domain_pool()
    if pool is None:
        return None
    domain = await pool.acquire()
    if domain is None:
        return None
    address = f"{_random_username()}@{domain}"
    return {"address": address, "token": address, "provider": "custom_domain"}


async def _custom_domain_wait(client: httpx.AsyncClient, token: str, timeout: int, interval: int) -> str:
    """自有域名：轮询 Cloudflare KV API，等待 Worker 存入验证链接。"""
    email_key = token.lower()
    encoded_key = url_quote(email_key, safe='')
    url = (
        f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}"
        f"/storage/kv/namespaces/{CF_KV_NAMESPACE_ID}/values/{encoded_key}"
    )
    headers = {"Authorization": f"Bearer {CF_API_TOKEN}"}
    elapsed = 0
    while elapsed < timeout:
        try:
            resp = await client.get(url, headers=headers)
            if resp.status_code == 200:
                import json
                try:
                    data = json.loads(resp.text)
                    link = data.get("link")
                except (json.JSONDecodeError, AttributeError):
                    link = resp.text.strip()
                if link and "serper.dev" in link:
                    return link
        except Exception:
            pass
        await asyncio.sleep(interval)
        elapsed += interval
    raise TimeoutError(f"自有域名邮箱等待验证邮件超时（{timeout}秒）")


# ============================================
# Provider: temp-mail.io（零认证，每次不同域名）
# ============================================

TEMPMAIILIO_API = "https://api.internal.temp-mail.io/api/v3"


async def _tempmailio_create(client: httpx.AsyncClient) -> dict:
    """temp-mail.io：零认证，每次生成不同域名的邮箱。"""
    resp = await client.post(
        f"{TEMPMAIILIO_API}/email/new",
        json={"min_name_length": 10, "max_name_length": 10},
        headers=BROWSER_UA,
    )
    resp.raise_for_status()
    data = resp.json()
    # token 存邮箱地址（收件箱用地址查询）
    return {"address": data["email"], "token": data["email"], "provider": "tempmailio"}


async def _tempmailio_wait(client: httpx.AsyncClient, token: str, timeout: int, interval: int) -> str:
    """temp-mail.io：轮询收件箱等待验证邮件。"""
    email_addr = token
    elapsed = 0
    while elapsed < timeout:
        resp = await client.get(
            f"{TEMPMAIILIO_API}/email/{email_addr}/messages",
            headers=BROWSER_UA,
        )
        if resp.status_code == 200:
            messages = resp.json()
            for msg in messages:
                sender = (msg.get("from", "") or "").lower()
                subject = (msg.get("subject", "") or "").lower()
                if "serper" in sender or "serper" in subject or "verify" in subject:
                    # 邮件正文可能在 body_text 或 body_html 中
                    body = msg.get("body_html", "") or msg.get("body_text", "") or msg.get("body", "")
                    link = extract_verification_link(body)
                    if link:
                        return link
        await asyncio.sleep(interval)
        elapsed += interval
    raise TimeoutError(f"temp-mail.io 等待验证邮件超时（{timeout}秒）")


# ============================================
# Provider: tempmail.lol
# ============================================

TEMPMAIL_LOL_API = "https://api.tempmail.lol/v2"


async def _tempmail_lol_create(client: httpx.AsyncClient) -> dict:
    """tempmail.lol：有代理时走代理创建，绕过 IP 限速。"""
    from config import IPROYAL_USER
    from proxy_module import get_proxy_url

    if IPROYAL_USER:
        proxy_url = get_proxy_url(lifetime="1m")
        async with httpx.AsyncClient(proxy=proxy_url, timeout=15, headers=BROWSER_UA) as proxy_client:
            resp = await proxy_client.post(f"{TEMPMAIL_LOL_API}/inbox/create")
            resp.raise_for_status()
            data = resp.json()
    else:
        resp = await client.post(f"{TEMPMAIL_LOL_API}/inbox/create", headers=BROWSER_UA)
        resp.raise_for_status()
        data = resp.json()

    return {"address": data["address"], "token": data["token"], "provider": "tempmail_lol"}


async def _tempmail_lol_wait(client: httpx.AsyncClient, token: str, timeout: int, interval: int) -> str:
    """tempmail.lol：轮询等待验证邮件。"""
    elapsed = 0
    while elapsed < timeout:
        resp = await client.get(f"{TEMPMAIL_LOL_API}/inbox", params={"token": token}, headers=BROWSER_UA)
        resp.raise_for_status()
        data = resp.json()
        if data.get("expired"):
            raise Exception("tempmail.lol 邮箱已过期")
        for email in data.get("emails", []):
            subject = email.get("subject", "")
            sender = email.get("from", "")
            body = email.get("body", "") or email.get("html", "")
            if "serper" in sender.lower() or "serper" in subject.lower() or "verify" in subject.lower():
                link = extract_verification_link(body)
                if link:
                    return link
        await asyncio.sleep(interval)
        elapsed += interval
    raise TimeoutError(f"tempmail.lol 等待验证邮件超时（{timeout}秒）")


# ============================================
# Provider: Mail.gw（mail.tm 同系，不同域名池）
# ============================================

async def _mailgw_create(client: httpx.AsyncClient) -> dict:
    """Mail.gw：和 mail.tm 相同 API，但域名池不同。"""
    resp = await client.get(f"{MAILGW_API_BASE}/domains")
    resp.raise_for_status()
    data = resp.json()
    domains = [d["domain"] for d in data.get("hydra:member", data) if d.get("isActive", True)]
    if not domains:
        raise Exception("Mail.gw 无可用域名")
    address = f"{_random_username()}@{random.choice(domains)}"
    password = "TempPass123!"
    resp = await client.post(f"{MAILGW_API_BASE}/accounts", json={"address": address, "password": password})
    resp.raise_for_status()
    resp = await client.post(f"{MAILGW_API_BASE}/token", json={"address": address, "password": password})
    resp.raise_for_status()
    token = resp.json()["token"]
    return {"address": address, "token": token, "provider": "mailgw"}


async def _mailgw_wait(client: httpx.AsyncClient, token: str, timeout: int, interval: int) -> str:
    """Mail.gw：轮询等待验证邮件。"""
    headers = {"Authorization": f"Bearer {token}"}
    elapsed = 0
    while elapsed < timeout:
        resp = await client.get(f"{MAILGW_API_BASE}/messages", headers=headers)
        resp.raise_for_status()
        messages = resp.json().get("hydra:member", resp.json())
        for msg in messages:
            from_addr = msg.get("from", {}).get("address", "")
            subject = msg.get("subject", "")
            if "serper" in from_addr.lower() or "serper" in subject.lower() or "verify" in subject.lower():
                detail = await client.get(f"{MAILGW_API_BASE}/messages/{msg['id']}", headers=headers)
                detail.raise_for_status()
                d = detail.json()
                body = d.get("text", "") or (d.get("html", [""])[0] if isinstance(d.get("html"), list) else d.get("html", ""))
                link = extract_verification_link(body)
                if link:
                    return link
        await asyncio.sleep(interval)
        elapsed += interval
    raise TimeoutError(f"Mail.gw 等待验证邮件超时（{timeout}秒）")


# ============================================
# Provider: Mail.tm
# ============================================

async def _mailtm_create(client: httpx.AsyncClient) -> dict:
    resp = await client.get(f"{MAILTM_API_BASE}/domains")
    resp.raise_for_status()
    data = resp.json()
    domains = [d["domain"] for d in data.get("hydra:member", data) if d.get("isActive", True)]
    if not domains:
        raise Exception("Mail.tm 无可用域名")
    address = f"{_random_username()}@{random.choice(domains)}"
    password = "TempPass123!"
    resp = await client.post(f"{MAILTM_API_BASE}/accounts", json={"address": address, "password": password})
    resp.raise_for_status()
    resp = await client.post(f"{MAILTM_API_BASE}/token", json={"address": address, "password": password})
    resp.raise_for_status()
    token = resp.json()["token"]
    return {"address": address, "token": token, "provider": "mailtm"}


async def _mailtm_wait(client: httpx.AsyncClient, token: str, timeout: int, interval: int) -> str:
    headers = {"Authorization": f"Bearer {token}"}
    elapsed = 0
    while elapsed < timeout:
        resp = await client.get(f"{MAILTM_API_BASE}/messages", headers=headers)
        resp.raise_for_status()
        messages = resp.json().get("hydra:member", resp.json())
        for msg in messages:
            from_addr = msg.get("from", {}).get("address", "")
            subject = msg.get("subject", "")
            if "serper" in from_addr.lower() or "serper" in subject.lower() or "verify" in subject.lower():
                detail = await client.get(f"{MAILTM_API_BASE}/messages/{msg['id']}", headers=headers)
                detail.raise_for_status()
                d = detail.json()
                body = d.get("text", "") or (d.get("html", [""])[0] if isinstance(d.get("html"), list) else d.get("html", ""))
                link = extract_verification_link(body)
                if link:
                    return link
        await asyncio.sleep(interval)
        elapsed += interval
    raise TimeoutError(f"Mail.tm 等待验证邮件超时（{timeout}秒）")


# ============================================
# Provider: Guerrilla Mail（25+ 域名）
# ============================================

GUERRILLA_API = "https://api.guerrillamail.com/ajax.php"


async def _guerrilla_create(client: httpx.AsyncClient) -> dict:
    resp = await client.get(GUERRILLA_API, params={"f": "get_email_address"}, headers=BROWSER_UA)
    resp.raise_for_status()
    data = resp.json()
    return {"address": data["email_addr"], "token": data["sid_token"], "provider": "guerrilla"}


async def _guerrilla_wait(client: httpx.AsyncClient, token: str, timeout: int, interval: int) -> str:
    elapsed = 0
    seq = 0
    while elapsed < timeout:
        resp = await client.get(
            GUERRILLA_API,
            params={"f": "check_email", "sid_token": token, "seq": seq},
            headers=BROWSER_UA,
        )
        resp.raise_for_status()
        data = resp.json()
        for msg in data.get("list", []):
            subject = msg.get("mail_subject", "")
            sender = msg.get("mail_from", "")
            if "serper" in sender.lower() or "serper" in subject.lower() or "verify" in subject.lower():
                detail = await client.get(
                    GUERRILLA_API,
                    params={"f": "fetch_email", "sid_token": token, "email_id": msg["mail_id"]},
                    headers=BROWSER_UA,
                )
                detail.raise_for_status()
                body = detail.json().get("mail_body", "")
                link = extract_verification_link(body)
                if link:
                    return link
        await asyncio.sleep(interval)
        elapsed += interval
    raise TimeoutError(f"Guerrilla Mail 等待验证邮件超时（{timeout}秒）")


# ============================================
# API 模式：provider 轮换池
# ============================================

# 轮换顺序（并发时每个任务自动分配不同 provider）
_API_PROVIDERS = ["tempmailio", "tempmail_lol", "mailgw", "mailtm", "guerrilla"]

_API_CREATE_DISPATCH = {
    "tempmailio":   _tempmailio_create,
    "tempmail_lol": _tempmail_lol_create,
    "mailgw":       _mailgw_create,
    "mailtm":       _mailtm_create,
    "guerrilla":    _guerrilla_create,
}

# 原子计数器：保证并发分配不同 provider
_api_counter = 0
_api_counter_lock = asyncio.Lock()


async def _create_api_email(client: httpx.AsyncClient) -> dict:
    """
    API 模式核心：轮询选择 provider，失败自动 fallback 到下一个。

    并发安全：原子计数器保证每个并发任务分配不同的 provider。
    例如 5 路并发 → 分别分配 1secmail, tempmail_lol, mailgw, mailtm, guerrilla。
    """
    global _api_counter
    async with _api_counter_lock:
        start_index = _api_counter % len(_API_PROVIDERS)
        _api_counter += 1

    for i in range(len(_API_PROVIDERS)):
        provider_name = _API_PROVIDERS[(start_index + i) % len(_API_PROVIDERS)]
        create_fn = _API_CREATE_DISPATCH[provider_name]
        try:
            result = await create_fn(client)
            print(f"   邮箱 provider: {provider_name}")
            return result
        except Exception as e:
            print(f"   [WARN] {provider_name} 失败: {e}")
            continue

    raise Exception("所有邮箱 API 均失败")


# ============================================
# 统一 wait dispatch
# ============================================

_WAIT_DISPATCH = {
    "custom_domain": _custom_domain_wait,
    "tempmailio":    _tempmailio_wait,
    "tempmail_lol":  _tempmail_lol_wait,
    "mailgw":        _mailgw_wait,
    "mailtm":        _mailtm_wait,
    "guerrilla":     _guerrilla_wait,
}


# ============================================
# 对外接口
# ============================================

async def create_temp_email(client: httpx.AsyncClient | None = None, force_provider: str | None = None) -> dict:
    """
    创建临时邮箱。

    策略:
      force_provider 指定 → 只用该 provider
      EMAIL_MODE=api（默认）→ 轮换 5 个临时邮箱 API
      EMAIL_MODE=domain   → 自有域名池 + API fallback

    返回: {"address", "token", "provider"}
    """
    should_close = client is None
    client = client or httpx.AsyncClient(timeout=30, headers=BROWSER_UA)

    try:
        # 1. 强制指定 provider
        if force_provider:
            if force_provider == "custom_domain":
                result = await _custom_domain_create(client)
                if result:
                    domain = result["address"].split("@")[1]
                    print(f"   邮箱 provider: custom_domain ({domain})")
                    return result
                raise Exception("自有域名池已耗尽或未配置")

            create_fn = _API_CREATE_DISPATCH.get(force_provider)
            if create_fn:
                result = await create_fn(client)
                print(f"   邮箱 provider: {force_provider}")
                return result
            raise Exception(f"未知 provider: {force_provider}")

        # 2. domain 模式：优先域名池，耗尽后 fallback 到 API 轮换
        if EMAIL_MODE == "domain" and _custom_domain_available():
            result = await _custom_domain_create(client)
            if result:
                domain = result["address"].split("@")[1]
                pool = get_domain_pool()
                remaining = pool.total_remaining() if pool else "?"
                print(f"   邮箱 provider: custom_domain ({domain}) [剩余额度: {remaining}]")
                return result
            else:
                pool = get_domain_pool()
                if pool:
                    print(f"   [INFO] 域名池已耗尽，fallback 到 API 轮换 ({pool.summary()})")

        # 3. API 模式（默认）：轮换所有 provider
        return await _create_api_email(client)

    finally:
        if should_close:
            await client.aclose()


async def wait_for_verification_email(
    token: str,
    provider: str = "1secmail",
    timeout: int = 120,
    interval: int = 3,
    client: httpx.AsyncClient | None = None,
) -> str:
    """轮询等待 Serper 的验证邮件，返回验证链接 URL"""
    should_close = client is None
    client = client or httpx.AsyncClient(timeout=30, headers=BROWSER_UA)
    try:
        wait_fn = _WAIT_DISPATCH.get(provider)
        if not wait_fn:
            raise ValueError(f"未知的邮箱 provider: {provider}")
        return await wait_fn(client, token, timeout, interval)
    finally:
        if should_close:
            await client.aclose()


# ============================================
# 测试入口
# ============================================

async def test_email_module():
    print("=== 测试邮箱模块 ===\n")
    print(f"当前模式: EMAIL_MODE={EMAIL_MODE}")
    print(f"API provider 池: {', '.join(_API_PROVIDERS)}\n")

    if EMAIL_MODE == "domain" and _custom_domain_available():
        pool = get_domain_pool()
        print(f"--- 自有域名池 ---")
        print(f"   {pool.summary()}")
        result = await _custom_domain_create(None)
        if result:
            print(f"   生成: {result['address']}")
            print(f"   {pool.summary()}")
        print()

    async with httpx.AsyncClient(timeout=30, headers=BROWSER_UA) as client:
        for name, create_fn in _API_CREATE_DISPATCH.items():
            print(f"--- 测试 {name} ---")
            try:
                result = await create_fn(client)
                print(f"   地址: {result['address']}")
                print(f"   [OK]\n")
            except Exception as e:
                print(f"   [FAIL] {e}\n")

    print("邮箱模块测试完成！")


if __name__ == "__main__":
    asyncio.run(test_email_module())
