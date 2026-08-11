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
│   │   └── handlers/
│   ├── runtime/
│   │   ├── execution.py
│   │   └── protocols.py
│   ├── llm/
│   ├── research/
│   │   ├── data.py
│   │   ├── market_data/
│   │   └── portfolio/
│   ├── tools/
│   ├── integrations/
│   │   ├── market_data/
│   │   │   └── providers/
│   │   └── mcp/
│   │       ├── config.py
│   │       ├── transport.py
│   │       └── runtime.py
│   ├── resources/
│   │   ├── default.mcp.json
│   │   └── skills/
│   └── skills/
├── .github/workflows/ci.yml
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
| `src/ticker_dossier/bootstrap.py` | 创建 `ResearchServices`、后端、工具注册表和 `AgentLoop` | CLI 绘制、金融计算或状态渲染 |
| `src/ticker_dossier/config.py` | 从本地环境文件补充环境变量 | 保存真实凭据或业务默认对象 |
| `src/ticker_dossier/security.py` | 路径、shell、出站文本和不可信内容检查 | 命令路由或领域判断 |
| `src/ticker_dossier/telemetry.py` | token usage 归一化、累计与可选成本估算 | 调用模型或负责终端展示 |
| `src/ticker_dossier/runtime/loop.py` | 模型轮次、收敛、会话与最终任务边界 | 直接执行工具或选择具体适配器 |
| `src/ticker_dossier/runtime/execution.py` | 工具权限、复用缓存、回执、observation 与副作用边界 | 模型调用或领域计算 |
| `src/ticker_dossier/runtime/protocols.py`、`tools.py` | `ModelBackend`、`Tool` 与 `ToolRegistry` 稳定契约 | 导入具体后端、金融或集成实现 |
| `src/ticker_dossier/llm/` | 真实模型与离线模型适配器 | 注册工具或决定 CLI 行为 |
| `src/ticker_dossier/research/` | Provider 选择/合并、金融模型、分析、质量门禁、回测和预测 | 终端交互和通用工具协议 |
| `src/ticker_dossier/research/data.py` | 旧市场数据 import 的 identity 兼容 facade | 缓存、并发、合并或 Provider 网络实现 |
| `src/ticker_dossier/research/market_data/` | ProviderChain 工作流及缓存、执行、覆盖诊断、选择/合并策略 | 具体 HTTP/API 适配器或 CLI 渲染 |
| `src/ticker_dossier/research/portfolio/` | 纸面组合模型、评分和纯渲染 | 文件位置选择和外部行情抓取 |
| `src/ticker_dossier/research/paper_portfolio.py` | 兼容 facade、账户存储、迁移与显式写操作 | 实时行情 Provider 实现 |
| `src/ticker_dossier/research/rendering.py` | 把领域结果渲染为面向用户的研究文本 | 访问终端、创建模型或持久化会话 |
| `src/ticker_dossier/tools/` | 把领域、文件、网页、消息等能力适配为 `Tool` | 重复实现领域算法 |
| `src/ticker_dossier/integrations/market_data/` | Provider contract、字段归一化与各数据源适配器 | 多源合并策略或 CLI 渲染 |
| `src/ticker_dossier/integrations/mcp/` | MCP 配置/信任、stdio 传输、发现/注册和生命周期 | Agent 收敛或领域策略 |
| `src/ticker_dossier/integrations/` | HTTP、消息与调度等其余 I/O 边界 | Agent 循环和 CLI 状态机 |
| `src/ticker_dossier/cli/handlers/` | session、research、portfolio、integrations、workflow 命令处理 | 维护第二份命令目录或实现领域算法 |
| `src/ticker_dossier/skills/` | 加载、校验和生成 Skill | 存放具体项目 Skill 内容 |
| `src/ticker_dossier/resources/` | wheel 内置的只读 Skills 与 MCP 默认配置 | 保存用户编辑或运行状态 |
| `skills/` | 可审查、可版本化的项目 Skill overlay | 临时会话状态或密钥 |
| `tests/` | 单元、集成和回归测试 | 生产时导入的实现 |
| `evals/` | 评估任务、指标、trace 和安全评估 | 运行时依赖 |

## 依赖方向

下面的箭头表示“左侧允许导入右侧”。稳定契约位于图的内侧，入口和具体适配器位于外侧。

```text
ticker_dossier.cli ───────────────┐
ticker_dossier.bootstrap ─────────┼──> llm / tools / integrations / research / skills
ticker_dossier.tools ─────────────┴──> runtime contracts + injected application services
ticker_dossier.integrations.mcp.runtime ─> runtime Tool + ToolRegistry
ticker_dossier.integrations.market_data ─> research models/symbols + integrations.http
ticker_dossier.llm ──────────────────> config + integrations.http + telemetry
ticker_dossier.research.market_data ─> research models/symbols + integrations.market_data
ticker_dossier.research ─────────────> config + security + focused integrations
ticker_dossier.runtime ──────────────> security
```

必须保持的规则：

1. `ticker_dossier.runtime` 不导入 `ticker_dossier.cli`、`ticker_dossier.research`、`ticker_dossier.tools`、`ticker_dossier.integrations` 或 `ticker_dossier.llm`。
2. `ticker_dossier.research` 不导入 CLI 或工具适配器；领域对象可脱离终端和工具注册表测试。
3. `ticker_dossier.tools` 可以依赖运行时契约和注入的具体能力，但其他层不应依赖 Tool 适配器来复用业务逻辑。
4. `integrations.market_data` 可使用领域值对象表达结果，但不能反向导入 `research.data` 或 `research.market_data`；多源优先级、合并和覆盖诊断由 `research.market_data.ProviderChain` 决定。
5. `ticker_dossier.llm` 和其他 `integrations` 是外部适配器；核心运行时只接收它们提供的对象，不反向选择实现。
6. 具体实现的批量导入、共享服务创建和应用自有资源的生命周期管理集中在 `ticker_dossier.bootstrap` 与 `ToolRegistry`；外部注入对象仍由调用方管理。
7. CLI handler 可以协调应用服务，但新的领域规则必须先进入 `research`；`command_catalog` 是名称、帮助、补全和 `handler_key` 的单一来源。
8. `tests` 与 `evals` 只能通过 `ticker_dossier.*` 导入生产代码。

当前不再保留 `research -> llm` 的具体模型依赖：`debate_orchestrator.py`
只消费 `research.protocols` 中的端口，具体 DeepSeek factory、单次 HTTP call timeout
和生命周期所有权均由 `bootstrap.py` 注入。仍有一个有意的兼容面：

- `research/data.py` 只以对象 identity 重导出旧 contract、Provider、`ProviderChain` 与历史 helper；实现不能重新回流到该 facade。

兼容面不应成为新增实现依赖的先例。

## Composition root

`src/ticker_dossier/bootstrap.py` 是核心应用的 composition root。它有意成为少数“知道所有具体实现”的模块。

`build_research_services()` 创建应用级 `ResearchServices`。当前它持有唯一的 `FinanceResearchAgent`；该 facade 及其 `ProviderChain` 会被 CLI、金融 Tool、演化 Tool 和调度 Tool 共同复用，避免一次进程内出现相互独立的研究服务和缓存。

`build_default_registry()` 执行以下工作：

1. 接受调用方提供的 `ResearchServices`，或创建默认服务。
2. 创建 `ToolRegistry`，通过 `provide_service("research", services)` 发布应用服务。
3. 把同一个 finance facade 绑定到金融、演化和调度 Tool factory，再注册文件、shell、记忆、Skill、网页和消息工具。
4. 读取项目或 wheel 内置的 MCP 配置，把已连接工具注册为 `mcp__<server>__<tool>`。
5. 由注册表同时持有服务引用和 `MCPRuntime` 等受管资源，交给调用方统一关闭。

`build_agent()` 执行以下工作：

1. 使用调用方传入的注册表，或创建默认注册表。
2. 尝试创建真实模型适配器；配置不可用时创建 `FakeBackend`。
3. 合并显式批准的工具和本地权限配置。
4. 从领域层注入证券提取与规范化回调，避免运行时反向导入金融代码。
5. 将后端、注册表、系统提示、observer、权限与回调交给 `AgentLoop`。

CLI 的提示词构建、终端 observer、输入组件和确定性命令路由属于界面生命周期，保留在 `ticker_dossier.cli.main`。`CommandRouter` 优先从注册表取得同一个 `ResearchServices.finance`；仅为旧测试/显式注入保留兼容 fallback。跨 CLI、服务或未来入口都需要的具体实现选择，应进入 `bootstrap.py`，而不是复制一套注册逻辑。

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
自然语言任务 ─> AgentSession ─> AgentLoop ─> ToolExecutor ─> ToolRegistry ─> Tool adapters
动态命令   ───> 展开为 user-level prompt ─────────────────────────────────────┘
内置命令   ───> command catalog ─> handler registry ─> research/integration services
```

- 自然语言任务进入多轮 Agent 循环。模型返回工具调用，`ToolExecutor` 依次处理公开范围、重复调用缓存、交易/持久状态/微信边界、权限、执行回执和不可信 observation 包装，再按原事件顺序回填上下文。
- Markdown 命令、项目 Skill 和 MCP prompt 先展开为普通用户内容，再走同一 Agent 路径；它们不获得 system 权限。
- 内置 slash command 由 `command_catalog.CommandSpec.handler_key` 定位 handler；`HANDLER_METHODS` 必须与 catalog 完全对应，`CommandRouter` 启动时会拒绝缺失或孤立 handler。
- handler 按 session、research、portfolio、integrations 和 workflow 分组，直接调用确定性能力，适合诊断和可重复操作；帮助和补全仍读取同一 catalog。
- 只有第一次模型请求在任何工具执行前失败时，金融任务才允许使用确定性兜底；后续失败不自动重放，避免重复副作用。

## 运行时契约

`ticker_dossier.runtime.protocols.ModelBackend` 定义模型最小结构契约。`runtime.tools.Tool` 用名称、说明、JSON 参数 schema 和执行函数表达一个能力；`ToolRegistry` 负责唯一注册、schema 导出、按名查找、应用服务、MCP 状态和受管资源关闭。

`AgentLoop` 只依赖以下抽象行为：

- 后端提供 `chat(messages, tools)` 并返回文本、工具调用和可选 usage。
- 注册表提供 schema、按名称查找和生命周期方法。
- observer 接收结构化事件，用于 CLI trace，而不是参与业务决策。
- `ToolExecutor.execute()` 对每次工具调用返回 `ExecutionResult`，其中包含原始输出、模型 observation、成功状态、进度状态、回执和 tool message。
- 权限层对每次工具调用返回 allow、confirm 或 deny；相同名称与参数只执行一次，后续调用复用已包装结果并发出 `tool_reused`。

真实交易伪装成纸面写入、无显式意图的持久状态写入、未经 `wechat_status` 的消息发送都会在执行器内拒绝。工具失败会成为可审计 observation，由模型决定是否修复；结果过长会截断，较早会话按预算压缩。压缩内容、工具输出、网页、MCP 和持久记忆都保留低信任标记，不能覆盖系统策略。

## 市场数据边界

市场数据已分成“外部适配”与“领域选择”两个接缝：

```text
research.data                       # 旧 import identity facade
└── research.market_data.ProviderChain
    ├── chain.py                    # 四类查询工作流与稳定对象接口
    ├── constants.py                # 覆盖标签与字段选择常量
    ├── cache.py                    # 深拷贝 TTL 缓存
    ├── execution.py                # 完整调用键 single-flight、并发 deadline 与熔断
    ├── request_state.py            # ContextVar 请求 deadline 与来源覆盖
    ├── coverage.py                 # 覆盖记录、防御性读取与诊断文本
    ├── selection.py                # quote/history/financial/news 选择合并纯逻辑
    ├── configuration.py            # 环境解析、默认 Provider 与状态诊断
    ├── serialization.py            # 历史行情 CSV
    └── integrations.market_data
        ├── base.py                 # Protocol + provider errors
        ├── _normalization.py       # 外部字段解析与归一化
        └── providers/
            ├── yahoo.py
            ├── akshare.py
            ├── tushare.py
            ├── alpha_vantage.py
            └── sample.py
```

具体 Provider/HTTP 逻辑只位于 `integrations.market_data`。`research.data` 不再承载策略，只保留旧 Provider、`ProviderChain` 和历史 helper 的 identity re-export，因此已有 `from ticker_dossier.research.data import YahooFinanceProvider` 不会失效。适配层反向依赖、旧对象 identity、选择/合并、覆盖副本、缓存过期、并发 deadline 和熔断均有 characterization/architecture tests。

## MCP 边界

MCP 按职责拆成四层：

| 模块 | 责任 |
| --- | --- |
| `integrations.mcp.config` | 读取项目/内置配置，校验 server，计算完整配置信任指纹，构造最小子进程环境 |
| `integrations.mcp.transport` | 单个 stdio 子进程、行分隔 JSON-RPC、超时、stderr 诊断和关闭 |
| `integrations.mcp.runtime` | client 生命周期、tool/prompt 发现、schema 清洗、命名冲突检查和注册 |
| `integrations.mcp.client` | 对旧公开与测试 import 的兼容 facade；不再承载实现 |

`MCPRuntime` 先交给 `ToolRegistry.manage()`，即使配置或发现中途失败也能在退出路径统一关闭。没有项目 `.mcp.json` 时，安装包使用 `resources/default.mcp.json`；项目文件存在时明确覆盖内置模板。外部 server 仍需与 `name + command + args + env + cwd + timeout` 绑定的信任 token。

## 状态与生成物边界

### 版本控制内

- `src/ticker_dossier/`：生产源码。
- `tests/` 与 `evals/`：验证资产。
- `src/ticker_dossier/resources/`：随 wheel 发布的只读 Skill 与 MCP 默认配置。
- `skills/`：项目 Skill overlay，修改后必须像代码一样审查。
- `docs/`、`pyproject.toml`、`.env.example` 和 `.mcp.json`：文档与无密钥配置。

`.mcp.json` 可以声明显式环境，但仓库版本不应包含 secret。需要敏感值的外部 server 应通过本地配置或受控启动环境提供。

### 项目本地状态

`.finance_agent/` 被 Git 忽略，可能包含：

- `project_memory.md` 与 `project_memory.json`；
- `finance_memory.jsonl` 与 `history_learning.jsonl`；
- `scheduled_jobs.json` 与 `wechat_outbox/`；
- `commands/` 中的项目本地动态命令；
- 兼容读取的旧 workspace 纸面组合文件。

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

### 纸面组合位置与迁移

纸面账户 schema 当前为 v2，持久化 `schema_version`、稳定 `account_id` 和 `origin`。默认写入 `~/.finance-agent/portfolios/portfolio_<name>.json`，同时兼容检查 workspace 的 `.finance_agent/portfolio_<name>.json`，但绝不自动复制、覆盖或合并。

状态规则如下：

1. `/portfolio locate [name]` 只检查两个位置，不创建或修改文件。
2. 只有 workspace 文件存在时，读取会继续使用它并显示迁移提示。
3. `/portfolio migrate [name]` 是显式的 workspace → 用户级迁移；目标已存在即拒绝。源文件先移动到带时间戳的 recovery backup，目标写入失败会恢复源文件。
4. 两个位置同时存在同名账户时，读取会明确显示冲突并使用用户级文件；`init`、`mark`、`sell`、`rebalance`、保存和迁移等所有写路径统一抛出 `PortfolioConflictError`。
5. `/portfolio review` 将最新成功行情复制到内存中的账户快照，标记每个价格的来源与时间；失败标的显式回退到账户记录价。该估值不会更新 JSON、交易流水或历史记录。
6. `/portfolio mark` 才是显式写入最新价格与账户历史的操作。

测试传入显式 `base_dir` 时使用隔离存储，不扫描真实用户目录；生产默认路径才应用双位置冲突锁。

### Wheel 内置资源

`load_skills()` 先加载 wheel 中的只读 Skills，再按 Skill 声明名叠加项目 `skills/`；同名项目 Skill 显式覆盖内置版本。MCP 同理：当前目录存在 `.mcp.json` 时使用项目配置，否则读取 `resources/default.mcp.json`。`materialize_project_defaults()` 可显式复制模板，默认不覆盖已有文件。

CI 会构建 wheel，在 checkout 外的新虚拟环境中安装并验证内置 Skills、MCP 配置、CLI、`--selfcheck` 和 `pip check`，避免 editable install 掩盖漏包。

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

1. 在 `ticker_dossier.cli.command_catalog` 添加带唯一 `handler_key` 的 `CommandSpec`。
2. 在对应的 `cli.handlers.session|research|portfolio|integrations|workflow` 模块实现方法，并把 key 映射到 `*_HANDLER_METHODS`。
3. 领域计算仍委托给 `research` 或注入的 integration service。

`CommandRouter` 会验证 catalog keys 与 handler registry 完全相等；帮助、别名和补全只读取 catalog，不维护第二份命令清单。

### 新增动态命令或 Skill

- 项目动态命令放入 `.finance_agent/commands/**/*.md`。
- 用户动态命令放入 `~/.finance-agent/commands/**/*.md`。
- 项目 Skill 放入 `skills/<name>/SKILL.md`，通过 loader 校验后按需读取；它可以按声明名覆盖 wheel 内置 Skill。

动态内容始终以 user-level 内容进入上下文，不能提升为系统策略。

### 新增 MCP server

在 `.mcp.json` 添加 stdio server，设置 `command`、`args`、`cwd`、可选 `env` 和 `timeoutSeconds`。工具名自动带 server 命名空间；一个 server 失败不会阻止其他 server。非内置配置需先审查源码和启动参数，再使用运行时给出的完整配置指纹信任。配置策略改在 `mcp.config`，协议/进程问题改在 `mcp.transport`，发现与注册问题改在 `mcp.runtime`。

## 已知技术债

已经落地的 Provider、executor、CLI handler、MCP 和 portfolio 子包不再列为待完成工作。当前仍存在以下受测试保护的耦合：

| 当前边界 | 剩余耦合 | 现有保护 |
| --- | --- | --- |
| `research/market_data/chain.py` | quote/history/financial/news 四类查询仍由同一个稳定 facade 协调 | 状态机制和纯策略已拆到八个 supporting modules；旧 import identity、single-flight、请求隔离、选择/cache/circuit 与依赖方向测试 |
| `research/paper_portfolio.py` | 存储 repository、显式迁移和交易 mutation 仍由兼容 facade 集中管理 | `portfolio/models.py`、`scoring.py`、`rendering.py` 已纯化；全写路径冲突测试 |
| `runtime/loop.py` | 模型收敛、Todo 进度、最终任务边界和报告质量重试仍共享循环 | 工具执行已由 `ToolExecutor` 隔离；session/security/事件顺序回归测试 |
| `research/debate_orchestrator.py` | prompt、并发模型调用、证据校验和裁决仍集中 | 后端只经 protocol/factory 注入；factory 生命周期、异常脱敏、规则 fallback 与 debate 回归测试 |
| `research/agent.py` | 领域 facade 同时承担任务解析、查询编排和结果聚合 | `ResearchServices` 保证进程内单实例，CLI/Tool 路径复用同一对象 |
| `cli/main.py` | 参数入口、交互生命周期、trace、首轮模型失败兜底仍集中 | CLI 回归测试与注册表统一关闭路径 |
| 静态检查 | strict mypy 覆盖 runtime、market-data/MCP integration、稳定 CLI contract，并可独立覆盖 `research/market_data`；Ruff 常规模块复杂度上限为 24，两个存量评分函数分别锁在 39/31 | CI 固定 target 清单、收紧后的 C901 门禁和架构 import tests |

`research.data`、`research.paper_portfolio`、`integrations.mcp.client` 和 `runtime.loop` 中的部分重导出是有意保留的兼容面，不代表实现仍位于旧模块。

## 架构验证

最低验证集：

```bash
python -m ruff check src tests evals
python -m pytest -q
python -m compileall -q src/ticker_dossier evals
python -m mypy --strict \
  src/ticker_dossier/bootstrap.py \
  src/ticker_dossier/security.py \
  src/ticker_dossier/runtime \
  src/ticker_dossier/research/protocols.py \
  src/ticker_dossier/research/models.py \
  src/ticker_dossier/research/market_data \
  src/ticker_dossier/llm/{deepseek,fake}.py \
  src/ticker_dossier/integrations/market_data \
  src/ticker_dossier/integrations/mcp \
  src/ticker_dossier/cli/{command_types,command_catalog,custom_commands,dynamic_commands}.py
ticker-dossier --selfcheck
python -m ticker_dossier /security
python -m ticker_dossier /mcp
python -m build
```

架构改动还应检查：

- 生产代码只通过 `ticker_dossier.*` 导入项目模块；
- `tests/test_architecture.py` 继续阻止 `runtime` 反向导入具体能力，以及 `research` 导入 CLI/Tool 适配器；
- `command_catalog` 与 handler registry 没有 missing/orphan key；
- 新工具只在 composition root 完成默认注册，并绑定到 registry-owned service；
- 测试不依赖真实用户状态、真实凭据或未声明网络；
- 注册表在成功和异常路径都会关闭；
- wheel 在 checkout 外仍包含 Skills/MCP 资源并可执行 CLI 自检；
- README、入口元数据和命令帮助保持一致。

GitHub Actions 把验证分成 quality、Python 3.11/3.13 tests 和 wheel package 三个 job。quality job 运行 Ruff（常规模块 C901≤24，并单独锁住两个存量函数）、compileall 与选定包的 strict mypy；tests job 触发架构 import gates；package job 只在前两者通过后执行仓库外安装冒烟测试。

## 参考的开源结构

- [smolagents 的单一 `src/smolagents` 包](https://github.com/huggingface/smolagents/tree/e3a5b8994b301983b91c0325546e9dc82eab8cf0/src/smolagents)：小型核心、显式工具抽象和可替换模型边界。
- [OpenAI Agents SDK 的公开包与内部运行循环](https://github.com/openai/openai-agents-python/tree/80e1baaefdfff291b3d7e55987219107c9736d80/src/agents)：运行时原语、工具、handoff 与 tracing 的清晰分工。
- [Aider 的单一顶级包](https://github.com/Aider-AI/aider/tree/5dc9490bb35f9729ef2c95d00a19ccd30c26339c/aider)：成熟 Python CLI 的命名空间、配置边界和测试组织。
- [OpenHands SDK workspace](https://github.com/OpenHands/software-agent-sdk/blob/281843c78094b179d570a48e3cac1857e259b1d7/pyproject.toml#L1-L3)：借鉴其 SDK、工具和工作区边界，但不复制当前项目不需要的多包 monorepo。
- [PyPA src layout](https://packaging.python.org/en/latest/discussions/src-layout-vs-flat-layout/)：避免仓库根目录意外导入并统一安装后的包行为。

这些项目提供的是结构启发；本仓库保留小型、本地优先和金融研究专用的约束，不追求复制它们的全部抽象。
