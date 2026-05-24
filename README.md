# Serper 注册机

批量注册 Serper.dev 账号并获取 API Key 的自动化工具。

## 项目结构

```
serper注册机/
├── config.py           # 配置文件（URL、sitekey、并发数、代理等）
├── .env                # 敏感配置（Capsolver API Key、代理，不入库）
├── .env.example        # .env 模板
├── email_module.py     # 模块1：临时邮箱（Mail.tm）
├── captcha_module.py   # 模块2：验证码解决（Capsolver: reCAPTCHA v2 + Turnstile）
├── register.py         # 模块3：单次注册流程（纯 httpx，无浏览器）
├── batch_register.py   # 模块4：并发批量注册
├── requirements.txt    # Python 依赖
└── api_keys.json       # 输出：注册成功的 API Key（自动生成）
```

## 技术架构

### 纯 HTTP 方案（无浏览器依赖）

所有 API 调用通过 httpx 直接发送，无需 Playwright/Selenium，启动快、资源消耗低。

### API 端点 (baseURL: `https://api.serper.dev`)

| 端点 | 方法 | 用途 | 验证码 |
|------|------|------|--------|
| `/auth/register` | POST | 注册账号 | reCAPTCHA v2 invisible + Turnstile |
| `/auth/login` | POST | 登录 | Turnstile |
| `/users/api-keys` | GET | 获取 API Key 列表 | Session Cookie |

### 验证码

- **reCAPTCHA v2 Invisible**: sitekey `6LeIQvYhAAAAAPeN8aXSjTMeCPC7qOCIEZE1_QI4`
- **Cloudflare Turnstile**: sitekey `0x4AAAAAAA_8HniKZ_83GBYh`
- 通过 Capsolver API 解决，无需浏览器内交互

### 注册流程 (6 步)

1. **并发启动**: 创建临时邮箱 + 解 reCAPTCHA v2 + 解 Turnstile（同时进行）
2. `POST /auth/register`（带验证码 token）
3. 解新的 Turnstile → `POST /auth/login`
4. 轮询 Mail.tm 等待验证邮件
5. GET 验证链接激活账号
6. 重新登录 → `GET /users/api-keys` 获取 API Key

> 相比旧版 8 步流程（含浏览器启动），新版并发第1步 + 减少 Turnstile 次数，单次注册约 15-20 秒。

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置

复制 `.env.example` 为 `.env`，填入你的 Capsolver API Key：

```bash
cp .env.example .env
```

编辑 `.env`：

```
CAPSOLVER_API_KEY=你的key
PROXY_URL=http://127.0.0.1:7897  # 可选，本地代理
```

其他配置（并发数等）在 `config.py` 中修改。

### 3. 运行

```bash
# 测试邮箱模块
python email_module.py

# 测试验证码模块（需要先填 Capsolver Key）
python captcha_module.py

# 运行单次注册（推荐 Windows 加 -X utf8 避免编码问题）
python -X utf8 register.py

# 批量注册（5个账号，3个并发）
python -X utf8 batch_register.py 5 3
```

## 注意事项

### 频率限制

Serper.dev 有注册频率限制：
- 每 IP 每小时约 5 次注册尝试
- 首次超限返回 400 "Registration failed"（伪装的频率限制）
- 继续尝试返回 429 "Too Many Requests"
- 建议每次注册间隔 15+ 分钟，或使用代理轮换 IP

### 其他

- 先跑通单次注册 (`python -X utf8 register.py`) 再跑批量
- 并发数不要设太高，建议 1-3，配合代理使用
- 如需代理，在 `.env` 中配置 `PROXY_URL`
- Windows 下运行建议加 `-X utf8` 避免 GBK 编码错误
- **不要把 `.env` 提交到版本控制**（已在 `.gitignore` 中排除）
