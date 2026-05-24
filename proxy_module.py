"""
模块5：旋转代理（IPRoyal Residential）

每次调用 get_proxy_url() 生成一个带 sticky session 的代理 URL：
- 同一个 session ID → 同一个出口 IP（在 lifetime 内）
- 不同 session ID → 不同出口 IP

IPRoyal 代理格式:
  http://用户名:密码_session-XXXXXXXX_lifetime-5m@geo.iproyal.com:12321

session ID: 8 位随机字母数字
lifetime: 单次注册持续时间，建议 5m（5分钟）
"""

import random
import string
from config import IPROYAL_USER, IPROYAL_PASS


IPROYAL_HOST = "geo.iproyal.com"
IPROYAL_HTTP_PORT = 12321
IPROYAL_SOCKS5_PORT = 32325

DEFAULT_LIFETIME = "5m"


def _random_session_id() -> str:
    """生成 8 位随机字母数字 session ID"""
    chars = string.ascii_lowercase + string.digits
    return ''.join(random.choices(chars, k=8))


def get_proxy_url(
    lifetime: str = DEFAULT_LIFETIME,
    session_id: str | None = None,
    country: str | None = None,
    protocol: str = "http",
) -> str:
    """
    生成一个 sticky session 代理 URL。

    参数:
        lifetime: 会话保持时间（如 "5m", "10m", "1h"）
        session_id: 指定 session ID（默认随机生成）
        country: 指定国家代码（如 "us", "de"），默认不限
        protocol: "http" 或 "socks5"

    返回: 代理 URL 字符串
    """
    if not IPROYAL_USER or not IPROYAL_PASS:
        raise ValueError("请在 .env 中配置 IPROYAL_USER 和 IPROYAL_PASS")

    sid = session_id or _random_session_id()

    # 参数拼在密码后面
    password_parts = [IPROYAL_PASS]
    if country:
        password_parts.append(f"country-{country}")
    password_parts.append(f"session-{sid}")
    password_parts.append(f"lifetime-{lifetime}")
    password_str = "_".join(password_parts)

    port = IPROYAL_SOCKS5_PORT if protocol == "socks5" else IPROYAL_HTTP_PORT
    scheme = "socks5" if protocol == "socks5" else "http"

    return f"{scheme}://{IPROYAL_USER}:{password_str}@{IPROYAL_HOST}:{port}"


# ============================================
# 测试入口
# ============================================
if __name__ == "__main__":
    import asyncio
    import httpx

    async def test_proxy():
        print("=== 测试 IPRoyal 代理 ===\n")

        if not IPROYAL_USER:
            print("[WARN] 请先在 .env 中配置 IPROYAL_USER 和 IPROYAL_PASS")
            return

        # 生成两个不同 session 的代理
        proxy1 = get_proxy_url()
        proxy2 = get_proxy_url()
        print(f"代理 1: {proxy1[:40]}...")
        print(f"代理 2: {proxy2[:40]}...")

        # 验证两个 session 拿到不同 IP
        async with httpx.AsyncClient(proxy=proxy1, timeout=15) as c1, \
                   httpx.AsyncClient(proxy=proxy2, timeout=15) as c2:
            r1 = await c1.get("https://api.ipify.org?format=json")
            r2 = await c2.get("https://api.ipify.org?format=json")
            ip1 = r1.json()["ip"]
            ip2 = r2.json()["ip"]
            print(f"\nSession 1 IP: {ip1}")
            print(f"Session 2 IP: {ip2}")

            if ip1 != ip2:
                print("[OK] 不同 session → 不同 IP")
            else:
                print("[WARN] IP 相同（可能是巧合，再试一次）")

            # 验证同一 session 保持同一 IP
            r1b = await c1.get("https://api.ipify.org?format=json")
            ip1b = r1b.json()["ip"]
            print(f"\nSession 1 再次请求: {ip1b}")
            if ip1 == ip1b:
                print("[OK] 同一 session → IP 不变")
            else:
                print("[WARN] 同一 session IP 变了")

    asyncio.run(test_proxy())
