"""Document type names → C++ types.

Kept as data so a project can extend it (e.g. with types from a types
document later) without touching the templates.
"""

from __future__ import annotations

DEFAULT_TYPES = {
    "bool": "bool",
    "int8": "std::int8_t",
    "int16": "std::int16_t",
    "int32": "std::int32_t",
    "int64": "std::int64_t",
    "uint8": "std::uint8_t",
    "uint16": "std::uint16_t",
    "uint32": "std::uint32_t",
    "uint64": "std::uint64_t",
    "int": "std::int32_t",
    "float": "float",
    "float32": "float",
    "double": "double",
    "float64": "double",
    "frames": "std::uint32_t",  # a count of frames at the component's rate
}


class CppTypes:
    def __init__(self, extra: dict[str, str] | None = None):
        self.table = dict(DEFAULT_TYPES)
        self.table.update(extra or {})

    def lookup(self, name: str) -> str | None:
        return self.table.get(name)

    def names(self) -> list[str]:
        return sorted(self.table)

    @staticmethod
    def default(cpp_type: str) -> str:
        if cpp_type == "bool":
            return "false"
        if cpp_type in ("float", "double"):
            return "0.0"
        return "0"
