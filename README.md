# AI 情感助手 (WebCheck_Ollama)

基于 Flask + DeepSeek API 的**双 AI 架构**聊天应用，支持流式实时对话、场景环境叙述、JWT 登录认证。

---

## 功能特性

| 功能 | 说明 |
|------|------|
| 双 AI 架构 | AI A 负责角色对话（尤夏），AI B 负责环境旁白叙述 + 地点追踪 |
| 流式输出 | SSE (Server-Sent Events) 实时推送，打字机逐字效果 |
| 登录认证 | JWT 令牌 + SHA-256 密码哈希，支持「记住密码」(30 天) |
| 历史记录 | 多轮对话持久化存储（SQLite），可切换/删除历史会话 |
| IPv6 支持 | 自动检测 Windows 永久公网 IPv6 地址（排除临时地址） |
| 响应式布局 | 桌面端侧边栏 + 移动端抽屉面板适配 |
| 现代 UI | 毛玻璃遮罩、消息气泡动画、环境叙述横向滚动时间轴 |

---

## 项目结构

```
WebCheck_Ollama/
├── app.py                 # Flask 后端主入口（路由、JWT、SSE 流式）
├── config_template.py     # 配置文件模板（复制为 config.py 使用）
├── config.py              # 实际配置（含 API Key、密码，已 gitignore）
├── requirements.txt       # Python 依赖
├── yuxia_prompt.txt       # AI A 角色提示词（尤夏人设）
├── .gitignore
├── static/
│   ├── css/
│   │   └── style.css      # 全局样式 + 动画 + 登录遮罩
│   └── js/
│       └── app.js         # 前端主逻辑（AuthManager、ChatManager、SSE 流式消费）
└── templates/
    └── index.html         # 主页面模板
```

---

## 快速开始

### 1. 克隆项目 & 安装依赖

```bash
git clone <your-repo-url>
cd WebCheck_Ollama
pip install -r requirements.txt
```

### 2. 创建配置文件

```bash
# Windows
copy config_template.py config.py

# macOS / Linux
cp config_template.py config.py
```

### 3. 编辑 `config.py`

打开 `config.py`，填写以下**必填项**：

```python
# DeepSeek API Key（必填）
DEEPSEEK_API_KEY = "sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"

# DeepSeek 模型名称
DEEPSEEK_MODEL = "deepseek-v4-pro"

# 登录密码（必填，修改为你自己的密码）
LOGIN_PASSWORD = "your_password_here"

# JWT 签名密钥（建议修改为随机字符串）
JWT_SECRET_KEY = "your-random-secret-key"
```

### 4. 启动服务

```bash
python app.py
```

启动后会打印访问地址：

```
==================================================
  AI 情感助手 (双AI) 服务启动中...
  模型: deepseek-v4-pro
  API: DeepSeek
  本地访问: http://127.0.0.1:5000
  IPv4 访问: http://192.168.x.x:5000
  IPv6 访问: http://[2408:xxxx:...]:5000
==================================================
```

### 5. 访问

浏览器打开 `http://127.0.0.1:5000`，输入你设置的密码登录即可开始对话。

---

## 配置说明 (`config.py`)

| 配置项 | 类型 | 说明 |
|--------|------|------|
| `CHARACTER_NAME` | `str` | 角色名称，显示在网页顶部 |
| `DEEPSEEK_API_KEY` | `str` | DeepSeek API 密钥，[在此获取](https://platform.deepseek.com/) |
| `DEEPSEEK_MODEL` | `str` | 使用的模型，如 `deepseek-v4-pro` / `deepseek-chat` |
| `LOGIN_PASSWORD` | `str` | 登录密码（明文配置，启动时自动 SHA-256 哈希） |
| `JWT_SECRET_KEY` | `str` | JWT 签名密钥，修改可强制所有已登录用户重新登录 |
| `JWT_EXPIRE_DAYS` | `int` | 「记住密码」的 JWT 有效期（天），不勾选时默认 24 小时 |
| `CHAT_PROMPT` | `str` | 从 `yuxia_prompt.txt` 自动加载，可编辑该文件修改角色人设 |
| `ATMOSPHERE_PROMPT` | `str` | AI B 环境叙述者的 system prompt |

---

## 架构说明

```
用户输入 "你好"
    |
    v
+---------------------------------------------------+
|                  Flask /api/chat                    |
|                                                     |
|  1. 提取当前地点（从最近环境叙述中解析【地点】）       |
|  2. 拼接动态 system prompt（地点 + 角色人设）         |
|                                                     |
|  +-- AI A: 对话助手 (chat_stream) ------------------+|
|  |  - 调用 DeepSeek API (stream=True)              ||
|  |  - 即时 SSE 流式推送给前端（打字机效果）           ||
|  |  - 自动剥离角色名前缀（尤夏：）                    ||
|  |  - 完成后保存到 SQLite                           ||
|  +--------------------------------------------------+|
|                                                     |
|  +-- AI B: 环境叙述者 (chat_sync) ------------------+|
|  |  - 基于最新对话生成环境描写 + 地点                 ||
|  |  - 输出格式：【地点】场景名\n环境描写              ||
|  |  - 前端在地点标签和时间轴中展示                    ||
|  +--------------------------------------------------+|
+---------------------------------------------------+
    |
    v
前端 SSE 消费 (app.js)
  - type: "conv_id"   -> 绑定会话 ID
  - type: "chunk"     -> 逐块追加 AI 回复文本
  - type: "location"  -> 更新地点标签
  - type: "atmosphere" -> 追加环境叙述卡片
```

### 安全性设计

| 项目 | 方案 |
|------|------|
| 密码存储 | 启动时 SHA-256 哈希，仅内存中保存哈希值 |
| API 鉴权 | JWT (HS256)，`Authorization: Bearer <token>` |
| Token 生命周期 | 不勾选「记住」-> 24h；勾选 -> `JWT_EXPIRE_DAYS` 天 |
| 防暴力破解 | 当前未实现（内网使用场景，可按需添加 fail2ban） |

---

## 技术栈

| 层级 | 技术 |
|------|------|
| 后端框架 | Flask 3.0 |
| AI API | DeepSeek (OpenAI 兼容协议) |
| 数据库 | SQLite (WAL 模式) |
| 认证 | PyJWT 2.8 (HS256) |
| 前端 | 原生 HTML/CSS/JS（零依赖） |
| 流式通信 | Server-Sent Events (SSE) |
| 样式 | CSS 变量 + 动画关键帧 + 响应式媒体查询 |

---

## 自定义角色人设

编辑 `yuxia_prompt.txt` 文件即可修改 AI 角色的性格、背景故事、说话风格。修改后重启服务生效。

该文件内容会在启动时自动加载为 `CHAT_PROMPT`，并被注入到每次对话的 system prompt 中。