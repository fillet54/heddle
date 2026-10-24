"""Compiler-style diagnostics shared by the front end and the checks."""

from __future__ import annotations

from dataclasses import dataclass

from .ir import Loc

ERROR = "error"
WARNING = "warning"


@dataclass(frozen=True)
class Diagnostic:
    level: str
    message: str
    loc: Loc

    def __str__(self) -> str:
        return f"{self.loc}: {self.level}: {self.message}"


class Diagnostics(list):
    """A list of Diagnostic with helpers for adding and summarising."""

    def error(self, message: str, loc: Loc) -> None:
        self.append(Diagnostic(ERROR, message, loc))

    def warning(self, message: str, loc: Loc) -> None:
        self.append(Diagnostic(WARNING, message, loc))

    @property
    def errors(self) -> list[Diagnostic]:
        return [d for d in self if d.level == ERROR]

    @property
    def warnings(self) -> list[Diagnostic]:
        return [d for d in self if d.level == WARNING]

    def sorted(self) -> list[Diagnostic]:
        return sorted(self, key=lambda d: (d.loc.file, d.loc.line or 0, d.level))
