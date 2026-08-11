"""Tool adapters for finance memory and learning workflows."""
from __future__ import annotations

from dataclasses import replace
from functools import partial
from typing import TYPE_CHECKING

from ticker_dossier.research.evolution import add_memory, extract_learning, render_memories
from ticker_dossier.research.predictions import (
    evaluation_history_period,
    evaluate_due_predictions,
    load_predictions,
    render_learning_report,
    render_prediction_record,
    render_predictions,
    render_scorecard,
    select_due_close,
)
from ticker_dossier.skills import generate_skill
from ticker_dossier.runtime.tools import Tool

if TYPE_CHECKING:
    from ticker_dossier.research.agent import FinanceResearchAgent


def _default_finance_agent() -> FinanceResearchAgent:
    """Build the legacy module-level fallback through the composition root."""
    from ticker_dossier.bootstrap import build_finance_research_agent

    return build_finance_research_agent()


def _finance_memory_add(
    content: str,
    category: str = "preference",
    source: str = "user",
    symbol: str = "",
    confidence: str = "medium",
) -> str:
    path = add_memory(content, category=category, source=source, symbol=symbol, confidence=confidence)
    return f"已写入金融记忆: {path}"


def _finance_memory_list(limit: int = 20) -> str:
    return render_memories(limit)


def _finance_evolve_from_trace(
    task: str,
    trace: str,
    answer: str = "",
    skill_name: str = "",
    overwrite: bool = True,
) -> str:
    learning = extract_learning(task=task, answer=answer, trace=trace)
    add_memory(learning, category="workflow", source="trace2skill", confidence="high")
    if not skill_name:
        return "\n".join([
            "Finance evolution completed.",
            f"- memory: .finance_agent/finance_memory.jsonl",
            "- skill: unchanged (core finance-research-evolution remains stable)",
            "",
            learning,
        ])
    result = generate_skill(
        task=task,
        trace=learning + "\n" + trace,
        skill_name=skill_name,
        description=(
            "当用户希望把金融研究纠错、数据源核验、风险边界、标的解析经验"
            "沉淀为可复用流程时使用。"
        ),
        overwrite=overwrite,
    )
    return "\n".join([
        "Finance evolution completed.",
        f"- memory: .finance_agent/finance_memory.jsonl",
        f"- skill: {result.path}",
        "",
        result.content,
    ])


def _prediction_record(
    symbol: str,
    direction: str,
    horizon_days: int = 30,
    confidence: float | None = None,
    thesis: str = "",
) -> str:
    agent = _default_finance_agent()
    try:
        return _prediction_record_with_agent(
            agent,
            symbol,
            direction,
            horizon_days,
            confidence,
            thesis,
        )
    finally:
        agent.close()


def _prediction_record_with_agent(
    agent: FinanceResearchAgent,
    symbol: str,
    direction: str,
    horizon_days: int = 30,
    confidence: float | None = None,
    thesis: str = "",
) -> str:
    record = agent.create_prediction_record(
        symbol=symbol,
        direction=direction,
        horizon_days=horizon_days,
        signal_strength=confidence,
        signal_source="model_supplied",
        use_calibration=True,
        thesis=thesis or "manual prediction",
    )
    return render_prediction_record(record)


def _prediction_list(limit: int = 20) -> str:
    return render_predictions(load_predictions(), limit)


def _prediction_evaluate(include_not_due: bool = False) -> str:
    agent = _default_finance_agent()
    try:
        return _prediction_evaluate_with_agent(agent, include_not_due)
    finally:
        agent.close()


def _prediction_evaluate_with_agent(
    agent: FinanceResearchAgent,
    include_not_due: bool = False,
) -> str:

    def get_historical_price(symbol: str, due_at: str) -> tuple[float, str]:
        period = evaluation_history_period(due_at)
        history = agent.provider.get_history(symbol, period, "1d")
        return select_due_close(history, due_at)

    def get_latest_price(symbol: str) -> tuple[float | None, str]:
        quote = agent.provider.get_quote(symbol)
        return quote.price, quote.as_of

    evaluated, card = evaluate_due_predictions(
        get_price=get_latest_price,
        get_historical_price=get_historical_price,
        include_not_due=include_not_due,
    )
    lines = [f"Evaluated predictions: {len(evaluated)}"]
    if evaluated:
        lines.append(render_predictions(evaluated, len(evaluated)))
    lines.append(render_scorecard(card))
    return "\n".join(lines)


def _prediction_learn(save_to_memory: bool = False) -> str:
    report = render_learning_report(load_predictions())
    if save_to_memory:
        path = add_memory(report, category="workflow", source="prediction-learn", confidence="high")
        return f"{report}\n\nSaved to finance memory: {path}"
    return report


finance_memory_add_tool = Tool(
    name="finance_memory_add",
    description="保存金融研究偏好、纠错、数据源经验、风险规则或策略复盘到本地记忆。",
    parameters={
        "type": "object",
        "properties": {
            "content": {"type": "string"},
            "category": {"type": "string", "description": "preference/correction/source/risk/workflow/symbol/strategy"},
            "source": {"type": "string"},
            "symbol": {"type": "string"},
            "confidence": {"type": "string", "description": "low/medium/high"},
        },
        "required": ["content"],
    },
    run=_finance_memory_add,
)

finance_memory_list_tool = Tool(
    name="finance_memory_list",
    description="列出本地金融研究记忆。",
    parameters={
        "type": "object",
        "properties": {"limit": {"type": "integer", "description": "最多返回条数"}},
    },
    run=_finance_memory_list,
)

finance_evolve_from_trace_tool = Tool(
    name="finance_evolve_from_trace",
    description="从金融研究任务轨迹中提炼记忆并生成/更新金融自进化 Skill。",
    parameters={
        "type": "object",
        "properties": {
            "task": {"type": "string"},
            "trace": {"type": "string"},
            "answer": {"type": "string"},
            "skill_name": {"type": "string"},
            "overwrite": {"type": "boolean"},
        },
        "required": ["task", "trace"],
    },
    run=_finance_evolve_from_trace,
)

prediction_record_tool = Tool(
    name="prediction_record",
    description=(
        "记录一次股票方向预测，保存 baseline、期限和 thesis，并用严格时间顺序的"
        "walk-forward 样本尝试校准。confidence 只是可选主观信号强度，不是统计概率。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "symbol": {"type": "string"},
            "direction": {"type": "string", "description": "up/down/neutral"},
            "horizon_days": {"type": "integer"},
            "confidence": {
                "type": "number",
                "description": "可选主观信号强度 0-1；不得声称为历史校准概率",
            },
            "thesis": {"type": "string"},
        },
        "required": ["symbol", "direction"],
    },
    run=_prediction_record,
)

prediction_list_tool = Tool(
    name="prediction_list",
    description="查看历史预测账本。",
    parameters={"type": "object", "properties": {"limit": {"type": "integer"}}},
    run=_prediction_list,
)

prediction_evaluate_tool = Tool(
    name="prediction_evaluate",
    description="对到期预测做事后评分；可选择 include_not_due 立即评估未到期预测用于演示。",
    parameters={"type": "object", "properties": {"include_not_due": {"type": "boolean"}}},
    run=_prediction_evaluate,
)

prediction_learn_tool = Tool(
    name="prediction_learn",
    description="根据已评分预测生成复盘报告，量化哪些方向、估计证据类型和 thesis 表现较弱，可保存到金融记忆。",
    parameters={"type": "object", "properties": {"save_to_memory": {"type": "boolean"}}},
    run=_prediction_learn,
)


evolution_tools = [
    finance_memory_add_tool,
    finance_memory_list_tool,
    finance_evolve_from_trace_tool,
    prediction_record_tool,
    prediction_list_tool,
    prediction_evaluate_tool,
    prediction_learn_tool,
]


def build_evolution_tools(agent: FinanceResearchAgent) -> list[Tool]:
    """Bind prediction operations to the application-owned research service."""
    overrides = {
        "prediction_record": partial(_prediction_record_with_agent, agent),
        "prediction_evaluate": partial(_prediction_evaluate_with_agent, agent),
    }
    return [
        replace(template, run=overrides.get(template.name, template.run))
        for template in evolution_tools
    ]
