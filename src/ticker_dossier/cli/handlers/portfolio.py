"""Paper-portfolio command handler and account selector parsing."""
from __future__ import annotations

from ticker_dossier.cli.command_types import HandlerResult

from ._shared import is_number


PORTFOLIO_HANDLER_METHODS = {"portfolio.manage": "handle_portfolio"}


class PortfolioCommandHandlers:
    def handle_portfolio(self, args: list[str], _think_enabled: str | bool) -> HandlerResult:
        args, name, account_explicit = _extract_account(args)
        if not args or args[0].lower() in {"status", "show", "list"}:
            if len(args) > 1 and not account_explicit:
                name = args[1]
            self._trace_tool("finance_show_paper_portfolio", {"name": name})
            return self._with_result_trace(
                "finance_show_paper_portfolio",
                self.finance.show_paper_portfolio(name),
            )
        action = args[0].lower()
        action_args = args[1:]
        if action == "locate":
            if action_args and not account_explicit:
                name = action_args[0]
            self._trace_tool("finance_locate_paper_portfolio", {"name": name})
            return self._with_result_trace(
                "finance_locate_paper_portfolio",
                self.finance.locate_paper_portfolio(name),
            )
        if action == "migrate":
            if action_args and not account_explicit:
                name = action_args[0]
            self._trace_tool("finance_migrate_paper_portfolio", {"name": name})
            return self._with_result_trace(
                "finance_migrate_paper_portfolio",
                self.finance.migrate_paper_portfolio(name),
            )
        if action == "trades":
            if action_args and not action_args[0].isdigit() and not account_explicit:
                name = action_args.pop(0)
            limit = int(action_args[0]) if action_args and action_args[0].isdigit() else 30
            self._trace_tool("finance_paper_trades", {"name": name, "limit": limit})
            return self._with_result_trace(
                "finance_paper_trades",
                self.finance.paper_trades(name, limit),
            )
        if action in {"pnl", "daily", "daily-pnl"}:
            if action_args and not action_args[0].isdigit() and not account_explicit:
                name = action_args.pop(0)
            limit = int(action_args[0]) if action_args and action_args[0].isdigit() else 30
            self._trace_tool("finance_paper_daily_pnl", {"name": name, "limit": limit})
            return self._with_result_trace(
                "finance_paper_daily_pnl",
                self.finance.paper_daily_pnl(name, limit),
            )
        if action in {"review", "diagnose"}:
            symbols = action_args
            self._trace_tool(
                "finance_review_paper_portfolio",
                {"symbols": symbols, "period": "6mo", "name": name},
            )
            return self._with_result_trace(
                "finance_review_paper_portfolio",
                self.finance.review_paper_portfolio(symbols, "6mo", name),
            )
        if action in {"init", "build"}:
            cash = 1_000_000.0
            symbols_start = 0
            if action_args and is_number(action_args[0]):
                cash = float(action_args[0])
                symbols_start = 1
            symbols = action_args[symbols_start:] or ["AAPL", "MSFT", "NVDA", "AMD", "GOOGL"]
            self._trace_tool("finance_build_paper_portfolio", {
                "symbols": symbols,
                "initial_cash": cash,
                "period": "1y",
                "name": name,
            })
            return self._with_result_trace(
                "finance_build_paper_portfolio",
                self.finance.build_paper_portfolio(symbols, cash, "1y", name=name),
            )
        if action in {"mark", "update"}:
            if action_args and not account_explicit:
                name = action_args[0]
            self._trace_tool("finance_mark_paper_portfolio", {"name": name})
            return self._with_result_trace(
                "finance_mark_paper_portfolio",
                self.finance.mark_paper_portfolio(name),
            )
        if action in {"rebalance", "rebuild"}:
            symbols = action_args or ["AAPL", "MSFT", "NVDA", "AMD", "GOOGL"]
            self._trace_tool(
                "finance_rebalance_paper_portfolio",
                {"symbols": symbols, "period": "1y", "name": name},
            )
            return self._with_result_trace(
                "finance_rebalance_paper_portfolio",
                self.finance.rebalance_paper_portfolio(symbols, "1y", name=name),
            )
        if action == "sell":
            if not action_args:
                return "用法：/portfolio sell AAPL [shares|all] [reason] [--account name]"
            symbol = action_args[0]
            shares: float | str = "all"
            reason_start = 1
            if len(action_args) > 1 and (
                action_args[1].lower() == "all" or is_number(action_args[1])
            ):
                shares = action_args[1]
                reason_start = 2
            if isinstance(shares, str) and is_number(shares):
                shares = float(shares)
            reason = " ".join(action_args[reason_start:]).strip() or "manual sell"
            self._trace_tool("finance_sell_paper_holding", {
                "symbol": symbol,
                "shares": shares,
                "name": name,
                "reason": reason,
            })
            return self._with_result_trace(
                "finance_sell_paper_holding",
                self.finance.sell_paper_holding(symbol, shares, name, reason),
            )
        return (
            "用法：/portfolio init [cash] [symbols...] [--account name] | /portfolio status [name] | "
            "/portfolio locate|migrate [name] | /portfolio mark [name] | "
            "/portfolio sell AAPL [shares|all] [reason] [--account name] | "
            "/portfolio trades [name] [limit] | /portfolio pnl [name] [limit] | "
            "/portfolio review|rebalance [symbols...] [--account name]"
        )


def _extract_account(args: list[str]) -> tuple[list[str], str, bool]:
    """Extract one unambiguous account selector while preserving legacy positionals."""
    cleaned: list[str] = []
    account = "default"
    explicit = False
    index = 0
    while index < len(args):
        token = args[index]
        if token == "--account":
            if explicit:
                raise ValueError("`--account` 只能指定一次。")
            if index + 1 >= len(args) or args[index + 1].startswith("--"):
                raise ValueError("用法：--account <name>")
            account = args[index + 1]
            explicit = True
            index += 2
            continue
        if token.startswith("--account="):
            if explicit:
                raise ValueError("`--account` 只能指定一次。")
            account = token.split("=", 1)[1].strip()
            if not account:
                raise ValueError("用法：--account=<name>")
            explicit = True
            index += 1
            continue
        cleaned.append(token)
        index += 1
    return cleaned, account, explicit
