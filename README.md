# 尤夏 — 和三无傲娇猫娘青梅同居了！

基于 **事件驱动 + AI 小镇式记忆 + 确定性故事时钟 + 场景化环境** 的三 AI 角色扮演聊天应用。你作为"青梅竹马"与猫娘**尤夏**同居；世界有自己的时间、地点、环境与偶发事件，尤夏也会自己行动。

> **v4** 重构：尤夏只负责说话（对白/消息/表情包）、手机独立通道、环境描写绑定场景、随机事件分级抽取、尤夏自主行动

---

## 功能特性

| 功能 | 说明 |
|------|------|
| **三 AI 架构** | AI A（尤夏对话）→ AI C（世界引擎）；环境改由场景系统按需生成 |
| **自然输出** | 尤夏只输出「对白 / 消息 / 表情包」，短句为主，不写动作和环境 |
| **手机通道** | 手机消息不进主聊天页；左下角手机图标 + 红点，点开才看 |
| **场景化环境** | 环境描写绑定「地点 + 时段」，首次进入生成并缓存，之后零成本复用 |
| **真实地点** | 地点是数据库实体；玩家点地点名即可移动，尤夏也会自己走开 |
| **异地只能手机** | 两人不在同一地点时，交流自动切到手机通道 |
| **随机事件分级** | 程序按权重抽取强度（60/25/12/3），影响越深越稀有，再由 LLM 具体化 |
| **尤夏自主行动** | 不等输入也会主动说话、发消息、换场景；闲置一段时间会主动找你 |
| **AI 小镇式记忆** | 观察/反思两层，检索得分 = 时效×0.5 + 重要度×0.3 + 相关性×0.2 |
| **确定性故事时钟** | 服务器持有唯一时间真值，每轮 +3 分钟，LLM 只能申请"跳跃类型" |
| **自然上下文** | 上层 LLM 把结构化状态写成一段叙述再喂给尤夏，不用方括号标签 |
| **账号系统** | SHA-256 + HttpOnly Cookie，多用户隔离 + 会话归属校验 |
| **自助 API Key** | 页面右上角齿轮可填自己的 Key，只存本机 localStorage |
| **PWA 支持** | manifest + Service Worker + 自动生成图标 |

---

## 项目结构

```
WebChatWithEvent/
├── app.py                    # Flask 入口（蓝图注册、静态资源、启动）
├── config.py / config_template.py
├── yuxia_prompt.txt          # 尤夏人设（只包含对白/消息/表情包的输出规则）
│
├── core/
│   ├── auth.py               # JWT / 会话归属校验 / 用户 Key
│   ├── database.py           # SQLite（消息通道、地点、场景缓存、记忆、表情包索引）
│   ├── deepseek_client.py    # DeepSeek 调用（思考模式开关、流式/同步、key 覆盖）
│   ├── embedding_client.py   # 可选 embedding（Ollama / sentence-transformers）
│   ├── textvec.py            # 向量与相似度（中文词面覆盖率降级）
│   ├── clock.py              # 确定性故事时钟
│   ├── locations.py          # 地点实体（归一化 / 新建 / 切换 / 双方位置）
│   ├── scenes.py             # 场景描写（地点 × 时段 → 缓存描写）
│   ├── memory.py             # AI 小镇记忆流（打分检索 / 反思阈值）
│   └── stickers.py           # 表情包（导入即用 / 视觉打标 / 语义匹配）
│
├── routes/
│   ├── auth_routes.py        # 登录 / 验证 / 登出
│   ├── chat_routes.py        # 聊天 SSE、场景、移动、手机、闲置、世界引擎
│   ├── event_routes.py       # 事件初始化 / 查询
│   └── history_routes.py     # 会话 CRUD
│
├── services/
│   ├── event_engine.py       # AI C：分级随机事件 + 事件总结
│   ├── memory_engine.py      # 记忆编排（写入 / 事件转记忆 / 反思）
│   ├── time_keeper.py        # 时钟编排（轮次 / 关键词 / 时间观察者）
│   ├── context_writer.py     # 自然情境撰写（带缓存）
│   ├── autonomy.py           # 尤夏自主行动（说话 / 发消息 / 换场景）
│   └── atmosphere.py         # 文本解析辅助
│
├── scripts/                  # 检测、测试、图标、隔离实例
├── static/                   # css / js / stickers / image
└── templates/index.html
```

---

## 快速开始

```bash
pip install -r requirements.txt
copy config_template.py config.py      # Windows
python app.py                          # http://127.0.0.1:5000
```

检查 Key：

```bash
python scripts/check_api_key.py
```

两种填 Key 的方式：`config.py` 里写死，或页面右上角齿轮填自己的（只存本机，通过 `X-DeepSeek-API-Key` 头透传，不落库）。

---

## 交互模型

### 输出格式

尤夏每次回复是一个或多个 JSON 对象，字段只有三个：

| 字段 | 去处 |
|------|------|
| `对白` | 主聊天页的对话气泡（仅当两人同处一地） |
| `消息` | 手机面板（不进主页，亮红点提醒） |
| `表情包` | 主聊天页的表情图片 |

动作、神态、环境一律不由尤夏输出——环境交给场景系统，动作由玩家自行想象。

### 场景与环境

- 环境描写绑定「地点 + 时段（清晨/白天/傍晚/夜晚）」，首次进入时生成一次并写入 `locations.variants`
- 主界面顶部显示当前地点，下面一行是该场景的环境描写，随进入的场景和时段切换
- 点击地点名可以打开地点列表，选择后玩家移动过去
- 两人不在同一地点时：顶部显示"尤夏在 X（你在 Y）"，输入框变成发手机消息

**场景可以新建，不是固定清单**：地点表初始 11 个，之后有三条创建路径——

| 谁 | 方式 |
|---|---|
| 玩家 | 地点下拉底部的「去没去过的地方…」输入框，回车即创建并前往 |
| 玩家 | 聊天框输入 `/去 某个新地名` |
| 尤夏 | 自主行动判定 `move` 时，若故事需要可以走到清单外的新地点 |

新场景首次进入会自动按当前时段生成环境描写并缓存；创建受 `LOCATION_CAP`（默认 40）上限约束，
尤夏自主新建还受开关 `AUTONOMY_ALLOW_NEW_LOCATION` 控制。

### 自主行动

尤夏会在两种时机自己做决定（说话 / 发手机消息 / 走去别的地点 / 什么都不做）：

| 触发 | 门槛 | 默认值 |
|------|------|--------|
| 每轮对话后 | 用户发言轮数 % `AUTONOMY_COOLDOWN_TURNS` == 0，再按概率 | 2 轮 / 35% |
| 闲置轮询 | 前端每 60 秒请求 `/api/idle`；距上次发言 ≥ `IDLE_THRESHOLD_SECONDS`，再按概率 | 120 秒 / 25% |

行动类型由 LLM 判断（`none/speak/phone/move`），服务端负责校验：
同处一地才能 `speak`（异地强制改成 `phone`），`move` 会登记地点并记一条 `scene_change` 事件；
走到清单外的新地点时事件标题会标注「（新场景）」。

想让她更主动，调大 `AUTONOMY_CHANCE` / `IDLE_CHANCE`、调小 `AUTONOMY_COOLDOWN_TURNS` / `IDLE_THRESHOLD_SECONDS` 即可。

> 想直观验证这条链路，可以跑 `python scripts/auto_test_server.py`：
> 它用临时库起一个端口 5051 的实例，并把概率门槛全部调到 100%。

---

## API 一览

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/chat` | 主聊天（SSE），自动按同处/异地选通道 |
| GET | `/api/scene/<conv>` | 场景视图（地点、时段、双方描写、可选地点） |
| POST | `/api/move/<conv>` | 移动（`who=player|yuxia`） |
| GET | `/api/phone/<conv>` | 手机消息 + 未读数 |
| POST | `/api/phone/read/<conv>` | 标记已读 |
| POST | `/api/idle/<conv>` | 闲置时让尤夏自主行动 |
| POST | `/api/atmosphere/<conv>` | 时间观察者 / 随机事件 / 事件总结 / 反思 / 自主行动（SSE） |
| GET/POST | `/api/history*`、`/api/events*` | 会话与事件 |
| GET | `/api/stickers/match`、`/api/stickers/stats` | 表情包 |

---

## 时间系统

时间真值是 `conversations.story_minutes`（显示为 `第一天 07:03`）。

| 层级 | 规则 | 是否用 LLM |
|------|------|-----------|
| 基础推进 | 每条用户消息 +3 分钟（`CLOCK_MINUTES_PER_TURN`） | 否 |
| 关键词跳跃 | 晚安→次日 07:00、午饭→12:00、放学→16:30、晚饭→18:00、深夜→22:30、明天→次日 08:00 | 否 |
| 时间观察者 | 隔 2 轮询问一次，只返回枚举（NONE/NEXT_MORNING/NOON/...） | 是（只给枚举） |

LLM 永远不输出时间字符串；服务端负责换算、禁止倒退、单次跳跃上限 3 天。

---

## 随机事件分级

程序按权重抽取强度，再交给 LLM 具体化，强度写进 `events.tier`：

| 等级 | 权重 | 影响 |
|------|------|------|
| `low` | 60% | 环境层面小变化，可以完全不理 |
| `medium` | 25% | 小意外，话题偏转 |
| `high` | 12% | 打断当前场景，必须反应 |
| `major` | 3% | 剧情级转折 |

---

## 记忆系统

```
score = 0.5 × 0.995^经过小时 + 0.3 × 重要度/10 + 0.2 × 相关性
```

- 每轮对话写入观察；里程碑/情绪转折等事件写成高重要度记忆
- 累计重要度 ≥ 80 触发"反思"，把零散观察归纳成高层认知
- 相关性优先用向量余弦；没有 embedding 时降级为中文二元组覆盖率 + Jaccard
- 老会话首次进入 v4 时自动回填最近 60 条消息

---

## 表情包（等你导入）

把图片丢进 `static/stickers/` 任意子目录、任意文件名，重启或调 `POST /api/stickers/refresh` 即可：

1. 手动旁注（同名 `.txt` 第一行）优先
2. 视觉模型自动打标（`deepseek-flash` 多模态，结果缓存，同图只花一次 token）
3. 文件名/目录名兜底

匹配顺序：精确情绪词 → 标签命中 → 语义/词面相似 → 随机兜底。

---

## 脚本

| 脚本 | 用途 |
|------|------|
| `python scripts/check_api_key.py` | 验证 Key、列模型、跑一次最小对话 |
| `python scripts/make_icons.py` | 重新生成 PWA 图标 |
| `python scripts/smoke_test.py` | 接口冒烟测试（隔离库 + 测试桩，零额度） |
| `python scripts/test_v3.py` | 时钟/地点/场景/手机/记忆/表情包/迁移 离线单元测试 |
| `python scripts/e2e_chat.py` | 真实对话全链路验证（消耗额度，跑在隔离临时库） |
| `python scripts/e2e_server.py` | 开发用隔离实例：临时库 + 端口 5050 + 账号 `e2e/e2e123` |
| `python scripts/auto_test_server.py` | 自主行动演示实例：概率门槛 100% + 端口 5051，用来观察尤夏自己行动 |
| `python scripts/test_scene_creation.py` | 场景创建探针：玩家/尤夏新建场景、自动描写、上限与开关 |
| `python scripts/timing_probe.py` | 首轮延迟分段测量（打印各阶段耗时，用 `YUXIA_TIMING=1` 打开计时日志） |

---

## 思考模式

`deepseek-flash` 默认开启思考模式，本项目跟随默认值（`DEEPSEEK_THINKING = True`，`DEEPSEEK_REASONING_EFFORT = "high"`）。

- 代价：单轮约 7 秒、视觉打标约 3 秒；思考 token 计费，并会占用 `max_tokens`（视觉打标已放大到 2048）
- 思考模式下 `temperature`/`top_p` 被忽略，本项目开启思考时不再发送这两个参数
- 关闭：把 `DEEPSEEK_THINKING` 设为 `False`

---

## 延迟与性能

首条消息最容易"卡"，因为除了主对话，还有两个附带任务。现在两者都不在玩家等待路径上：

| 任务 | 处理方式 | 阻塞首字？ |
|------|----------|-----------|
| 自然情境撰写（额外 LLM 调用） | 首轮/过期时先用兜底叙述，**后台线程**撰写，下一轮生效（`CONTEXT_WRITER_BACKGROUND`） | 否 |
| 场景环境描写（每地点每时段一次） | `/api/scene` 立即返回 `pending: true`，**后台线程**生成，前端 1.8 秒后重取 | 否 |
| embedding 探测 | `EMBEDDING_ENABLED=False` 时完全跳过（之前每次首轮会花 4 秒连 Ollama） | 否 |

实测（`deepseek-flash` + 思考模式）：

```
首轮：服务端准备 190 ms + 模型首 token → 用户看到回复约 1.2–2.0 s（优化前 8.4 s）
后续轮：约 1.1–1.9 s
/api/scene 冷启动：0.06 s（pending），后台约 3–5 s 后可取到描写
```

调优入口：

```python
EMBEDDING_ENABLED = False        # 装好 Ollama 模型或 sentence-transformers 后再开
CONTEXT_WRITER_BACKGROUND = True # 关掉会用同步撰写（首轮 +3 秒）
DEEPSEEK_REASONING_EFFORT = "high"  # 改 low 可再快一些
```

排查用：`python scripts/timing_probe.py`（打印请求内各阶段耗时），或用 `YUXIA_TIMING=1 python app.py` 在服务端日志里看分段。

---

## 安全

- 所有按 `conversation_id` 的接口都做归属校验（用户 2 读不到用户 1 的场景/手机/事件）
- `config.py` 在 `.gitignore` 中，不会误提交
- 用户 Key 只存浏览器 localStorage，仅请求头透传
- `/api/config` 只暴露"服务端是否配置了 Key"的布尔值

---

## 已知限制

- 官方 API 没有 embeddings 接口，语义检索依赖本地 Ollama / sentence-transformers，都没有时用中文词面相似度
- 时间观察者会依据语义跳跃（例如提到吃饭可能直接跳到饭点），觉得太激进可以调大 `TIME_OBSERVER_INTERVAL`
- 思考模式偶尔会让模型漏掉严格 JSON 格式，服务端会把纯文本按当前通道兜底存成一条消息
- 老库里的 `conversation_summaries` / `prompt_config` 是历史遗留表（数据保留，代码不再写入）

---

## 技术栈

| 层级 | 技术 |
|------|------|
| 后端 | Flask 3.x（蓝图 + 模块级依赖注入） |
| LLM | DeepSeek `deepseek-flash`（多模态，思考模式可配） |
| 存储 | SQLite（WAL）：消息、事件、地点、场景缓存、记忆、表情包索引 |
| 检索 | 向量余弦 / 中文二元组覆盖率（自动降级） |
| 认证 | PyJWT + HttpOnly Cookie |
| 前端 | 原生 HTML/CSS/JS，SSE 流式，手机面板 + 场景卡 |
| 离线 | PWA（manifest + Service Worker + 自动生成图标） |
