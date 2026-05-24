# Serper 批量注册工具

批量自动注册 [Serper.dev](https://serper.dev) 账号并获取 API Key 的自动化工具。

纯 HTTP 实现，无需浏览器，支持多邮箱 API 轮换 + 旋转代理，开箱即用。

## 前置条件

使用本工具前，你需要注册以下两个服务：

### 1. Capsolver（必须）

用于自动解决 Serper 的 reCAPTCHA v2 和 Cloudflare Turnstile 验证码。

- 注册地址：https://capsolver.com
- 注册后充值，进入后台复制 API Key
- 费用：约 $0.005/次注册（reCAPTCHA ~$0.002 + Turnstile ~$0.003）

### 2. IPRoyal 旋转住宅代理（必须）

每次注册自动分配不同的住宅 IP，绕过 Serper 的 IP 频率限制。

- 注册地址：https://iproyal.com
- 购买 Residential Proxies，选 Pay As You Go 即可
- 进入后台获取用户名和密码
- 费用：$7/GB（每次注册约 200KB，1GB 够注册约 5000 个账号）

> **为什么代理是必须的？** Serper 对同一 IP 每小时只允许约 5 次注册。超限后返回 400 错误，且伪装成"Registration failed"而非明确的频率限制提示。使用住宅代理后，每次注册自动换 IP，不再有此限制。住宅 IP 来自真实家庭宽带，比机房 IP 更难被识别。

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置

```bash
cp .env.example .env
```

编辑 `.env`，填入你的密钥：

```ini
# 必填
CAPSOLVER_API_KEY=你的Capsolver_API_Key
IPROYAL_USER=你的IPRoyal用户名
IPROYAL_PASS=你的IPRoyal密码
```

只需要填这三项就能跑起来，其他配置都有默认值。

### 3. 运行

```bash
# 先测试各模块是否正常
python -X utf8 email_module.py     # 测试 5 个邮箱 API
python -X utf8 captcha_module.py   # 测试验证码解决

# 注册单个账号
python -X utf8 register.py

# 批量注册：10 个账号，5 路并发
python -X utf8 batch_register.py 10 5

# 限时注册：5 路并发跑 5 分钟
python -X utf8 batch_register.py 0 5 auto 300

# 限时 + 限速：5 路并发跑 5 分钟，每分钟最多 10 个
python -X utf8 batch_register.py 0 5 auto 300 10
```

> Windows 下建议加 `-X utf8` 避免 GBK 编码问题。

注册结果自动保存到 `api_keys.json`。

## 邮箱方案

本工具支持两种邮箱方案，通过 `.env` 中的 `EMAIL_MODE` 切换。

### 方案一：API 轮换模式（默认，推荐）

```ini
EMAIL_MODE=api
```

轮换使用 5 个临时邮箱 API，每次注册自动分配不同的 provider 和域名：

| Provider | 域名特点 | 认证 |
|----------|----------|------|
| temp-mail.io | 每次随机域名 | 无 |
| tempmail.lol | 动态子域名 | 无 |
| mail.gw | 多个可用域名 | Bearer Token |
| mail.tm | 多个可用域名 | Bearer Token |
| guerrilla | 25+ 个域名 | Session |

5 路并发时，每个任务自动分配不同 provider，保证每次注册的邮箱域名都不相同。如果某个 provider 临时不可用，自动 fallback 到下一个。

**无需额外配置，开箱即用。**

### 方案二：自建域名池模式

```ini
EMAIL_MODE=domain
```

适合有自己域名的用户。通过 Cloudflare Email Routing + Worker + KV 接收验证邮件。

需要额外配置：

```ini
CUSTOM_EMAIL_DOMAIN=mysite.xyz,mysite.uk    # 多个域名逗号分隔
CF_ACCOUNT_ID=你的Cloudflare账号ID
CF_API_TOKEN=你的Cloudflare_API_Token
CF_KV_NAMESPACE_ID=你的KV命名空间ID
```

域名池会自动管理每个域名的使用频率（默认每个域名每 30 分钟最多 3 个），超限后自动 fallback 到 API 轮换。

> **注意**：实测 Serper 会检测邮箱域名，同一域名短时间内注册 4-5 个账号后会被封禁，恢复时间超过数小时。因此域名越多越好，建议 5 个以上。

Cloudflare Worker 配置方法见 `cloudflare_worker.js` 和 `技术思路说明.md`。

## 项目结构

```
├── config.py              配置中心（EMAIL_MODE、代理、验证码等）
├── email_module.py        邮箱模块（5 个 API 轮换 + 自建域名池）
├── captcha_module.py      验证码模块（Capsolver: reCAPTCHA + Turnstile）
├── proxy_module.py        代理模块（IPRoyal 旋转住宅代理 + 粘性会话）
├── register.py            单次注册完整流程（6 步）
├── batch_register.py      批量并发注册（按数量/按时间/限速）
├── domain_pool.py         域名池管理器（滑动窗口频率控制）
├── stress_test.py         压力测试框架
├── cloudflare_worker.js   Cloudflare Worker（自建域名方案用）
├── .env.example           配置模板
├── .gitignore             排除敏感文件
└── api_keys.json          输出：注册成功的账号和 API Key（自动生成）
```

## 注册流程

每个账号的注册分 6 步，单次约 30-60 秒：

```
步骤 1（并发执行，节省时间）:
  ├── 创建临时邮箱        → 从 5 个 API 中轮换选择
  ├── 解 reCAPTCHA v2     → Capsolver
  └── 解 Turnstile        → Capsolver

步骤 2: POST /auth/register
  → 通过 IPRoyal 代理发送，被限速则自动换 IP 重试（最多 5 次）

步骤 3: POST /auth/login
  → 解新的 Turnstile → 登录获取 session

步骤 4: 等待验证邮件
  → 轮询邮箱 API 等待 Serper 的验证邮件

步骤 5: 验证账号
  → 提取验证链接中的 token → POST /users/verify-email

步骤 6: 获取 API Key
  → 重新登录 → GET /users/api-keys
  → 保存到 api_keys.json
```

## 代理机制详解

### 旋转 IP

每次注册自动分配一个新的住宅 IP：

```
账号 A → IP-1（美国家庭宽带）→ 注册成功
账号 B → IP-2（德国家庭宽带）→ 注册成功
账号 C → IP-3（日本家庭宽带）→ 注册成功
```

### 粘性会话

同一个账号的注册流程（注册→登录→验证→取 Key）全程使用同一个 IP，避免中途换 IP 导致 session 失效：

```
账号 A：会话 ID = "abc123" → 全程走 IP-1（5 分钟内不变）
账号 B：会话 ID = "xyz789" → 全程走 IP-2（5 分钟内不变）
```

### 选择性代理

只有 Serper 的请求走代理，Capsolver 和邮箱 API 直连，节省代理流量：

| 服务 | 走代理 | 原因 |
|------|--------|------|
| Serper.dev | 是 | 有 IP 频率限制 |
| Capsolver | 否 | 无限制 |
| 临时邮箱 API | 否 | 无限制（tempmail.lol 除外，会用代理绕过 429） |

### 代理预检

注册前自动检测代理 IP 是否可用，不可用则自动换 IP，最多尝试 5 次：

```
拿到代理 IP → 访问 Serper API → 能连通？
  能 → 用这个 IP 注册
  不能 → 换一个 IP → 再试
```

## 成本估算

| 项目 | 单价 | 每次注册 |
|------|------|----------|
| Capsolver reCAPTCHA v2 | ~$2/千次 | ~$0.002 |
| Capsolver Turnstile x3 | ~$1/千次 | ~$0.003 |
| IPRoyal 代理流量 | $7/GB | ~$0.0014 |
| 临时邮箱 | 免费 | $0 |
| **合计** | | **~$0.007/个** |

约 5 分钱人民币一个账号。$7 的代理流量可以注册约 5000 个账号。

## 高级用法

### 压力测试

```bash
# 查看当前配置的推荐参数
python -X utf8 stress_test.py recommend

# 快速测试（每组 2 分钟）
python -X utf8 stress_test.py quick

# 单组测试：5 并发，不限速
python -X utf8 stress_test.py single 5 0 0 300
```

### 指定邮箱 provider

```bash
# 只用 temp-mail.io
python -X utf8 batch_register.py 5 3 tempmailio

# 只用自建域名
python -X utf8 batch_register.py 5 3 domain
```

### 频率控制参数

在 `.env` 中可调：

```ini
# 两次注册之间的最小间隔（秒），降低被检测风险
REGISTER_INTERVAL=5

# 最大并发数
MAX_CONCURRENCY=5

# 域名池参数（仅 EMAIL_MODE=domain）
DOMAIN_MAX_PER_WINDOW=3      # 每个域名每窗口最多 3 个
DOMAIN_WINDOW_SECONDS=1800   # 窗口 30 分钟
```

## 注意事项

- **先跑通单次注册** (`python -X utf8 register.py`) 再跑批量
- 并发数建议 3-5，过高会触发 Serper 的全局检测
- 单次批量注册建议不超过 5 分钟，间隔 30 分钟以上
- 每天建议不超过 100 个账号
- **不要把 `.env` 提交到 GitHub**（已在 `.gitignore` 中排除）
