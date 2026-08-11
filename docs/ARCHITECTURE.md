# TickerDossier 架构

本文描述仓库当前结构、依赖规则和扩展约定。目标是让入口稳定、核心运行时可测试、领域逻辑可复用，并把网络、进程和本地状态留在明确的边界上。

## 设计目标

- 所有生产包代码统一位于 `src/ticker_dossier/`，只使用 `ticker_dossier.*` 包路径。
- `runtime` 保持小而稳定，通过 `Tool`、`ToolRegistry` 和模型 `chat` 接口驱动能力。
- 金融计算、外部 I/O、CLI 展示和应用组装分开演进。
- 交互入口与一次性入口复用同一套组装逻辑和命令目录。
- 本地状态、用户状态、凭据和可版本化源码有清晰边界。
- 安全策略在执行层生效，不能只依赖提示词或调用方自律。

项目的非目标包括真实交易执行、券商连接、托管式后台服务和面向多租户的远程沙箱。

## 仓库布局

```text
ticker-dossier/
├── src/ticker_dossier/
│   ├── __main__.py
│   ├── bootstrap.py
│   ├── config.py
│   ├── security.py
│   ├── telemetry.py
│   ├── cli/
│   ├── runtime/
│   ├── llm/
│   ├── research/
│   ├── tools/
│   ├── integrations/
│   │   └── mcp/
│   └── skills/
├── skills/
├── tests/
├── evals/
├── docs/
├── pyproject.toml
├── .env.example
└── .mcp.json
```

`src` layout 可以避免从仓库根目录意外导入未安装代码。开发环境通过 editable install 使用同一包；测试配置中的 `pythonpath = ["src"]` 只提供直接运行测试时的兼容路径。

## 目录职责

| 路径 | 责任 | 不应承担 |
| --- | --- | --- |
| `src/ticker_dossier/__main__.py` | 把模块运行转交给 CLI | 组装业务对象或实现命令 |
| `src/ticker_dossier/bootstrap.py` | 创建后端、工具注册表和 `AgentLoop` | CLI 绘制、金融计算或状态渲染 |
| `src/ticker_dossier/config.py` | 从本地环境文件补充环境变量 | 保存真实凭据或业务默认对象 |
| `src/ticker_dossier/security.py` | 路径、shell、出站文本和不可信内容检查 | 命令路由或领域判断 |
| `src/ticker_dossier/telemetry.py` | token usage 归一化、累计与可选成本估算 | 调用模型或负责终端展示 |
| `src/ticker_dossier/runtime/` | Agent 循环、会话、上下文、权限决策、提示策略、工具契约 | 导入具体金融能力或具体外部服务 |
| `src/ticker_dossier/llm/` | 真实模型与离线模型适配器 | 注册工具或决定 CLI 行为 |
| `src/ticker_dossier/research/` | 金融数据模型、分析、质量门禁、回测、预测与纸面组合 | 终端交互和通用工具协议 |
| `src/ticker_dossier/research/rendering.py` | 把领域结果渲染为面向用户的研究文本 | 访问终端、创建模型或持久化会话 |
| `src/ticker_dossier/tools/` | 把领域、文件、网页、消息等能力适配为 `Tool` | 重复实现领域算法 |
| `src/ticker_dossier/integrations/` | HTTP、MCP、消息与调度等 I/O 边界 | Agent 循环和 CLI 状态机 |
| `src/ticker_dossier/skills/` | 加载、校验和生成 Skill | 存放具体项目 Skill 内容 |
| `skills/` | 可审查、可版本化的项目 Skill | 临时会话状态或密钥 |
| `tests/` | 单元、集成和回归测试 | 生产时导入的实现 |
| `evals/` | 评估任务、指标、trace 和安全评估 | 运行时依赖 |

## 依赖方向

下面的箭头表示“左侧允许导入右侧”。稳定契约位于图的内侧，入口和具体适配器位于外侧。

```text
ticker_dossier.cli ───────────────┐
ticker_dossier.bootstrap ─────────┼──> llm / tools / integrations / research / skills
ticker_dossier.tools ─────────────┴──> runtime contracts
ticker_dossier.integrations.mcp ─────> runtime Tool + ToolRegistry
ticker_dossier.llm ──────────────────> config + integrations.http + telemetry
ticker_dossier.research ─────────────> config + security + integrations.http
ticker_dossier.runtime ──────────────> security
```

必须保持的规则：

1. `ticker_dossier.runtime` 不导入 `ticker_dossier.cli`、`ticker_dossier.research`、`ticker_dossier.tools`、`ticker_dossier.integrations` 或 `ticker_dossier.llm`。
2. `ticker_dossier.research` 不导入 CLI 或工具适配器；领域对象可脱离终端和工具注册表测试。
3. `ticker_dossier.tools` 可以依赖运行时契约和具体能力，但其他层不应依赖具体工具模块来复用业务逻辑。
4. `ticker_dossier.llm` 和 `ticker_dossier.integrations` 是外部适配器；核心运行时只接收它们提供的对象，不反向选择实现。
5. 具体实现的批量导入和生命周期管理集中在 `ticker_dossier.bootstrap`。
6. CLI 可以协调应用服务，但新的领域规则必须先进入 `research`，不能只存在于命令 handler。
7. `tests` 与 `evals` 只能通过 `ticker_dossier.*` 导入生产代码。

当前有两个需要继续收敛的例外：

- `research/data.py`、`research/resolver.py` 和 `research/web.py` 仍直接使用共享 HTTP 集成。后续拆分 Provider 适配器时，纯选择与合并规则应留在领域层，网络实现移到集成层。
- `research/debate_orchestrator.py` 在缺少注入后端时会延迟创建具体模型适配器。后续应由组装点注入模型工厂，使领域编排不选择基础设施。

这些例外是已知技术债，不应成为新增依赖的先例。

## Composition root

`src/ticker_dossier/bootstrap.py` 是核心应用的 composition root。它有意成为少数“知道所有具体实现”的模块。

`build_default_registry()` 执行以下工作：

1. 创建空的 `ToolRegistry`。
2. 导入并注册文件、shell、记忆、Skill、金融、网页、调度和消息工具。
3. 读取项目 MCP 配置，把已连接工具注册为 `mcp__<server>__<tool>`。
4. 把注册表及其受管子进程生命周期交给调用方。

`build_agent()` 执行以下工作：

1. 使用调用方传入的注册表，或创建默认注册表。
2. 尝试创建真实模型适配器；配置不可用时创建 `FakeBackend`。
3. 合并显式批准的工具和本地权限配置。
4. 从领域层注入证券提取与规范化回调，避免运行时反向导入金融代码。
5. 将后端、注册表、系统提示、observer、权限与回调交给 `AgentLoop`。

CLI 的提示词构建、终端 observer、输入组件和确定性命令路由属于界面生命周期，保留在 `ticker_dossier.cli.main`。跨 CLI、服务或未来入口都需要的具体实现选择，应移入 `bootstrap.py`，而不是复制一套注册逻辑。

可以直接复用组装 API：

```python
from ticker_dossier.bootstrap import build_default_registry

registry = build_default_registry()
try:
    print(registry.names())
finally:
    registry.close()
```

调用方必须关闭注册表，以回收受管 MCP 子进程。

## 运行路径

两个主入口最终都调用 `ticker_dossier.cli.main:main`：

```text
ticker-dossier
python -m ticker_dossier
```

旧命令 `finance-agent` 仍映射到同一入口，供已有本地脚本平滑迁移。

CLI 内部有三条可观察路径：

```text
自然语言任务 ─> AgentSession ─> AgentLoop ─> ToolRegistry ─> Tool adapters
动态命令   ───> 展开为 user-level prompt ────────────────────┘
内置命令   ───> CommandRouter ─> deterministic research/integration calls
```

- 自然语言任务进入多轮 Agent 循环。模型返回工具调用，运行时检查权限、执行工具，并把 observation 回填上下文。
- Markdown 命令、项目 Skill 和 MCP prompt 先展开为普通用户内容，再走同一 Agent 路径；它们不获得 system 权限。
- 内置 slash command 由 `CommandRouter` 直接调用确定性能力，适合诊断和可重复操作。
- 只有第一次模型请求在任何工具执行前失败时，金融任务才允许使用确定性兜底；后续失败不自动重放，避免重复副作用。

## 运行时契约

`ticker_dossier.runtime.tools.Tool` 用名称、说明、JSON 参数 schema 和执行函数表达一个能力。`ToolRegistry` 负责唯一注册、schema 导出、查找、MCP 状态和资源关闭。

`AgentLoop` 只依赖以下抽象行为：

- 后端提供 `chat(messages, tools)` 并返回文本、工具调用和可选 usage。
- 注册表提供 schema、按名称查找和生命周期方法。
- observer 接收结构化事件，用于 CLI trace，而不是参与业务决策。
- 权限层对每次工具调用返回 allow、confirm 或 deny。

工具失败会成为可审计 observation，由模型决定是否修复；结果过长会截断，较早会话按预算压缩。压缩内容、工具输出、网页、MCP 和持久记忆都保留低信任标记，不能覆盖系统策略。

## 状态与生成物边界

### 版本控制内

- `src/ticker_dossier/`：生产源码。
- `tests/` 与 `evals/`：验证资产。
- `skills/`：项目 Skill 源文件，修改后必须像代码一样审查。
- `docs/`、`pyproject.toml`、`.env.example` 和 `.mcp.json`：文档与无密钥配置。

`.mcp.json` 可以声明显式环境，但仓库版本不应包含 secret。需要敏感值的外部 server 应通过本地配置或受控启动环境提供。

### 项目本地状态

`.finance_agent/` 被 Git 忽略，可能包含：

- `project_memory.md` 与 `project_memory.json`；
- `finance_memory.jsonl` 与 `history_learning.jsonl`；
- `scheduled_jobs.json` 与 `wechat_outbox/`；
- `commands/` 中的项目本地动态命令；
- 旧位置首次迁移时读取的预测或纸面组合文件。

`.agent_task_list` 是工作区内的临时规划状态，也不属于源码。

### 用户级状态

- `~/.finance-agent/predictions.jsonl`：默认预测账本；
- `~/.finance-agent/portfolios/`：默认纸面组合；
- `~/.finance-agent/commands/`：用户动态命令；
- `~/.finance_agent_history`：CLI 输入历史。

这些位置可能含个人研究内容。测试应通过临时目录或环境变量覆盖它们，不能读取开发者的真实用户状态。

品牌迁移后优先使用 `TICKER_DOSSIER_LANG`、`TICKER_DOSSIER_APPROVED_TOOLS`、
`TICKER_DOSSIER_AUTO_APPROVE` 和 `TICKER_DOSSIER_TRUSTED_MCP_SERVERS`。
旧的 `FINANCE_AGENT_LANG` 与 `MINI_OPENCLAW_*` 变量继续作为兼容回退，现有状态目录也不自动改名，以免静默丢失用户数据。

### 构建与测试生成物

`.venv/`、`build/`、`dist/`、`*.egg-info/`、coverage 输出、解释器缓存、测试缓存、`artifacts/` 和 `out-*` 都是可重建生成物，不应成为运行时输入或提交内容。

导出文件由用户提供目标路径。调用代码必须经过工作区和敏感内容检查，调用者负责决定是否纳入版本控制。

`skills/finance-history-learning/SKILL.md` 是特殊边界：历史学习可以更新它，但它仍是可版本化源码。任何自动更新都应查看 diff、运行校验并由人确认，不能把它当成无审查的缓存。

## 安全边界

安全策略由 `ticker_dossier.security` 与 `ticker_dossier.runtime.permissions` 协作执行：

- 工作区根目录由进程启动目录决定；文件能力拒绝越界、敏感目录和非普通文件。
- 写入前检查疑似 key、token、secret 和 password。
- shell 只允许受限的单命令语法；控制符、高危程序和危险 Git 操作会被拒绝或进入确认层。
- Python 自动批准仅适用于审查过的入口，不代表通用宿主沙箱。
- web fetch 使用出站域名白名单，并在请求前扫描可能外泄的敏感值。
- 模型工具触发的消息投递在本地 `dry-run` 之外进入确认层；显式 CLI 发送命令本身是用户的直接请求。
- 非内置 MCP 配置默认阻止启动，必须提供绑定 `name + command + args + env + cwd + timeout` 的信任指纹。
- MCP 子进程只继承最小运行环境；外部 observation 进入独立的不可信数据边界。
- 真实交易请求由执行层拒绝，纸面组合能力不得被描述成真实成交。

这是一套应用层防护，不是完整的操作系统隔离。没有可用的 `bubblewrap` 时，已批准的本地进程仍拥有当前用户权限。

## 扩展方式

### 新增领域能力

1. 在 `ticker_dossier.research` 中实现纯计算或领域服务。
2. 将网络和进程 I/O 放入 `ticker_dossier.integrations`，通过参数传给领域代码。
3. 为模型调用创建 `ticker_dossier.tools` 适配器。
4. 只在 `build_default_registry()` 注册具体工具。
5. 为领域逻辑、权限决策和 CLI 路径分别补测试。

### 新增模型后端

实现与当前后端相同的 `chat(messages, tools)` 行为，保留 usage 和工具调用结构，再由 `build_agent()` 选择或注入。不要让 `AgentLoop` 根据环境变量导入具体 SDK。

### 新增内置命令

在 `ticker_dossier.cli.command_catalog` 添加 `CommandSpec`，再在 `ticker_dossier.cli.commands.CommandRouter` 实现处理。命令目录是帮助和补全的单一来源；领域计算仍应委托给 `research`。

### 新增动态命令或 Skill

- 项目动态命令放入 `.finance_agent/commands/**/*.md`。
- 用户动态命令放入 `~/.finance-agent/commands/**/*.md`。
- 项目 Skill 放入 `skills/<name>/SKILL.md`，通过 loader 校验后按需读取。

动态内容始终以 user-level 内容进入上下文，不能提升为系统策略。

### 新增 MCP server

在 `.mcp.json` 添加 stdio server，设置 `command`、`args`、`cwd`、可选 `env` 和 `timeoutSeconds`。工具名自动带 server 命名空间；一个 server 失败不会阻止其他 server。非内置配置需先审查源码和启动参数，再使用运行时给出的指纹临时信任。

## 已知大文件与后续拆分

迁移目录只解决命名空间和所有权问题，没有自动消除模块内部复杂度。后续应按稳定接缝逐步拆分，并保持外部 import 与测试先稳定：

| 当前模块 | 主要问题 | 建议接缝 |
| --- | --- | --- |
| `research/data.py` | Provider、并发、缓存、规范化、合并和诊断集中 | `providers/`、`normalization.py`、`selection.py`、`cache.py` |
| `research/paper_portfolio.py` | 存储、迁移、评分、交易和渲染耦合 | repository、scoring、service、rendering |
| `runtime/loop.py` | 循环、收敛、权限回执、任务边界和质量重试集中 | executor、convergence、receipts、policy guards |
| `research/debate_orchestrator.py` | prompt、并发模型调用、校验和裁决集中 | roles、runner、evidence validation、judging |
| `research/agent.py` | 领域 facade 同时承担路由与聚合 | query services、facade、task parsing |
| `cli/commands.py` | session、research 和 workflow handler 集中 | 按命令类别拆 handler，并保留统一 router |
| `integrations/mcp/client.py` | 配置、信任、传输、协议和注册集中 | config、trust、stdio transport、JSON-RPC client |
| `cli/main.py` | 入口解析、交互会话、trace 和兜底集中 | application runner、session UI、trace observer |

拆分顺序建议从 `research/data.py` 和 `cli/commands.py` 开始，因为二者修改频率高且已有明确行为测试。每次只移动一个接缝，先增加 characterization tests，再改 import，最后删除兼容层；不要一次性重写算法和目录。

## 架构验证

最低验证集：

```bash
python -m pytest -q
python -m compileall -q src/ticker_dossier evals
ticker-dossier --selfcheck
python -m ticker_dossier /security
python -m ticker_dossier /mcp
```

架构改动还应检查：

- 生产代码只通过 `ticker_dossier.*` 导入项目模块；
- `runtime` 没有反向导入具体能力；
- 新工具只在 composition root 完成默认注册；
- 测试不依赖真实用户状态、真实凭据或未声明网络；
- 注册表在成功和异常路径都会关闭；
- README、入口元数据和命令帮助保持一致。

## 参考的开源结构

- [smolagents 的单一 `src/smolagents` 包](https://github.com/huggingface/smolagents/tree/e3a5b8994b301983b91c0325546e9dc82eab8cf0/src/smolagents)：小型核心、显式工具抽象和可替换模型边界。
- [OpenAI Agents SDK 的公开包与内部运行循环](https://github.com/openai/openai-agents-python/tree/80e1baaefdfff291b3d7e55987219107c9736d80/src/agents)：运行时原语、工具、handoff 与 tracing 的清晰分工。
- [Aider 的单一顶级包](https://github.com/Aider-AI/aider/tree/5dc9490bb35f9729ef2c95d00a19ccd30c26339c/aider)：成熟 Python CLI 的命名空间、配置边界和测试组织。
- [OpenHands SDK workspace](https://github.com/OpenHands/software-agent-sdk/blob/281843c78094b179d570a48e3cac1857e259b1d7/pyproject.toml#L1-L3)：借鉴其 SDK、工具和工作区边界，但不复制当前项目不需要的多包 monorepo。
- [PyPA src layout](https://packaging.python.org/en/latest/discussions/src-layout-vs-flat-layout/)：避免仓库根目录意外导入并统一安装后的包行为。

这些项目提供的是结构启发；本仓库保留小型、本地优先和金融研究专用的约束，不追求复制它们的全部抽象。
