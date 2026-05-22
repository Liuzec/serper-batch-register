"""
模块1：临时邮箱
使用 Mail.tm 的免费 API 来：
1. 获取可用域名
2. 创建临时邮箱账号
3. 轮询等待验证邮件
4. 从邮件中提取验证链接
"""

import httpx
import asyncio
import random
import string
import re
from config import MAILTM_API_BASE


async def get_available_domains() -> list[str]:
    """获取 Mail.tm 当前可用的邮箱域名"""
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{MAILTM_API_BASE}/domains")
        resp.raise_for_status()
        data = resp.json()
        domains = [item["domain"] for item in data.get("hydra:member", data) if item.get("isActive", True)]
        return domains


def generate_random_username(length: int = 12) -> str:
    """生成随机用户名"""
    chars = string.ascii_lowercase + string.digits
    return ''.join(random.choices(chars, k=length))


async def create_temp_email(password: str = "TempPass123!") -> dict:
    """
    创建一个临时邮箱账号
    返回: {"address": "xxx@domain.com", "password": "...", "token": "..."}
    """
    domains = await get_available_domains()
    if not domains:
        raise Exception("没有可用的 Mail.tm 域名")

    domain = random.choice(domains)
    username = generate_random_username()
    address = f"{username}@{domain}"

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{MAILTM_API_BASE}/accounts",
            json={"address": address, "password": password}
        )
        resp.raise_for_status()
        account_data = resp.json()

        resp = await client.post(
            f"{MAILTM_API_BASE}/token",
            json={"address": address, "password": password}
        )
        resp.raise_for_status()
        token_data = resp.json()

        return {
            "address": address,
            "password": password,
            "token": token_data["token"],
            "account_id": account_data["id"]
        }


def _extract_body(detail: dict) -> str:
    """从邮件详情中提取正文，兼容 text 和 html 两种格式"""
    text = detail.get("text", "")
    if text:
        return text
    html = detail.get("html")
    if isinstance(html, list):
        return html[0] if html else ""
    if isinstance(html, str):
        return html
    return ""


async def wait_for_verification_email(token: str, timeout: int = 120, interval: int = 3) -> str:
    """
    轮询等待 Serper 的验证邮件，提取验证链接

    参数:
        token: Mail.tm 的 Bearer token
        timeout: 最长等待秒数
        interval: 每次轮询间隔秒数

    返回: 验证链接 URL
    """
    headers = {"Authorization": f"Bearer {token}"}
    elapsed = 0

    async with httpx.AsyncClient() as client:
        while elapsed < timeout:
            resp = await client.get(
                f"{MAILTM_API_BASE}/messages",
                headers=headers
            )
            resp.raise_for_status()
            data = resp.json()
            messages = data.get("hydra:member", data)

            for msg in messages:
                from_addr = msg.get("from", {}).get("address", "")
                subject = msg.get("subject", "")

                if "serper" in from_addr.lower() or "serper" in subject.lower() or "verify" in subject.lower():
                    msg_id = msg["id"]
                    detail_resp = await client.get(
                        f"{MAILTM_API_BASE}/messages/{msg_id}",
                        headers=headers
                    )
                    detail_resp.raise_for_status()
                    detail = detail_resp.json()

                    body = _extract_body(detail)
                    link = extract_verification_link(body)
                    if link:
                        return link

            await asyncio.sleep(interval)
            elapsed += interval

    raise TimeoutError(f"等待验证邮件超时（{timeout}秒）")


def extract_verification_link(email_body: str) -> str | None:
    """从邮件正文中提取 Serper 验证链接"""
    pattern = r'https://serper\.dev/confirm-email\?token=[a-zA-Z0-9_\-\.%]+'
    match = re.search(pattern, email_body)
    if match:
        return match.group(0)

    # 兜底：匹配任何 serper.dev 的链接
    fallback = r'https://serper\.dev/[^\s"<>]+'
    match = re.search(fallback, email_body)
    if match:
        return match.group(0)
    return None


# ============================================
# 测试入口
# ============================================
async def test_email_module():
    print("=== 测试邮箱模块 ===")

    print("\n1. 获取可用域名...")
    domains = await get_available_domains()
    print(f"   可用域名: {domains}")

    print("\n2. 创建临时邮箱...")
    email_info = await create_temp_email()
    print(f"   邮箱地址: {email_info['address']}")
    print(f"   Token: {email_info['token'][:20]}...")

    print("\n邮箱模块测试通过！")
    return email_info


if __name__ == "__main__":
    asyncio.run(test_email_module())
