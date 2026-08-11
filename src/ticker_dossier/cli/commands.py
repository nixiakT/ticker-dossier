"""Interactive slash commands."""
from __future__ import annotations

import json
import os
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

from ticker_dossier.runtime import permissions
from ticker_dossier.runtime.context import redact_sensitive_text
from ticker_dossier.research.agent import FinanceResearchAgent
from ticker_dossier.research.data import ProviderError
from ticker_dossier.research.evolution import add_memory, extract_learning, render_memories
from ticker_dossier.integrations.http import proxy_label, test_connectivity
from ticker_dossier.research.predictions import (
    evaluation_history_period,
    evaluate_due_predictions,
    load_predictions,
    record_prediction,
    render_learning_report,
    render_prediction_record,
    render_predictions,
    render_scorecard,
    select_due_close,
)
from ticker_dossier.runtime.memory import Memory
from ticker_dossier.cli.ui import current_lang
from ticker_dossier.skills.loader import load_skills
from ticker_dossier.integrations.scheduler import add_job, list_jobs, render_jobs, run_due_jobs
from ticker_dossier.security import SecurityError, guard_write, safety_summary
from ticker_dossier.runtime.tools import ToolRegistry
from ticker_dossier.tools.web_tools import web_fetch_tool, web_search_tool
from ticker_dossier.integrations.wechat import connector_status, send_markdown, send_text


@dataclass
class CommandResult:
    handled: bool
    output: str = ""
    exit: bool = False
    clear: bool = False
    selfcheck: bool = False
    compact: bool = False
    think: str | None = None


class CommandRouter:
    def __init__(
        self,
        registry: ToolRegistry,
        finance_agent: FinanceResearchAgent | None = None,
        trace: Callable[[str, str], None] | None = None,
    ):
        self.registry = registry
        self.finance = finance_agent or FinanceResearchAgent()
        self.trace = trace

    def handle(self, raw: str, think_enabled: str | bool = False) -> CommandResult:
        text = raw.strip()
        if not text.startswith("/"):
            return CommandResult(False)
        try:
            parts = shlex.split(text)
        except ValueError as exc:
            return CommandResult(True, f"命令解析失败：{exc}")
        if not parts:
            return CommandResult(False)

        command = parts[0].lower()
        args = parts[1:]
        if command in {"/exit", "/quit"}:
            return CommandResult(True, exit=True)
        if command == "/clear":
            return CommandResult(True, clear=True, output=_msg("Session context cleared.", "已清空当前会话上下文。"))
        if command == "/compact":
            return CommandResult(True, compact=True)
        if command == "/selfcheck":
            return CommandResult(True, selfcheck=True)
        if command == "/think":
            return self._think(args, think_enabled)
        if command == "/trace":
            return self._trace_mode(args, think_enabled)
        if command == "/lang":
            return self._lang(args)
        if command == "/tools":
            return CommandResult(True, self._tools())
        if command == "/skills":
            return CommandResult(True, self._skills())
        if command == "/status":
            return CommandResult(True, self._status(think_enabled))
        if command == "/security":
            return CommandResult(True, safety_summary())
        if command == "/mcp":
            return CommandResult(True, self._mcp())
        if command == "/proxy":
            return CommandResult(True, self._proxy(args))
        if command == "/wechat":
            return CommandResult(True, self._wechat(args))
        if command == "/remember":
            return CommandResult(True, self._remember(args))
        if command == "/memory":
            return CommandResult(True, self._memory(args))
        if command == "/evolve":
            return CommandResult(True, self._evolve(args))
        if command == "/predict":
            return CommandResult(True, self._predict(args))
        if command == "/schedule":
            return CommandResult(True, self._schedule(args))
        if command == "/portfolio":
            return CommandResult(True, self._portfolio(args))
        if command in {"/learn-history", "/learn"}:
            return CommandResult(True, self._learn_history(args))
        if command == "/sources":
            return CommandResult(True, self._sources())
        if command == "/search":
            try:
                return CommandResult(True, self._search(args))
            except (SecurityError, ValueError) as exc:
                return CommandResult(True, str(exc))
        if command == "/fetch":
            try:
                return CommandResult(True, self._fetch(args))
            except (SecurityError, ValueError) as exc:
                return CommandResult(True, str(exc))

        finance_commands = {
            "/quote": self._quote,
            "/resolve": self._resolve,
            "/history": self._history,
            "/financials": self._financials,
            "/news": self._news,
            "/indicators": self._indicators,
            "/report": self._report,
            "/quality": self._quality,
            "/compare": self._compare,
            "/debate": self._debate,
            "/backtest": self._backtest,
            "/brief": self._brief,
            "/export-report": self._export_report,
        }
        if command in finance_commands:
            try:
                return CommandResult(True, finance_commands[command](args))
            except ValueError as exc:
                return CommandResult(True, str(exc))
            except ProviderError as exc:
                return CommandResult(True, f"数据获取失败：{_preview(str(exc), 360)}")
        return CommandResult(True, _msg(
            f"Unknown command: {command}\nType /help for available commands.",
            f"未知命令：{command}\n输入 /help 查看可用命令。",
        ))

    def _quote(self, args: list[str]) -> str:
        symbol = _require_arg(args, "/quote AAPL")
        self._trace_tool("finance_get_quote", {"symbol": symbol})
        return self._with_result_trace("finance_get_quote", self.finance.get_quote(symbol))

    def _resolve(self, args: list[str]) -> str:
        query = " ".join(args).strip()
        if not query:
            raise ValueError("用法：/resolve minimax")
        self._trace_tool("finance_resolve_symbol", {"query": query})
        return self._with_result_trace("finance_resolve_symbol", self.finance.resolve_symbol(query))

    def _history(self, args: list[str]) -> str:
        symbol = _require_arg(args, "/history AAPL [period]")
        period = args[1] if len(args) > 1 else "1y"
        self._trace_tool("finance_get_price_history", {"symbol": symbol, "period": period})
        return self._with_result_trace("finance_get_price_history", self.finance.get_price_history(symbol, period))

    def _financials(self, args: list[str]) -> str:
        symbol = _require_arg(args, "/financials AAPL")
        self._trace_tool("finance_get_financials", {"symbol": symbol})
        return self._with_result_trace("finance_get_financials", self.finance.get_financials(symbol))

    def _news(self, args: list[str]) -> str:
        symbol = _require_arg(args, "/news AAPL [limit]")
        if len(args) > 1:
            try:
                limit = int(args[1])
            except ValueError as exc:
                raise ValueError("用法：/news AAPL [非负整数 limit]") from exc
            if limit < 0:
                raise ValueError("用法：/news AAPL [非负整数 limit]")
        else:
            limit = 5
        self._trace_tool("finance_get_news", {"symbol": symbol, "limit": limit})
        return self._with_result_trace("finance_get_news", self.finance.get_news(symbol, limit))

    def _indicators(self, args: list[str]) -> str:
        symbol = _require_arg(args, "/indicators AAPL [period]")
        period = args[1] if len(args) > 1 else "1y"
        self._trace_tool("finance_calculate_indicators", {"symbol": symbol, "period": period})
        return self._with_result_trace("finance_calculate_indicators", self.finance.calculate_indicators(symbol, period))

    def _report(self, args: list[str]) -> str:
        symbol = _require_arg(args, "/report AAPL [period]")
        period = args[1] if len(args) > 1 else "1y"
        self._trace_tool("finance_generate_report", {"symbol": symbol, "period": period})
        return self._with_result_trace("finance_generate_report", self.finance.generate_report(symbol, period))

    def _export_report(self, args: list[str]) -> str:
        symbol = _require_arg(args, "/export-report AAPL [period] [reports/aapl.md]")
        period = args[1] if len(args) > 1 else "1y"
        output_path = args[2] if len(args) > 2 else f"reports/{symbol.lower()}-{period}.md"
        self._trace_tool("finance_generate_report", {"symbol": symbol, "period": period})
        report = self.finance.generate_report(symbol, period)
        resolved = guard_write(output_path, report)
        resolved.write_text(report, encoding="utf-8")
        try:
            display_path = str(resolved.relative_to(Path.cwd()))
        except ValueError:
            display_path = str(resolved)
        return self._with_result_trace("finance_generate_report", f"研究报告已保存到: {display_path}")

    def _quality(self, args: list[str]) -> str:
        symbol = _require_arg(args, "/quality AAPL [period]")
        period = args[1] if len(args) > 1 else "1y"
        self._trace_tool("finance_quality_screen", {"symbol": symbol, "period": period})
        return self._with_result_trace("finance_quality_screen", self.finance.quality_screen(symbol, period))

    def _compare(self, args: list[str]) -> str:
        symbols = _require_many(args, "/compare NVDA AMD [period]")
        period = _period_arg(args[-1])
        if period:
            symbols = args[:-1]
        else:
            period = "1y"
        self._trace_tool("finance_compare_stocks", {"symbols": symbols, "period": period})
        return self._with_result_trace("finance_compare_stocks", self.finance.compare_stocks(symbols, period))

    def _debate(self, args: list[str]) -> str:
        symbols = _require_many(args, "/debate NVDA AMD [period]")
        period = _period_arg(args[-1])
        if period:
            symbols = args[:-1]
        else:
            period = "1y"
        self._trace_tool("finance_debate_stocks", {"symbols": symbols, "period": period})
        return self._with_result_trace("finance_debate_stocks", self.finance.debate_stocks(symbols, period))

    def _backtest(self, args: list[str]) -> str:
        symbol = _require_arg(args, "/backtest TSLA 20 60 [period]")
        fast = int(args[1]) if len(args) > 1 and args[1].isdigit() else None
        slow = int(args[2]) if len(args) > 2 and args[2].isdigit() else None
        period = args[3] if len(args) > 3 else "2y"
        strategy = f"{fast or 20} 日均线上穿 {slow or 60} 日均线策略"
        self._trace_tool("finance_backtest_strategy", {
            "symbol": symbol,
            "strategy": strategy,
            "period": period,
        })
        return self._with_result_trace(
            "finance_backtest_strategy",
            self.finance.backtest_strategy(symbol, strategy, period, fast, slow),
        )

    def _brief(self, args: list[str]) -> str:
        symbols = _require_many(args, "/brief AAPL MSFT NVDA")
        self._trace_tool("finance_daily_brief", {"symbols": symbols})
        return self._with_result_trace("finance_daily_brief", self.finance.daily_brief(symbols))

    def _tools(self) -> str:
        names = self.registry.names()
        return "已注册工具：\n" + "\n".join(f"- {name}" for name in names)

    def _skills(self) -> str:
        try:
            skills = load_skills()
        except Exception as exc:  # noqa: BLE001 - surface one broken project Skill clearly
            return _msg(f"Skills failed to load: {exc}", f"Skills 加载失败：{exc}")
        if not skills:
            return _msg("Skills: none discovered.", "Skills：未发现项目 Skill。")
        title = _msg("Skills (loaded on demand):", "Skills（按需加载）：")
        return title + "\n" + "\n".join(
            f"- /{skill.name}: {skill.description}" for skill in skills
        )

    def _status(self, think_enabled: str | bool) -> str:
        diagnostics = self.finance.provider.diagnostics()
        enabled_sources = [row["name"] for row in diagnostics if row.get("status") == "enabled"]
        try:
            skill_count = len(load_skills())
        except Exception:
            skill_count = 0
        statuses = self.registry.mcp_statuses()
        connected_mcp = sum(row.get("status") == "connected" for row in statuses)
        mcp_summary = f"{connected_mcp}/{len(statuses)}" if statuses else "0/0"
        think_label = _think_label(think_enabled)
        if current_lang() == "en":
            return "\n".join([
                "TickerDossier status:",
                f"- Model: {os.environ.get('DEEPSEEK_MODEL', 'not configured')}",
                f"- Base URL: {_safe_base_url(os.environ.get('DEEPSEEK_BASE_URL', ''))}",
                f"- Tools: {len(self.registry)}",
                f"- Skills: {skill_count} (on demand)",
                f"- MCP tools: {', '.join(self._mcp_tool_names()) or 'not connected'}",
                f"- MCP servers: {mcp_summary}",
                f"- Proxy: {proxy_label()}",
                f"- WeChat: {_wechat_mode_label()}",
                f"- trace: {'on' if think_label == 'on' else 'off'} (off by default; use /trace on for details)",
                f"- Data sources: {', '.join(enabled_sources) if enabled_sources else 'no real source enabled'}",
                "- License: MIT",
                "- Boundary: research only, no auto trading",
            ])
        return "\n".join([
            "TickerDossier 状态：",
            f"- 模型: {os.environ.get('DEEPSEEK_MODEL', '未配置')}",
            f"- Base URL: {_safe_base_url(os.environ.get('DEEPSEEK_BASE_URL', ''))}",
            f"- 工具数: {len(self.registry)}",
            f"- Skills: {skill_count}（按需加载）",
            f"- MCP 工具: {', '.join(self._mcp_tool_names()) or '未接入'}",
            f"- MCP 服务: {mcp_summary}",
            f"- Proxy: {proxy_label()}",
            f"- WeChat: {_wechat_mode_label()}",
            f"- trace: {'on' if think_label == 'on' else 'off'}（默认 off；/trace on 展开详情）",
            f"- 数据源: {', '.join(enabled_sources) if enabled_sources else '无可用真实数据源'}",
            "- License: MIT",
            "- 边界: research only, no auto trading",
        ])

    def _mcp(self) -> str:
        names = self._mcp_tool_names()
        statuses = self.registry.mcp_statuses()
        prompts = self.registry.mcp_prompts()
        if not names and not statuses and not prompts:
            return _msg("MCP: no configured servers, tools, or prompts.", "MCP：未配置服务、工具或 prompt。")
        lines = [_msg("MCP runtime:", "MCP 运行状态：")]
        if statuses:
            lines.append(_msg("Servers:", "服务："))
            for row in statuses:
                detail = f" - {row.get('detail')}" if row.get("detail") else ""
                lines.append(f"- {row.get('name')}: {row.get('status')}{detail}")
        if names:
            lines.append(_msg("Tools:", "工具："))
            lines.extend(f"- {name}" for name in names)
        if prompts:
            lines.append(_msg("Prompt commands:", "Prompt 命令："))
            lines.extend(
                f"- /mcp:{row.get('server')}:{row.get('name')}: {row.get('description', '')}".rstrip()
                for row in prompts
            )
        return "\n".join(lines)

    def _mcp_tool_names(self) -> list[str]:
        return [name for name in self.registry.names() if name.startswith("mcp__")]

    def _proxy(self, args: list[str]) -> str:
        if not args or args[0].lower() in {"status", "show"}:
            if current_lang() == "en":
                return "\n".join([
                    f"Proxy: {proxy_label()}",
                    "Set FINANCE_HTTP_PROXY for persistence, for example http://127.0.0.1:7897.",
                ])
            return "\n".join([
                f"Proxy: {proxy_label()}",
                "配置环境变量 FINANCE_HTTP_PROXY 可持久启用，例如 http://127.0.0.1:7897。",
            ])
        action = args[0].lower()
        if action == "test":
            url = args[1] if len(args) > 1 else "https://html.duckduckgo.com/html/?q=SpaceX+SPCX"
            return test_connectivity(url)
        if action == "set":
            if len(args) < 2:
                return "用法：/proxy set http://127.0.0.1:7897"
            os.environ["FINANCE_HTTP_PROXY"] = args[1]
            if current_lang() == "en":
                return f"Proxy set for current process: {proxy_label()}\nFor persistence, write this to .env.local: FINANCE_HTTP_PROXY={args[1]}"
            return f"当前进程代理已设置为: {proxy_label()}\n如需持久化，请写入 .env.local：FINANCE_HTTP_PROXY={args[1]}"
        if action in {"off", "disable"}:
            os.environ.pop("FINANCE_HTTP_PROXY", None)
            return _msg("FINANCE_HTTP_PROXY disabled for current process.", "当前进程 FINANCE_HTTP_PROXY 已关闭。")
        return _msg(
            "Usage: /proxy status | /proxy test [url] | /proxy set http://127.0.0.1:7897 | /proxy off",
            "用法：/proxy status | /proxy test [url] | /proxy set http://127.0.0.1:7897 | /proxy off",
        )

    def _wechat(self, args: list[str]) -> str:
        if not args or args[0].lower() in {"status", "show"}:
            self._trace_tool("wechat_status", {})
            return self._with_result_trace("wechat_status", connector_status())
        action = args[0].lower()
        if action in {"send", "send-md"}:
            confirmed = len(args) > 1 and args[1].lower() == "--confirm"
            content = " ".join(args[2:] if confirmed else args[1:]).strip()
            if not content:
                return _msg(
                    f"Usage: /wechat {action} [--confirm] <message>",
                    f"用法：/wechat {action} [--confirm] <内容>",
                )
            self._trace_tool("wechat_status", {})
            status = self._with_result_trace("wechat_status", connector_status())
            verdict = permissions.check("wechat_send", {"content": content}, Path.cwd())
            if verdict == "deny":
                return status + "\n" + permissions.denial_message(
                    "wechat_send", {"content": "[redacted]"}, Path.cwd()
                )
            if verdict == "confirm" and not confirmed:
                return "\n".join([
                    status,
                    "[权限层] 当前连接会产生真实外传，尚未发送。",
                    f"确认目标无误后，重新执行 /wechat {action} --confirm <内容>。",
                ])
            msgtype = "markdown" if action == "send-md" else "text"
            self._trace_tool("wechat_send", {"msgtype": msgtype, "content": "[redacted]"})
            result = send_markdown(content) if action == "send-md" else send_text(content)
            return status + "\n" + self._with_result_trace("wechat_send", result.render())
        return _msg(
            "Usage: /wechat status | /wechat send [--confirm] <message> | /wechat send-md [--confirm] <markdown>",
            "用法：/wechat status | /wechat send [--confirm] <内容> | /wechat send-md [--confirm] <markdown>",
        )

    def _memory(self, args: list[str]) -> str:
        if not args or args[0].lower() in {"list", "show"}:
            limit = int(args[1]) if len(args) > 1 and args[1].isdigit() else 20
            self._trace_tool("finance_memory_list", {"limit": limit})
            return self._with_result_trace("finance_memory_list", render_memories(limit))
        action = args[0].lower()
        if action == "add":
            content = " ".join(args[1:]).strip()
            if not content:
                return _msg("Usage: /memory add <note>", "用法：/memory add <记忆内容>")
            self._trace_tool("finance_memory_add", {"category": "preference", "content": content})
            path = add_memory(content, category="preference", source="cli")
            return self._with_result_trace("finance_memory_add", f"已写入金融记忆: {path}")
        return _msg("Usage: /memory list [limit] | /memory add <note>", "用法：/memory list [条数] | /memory add <记忆内容>")

    def _remember(self, args: list[str]) -> str:
        memory = Memory()
        if not args:
            recalled = memory.recall().strip()
            return recalled or _msg("Project memory is empty.", "项目长期记忆为空。")
        note = " ".join(args).strip()
        self._trace_tool("remember", {"note": note})
        path = memory.write(note)
        return self._with_result_trace("remember", f"已写入跨会话项目记忆: {path}\n- {note}")

    def _evolve(self, args: list[str]) -> str:
        text = " ".join(args).strip()
        if not text:
            return _msg(
                "Usage: /evolve <finance correction, workflow, or task trace>",
                "用法：/evolve <金融纠错、流程经验或任务轨迹>",
            )
        learning = extract_learning(task=text)
        self._trace_tool("finance_evolve_from_trace", {"task": text})
        add_memory(learning, category="workflow", source="cli-evolve", confidence="high")
        output = "\n".join([
            "Finance evolution completed.",
            "- memory: .finance_agent/finance_memory.jsonl",
            "- skill: unchanged (core finance-research-evolution remains stable)",
            "",
            learning,
        ])
        return self._with_result_trace("finance_evolve_from_trace", output)

    def _predict(self, args: list[str]) -> str:
        if not args or args[0].lower() in {"list", "show"}:
            limit = int(args[1]) if len(args) > 1 and args[1].isdigit() else 20
            self._trace_tool("prediction_list", {"limit": limit})
            return self._with_result_trace("prediction_list", render_predictions(load_predictions(), limit))
        action = args[0].lower()
        if action == "record":
            if len(args) < 3:
                return "用法：/predict record AAPL up [horizon_days] [confidence] [thesis]"
            symbol = args[1]
            direction = args[2]
            horizon = int(args[3]) if len(args) > 3 and args[3].isdigit() else 30
            confidence = float(args[4]) if len(args) > 4 and _is_number(args[4]) else 0.5
            thesis_start = 5 if len(args) > 4 and _is_number(args[4]) else 4
            thesis = " ".join(args[thesis_start:]).strip() or "manual prediction"
            self._trace_tool("prediction_record", {
                "symbol": symbol,
                "direction": direction,
                "horizon_days": horizon,
                "confidence": confidence,
            })
            create_prediction = getattr(self.finance, "create_prediction_record", None)
            if callable(create_prediction):
                record = create_prediction(
                    symbol=symbol,
                    direction=direction,
                    horizon_days=horizon,
                    signal_strength=confidence,
                    signal_source="user_supplied",
                    use_calibration=False,
                    thesis=thesis,
                )
            else:
                snapshot = self.finance.snapshot(symbol, "3mo", 0)
                record = record_prediction(
                    symbol=snapshot.symbol,
                    direction=direction,
                    horizon_days=horizon,
                    confidence=confidence,
                    confidence_kind="user_supplied",
                    signal_strength=confidence,
                    thesis=thesis,
                    baseline_price=snapshot.quote.price,
                    baseline_as_of=snapshot.quote.as_of,
                    source=snapshot.quote.source,
                )
            output = render_prediction_record(record)
            return self._with_result_trace("prediction_record", output)
        if action == "eval":
            include_not_due = len(args) > 1 and args[1].lower() in {"all", "--all", "now"}
            self._trace_tool("prediction_evaluate", {"include_not_due": include_not_due})

            def get_historical_price(symbol: str, due_at: str) -> tuple[float, str]:
                period = evaluation_history_period(due_at)
                history = self.finance.provider.get_history(symbol, period, "1d")
                return select_due_close(history, due_at)

            def get_latest_price(symbol: str) -> tuple[float | None, str]:
                quote = self.finance.provider.get_quote(symbol)
                return quote.price, quote.as_of

            evaluated, card = evaluate_due_predictions(
                get_price=get_latest_price,
                get_historical_price=get_historical_price,
                include_not_due=include_not_due,
            )
            output = "\n".join([
                f"Evaluated predictions: {len(evaluated)}",
                render_predictions(evaluated, len(evaluated)) if evaluated else "",
                render_scorecard(card),
            ]).strip()
            return self._with_result_trace("prediction_evaluate", output)
        if action in {"learn", "scorecard", "review"}:
            save_to_memory = len(args) > 1 and args[1].lower() in {"save", "--save", "memory"}
            self._trace_tool("prediction_learn", {"save_to_memory": save_to_memory})
            output = render_learning_report(load_predictions())
            if save_to_memory:
                path = add_memory(output, category="workflow", source="prediction-learn", confidence="high")
                output = f"{output}\n\nSaved to finance memory: {path}"
            return self._with_result_trace("prediction_learn", output)
        return (
            "用法：/predict record AAPL up [horizon_days] [confidence] [thesis] | "
            "/predict list | /predict eval [all] | /predict learn [save]"
        )

    def _schedule(self, args: list[str]) -> str:
        if not args or args[0].lower() in {"list", "show"}:
            self._trace_tool("schedule_list", {})
            return self._with_result_trace("schedule_list", render_jobs(list_jobs()))
        action = args[0].lower()
        if action == "brief":
            if len(args) < 2:
                return "用法：/schedule brief AAPL,MSFT,NVDA [interval_minutes]"
            symbols = args[1]
            interval = int(args[2]) if len(args) > 2 and args[2].isdigit() else 1440
            self._trace_tool("schedule_wechat_brief", {"symbols": symbols, "interval_minutes": interval})
            job = add_job("wechat_brief", {"symbols": symbols}, interval)
            return self._with_result_trace("schedule_wechat_brief", f"Scheduled {job.id} next={job.next_run_at}")
        if action == "message":
            message = " ".join(args[1:]).strip()
            if not message:
                return "用法：/schedule message <content>"
            self._trace_tool("schedule_wechat_message", {"message": message})
            job = add_job("wechat_message", {"message": message}, 1440)
            return self._with_result_trace("schedule_wechat_message", f"Scheduled {job.id} next={job.next_run_at}")
        if action == "portfolio":
            name = args[1] if len(args) > 1 else "default"
            interval = int(args[2]) if len(args) > 2 and args[2].isdigit() else 1440
            self._trace_tool("schedule_portfolio_mark", {"name": name, "interval_minutes": interval})
            job = add_job("wechat_portfolio_mark", {"name": name}, interval)
            return self._with_result_trace("schedule_portfolio_mark", f"Scheduled {job.id} next={job.next_run_at}")
        if action == "run":
            self._trace_tool("schedule_run_due", {})
            results = run_due_jobs(self._run_scheduled_job)
            if not results:
                return self._with_result_trace("schedule_run_due", "No due scheduled jobs.")
            lines = ["Scheduled jobs executed:"]
            for job, result in results:
                lines.append(f"- {job.id} {job.kind}: {result}")
            return self._with_result_trace("schedule_run_due", "\n".join(lines))
        return "用法：/schedule list | /schedule brief AAPL,MSFT,NVDA [interval_minutes] | /schedule portfolio [name] [interval_minutes] | /schedule message <content> | /schedule run"

    def _portfolio(self, args: list[str]) -> str:
        if not args or args[0].lower() in {"status", "show", "list"}:
            name = args[1] if len(args) > 1 else "default"
            self._trace_tool("finance_show_paper_portfolio", {"name": name})
            return self._with_result_trace("finance_show_paper_portfolio", self.finance.show_paper_portfolio(name))
        action = args[0].lower()
        if action == "trades":
            limit = int(args[1]) if len(args) > 1 and args[1].isdigit() else 30
            self._trace_tool("finance_paper_trades", {"name": "default", "limit": limit})
            return self._with_result_trace("finance_paper_trades", self.finance.paper_trades("default", limit))
        if action in {"pnl", "daily", "daily-pnl"}:
            limit = int(args[1]) if len(args) > 1 and args[1].isdigit() else 30
            self._trace_tool("finance_paper_daily_pnl", {"name": "default", "limit": limit})
            return self._with_result_trace("finance_paper_daily_pnl", self.finance.paper_daily_pnl("default", limit))
        if action in {"review", "diagnose"}:
            symbols = args[1:]
            self._trace_tool("finance_review_paper_portfolio", {"symbols": symbols, "period": "6mo"})
            return self._with_result_trace(
                "finance_review_paper_portfolio",
                self.finance.review_paper_portfolio(symbols, "6mo", "default"),
            )
        if action in {"init", "build"}:
            cash = 1_000_000.0
            symbols_start = 1
            if len(args) > 1 and _is_number(args[1]):
                cash = float(args[1])
                symbols_start = 2
            symbols = args[symbols_start:] or ["AAPL", "MSFT", "NVDA", "AMD", "GOOGL"]
            self._trace_tool("finance_build_paper_portfolio", {
                "symbols": symbols,
                "initial_cash": cash,
                "period": "1y",
            })
            return self._with_result_trace(
                "finance_build_paper_portfolio",
                self.finance.build_paper_portfolio(symbols, cash, "1y"),
            )
        if action in {"mark", "update"}:
            name = args[1] if len(args) > 1 else "default"
            self._trace_tool("finance_mark_paper_portfolio", {"name": name})
            return self._with_result_trace("finance_mark_paper_portfolio", self.finance.mark_paper_portfolio(name))
        if action in {"rebalance", "rebuild"}:
            symbols = args[1:] or ["AAPL", "MSFT", "NVDA", "AMD", "GOOGL"]
            self._trace_tool("finance_rebalance_paper_portfolio", {"symbols": symbols, "period": "1y"})
            return self._with_result_trace(
                "finance_rebalance_paper_portfolio",
                self.finance.rebalance_paper_portfolio(symbols, "1y"),
            )
        if action == "sell":
            if len(args) < 2:
                return "用法：/portfolio sell AAPL [shares|all] [reason]"
            symbol = args[1]
            shares: float | str = "all"
            reason_start = 2
            if len(args) > 2 and (args[2].lower() == "all" or _is_number(args[2])):
                shares = args[2]
                reason_start = 3
            if isinstance(shares, str) and _is_number(shares):
                shares = float(shares)
            reason = " ".join(args[reason_start:]).strip() or "manual sell"
            self._trace_tool("finance_sell_paper_holding", {"symbol": symbol, "shares": shares, "reason": reason})
            return self._with_result_trace(
                "finance_sell_paper_holding",
                self.finance.sell_paper_holding(symbol, shares, "default", reason),
            )
        return (
            "用法：/portfolio init [cash] [symbols...] | /portfolio status [name] | "
            "/portfolio mark [name] | /portfolio sell AAPL [shares|all] [reason] | "
            "/portfolio trades [limit] | /portfolio pnl [limit] | /portfolio review [symbols...] | /portfolio rebalance [symbols...]"
        )

    def _learn_history(self, args: list[str]) -> str:
        symbol = _require_arg(args, "/learn-history AAPL [period] [horizon_days]")
        period = args[1] if len(args) > 1 else "2y"
        horizon = int(args[2]) if len(args) > 2 and args[2].isdigit() else 20
        self._trace_tool("finance_learn_from_history", {
            "symbol": symbol,
            "period": period,
            "horizon_days": horizon,
            "record": True,
            "update_skill": True,
        })
        return self._with_result_trace(
            "finance_learn_from_history",
            self.finance.learn_from_history(symbol, period, horizon, True, True),
        )

    def _run_scheduled_job(self, job) -> str:  # noqa: ANN001
        if job.kind == "wechat_brief":
            brief = self.finance.daily_brief(job.payload.get("symbols", ""))
            return send_markdown(brief, title="TickerDossier Brief").status
        if job.kind == "wechat_message":
            return send_text(job.payload.get("message", ""), title="TickerDossier").status
        if job.kind == "wechat_portfolio_mark":
            report = self.finance.mark_paper_portfolio(job.payload.get("name", "default"))
            return send_markdown(report, title="TickerDossier Portfolio").status
        return f"unsupported job kind: {job.kind}"

    def _sources(self) -> str:
        diagnostics = self.finance.provider.diagnostics()
        lines = [_msg("Current data sources:", "当前数据源状态：")]
        for index, row in enumerate(diagnostics, start=1):
            detail = f" - {row['detail']}" if row.get("detail") else ""
            lines.append(f"{index}. {row['name']}: {row['status']}{detail}")
        return "\n".join(lines)

    def _search(self, args: list[str]) -> str:
        query = " ".join(args).strip()
        if not query:
            raise ValueError("用法：/search 智谱 02513 股票")
        self._trace_tool("web_search", {"query": query, "limit": 5})
        return self._with_result_trace("web_search", web_search_tool.run(query=query, limit=5))

    def _fetch(self, args: list[str]) -> str:
        url = _require_arg(args, "/fetch https://xueqiu.com/S/02513")
        self._trace_tool("web_fetch", {"url": url})
        return self._with_result_trace("web_fetch", web_fetch_tool.run(url=url))

    def _think(self, args: list[str], think_enabled: str | bool) -> CommandResult:
        if not args:
            state = _think_label(think_enabled)
            return CommandResult(True, _msg(f"thinking state: {state}", f"thinking 当前状态：{state}"))
        value = args[0].lower()
        if value in {"on", "true", "1"}:
            return CommandResult(True, _msg(
                "thinking expanded: timestamps, elapsed time, model turns, tool calls and result previews are shown.",
                "thinking 已开启：会显示时间、耗时、模型回合、工具调用和结果摘要。",
            ), think="on")
        if value in {"compact", "summary", "folded"}:
            return CommandResult(True, _msg(
                "thinking compact: detailed trace is folded into a one-line summary. Use /trace after a task to expand the last trace.",
                "thinking compact：详细轨迹会收起成一行摘要。任务后输入 /trace 可展开上一轮轨迹。",
            ), think="compact")
        if value in {"off", "false", "0"}:
            return CommandResult(True, _msg("thinking disabled.", "thinking 已关闭。"), think="off")
        return CommandResult(True, _msg(
            "Usage: /think on | /think compact | /think off",
            "用法：/think on | /think compact | /think off",
        ))

    def _trace_mode(self, args: list[str], think_enabled: str | bool) -> CommandResult:
        if not args:
            state = "on" if _think_label(think_enabled) == "on" else "off"
            return CommandResult(True, _msg(f"trace state: {state}", f"trace 当前状态：{state}"))
        value = args[0].lower()
        if value in {"on", "true", "1"}:
            return CommandResult(True, _msg(
                "trace on: model turns, tool calls, arguments and result previews stay visible.",
                "trace on：模型回合、工具调用、参数和结果摘要会全部保留在终端。",
            ), think="on")
        if value in {"off", "false", "0"}:
            return CommandResult(True, _msg(
                "trace off: progress is folded into a live status and one completion summary (default).",
                "trace off：执行过程折叠为动态状态和一行完成摘要（默认）。",
            ), think="compact")
        return CommandResult(True, _msg("Usage: /trace on | /trace off", "用法：/trace on | /trace off"))

    def _lang(self, args: list[str]) -> CommandResult:
        if not args:
            configured_lang = os.environ.get(
                "TICKER_DOSSIER_LANG",
                os.environ.get("FINANCE_AGENT_LANG", "zh"),
            )
            return CommandResult(True, f"当前语言 / Current language: {configured_lang}")
        value = args[0].lower()
        if value not in {"zh", "cn", "en"}:
            return CommandResult(True, "用法：/lang zh 或 /lang en")
        os.environ["TICKER_DOSSIER_LANG"] = "en" if value == "en" else "zh"
        return CommandResult(True, "Language set to English." if value == "en" else "语言已切换为中文。")

    def _trace_tool(self, name: str, arguments: dict) -> None:
        if self.trace:
            self.trace("tool", f"{name} {_json_preview(arguments)}")

    def _with_result_trace(self, name: str, output: str) -> str:
        if self.trace:
            self.trace("tool result", f"{name} -> {_preview(output)}")
        return output


def _require_arg(args: list[str], usage: str) -> str:
    if not args:
        raise ValueError(f"用法：{usage}")
    return args[0]


def _require_many(args: list[str], usage: str) -> list[str]:
    if not args:
        raise ValueError(f"用法：{usage}")
    return args


def _period_arg(value: str) -> str:
    normalized = value.lower()
    return normalized if normalized in {"1mo", "3mo", "6mo", "1y", "2y", "5y"} else ""


def _preview(text: str, limit: int = 180) -> str:
    clean = " ".join(redact_sensitive_text(str(text)).split())
    if len(clean) <= limit:
        return clean
    return clean[:limit] + "..."


def _json_preview(value: object) -> str:
    try:
        raw = json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))
    except TypeError:
        raw = str(value)
    return redact_sensitive_text(raw)


def _safe_base_url(value: str) -> str:
    if not value:
        return _msg("not configured", "未配置")
    parsed = urlsplit(value)
    if not parsed.netloc:
        return value.split("?")[0]
    host = parsed.hostname or parsed.netloc
    port = ""
    try:
        if parsed.port:
            port = f":{parsed.port}"
    except ValueError:
        port = ""
    return f"{parsed.scheme}://{host}{port}{parsed.path.rstrip('/')}"


def _think_label(value: str | bool) -> str:
    if value is True:
        return "on"
    if value is False or value is None:
        return "off"
    normalized = str(value).lower()
    return normalized if normalized in {"on", "compact", "off"} else "compact"


def _msg(en: str, zh: str) -> str:
    return en if current_lang() == "en" else zh


def _wechat_mode_label() -> str:
    mode = os.environ.get("FINANCE_WECHAT_MODE", "").strip() or "auto"
    if os.environ.get("FINANCE_WECHAT_WEBHOOK"):
        return f"{mode}/webhook"
    if os.environ.get("FINANCE_WECHAT_RELAY_URL"):
        return f"{mode}/relay"
    return "dry-run"


def _is_number(value: str) -> bool:
    try:
        float(value)
    except ValueError:
        return False
    return True
