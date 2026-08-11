"""Memory, prediction, learning, and scheduling command handlers."""
from __future__ import annotations

from ticker_dossier.cli.command_types import HandlerResult

from ._shared import is_number, msg, require_arg


WORKFLOW_HANDLER_METHODS = {
    "workflow.memory": "handle_memory",
    "workflow.remember": "handle_remember",
    "workflow.evolve": "handle_evolve",
    "workflow.predict": "handle_predict",
    "workflow.schedule": "handle_schedule",
    "workflow.learn_history": "handle_learn_history",
}


class WorkflowCommandHandlers:
    def handle_memory(self, args: list[str], _think_enabled: str | bool) -> HandlerResult:
        if not args or args[0].lower() in {"list", "show"}:
            limit = int(args[1]) if len(args) > 1 and args[1].isdigit() else 20
            self._trace_tool("finance_memory_list", {"limit": limit})
            return self._with_result_trace(
                "finance_memory_list",
                self.render_memories(limit),
            )
        action = args[0].lower()
        if action == "add":
            content = " ".join(args[1:]).strip()
            if not content:
                return msg("Usage: /memory add <note>", "用法：/memory add <记忆内容>")
            self._trace_tool(
                "finance_memory_add",
                {"category": "preference", "content": content},
            )
            path = self.add_memory(content, category="preference", source="cli")
            return self._with_result_trace("finance_memory_add", f"已写入金融记忆: {path}")
        return msg(
            "Usage: /memory list [limit] | /memory add <note>",
            "用法：/memory list [条数] | /memory add <记忆内容>",
        )

    def handle_remember(self, args: list[str], _think_enabled: str | bool) -> HandlerResult:
        memory = self.memory_factory()
        if not args:
            recalled = memory.recall().strip()
            return recalled or msg("Project memory is empty.", "项目长期记忆为空。")
        note = " ".join(args).strip()
        self._trace_tool("remember", {"note": note})
        path = memory.write(note)
        return self._with_result_trace("remember", f"已写入跨会话项目记忆: {path}\n- {note}")

    def handle_evolve(self, args: list[str], _think_enabled: str | bool) -> HandlerResult:
        text = " ".join(args).strip()
        if not text:
            return msg(
                "Usage: /evolve <finance correction, workflow, or task trace>",
                "用法：/evolve <金融纠错、流程经验或任务轨迹>",
            )
        learning = self.extract_learning(task=text)
        self._trace_tool("finance_evolve_from_trace", {"task": text})
        self.add_memory(
            learning,
            category="workflow",
            source="cli-evolve",
            confidence="high",
        )
        output = "\n".join([
            "Finance evolution completed.",
            "- memory: .finance_agent/finance_memory.jsonl",
            "- skill: unchanged (core finance-research-evolution remains stable)",
            "",
            learning,
        ])
        return self._with_result_trace("finance_evolve_from_trace", output)

    def handle_predict(self, args: list[str], _think_enabled: str | bool) -> HandlerResult:
        if not args or args[0].lower() in {"list", "show"}:
            limit = int(args[1]) if len(args) > 1 and args[1].isdigit() else 20
            self._trace_tool("prediction_list", {"limit": limit})
            return self._with_result_trace(
                "prediction_list",
                self.render_predictions(self.load_predictions(), limit),
            )
        action = args[0].lower()
        if action == "record":
            if len(args) < 3:
                return "用法：/predict record AAPL up [horizon_days] [confidence] [thesis]"
            symbol = args[1]
            direction = args[2]
            horizon = int(args[3]) if len(args) > 3 and args[3].isdigit() else 30
            confidence = float(args[4]) if len(args) > 4 and is_number(args[4]) else 0.5
            thesis_start = 5 if len(args) > 4 and is_number(args[4]) else 4
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
                record = self.record_prediction(
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
            output = self.render_prediction_record(record)
            return self._with_result_trace("prediction_record", output)
        if action == "eval":
            include_not_due = len(args) > 1 and args[1].lower() in {"all", "--all", "now"}
            self._trace_tool("prediction_evaluate", {"include_not_due": include_not_due})

            def get_historical_price(symbol: str, due_at: str) -> tuple[float, str]:
                period = self.evaluation_history_period(due_at)
                history = self.finance.provider.get_history(symbol, period, "1d")
                return self.select_due_close(history, due_at)

            def get_latest_price(symbol: str) -> tuple[float | None, str]:
                quote = self.finance.provider.get_quote(symbol)
                return quote.price, quote.as_of

            evaluated, card = self.evaluate_due_predictions(
                get_price=get_latest_price,
                get_historical_price=get_historical_price,
                include_not_due=include_not_due,
            )
            output = "\n".join([
                f"Evaluated predictions: {len(evaluated)}",
                self.render_predictions(evaluated, len(evaluated)) if evaluated else "",
                self.render_scorecard(card),
            ]).strip()
            return self._with_result_trace("prediction_evaluate", output)
        if action in {"learn", "scorecard", "review"}:
            save_to_memory = len(args) > 1 and args[1].lower() in {"save", "--save", "memory"}
            self._trace_tool("prediction_learn", {"save_to_memory": save_to_memory})
            output = self.render_learning_report(self.load_predictions())
            if save_to_memory:
                path = self.add_memory(
                    output,
                    category="workflow",
                    source="prediction-learn",
                    confidence="high",
                )
                output = f"{output}\n\nSaved to finance memory: {path}"
            return self._with_result_trace("prediction_learn", output)
        return (
            "用法：/predict record AAPL up [horizon_days] [confidence] [thesis] | "
            "/predict list | /predict eval [all] | /predict learn [save]"
        )

    def handle_schedule(self, args: list[str], _think_enabled: str | bool) -> HandlerResult:
        if not args or args[0].lower() in {"list", "show"}:
            self._trace_tool("schedule_list", {})
            return self._with_result_trace(
                "schedule_list",
                self.render_jobs(self.list_jobs()),
            )
        action = args[0].lower()
        if action == "brief":
            if len(args) < 2:
                return "用法：/schedule brief AAPL,MSFT,NVDA [interval_minutes]"
            symbols = args[1]
            interval = int(args[2]) if len(args) > 2 and args[2].isdigit() else 1440
            self._trace_tool(
                "schedule_wechat_brief",
                {"symbols": symbols, "interval_minutes": interval},
            )
            job = self.add_job("wechat_brief", {"symbols": symbols}, interval)
            return self._with_result_trace(
                "schedule_wechat_brief",
                f"Scheduled {job.id} next={job.next_run_at}",
            )
        if action == "message":
            message = " ".join(args[1:]).strip()
            if not message:
                return "用法：/schedule message <content>"
            self._trace_tool("schedule_wechat_message", {"message": message})
            job = self.add_job("wechat_message", {"message": message}, 1440)
            return self._with_result_trace(
                "schedule_wechat_message",
                f"Scheduled {job.id} next={job.next_run_at}",
            )
        if action == "portfolio":
            name = args[1] if len(args) > 1 else "default"
            interval = int(args[2]) if len(args) > 2 and args[2].isdigit() else 1440
            self._trace_tool(
                "schedule_portfolio_mark",
                {"name": name, "interval_minutes": interval},
            )
            job = self.add_job("wechat_portfolio_mark", {"name": name}, interval)
            return self._with_result_trace(
                "schedule_portfolio_mark",
                f"Scheduled {job.id} next={job.next_run_at}",
            )
        if action == "run":
            self._trace_tool("schedule_run_due", {})
            results = self.run_due_jobs(self._run_scheduled_job)
            if not results:
                return self._with_result_trace("schedule_run_due", "No due scheduled jobs.")
            lines = ["Scheduled jobs executed:"]
            for job, result in results:
                lines.append(f"- {job.id} {job.kind}: {result}")
            return self._with_result_trace("schedule_run_due", "\n".join(lines))
        return (
            "用法：/schedule list | /schedule brief AAPL,MSFT,NVDA [interval_minutes] | "
            "/schedule portfolio [name] [interval_minutes] | /schedule message <content> | /schedule run"
        )

    def handle_learn_history(self, args: list[str], _think_enabled: str | bool) -> HandlerResult:
        symbol = require_arg(args, "/learn-history AAPL [period] [horizon_days]")
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
            return self.send_markdown(brief, title="TickerDossier Brief").status
        if job.kind == "wechat_message":
            return self.send_text(job.payload.get("message", ""), title="TickerDossier").status
        if job.kind == "wechat_portfolio_mark":
            report = self.finance.mark_paper_portfolio(job.payload.get("name", "default"))
            return self.send_markdown(report, title="TickerDossier Portfolio").status
        return f"unsupported job kind: {job.kind}"
