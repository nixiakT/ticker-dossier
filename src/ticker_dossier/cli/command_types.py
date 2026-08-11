"""Small value objects shared by the command router and handler modules."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class CommandResult:
    handled: bool
    output: str = ""
    exit: bool = False
    clear: bool = False
    selfcheck: bool = False
    compact: bool = False
    think: str | None = None


HandlerResult = CommandResult | str
