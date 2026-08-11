"""Tool adapters for scheduled briefs and portfolio updates."""
from __future__ import annotations

from dataclasses import replace
from functools import partial

from ticker_dossier.research.agent import FinanceResearchAgent
from ticker_dossier.integrations.scheduler import add_job, list_jobs, render_jobs, run_due_jobs
from ticker_dossier.integrations.wechat import send_markdown, send_text
from ticker_dossier.runtime.tools import Tool


def _schedule_wechat_brief(symbols: str, interval_minutes: int = 1440) -> str:
    job = add_job("wechat_brief", {"symbols": symbols}, interval_minutes)
    return f"Scheduled WeChat brief: {job.id} every {job.interval_minutes}m next={job.next_run_at}"


def _schedule_wechat_message(message: str, interval_minutes: int = 1440) -> str:
    job = add_job("wechat_message", {"message": message}, interval_minutes)
    return f"Scheduled WeChat message: {job.id} every {job.interval_minutes}m next={job.next_run_at}"


def _schedule_portfolio_mark(name: str = "default", interval_minutes: int = 1440) -> str:
    job = add_job("wechat_portfolio_mark", {"name": name}, interval_minutes)
    return f"Scheduled portfolio mark: {job.id} every {job.interval_minutes}m next={job.next_run_at}"


def _schedule_list() -> str:
    return render_jobs(list_jobs())


def _schedule_run_due() -> str:
    return _schedule_run_due_with_agent(None)


def _schedule_run_due_with_agent(agent: FinanceResearchAgent | None) -> str:
    results = run_due_jobs(partial(_run_job, finance_agent=agent))
    if not results:
        return "No due scheduled jobs."
    lines = ["Scheduled jobs executed:"]
    for job, result in results:
        lines.append(f"- {job.id} {job.kind}: {result}")
    return "\n".join(lines)


def _run_job(job, *, finance_agent: FinanceResearchAgent | None = None) -> str:  # noqa: ANN001
    if job.kind == "wechat_brief":
        agent = finance_agent or FinanceResearchAgent()
        symbols = job.payload.get("symbols", "")
        brief = agent.daily_brief(symbols)
        return send_markdown(brief, title="TickerDossier Brief").status
    if job.kind == "wechat_message":
        return send_text(job.payload.get("message", ""), title="TickerDossier").status
    if job.kind == "wechat_portfolio_mark":
        agent = finance_agent or FinanceResearchAgent()
        report = agent.mark_paper_portfolio(job.payload.get("name", "default"))
        return send_markdown(report, title="TickerDossier Portfolio").status
    return f"unsupported job kind: {job.kind}"


schedule_wechat_brief_tool = Tool(
    name="schedule_wechat_brief",
    description="定时把自选股简报发送到微信连接器；需要外部 cron 或 /schedule run 驱动。",
    parameters={
        "type": "object",
        "properties": {
            "symbols": {"type": "string", "description": "逗号或空格分隔的股票列表"},
            "interval_minutes": {"type": "integer", "description": "执行间隔分钟，默认 1440"},
        },
        "required": ["symbols"],
    },
    run=_schedule_wechat_brief,
)

schedule_wechat_message_tool = Tool(
    name="schedule_wechat_message",
    description="定时发送固定微信消息；需要外部 cron 或 /schedule run 驱动。",
    parameters={
        "type": "object",
        "properties": {
            "message": {"type": "string"},
            "interval_minutes": {"type": "integer"},
        },
        "required": ["message"],
    },
    run=_schedule_wechat_message,
)

schedule_portfolio_mark_tool = Tool(
    name="schedule_portfolio_mark",
    description="定时给纸面投资组合估值并发送微信连接器；需要外部 cron 或 /schedule run 驱动。",
    parameters={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "纸面组合账户名，默认 default"},
            "interval_minutes": {"type": "integer", "description": "执行间隔分钟，默认 1440"},
        },
    },
    run=_schedule_portfolio_mark,
)

schedule_list_tool = Tool(
    name="schedule_list",
    description="列出本地定时任务。",
    parameters={"type": "object", "properties": {}},
    run=_schedule_list,
)

schedule_run_due_tool = Tool(
    name="schedule_run_due",
    description="执行所有到期的本地定时任务。",
    parameters={"type": "object", "properties": {}},
    run=_schedule_run_due,
)


scheduler_tools = [
    schedule_wechat_brief_tool,
    schedule_wechat_message_tool,
    schedule_portfolio_mark_tool,
    schedule_list_tool,
    schedule_run_due_tool,
]


def build_scheduler_tools(agent: FinanceResearchAgent) -> list[Tool]:
    """Bind due-job execution to the shared application research service."""
    return [
        replace(
            template,
            run=(
                partial(_schedule_run_due_with_agent, agent)
                if template.name == "schedule_run_due"
                else template.run
            ),
        )
        for template in scheduler_tools
    ]
