import os
from dotenv import load_dotenv

load_dotenv()

# Capsolver API Key（优先从 .env 读取，其次从环境变量读取）
CAPSOLVER_API_KEY = os.getenv("CAPSOLVER_API_KEY", "")

# Serper 注册页面信息
SERPER_SIGNUP_URL = "https://serper.dev/signup"
SERPER_LOGIN_URL = "https://serper.dev/login"
SERPER_DASHBOARD_URL = "https://serper.dev/dashboard"
TURNSTILE_SITEKEY = "0x4AAAAAAA_8HniKZ_83GBYh"
RECAPTCHA_SITEKEY = "6LeIQvYhAAAAAPeN8aXSjTMeCPC7qOCIEZE1_QI4"

# Mail.tm API
MAILTM_API_BASE = "https://api.mail.tm"

# 并发配置
MAX_CONCURRENCY = 5

# 代理配置（后续使用）
PROXY_LIST = [
    # "http://user:pass@ip:port",
]

# API Key 保存路径
OUTPUT_FILE = "api_keys.json"

# 浏览器配置
BROWSER_CHANNEL = "chrome"
HEADLESS = True
