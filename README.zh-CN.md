# gtm-engine (开源版)

<p align="center">
  <strong>语言 / Language:</strong>
  <a href="README.md">English</a> |
  <strong>简体中文</strong> |
  <a href="README.ja.md">日本語</a> |
  <a href="README.es.md">Español</a> |
  <a href="README.de.md">Deutsch</a> |
  <a href="README.ko.md">한국어</a>
</p>

<p align="center">
  <a href="https://github.com/henryroxstar/gtm-engine/stargazers"><img src="https://img.shields.io/github/stars/henryroxstar/gtm-engine?style=flat&label=Stars" alt="Stars" /></a>
  <a href="https://twitter.com/intent/tweet?text=The%20open-source%20GTM%20agent%20harness%20for%20startups%3A%2063%20skills%2C%20zero%20auto-spam%2C%20runs%20locally%20in%20Claude%20Code%20or%20Antigravity.&url=https%3A%2F%2Fgithub.com%2Fhenryroxstar%2Fgtm-engine"><img src="https://img.shields.io/badge/Share%20on-X-black?style=flat&logo=x" alt="Share on X" /></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-Apache_2.0-blue.svg" alt="License: Apache 2.0" /></a>
  <a href="https://www.python.org/"><img src="https://img.shields.io/badge/python-3.11+-3776AB.svg" alt="Python 3.11+" /></a>
  <a href="https://docs.anthropic.com/en/api/agent-sdk/overview"><img src="https://img.shields.io/badge/built%20with-Claude%20Agent%20SDK-d97757.svg" alt="Built with Claude Agent SDK" /></a>
  <a href="https://modelcontextprotocol.io/"><img src="https://img.shields.io/badge/connectivity-MCP--first-6E56CF.svg" alt="MCP-first" /></a>
  <a href="#为什么采用这种架构"><img src="https://img.shields.io/badge/human%20gates-%E4%B8%8D%E5%8F%AF%E7%BB%95%E8%BF%87%E7%9A%84%E5%AE%A1%E6%89%B9%E9%97%A8%E7%A6%81-2ea44f.svg" alt="Human Gates" /></a>
  <a href="#为什么采用这种架构"><img src="https://img.shields.io/badge/tenants-%E7%A7%9F%E6%88%B7%E9%9A%94%E7%A6%BB-blueviolet.svg" alt="Tenant Isolation" /></a>
  <a href="#支持的-ai-工作区与环境"><img src="https://img.shields.io/badge/workspaces-Claude%20%7C%20Antigravity%20%7C%20Cursor%20%7C%20Codex-orange.svg" alt="Harness Support" /></a>
</p>

![GTM 内容操作系统与商机拓展流水线](docs/assets/gtm-pipeline-flow.png)

*面向 B2B 软件初创团队的开源 Go-To-Market (GTM) AI Agent 运行框架。助力早期初创公司在销售开拓、技术售前 (Pre-sales) 和市场营销活动中实现 10 倍效率提升。*

```
      .-"""-.
     /  o o  \        一个大脑，多双严谨的双手 ——
     \   ^   /        每一次触达与发布，都必须经你亲手批准
      )-----(
     / /| |\ \
    ( ( | | ) )
     \_/ | \_/
        `-`
```

### 你是创始人，同时也是整个 GTM 团队。

你身兼数职，资源精简，全力奔跑在寻找产品与市场契合点 (PMF) 的路上。签单从来不只是全部的工作：你必须亲手**搭建商机漏斗**；你必须把客户真实声音带回产研端，让产品向 PMF 靠拢；你必须在每次通话中流利介绍一个尚未完全成型、文档两周前就已过时的产品——同时还不能把工程师拖进每一次会议；每周市场风云变幻，你还要持续测试新话术、洞察行业信号、发布专业内容吸引目标采购者。

这是五个全职岗位的体量。传统的做法是招五个人。而你现在只有一台笔记本电脑、一个 Claude 订阅，以及这一周的时间。

**gtm-engine 就是陪伴你完成这五项工作的专业框架。** 自动化客户线索挖掘、会议深度背调、战略客户计划书、技术演示材料、每周市场雷达，以及全平台品牌内容创作（领英动态、深度博客、播客脚本、图表）——全部由你输入的一句话驱动，全部采用你的真实品牌语调，全部基于你公司的真实知识库。并且，它作为 AI Agent，**在底层架构上完全无法自行向外发送邮件、发布帖子或泄露数据**。它不需要你盲目信任，因为它的系统架构设计即保证了它无法越权。

三大铁律始终不变：
1. **未经你的明确审批，绝不发送任何邮件或公开发布任何内容**；
2. **每家公司的数据在各自专属 profile 中严格物理隔离**；
3. **Agent 严禁任何原生 HTTP 请求或未经审查的终端 Shell 执行权限**。绝大多数 AI 框架要求你赋予泛化的危险权限；本系统从根本上消除了需要盲目信任的漏洞隐患。

![Content OS Architecture](docs/assets/content-os-grade-a-plus-architecture.png)

---

### 为什么选择 gtm-engine？（架构级优势对比）

| 核心维度 | 黑盒“AI SDR”商业平台 (如 11x, Artisan) | 原生 Prompt 对话 (ChatGPT / Claude) | 泛化 Agent 框架 (CrewAI / LangChain) | **gtm-engine (本系统)** |
|---|---|---|---|---|
| **使用成本** | 500 – 3,000 美元 / 月 | 20 美元 / 月（伴随大量手工复制粘贴） | 纯 Token 费用 + 服务器部署成本 | **0 美元底价**（完全依赖你现有的 Claude 订阅） |
| **外发安全性** | 自动向目标客户发送冷邮件（极高声誉风险） | 手动逐条复制与审核 | 赋予模型泛化的工具执行权限 | **不可绕过的人类审批门禁**（模型完全无权自行发送） |
| **公司业务上下文** | 机械的外部网页爬取 | 每次对话重新粘贴公司背景 | 需要自行搭建向量数据库与外挂管道 | **Profile 第二大脑**（一次录入，全技能自动继承） |
| **覆盖工作流丰富度**| 仅局限于冷邮件发送 | 仅局限于纯文本生成 | 需自行编写复杂的代码与节点图 | **63 项预置技能 & 10 大工作包**（涵盖视频、PPT、文章、SDR） |
| **数据隐私与安全** | 第三方商业云厂商锁定与外泄担忧 | 数据可能参与模型训练迭代 | 视用户自建配置而定 | **100% 本地运行 / 物理隔离**（数据绝不脱离你的掌控） |

---

### 30 秒快速上手

```bash
# 1. 克隆代码仓库
git clone https://github.com/henryroxstar/gtm-engine.git && cd gtm-engine

# 2. 在你顺手的 AI 工作区中打开本项目（Claude Desktop、Google Antigravity、Cursor 或 Codex）

# 3. 在对话框中直接说出：
"set me up" --site yourcompany.com
```
*首次运行完全无需 Docker，无需后台服务器，无需配置任何第三方 API Key。*

---

## 实际运行演示

无需繁琐的手工配置。打开工作区，输入一句话即可触发端到端流水线。看看周一早晨一句简单的“帮我发条领英”是如何执行的：

![实际运行演示：从提示词到人工审批](docs/assets/see-it-work-workflows.png)

六十秒之前，你面对的是空白的屏幕与头疼的待办；现在，你获得了一篇基于深度调研、高度契合你专业水准的完整内容，并且每一个字都由你亲自核准。

同样的单句指令可以驱动你全周的核心工作：

---

## 从这里开始

| 你的角色或需求 | 推荐入口 | 预估耗时 |
|---|---|---|
| **非技术背景** —— 专注于业务销售而非代码 | 查看 [`END-USER-ONBOARDING.md`](END-USER-ONBOARDING.md)，无需终端与命令，包含常见答疑 | 30 分钟 |
| **技术人员 / 开发者** —— 喜欢在终端和本地驱动 | 查看下方的 [快速上手指南](#快速上手指南-chat-对话模式) | 10 分钟 |
| **技术选型评估者** —— 关注系统架构、控制流与安全性 | 查看 [为什么采用这种架构](#为什么采用这种架构) | 10 分钟 |

---

## 四种运行与集成方式

**1 · Chat 对话模式（默认推荐 — 零环境门槛）**
在任何你喜欢的 AI 工作区打开本仓库 —— **Claude Desktop**、**Google Antigravity**、**Cursor** 或 **Codex**，直接输入 `"set me up"`。所有 GTM 技能均在本地调用，读取你的 profile，以你的语调生成。无 VPS、无 Docker、无数据库服务，一切由你交互掌控。→ [快速上手指南](#快速上手指南-chat-对话模式)

**2 · 高级自托管模式（24/7 自主运行 Agent）**
部署基于 **Claude Agent SDK** 的自动化运行时 —— 可在本地后台或自己的 **VPS** 上运行，24/7 自动巡检新闻 → 规划 → 深度调研 → 创作，在 Telegram 审批门禁前自动等待人类指令。详见 [`docs/DEPLOY.md`](docs/DEPLOY.md)。

**3 · 客户端 REST API 开发**
启动本地 FastAPI 后端（通过 `./scripts/stack.sh start`，监听 `:8000` 端口）配合 Postgres 与 Redis。专为开发自定义前端界面、仪表盘或移动客户端的工程师设计，提供标准的 OpenAPI 路由。

**4 · 接入型 GTM MCP 服务端**
通过可流式传输的 HTTP FastMCP（部署 `deploy/Dockerfile.mcp`，监听 `:8001` 端口）对外暴露精选 GTM 引擎工具，支持 API Key 认证 (`sk-...`)。允许第三方外部 Agent（如外部 Claude 实例、LangChain、AutoGen 或 CrewAI 集群）直接连接 GTM 引擎作为远程工具提供方。

### 选择适合你的路径（避免混淆）

| 路径 | 适用人群 | 如何运行 | 严禁的操作（避免走弯路） |
|---|---|---|---|
| **Chat 模式 (默认)** | 创始人、销售代表、市场营销者 | 在 AI 工作区打开仓库 $\rightarrow$ 键入 `"set me up"` | **不要**启动 Docker，**不要**运行 `./scripts/stack.sh`。完全无需本地服务！ |
| **自托管 Agent 模式** | 需要 24/7 自动化运行的团队 | 按照 [`docs/DEPLOY.md`](docs/DEPLOY.md) 部署 Docker Compose | **不要**指望在此模式下进行即兴即时对话；它运行在后台，由 Telegram 门禁驱动。 |
| **客户端 API 开发** | 正在为本引擎开发前端界面的工程师 | 运行 `./scripts/stack.sh start` 启动本地 FastAPI 接口 | 如果你只是想在聊天中使用 GTM 技能，**不要**启动这个服务；Chat 模式完全轻量独立。 |
| **Inbound MCP 服务端** | 连接第三方外部 Agent 到 GTM 工具 | 在 8001 端口部署 FastMCP 容器并配置 API Key | **不要**在未配置 API Key 鉴权 (`sk-...`) 和预算限额时公开暴露。 |

### 支持的 AI 工作区与环境

| 工作区 / Harness | 支持级别 | 技能加载机制 | 说明 |
|---|---|---|---|
| **Claude Desktop / Code** | 原生支持 | 插件系统 (`plugin/`) | 原生支持全部 63 项技能、MCP 工具与审批门禁 |
| **Google Antigravity** | 原生支持 | `.agents/` 自动发现机制 | 支持多 Agent 编排、原生 `run_command` 与文件工具映射 |
| **Cursor / Codex** | 完全兼容 | `.agents/AGENTS.md` + 规则配置 | 交互式对话体验；通过提示词直接调用底层技能 |
| **Headless VPS (Agent SDK)**| 专用容器环境 | 容器化 Agent 运行循环 | 配合 Telegram 审批机器人实现 24/7 自动化无人值守 |

---

## 快速上手指南 (Chat 对话模式)

**环境要求：** Python 3.11+ 以及 [`uv`](https://docs.astral.sh/uv/)。在 Chat 模式下，AI Agent 会在第 1 步自动协助你安装环境，无需手动折腾。

**第 1 步 — 初始化引擎与企业画像 (Profile)**
在对话框中键入 `"set me up"`。引擎会运行环境检查，随后通过简短交互，根据你的官网 URL 自动提炼并初始化你的专属公司画像。

**第 2 步 — 配置工具与密钥 (可选连接器)**
`"set me up"` 会搭建好你的 profile，所有外部数据提供商均为可选配置（默认优雅降级至免 Key 的公开网络搜索）：
- **商机挖掘连接器**（Vibe、RocketReach、Apollo）：提供已验证的商业邮箱与采购意向信号。
- **爬虫连接器**（Firecrawl）：提供动态页面深度解析能力。
- **设置预算硬上限**：在初始化时设定每月及单次运行的支出上限，系统在每次付费调用前都会自动校验额度，杜绝超额消耗。

#### 环境健康体检 (`check_env`)
运行内置的诊断命令，确保你的企业画像、外部工具连接与预算限额就绪：
```bash
uv run python -m gtm_core.check_env
```

| 检查项 | 验证内容 | 未配置时的处理机制 |
|---|---|---|
| **企业画像 (Profile)** | 校验 `profiles/<active>/` 目录完整性与语法 | 发出提醒并建议运行 `"set me up"` |
| **商机拓展连接器** | 检查 Vibe、RocketReach、Apollo 凭证状态 | 自动平滑降级至公开网络搜索模式 |
| **预算支出上限** | 确认每笔及每月调用支出保护限额 | 严格阻止任何潜在计费调用，保护钱包 |
| **模型调度注册表** | 检查 `gtm_core/models.toml` 端点可用性 | 默认采用当前工作区的原生接入模型 |

---

## 今天想做什么？（核心任务快速索引）

直接说出你当前的目标，无需刻意记忆技能清单：

| 你的即时目标 | 对 AI 说的话 | 触发的核心技能 | 输出交付物 |
|---|---|---|---|
| **挖掘高匹配客户与关键买家** | `"find prospects in [目标行业/市场]"` | `prospect`, `draft-outreach` | 评分客户简报、HubSpot CSV 表、经过验证的采购者联系方式 |
| **备战重要商业拜访或演示** | `"prep me for my call with [公司名]"` | `call-prep`, `account-dossier` | 5 分钟速读简报、SPIN 探询提问库、对标标杆案例 |
| **发布高质量领英或社交动态** | `"draft my LinkedIn post about [热点/话题]"` | `content-radar`, `content-studio` | 3 组候选切入角度 (门禁 1) $\rightarrow$ 格式严格质检的成稿 (门禁 2) |
| **在 Reddit / 领英专业讨论中互动** | `"reply to this post: [URL]"` | `linkedin-reply`, `reddit-reply` | 价值优先、不带营销味的专业回复方案（待你审核） |
| **撰写企业级解决方案设计书** | `"design the solution for [公司名]"` | `solution-discovery`, `solution-design`| 规范的技术架构设计文档 (SAD)，包含现状与目标架构图 |
| **制定年度/季度战略客户攻坚计划** | `"build an account plan for [公司名]"` | `account-plan` | 买方委员会角色图谱、MEDDPICC 记分卡、5 步落地执行计划 |
| **检查系统运行环境状态** | `"run environment check"` | `check_env` CLI | 全套密钥、画像健康度及预算防护审核报告 |

> 完整的 63 项技能目录请参阅 [`docs/SKILLS.md`](docs/SKILLS.md)。

---

## 为什么采用这种架构

1. **两道人类审批门禁是永久性的；任何产物都不可自动对外发送。**
   每个工作流都在 **门禁 1**（批准方向与钩子）和 **门禁 2**（批准最终发布的每个字节）自动暂停；系统全局硬编码 `autopublish: false`，发布目标端点由底层安全锁死。GTM 动作代表你的商业声誉与客户隐私，关键决策必须由人核准。
2. **多租户企业画像物理隔离。**
   每个公司是独立的 Profile，拥有专属的数据沉淀、账本和客户记录。同一个引擎可以安全切换服务多家公司，杜绝数据串扰。
3. **Claude 作为大脑；MCP 工具作为唯一双手。**
   Agent 绝不进行原生裸 HTTP 请求，所有外部网络调用、数据爬取与发布均由安全策略拦截器裁决，凭证不暴露在模型上下文内部。
4. **纯粹由企业画像驱动，代码中零公司硬编码。**
   品牌风格、ICP、人群画像、话术库全部在运行时动态装载。
5. **业务流程是数据，而不是代码。**
   新增或调整工作流只需要配置一个声明式的 Pack 节点拓扑图，无需修改引擎底层核心代码。

---

## Star History

[![Star History Chart](https://api.star-history.com/svg?repos=henryroxstar/gtm-engine&type=Date)](https://star-history.com/#henryroxstar/gtm-engine&Date)

---

## 开源协议

本项目采用 **Apache License 2.0** 开源许可协议 —— 详见 [`LICENSE`](LICENSE)。允许自由使用、修改和分发，并包含明确的专利授权条款。
