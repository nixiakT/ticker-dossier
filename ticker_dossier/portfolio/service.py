"""Safe paper-portfolio storage, migration, and mutation service."""
from __future__ import annotations

import json
import math
import os
import shutil
import uuid
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from ticker_dossier.market_data.models import Quote, StockSnapshot

from .models import (
    CURRENT_SCHEMA_VERSION,
    AccountLocations,
    CandidateScore,
    Holding,
    PortfolioAccount,
    PortfolioMigration,
    PortfolioValuation,
)
from .rendering import (
    _daily_pnl_rows,
    _date_key,
    _empty_daily_row,
    _money,
    _realized_pnl,
    _storage_warning_lines,
    _valuation_price_status,
    portfolio_value,
    render_account,
    render_daily_pnl,
    render_portfolio_review,
    render_recommendation,
    render_transactions,
)
from .scoring import (
    _clamp,
    _component_text,
    _holding_diagnosis,
    _is_weak_holding,
    _normalize_holding_weights,
    _num,
    _ratio_pct,
    _review_basis,
    _score_snapshot,
    _score_verdict,
    _source_adjustment,
    _target_weights,
    score_candidates,
)

# Keep legacy direct imports available without coupling the storage/mutation
# implementation back to pure scoring and rendering internals.
_COMPATIBILITY_EXPORTS = (
    _component_text,
    _daily_pnl_rows,
    _date_key,
    _empty_daily_row,
    _holding_diagnosis,
    _is_weak_holding,
    _money,
    _num,
    _ratio_pct,
    _realized_pnl,
    _review_basis,
    _score_snapshot,
    _score_verdict,
    _source_adjustment,
    _storage_warning_lines,
    _valuation_price_status,
    render_account,
    render_daily_pnl,
    render_portfolio_review,
    render_recommendation,
    render_transactions,
)


LEGACY_PORTFOLIO_DIR = Path.cwd() / ".finance_agent"
DEFAULT_PORTFOLIO_DIR = Path(
    os.getenv("FINANCE_PORTFOLIO_DIR", Path.home() / ".finance-agent" / "portfolios")
).expanduser()
PORTFOLIO_DIR = DEFAULT_PORTFOLIO_DIR
_IMPORTED_PORTFOLIO_DIR = PORTFOLIO_DIR
DEFAULT_ACCOUNT = "default"


class PortfolioError(RuntimeError):
    """Base error for safe paper-portfolio storage operations."""


class PortfolioConflictError(PortfolioError):
    """Raised when a write would be ambiguous across two account files."""


class PortfolioNotFoundError(PortfolioError):
    """Raised when a read-only operation requires an existing account."""


def account_path(name: str = DEFAULT_ACCOUNT, base_dir: Path | None = None) -> Path:
    if base_dir is not None:
        return _account_path_in(base_dir, name)
    return inspect_account_locations(name).active_path


def inspect_account_locations(name: str = DEFAULT_ACCOUNT) -> AccountLocations:
    """Inspect both supported locations without creating or copying anything."""
    user_path = _account_path_in(_configured_portfolio_dir(), name)
    workspace_path = _account_path_in(LEGACY_PORTFOLIO_DIR, name)
    same_path = user_path == workspace_path
    return AccountLocations(
        name=_clean_account_name(name),
        user_path=user_path,
        workspace_path=workspace_path,
        user_exists=user_path.exists(),
        workspace_exists=False if same_path else workspace_path.exists(),
    )


def render_account_locations(name: str = DEFAULT_ACCOUNT) -> str:
    locations = inspect_account_locations(name)
    lines = [
        f"# 纸面账户位置：{locations.name}",
        "",
        f"- 用户级: `{locations.user_path}` ({'存在' if locations.user_exists else '不存在'})",
        f"- Workspace: `{locations.workspace_path}` ({'存在' if locations.workspace_exists else '不存在'})",
        f"- 当前读取: `{locations.active_path}`",
    ]
    if locations.conflict:
        lines.extend([
            "- 状态: **冲突**。只读命令暂时使用用户级账户；所有写命令均会拒绝，避免改错账本。",
            "- 处理: 请先分别备份并核对两份 JSON；目标文件存在时 `/portfolio migrate` 不会覆盖或合并。",
        ])
    elif locations.workspace_exists:
        lines.extend([
            "- 状态: 正在兼容读取 workspace 账户，未发生自动复制。",
            f"- 建议: 确认后执行 `/portfolio migrate {locations.name}` 迁至用户级目录。",
        ])
    elif locations.user_exists:
        lines.append("- 状态: 用户级账户位置正常。")
    else:
        lines.append("- 状态: 尚未创建该账户。")
    return "\n".join(lines)


def migrate_account(name: str = DEFAULT_ACCOUNT) -> PortfolioMigration:
    """Move one workspace account to the user directory without overwriting or merging."""
    locations = inspect_account_locations(name)
    if locations.user_exists:
        raise PortfolioConflictError(
            f"迁移已拒绝：目标 `{locations.user_path}` 已存在；不会覆盖或合并。"
            "请先备份并人工核对两份账户，再将冲突文件改名后重试。"
        )
    if not locations.workspace_exists:
        raise PortfolioNotFoundError(
            f"迁移已拒绝：workspace 中不存在账户 `{locations.name}`（{locations.workspace_path}）。"
        )

    source_data = json.loads(locations.workspace_path.read_text(encoding="utf-8"))
    account = _account_from_data(source_data, locations.name, locations.workspace_path)
    account.schema_version = CURRENT_SCHEMA_VERSION
    account.origin = "workspace"
    locations.user_path.parent.mkdir(parents=True, exist_ok=True)

    backup = _migration_backup_path(locations.workspace_path)
    backup.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(locations.workspace_path), str(backup))
    try:
        if locations.user_path.exists():
            raise PortfolioConflictError(
                f"迁移已拒绝：目标 `{locations.user_path}` 在迁移期间出现；未覆盖该文件。"
            )
        _write_account_exclusive(account, locations.user_path)
    except Exception:
        if not locations.workspace_path.exists() and backup.exists():
            shutil.move(str(backup), str(locations.workspace_path))
        raise
    return PortfolioMigration(locations.name, locations.workspace_path, locations.user_path, backup)


def render_portfolio_migration(result: PortfolioMigration) -> str:
    return "\n".join([
        f"# 纸面账户迁移完成：{result.name}",
        "",
        f"- 新位置: `{result.destination}`",
        f"- 原文件恢复备份: `{result.recovery_backup}`",
        "- 未覆盖、未合并任何已有目标账户。",
    ])


def value_account_read_only(
    account: PortfolioAccount,
    snapshots: list[StockSnapshot],
) -> PortfolioValuation:
    """Return an in-memory mark while preserving the loaded ledger byte-for-byte."""
    snapshot_by_symbol = {snapshot.symbol.upper(): snapshot for snapshot in snapshots}
    marked = replace(
        account,
        holdings=[replace(holding) for holding in account.holdings],
        transactions=list(account.transactions),
        history=list(account.history),
        storage_warnings=list(account.storage_warnings),
    )
    fresh: list[str] = []
    stale: list[str] = []
    price_as_of: dict[str, str] = {}
    price_sources: dict[str, str] = {}
    observed_times: list[str] = []
    for holding in marked.holdings:
        symbol = holding.symbol.upper()
        snapshot = snapshot_by_symbol.get(symbol)
        quote = snapshot.quote if snapshot else None
        if quote and quote.price is not None and quote.price > 0:
            holding.last_price = float(quote.price)
            holding.market_value = holding.shares * holding.last_price
            as_of = quote.as_of or snapshot.fetched_at or "未知"
            price_as_of[symbol] = as_of
            price_sources[symbol] = quote.source or "未知来源"
            fresh.append(symbol)
            if as_of != "未知":
                observed_times.append(as_of)
        else:
            holding.market_value = holding.shares * holding.last_price
            price_as_of[symbol] = account.updated_at or "未知"
            price_sources[symbol] = "账户缓存"
            stale.append(symbol)
    marked.holdings = _normalize_holding_weights(marked.holdings, marked.cash)
    return PortfolioValuation(
        account=marked,
        as_of=max(observed_times) if observed_times else (account.updated_at or "未知"),
        fresh_symbols=tuple(fresh),
        stale_symbols=tuple(stale),
        price_as_of=price_as_of,
        price_sources=price_sources,
    )


def create_account(
    *,
    initial_cash: float = 1_000_000.0,
    name: str = DEFAULT_ACCOUNT,
    overwrite: bool = False,
    base_dir: Path | None = None,
) -> PortfolioAccount:
    _ensure_safe_write(name, base_dir)
    path = account_path(name, base_dir)
    if path.exists() and not overwrite:
        return load_account(name, base_dir, for_write=True)
    if path.exists() and overwrite:
        _backup_account(path)
    now = _iso_now()
    account = PortfolioAccount(
        name=_clean_account_name(name),
        initial_cash=max(float(initial_cash), 0.0),
        cash=max(float(initial_cash), 0.0),
        created_at=now,
        updated_at=now,
        account_id=str(uuid.uuid4()),
        origin=_origin_for_path(path),
        storage_path=str(path),
    )
    save_account(account, base_dir)
    return account


def load_account(
    name: str = DEFAULT_ACCOUNT,
    base_dir: Path | None = None,
    *,
    create_if_missing: bool = True,
    for_write: bool = False,
) -> PortfolioAccount:
    locations = inspect_account_locations(name) if base_dir is None else None
    if for_write:
        _ensure_safe_write(name, base_dir)
    path = account_path(name, base_dir)
    if not path.exists():
        if not create_if_missing:
            raise PortfolioNotFoundError(
                f"纸面账户 `{_clean_account_name(name)}` 不存在；请先执行 `/portfolio init --account {_clean_account_name(name)}`。"
            )
        return create_account(name=name, base_dir=base_dir)
    data = json.loads(path.read_text(encoding="utf-8"))
    account = _account_from_data(data, name, path)
    if locations and locations.conflict:
        account.storage_warnings.append(
            "检测到用户级与 workspace 两份同名账户；本次只读使用用户级文件，所有写操作已锁定。"
            "请执行 `/portfolio locate " + locations.name + "` 查看位置。"
        )
    elif locations and locations.workspace_exists:
        account.storage_warnings.append(
            "当前兼容读取 workspace 账户，未自动迁移；可先 `/portfolio locate "
            + locations.name + "`，确认后再显式迁移。"
        )
    return account


def save_account(account: PortfolioAccount, base_dir: Path | None = None) -> Path:
    _ensure_safe_write(account.name, base_dir)
    path = account_path(account.name, base_dir)
    account.schema_version = CURRENT_SCHEMA_VERSION
    account.account_id = account.account_id or str(uuid.uuid4())
    account.origin = account.origin or _origin_for_path(path)
    account.storage_path = str(path)
    _write_account(account, path)
    return path


def _backup_account(path: Path) -> Path:
    backup_dir = path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    backup = backup_dir / f"{path.stem}_{stamp}{path.suffix}"
    shutil.copy2(path, backup)
    return backup


def construct_portfolio(
    snapshots: list[StockSnapshot],
    *,
    initial_cash: float = 1_000_000.0,
    max_positions: int = 5,
    max_weight: float = 0.30,
    min_score: float = 40.0,
    cash_reserve: float = 0.10,
    name: str = DEFAULT_ACCOUNT,
    overwrite: bool = True,
    base_dir: Path | None = None,
) -> tuple[PortfolioAccount, list[CandidateScore]]:
    scores = score_candidates(snapshots)
    account = create_account(initial_cash=initial_cash, name=name, overwrite=overwrite, base_dir=base_dir)
    selected = [
        score for score in scores
        if score.score >= min_score and score.price is not None and score.price > 0
    ][:max(max_positions, 1)]

    investable = account.initial_cash * (1 - _clamp(cash_reserve, 0.0, 0.8))
    weights = _target_weights(selected, max_weight)
    holdings: list[Holding] = []
    used_cash = 0.0
    transactions: list[dict[str, Any]] = []
    for score, weight in zip(selected, weights, strict=False):
        assert score.price is not None
        market_value = investable * weight
        shares = math.floor(market_value / score.price)
        if shares <= 0:
            continue
        actual_value = shares * score.price
        used_cash += actual_value
        holdings.append(Holding(
            symbol=score.symbol,
            shares=float(shares),
            avg_cost=float(score.price),
            last_price=float(score.price),
            market_value=float(actual_value),
            weight=0.0,
            thesis=score.thesis,
        ))
        transactions.append(_transaction(
            action="BUY",
            symbol=score.symbol,
            shares=float(shares),
            price=float(score.price),
            reason=f"initial build: {score.thesis}",
        ))

    account.cash = account.initial_cash - used_cash
    account.holdings = _normalize_holding_weights(holdings, account.cash)
    account.transactions = transactions
    account.updated_at = _iso_now()
    account.history.append(_history_row(account, event="construct", notes="initial paper portfolio"))
    save_account(account, base_dir)
    return account, scores


def rebalance_portfolio(
    snapshots: list[StockSnapshot],
    *,
    name: str = DEFAULT_ACCOUNT,
    max_positions: int = 5,
    max_weight: float = 0.30,
    min_score: float = 40.0,
    cash_reserve: float = 0.10,
    base_dir: Path | None = None,
) -> tuple[PortfolioAccount, list[CandidateScore]]:
    account = load_account(name, base_dir, for_write=True)
    latest_prices = {
        snapshot.symbol: snapshot.quote.price
        for snapshot in snapshots
        if snapshot.quote.price is not None and snapshot.quote.price > 0
    }
    for holding in account.holdings:
        latest_price = latest_prices.get(holding.symbol)
        if latest_price is not None:
            holding.last_price = float(latest_price)
            holding.market_value = holding.shares * float(latest_price)
    account.holdings = _normalize_holding_weights(account.holdings, account.cash)
    old_holdings = {holding.symbol: replace(holding) for holding in account.holdings}
    total = portfolio_value(account)
    scores = score_candidates(snapshots)
    selected = [
        score for score in scores
        if score.score >= min_score and score.price is not None and score.price > 0
    ][:max(max_positions, 1)]
    investable = total * (1 - _clamp(cash_reserve, 0.0, 0.8))
    weights = _target_weights(selected, max_weight)
    holdings: list[Holding] = []
    used_cash = 0.0
    for score, weight in zip(selected, weights, strict=False):
        assert score.price is not None
        market_value = investable * weight
        shares = math.floor(market_value / score.price)
        if shares <= 0:
            continue
        actual_value = shares * score.price
        used_cash += actual_value
        holdings.append(Holding(
            symbol=score.symbol,
            shares=float(shares),
            avg_cost=_rebalance_avg_cost(old_holdings.get(score.symbol), float(shares), float(score.price)),
            last_price=float(score.price),
            market_value=float(actual_value),
            weight=0.0,
            thesis=score.thesis,
        ))
    _record_rebalance_transactions(account, old_holdings, holdings)
    account.cash = total - used_cash
    account.holdings = _normalize_holding_weights(holdings, account.cash)
    account.updated_at = _iso_now()
    account.history.append(_history_row(account, event="rebalance", notes="paper portfolio rebalance"))
    save_account(account, base_dir)
    return account, scores


def mark_to_market(
    *,
    get_quote: Callable[[str], Quote],
    name: str = DEFAULT_ACCOUNT,
    base_dir: Path | None = None,
    notes: str = "",
) -> PortfolioAccount:
    account = load_account(name, base_dir, for_write=True)
    prices: dict[str, float] = {}
    for holding in account.holdings:
        quote = get_quote(holding.symbol)
        if quote.price is not None and quote.price > 0:
            prices[holding.symbol] = float(quote.price)
            holding.last_price = float(quote.price)
            holding.market_value = holding.shares * float(quote.price)
    account.holdings = _normalize_holding_weights(account.holdings, account.cash)
    account.updated_at = _iso_now()
    account.history.append(_history_row(account, event="mark", notes=notes or "mark to market", prices=prices))
    save_account(account, base_dir)
    return account


def sell_holding(
    symbol: str,
    *,
    shares: float | str = "all",
    price: float | None = None,
    reason: str = "manual sell",
    name: str = DEFAULT_ACCOUNT,
    base_dir: Path | None = None,
) -> PortfolioAccount:
    account = load_account(name, base_dir, for_write=True)
    target = symbol.upper()
    sell_all = str(shares).lower() == "all"
    requested_shares: float | None = None
    if not sell_all:
        try:
            requested_shares = float(shares)
        except (TypeError, ValueError):
            account.history.append(_history_row(account, event="sell_failed", notes=f"invalid shares: {shares}"))
            save_account(account, base_dir)
            return account
        if requested_shares <= 0:
            account.history.append(_history_row(account, event="sell_failed", notes=f"invalid shares: {shares}"))
            save_account(account, base_dir)
            return account
    remaining: list[Holding] = []
    sold = False
    for holding in account.holdings:
        if holding.symbol.upper() != target:
            remaining.append(holding)
            continue
        sell_shares = holding.shares if sell_all else min(requested_shares or 0.0, holding.shares)
        if sell_shares <= 0:
            remaining.append(holding)
            continue
        sell_price = float(price if price is not None and price > 0 else holding.last_price)
        if sell_price <= 0:
            remaining.append(holding)
            continue
        proceeds = sell_shares * sell_price
        realized = (sell_price - holding.avg_cost) * sell_shares
        account.cash += proceeds
        account.transactions.append(_transaction(
            action="SELL",
            symbol=holding.symbol,
            shares=float(sell_shares),
            price=sell_price,
            reason=reason,
            realized_pnl=realized,
        ))
        sold = True
        left = holding.shares - sell_shares
        if left > 0:
            holding.shares = left
            holding.last_price = sell_price
            holding.market_value = left * sell_price
            remaining.append(holding)
    if not sold:
        account.history.append(_history_row(account, event="sell_failed", notes=f"{target} not held"))
    else:
        account.holdings = _normalize_holding_weights(remaining, account.cash)
        account.updated_at = _iso_now()
        account.history.append(_history_row(account, event="sell", notes=reason))
    save_account(account, base_dir)
    return account


def _record_rebalance_transactions(
    account: PortfolioAccount,
    old_holdings: dict[str, Holding],
    new_holdings: list[Holding],
) -> None:
    new_by_symbol = {holding.symbol: holding for holding in new_holdings}
    for symbol, old in old_holdings.items():
        new = new_by_symbol.get(symbol)
        new_shares = new.shares if new else 0.0
        if old.shares > new_shares:
            sold = old.shares - new_shares
            price = new.last_price if new else old.last_price
            account.transactions.append(_transaction(
                action="SELL",
                symbol=symbol,
                shares=sold,
                price=price,
                reason="rebalance reduce/exit",
                realized_pnl=(price - old.avg_cost) * sold,
            ))
    for new in new_holdings:
        old = old_holdings.get(new.symbol)
        old_shares = old.shares if old else 0.0
        if new.shares > old_shares:
            bought = new.shares - old_shares
            account.transactions.append(_transaction(
                action="BUY",
                symbol=new.symbol,
                shares=bought,
                price=new.last_price,
                reason=f"rebalance add: {new.thesis}",
            ))


def _rebalance_avg_cost(old: Holding | None, new_shares: float, trade_price: float) -> float:
    if old is None or old.shares <= 0 or new_shares <= 0:
        return trade_price
    if new_shares <= old.shares:
        return old.avg_cost
    bought = new_shares - old.shares
    return ((old.avg_cost * old.shares) + (trade_price * bought)) / new_shares


def _transaction(
    *,
    action: str,
    symbol: str,
    shares: float,
    price: float,
    reason: str,
    realized_pnl: float = 0.0,
) -> dict[str, Any]:
    return {
        "as_of": _iso_now(),
        "action": action,
        "symbol": symbol,
        "shares": shares,
        "price": price,
        "amount": shares * price,
        "realized_pnl": realized_pnl,
        "reason": reason,
    }


def _history_row(
    account: PortfolioAccount,
    *,
    event: str,
    notes: str = "",
    prices: dict[str, float] | None = None,
) -> dict[str, Any]:
    total = portfolio_value(account)
    ret = (total - account.initial_cash) / account.initial_cash * 100 if account.initial_cash else 0.0
    return {
        "as_of": _iso_now(),
        "event": event,
        "total_value": total,
        "cash": account.cash,
        "return_pct": ret,
        "positions": [
            {
                "symbol": holding.symbol,
                "shares": holding.shares,
                "price": holding.last_price,
                "market_value": holding.market_value,
                "weight": holding.weight,
            }
            for holding in account.holdings
        ],
        "prices": prices or {},
        "notes": notes,
    }


def _clean_account_name(name: str) -> str:
    return "".join(
        ch for ch in (name or DEFAULT_ACCOUNT) if ch.isalnum() or ch in {"-", "_"}
    ) or DEFAULT_ACCOUNT


def _account_path_in(directory: Path, name: str) -> Path:
    return directory / f"portfolio_{_clean_account_name(name)}.json"


def _ensure_safe_write(name: str, base_dir: Path | None) -> None:
    if base_dir is not None:
        return
    locations = inspect_account_locations(name)
    if locations.conflict:
        raise PortfolioConflictError(
            f"写入已拒绝：账户 `{locations.name}` 同时存在于用户级与 workspace 目录。"
            f"请先执行 `/portfolio locate {locations.name}` 并人工核对；系统不会静默选择、覆盖或合并。"
        )


def _origin_for_path(path: Path) -> str:
    if path == _account_path_in(
        _configured_portfolio_dir(),
        path.stem.removeprefix("portfolio_"),
    ):
        return "user"
    if path == _account_path_in(LEGACY_PORTFOLIO_DIR, path.stem.removeprefix("portfolio_")):
        return "workspace"
    return "custom"


def _configured_portfolio_dir() -> Path:
    """Resolve late-loaded env while preserving an explicit in-process override."""
    if PORTFOLIO_DIR != _IMPORTED_PORTFOLIO_DIR:
        return PORTFOLIO_DIR
    configured = os.getenv("FINANCE_PORTFOLIO_DIR", "").strip()
    if configured:
        return Path(configured).expanduser()
    return PORTFOLIO_DIR


def _legacy_account_id(data: dict[str, Any]) -> str:
    fingerprint = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"ticker-dossier:portfolio:{fingerprint}"))


def _account_from_data(data: dict[str, Any], name: str, path: Path) -> PortfolioAccount:
    holdings = [Holding(**row) for row in data.get("holdings", [])]
    try:
        schema_version = int(data.get("schema_version") or 1)
    except (TypeError, ValueError):
        schema_version = 1
    return PortfolioAccount(
        name=str(data.get("name") or _clean_account_name(name)),
        initial_cash=float(data.get("initial_cash") or 0),
        cash=float(data.get("cash") or 0),
        holdings=holdings,
        transactions=list(data.get("transactions", [])),
        history=list(data.get("history", [])),
        created_at=str(data.get("created_at") or ""),
        updated_at=str(data.get("updated_at") or ""),
        schema_version=schema_version,
        account_id=str(data.get("account_id") or _legacy_account_id(data)),
        origin=str(data.get("origin") or _origin_for_path(path)),
        storage_path=str(path),
    )


def _account_payload(account: PortfolioAccount) -> dict[str, Any]:
    return {
        "schema_version": CURRENT_SCHEMA_VERSION,
        "account_id": account.account_id or str(uuid.uuid4()),
        "origin": account.origin,
        "name": account.name,
        "initial_cash": account.initial_cash,
        "cash": account.cash,
        "holdings": [holding.__dict__ for holding in account.holdings],
        "transactions": account.transactions[-1000:],
        "history": account.history[-500:],
        "created_at": account.created_at,
        "updated_at": account.updated_at,
    }


def _write_account(account: PortfolioAccount, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(_account_json(account), encoding="utf-8")
    temporary.replace(path)


def _write_account_exclusive(account: PortfolioAccount, path: Path) -> None:
    """Publish a complete account only when the destination is still absent."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(_account_json(account))
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError as exc:
            raise PortfolioConflictError(
                f"迁移已拒绝：目标 `{path}` 在迁移期间出现；未覆盖该文件。"
            ) from exc
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def _account_json(account: PortfolioAccount) -> str:
    return json.dumps(_account_payload(account), ensure_ascii=False, indent=2)


def _migration_backup_path(source: Path) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    return source.parent / "backups" / f"{source.stem}_migrated_{stamp}{source.suffix}"


def _iso_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
