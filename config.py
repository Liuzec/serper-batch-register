import os
from dotenv import load_dotenv

load_dotenv()

# ============================================
# 邮箱模式（核心配置）
# ============================================
# "api"    — 默认，轮换多个临时邮箱 API（1secmail/tempmail.lol/mail.gw/mail.tm/guerrilla）
# "domain" — 自建域名池 + Cloudflare Worker + KV（需要自有域名）
EMAIL_MODE = os.getenv("EMAIL_MODE", "api")

# ============================================
# 必填：验证码服务
# ============================================
CAPSOLVER_API_KEY = os.getenv("CAPSOLVER_API_KEY", "")

# ============================================
# 必填：代理（二选一）
# ============================================
# 方式1: IPRoyal 旋转代理（推荐，每次注册自动换 IP）
IPROYAL_USER = os.getenv("IPROYAL_USER", "")
IPROYAL_PASS = os.getenv("IPROYAL_PASS", "")
# 方式2: 固定代理（所有请求走同一 IP，容易被封）
PROXY_URL = os.getenv("PROXY_URL", "")

# ============================================
# 自建域名池配置（仅 EMAIL_MODE=domain 时需要）
# ============================================
# 多个域名逗号分隔，如: mysite.xyz,mysite.uk,another.org
# 需要: 每个域名配置 Cloudflare Email Routing + Worker + KV
CUSTOM_EMAIL_DOMAIN = os.getenv("CUSTOM_EMAIL_DOMAIN", "")
CF_ACCOUNT_ID = os.getenv("CF_ACCOUNT_ID", "")
CF_API_TOKEN = os.getenv("CF_API_TOKEN", "")
CF_KV_NAMESPACE_ID = os.getenv("CF_KV_NAMESPACE_ID", "")

# 域名池频率控制
DOMAIN_MAX_PER_WINDOW = int(os.getenv("DOMAIN_MAX_PER_WINDOW", "3"))
DOMAIN_WINDOW_SECONDS = int(os.getenv("DOMAIN_WINDOW_SECONDS", "1800"))

# ============================================
# 通用频率控制
# ============================================
# 两次注册之间的最小间隔（秒），防止瞬间并发触发检测
REGISTER_INTERVAL = float(os.getenv("REGISTER_INTERVAL", "0"))

# ============================================
# Serper 站点信息（一般不需要改）
# ============================================
SERPER_SIGNUP_URL = "https://serper.dev/signup"
SERPER_LOGIN_URL = "https://serper.dev/login"
SERPER_DASHBOARD_URL = "https://serper.dev/dashboard"
TURNSTILE_SITEKEY = "0x4AAAAAAA_8HniKZ_83GBYh"
RECAPTCHA_SITEKEY = "6LeIQvYhAAAAAPeN8aXSjTMeCPC7qOCIEZE1_QI4"

# ============================================
# 邮箱 API 地址
# ============================================
MAILTM_API_BASE = "https://api.mail.tm"
MAILGW_API_BASE = "https://api.mail.gw"

# ============================================
# 其他
# ============================================
MAX_CONCURRENCY = int(os.getenv("MAX_CONCURRENCY", "5"))
OUTPUT_FILE = os.getenv("OUTPUT_FILE", "api_keys.json")
