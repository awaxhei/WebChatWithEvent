# 和猫娘青梅同居了！————基于事件驱动的AI角色扮演聊天

基于 事件驱动 的**三 AI 架构**角色扮演聊天应用。你作为"青梅竹马"（互为青梅呢），与猫娘**尤夏**进行日常对话。AI C 作为"世界引擎"驱动故事进展，追踪事件节点与时间线。

---

## 功能特性

| 功能 | 说明 |
|------|------|
| **三 AI 架构** | AI A（尤夏对话）→ AI B（环境旁白）→ AI C（世界引擎/事件管理） |
| **流式对话** | SSE 实时推送，打字机逐字效果 |
| **账号系统** | 多用户支持，数据完全隔离；SHA-256 密码哈希 + HttpOnly Cookie 登录 |
| **世界引擎** | AI C 自动追踪故事时间线，记录事件节点，生成随机事件推动情节 |
| **事件面板** | 右侧事件时间轴，展示故事时间、事件卡片（里程碑/场景/情绪/偶然） |
| **故事推进** | 推进模式按钮，一键触发世界引擎主动推动情节发展 |
| **随机事件** | 世界自发生成偶然事件（如路边出现小猫），以旁白形式插入对话流 |
| **历史记录** | 多轮对话持久化（SQLite），可切换/删除历史会话 |
| **环境叙述** | 浮动文字显示当前场景氛围与角色神态 |
| **响应式布局** | 桌面端三栏（历史+聊天+事件）、移动端滑出面板 |
| **Cookie 登录** | 勾选"记住"可维持 30 天免登录，HttpOnly 防 XSS |

---

## 项目结构

```
WebCheck_Ollama/
├── app.py                 # Flask 后端 (路由、JWT、SSE、三 AI 编排、账号系统)
├── config.py              # 配置文件 (API Key、账号、JWT 密钥)
├── config_template.py     # 配置模板
├── requirements.txt       # Python 依赖
├── yuxia_prompt.txt       # AI A 角色提示词（尤夏人设）
├── README.md
├── .gitignore
├── static/
│   ├── css/
│   │   └── style.css      # 全局样式 + 动画 + 事件面板 + 登录遮罩
│   └── js/
│       └── app.js         # 前端 (AuthManager、ChatManager、EventPanel、SSE)
└── templates/
    └── index.html         # 主页面模板
```

---

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置文件

从模板复制：

```bash
copy config_template.py config.py    # Windows
cp config_template.py config.py      # macOS/Linux
```

编辑 `config.py`：

```python
# DeepSeek API Key
DEEPSEEK_API_KEY = "sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"

# 预置账号（用户名: SHA-256(密码)）
# 生成密码哈希：python -c "import hashlib; print(hashlib.sha256('mypass'.encode()).hexdigest())"
PRESET_ACCOUNTS = {
    "admin": hashlib.sha256("admin123".encode()).hexdigest(),
}
```

### 3. 启动

```bash
python app.py
```

浏览器打开 `http://127.0.0.1:5000`，用预设账号登录。

---

## 三 AI 架构

```
用户输入 → Flask /api/chat
    │
    ├── AI A (尤夏对话) — 流式 SSE
    │   └── 角色扮演回复 + 注入事件上下文
    │
    ├── AI B (环境叙述) — 同步调用
    │   └── 环境描写 + 地点标签（浮动文字显示）
    │
    └── AI C (世界引擎) — 每 N 轮触发（或 /推进）
        ├── 追踪故事时间线
        ├── 识别并记录事件节点（里程碑/场景/情绪/时间）
        ├── 生成随机事件（概率性，有逻辑关联）
        ├── 推送到事件面板 + 世界旁白插入对话流
        └── 判断是否需要推进故事
```

---

## 账号系统

| 特性 | 说明 |
|------|------|
| 密码安全 | SHA-256 哈希存储，config.py 中直接写哈希值 |
| 登录方式 | 用户名 + 密码，HttpOnly Cookie（30 天/1 天） |
| 数据隔离 | 每个用户只能看到自己的对话历史 |
| 预置账号 | 在 `config.py` 的 `PRESET_ACCOUNTS` 中配置 |
| 旧数据迁移 | 启动时自动将旧对话归属到第一个管理员 |

**添加新用户**：在 `config.py` 增加一行并重启：

```python
PRESET_ACCOUNTS = {
    "admin": hashlib.sha256("admin123".encode()).hexdigest(),
    "alice": hashlib.sha256("alice_pass".encode()).hexdigest(),
}
```

---

## 事件面板

桌面端右侧常驻面板，展示：

- **故事时间**：当前故事内时间（如"第一天下午 15:20"）
- **事件卡片**：按时间排列的事件节点
  - 🏆 里程碑 / 🚪 场景转换 / 💭 情绪转折 / ⏰ 时间推进 / 🎲 偶然事件
  - 每张卡片显示标题、描述、状态（进行中/已完成）、时间点
- **故事摘要**：一句话概括当前进展
- **推进提示**：AI C 的建议

移动端通过点击顶部标题切换显示。面板可折叠（桌面端）或滑出（移动端）。

---

## 配置说明

| 配置项 | 说明 |
|--------|------|
| `CHARACTER_NAME` | 角色名，默认"尤夏" |
| `DEEPSEEK_API_KEY` | DeepSeek API 密钥 |
| `DEEPSEEK_MODEL` | 模型名称 |
| `PRESET_ACCOUNTS` | 预置账号字典（用户名: SHA-256 哈希） |
| `JWT_SECRET_KEY` | JWT 签名密钥 |
| `JWT_EXPIRE_DAYS` | "记住"时的 Cookie 有效期（天） |
| `EVENT_AI_INTERVAL` | AI C 触发间隔（默认每 2 轮对话） |
| `yuxia_prompt.txt` | AI A 角色人设（启动时自动加载） |

---

## 技术栈

| 层级 | 技术 |
|------|------|
| 后端 | Flask 3.x |
| AI | DeepSeek API (OpenAI 兼容) |
| 数据库 | SQLite (WAL 模式) |
| 认证 | PyJWT + HttpOnly Cookie |
| 前端 | 原生 HTML/CSS/JS，零框架依赖 |
| 通信 | Server-Sent Events (SSE) |
| 样式 | CSS 变量 + 动画关键帧 + 响应式 |