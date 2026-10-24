"""The guard and action expression language, and its C++ translation.

Guards and actions in a component document use a deliberately tiny,
side-effect-free language so they can be checked and translated exactly::

    guard   := or
    or      := and ("or" and)*
    and     := not ("and" not)*
    not     := "not" not | compare
    compare := sum (("==" | "!=" | "<" | "<=" | ">" | ">=") sum)?
    sum     := term (("+" | "-") term)*
    term    := unary (("*" | "/" | "%") unary)*
    unary   := "-" unary | atom
    atom    := NUMBER | "true" | "false" | NAME | "(" or ")"

    actions := assign (";" assign)*
    assign  := NAME ("=" | "+=" | "-=" | "*=" | "/=") or

There are no calls, loops or pointers: every expression terminates and has
no hidden effects. Names are resolved by the caller, which decides what a
name becomes in the target language.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

TOKEN = re.compile(
    r"\s*(?:(?P<num>\d+(?:\.\d+)?)|(?P<name>[A-Za-z_]\w*)"
    r"|(?P<op>==|!=|<=|>=|\+=|-=|\*=|/=|[-+*/%<>=();]))"
)

KEYWORDS = {"and", "or", "not", "true", "false"}
COMPARE = {"==", "!=", "<", "<=", ">", ">="}
ASSIGN = {"=", "+=", "-=", "*=", "/="}

# Precedence used when printing, so parentheses appear only where needed.
PREC = {"or": 1, "and": 2, "not": 3, "cmp": 4, "+": 5, "-": 5, "*": 6, "/": 6, "%": 6,
        "neg": 7, "atom": 8}
CPP_OP = {"or": "||", "and": "&&"}


class ExprError(ValueError):
    pass


@dataclass(frozen=True)
class Node:
    kind: str           # "num" | "bool" | "name" | "not" | "neg" | "bin" | "cmp"
    value: str = ""     # literal, name or operator
    left: "Node | None" = None
    right: "Node | None" = None


@dataclass(frozen=True)
class Assign:
    target: str
    op: str
    value: Node


def tokenize(text: str) -> list[tuple[str, str]]:
    tokens, pos = [], 0
    text = text.rstrip()
    while pos < len(text):
        m = TOKEN.match(text, pos)
        if not m or m.end() == pos:
            raise ExprError(f"unexpected character {text[pos:].lstrip()[:1]!r} in '{text}'")
        kind = m.lastgroup
        value = m.group(kind)
        if kind == "name" and value in KEYWORDS:
            kind = "kw"
        tokens.append((kind, value))
        pos = m.end()
    return tokens


class _Parser:
    def __init__(self, text: str):
        self.text = text
        self.tokens = tokenize(text)
        self.i = 0

    def peek(self, offset: int = 0) -> tuple[str, str] | None:
        j = self.i + offset
        return self.tokens[j] if j < len(self.tokens) else None

    def accept(self, *values: str) -> str | None:
        tok = self.peek()
        if tok and tok[1] in values and tok[0] in ("op", "kw"):
            self.i += 1
            return tok[1]
        return None

    def expect(self, value: str) -> None:
        if not self.accept(value):
            found = self.peek()
            raise ExprError(f"expected '{value}' but found "
                            f"{repr(found[1]) if found else 'end of expression'} in '{self.text}'")

    def done(self) -> None:
        if self.peek() is not None:
            raise ExprError(f"unexpected '{self.peek()[1]}' in '{self.text}'")

    # -- grammar -------------------------------------------------------------

    def or_(self) -> Node:
        node = self.and_()
        while self.accept("or"):
            node = Node("bin", "or", node, self.and_())
        return node

    def and_(self) -> Node:
        node = self.not_()
        while self.accept("and"):
            node = Node("bin", "and", node, self.not_())
        return node

    def not_(self) -> Node:
        if self.accept("not"):
            return Node("not", left=self.not_())
        return self.compare()

    def compare(self) -> Node:
        node = self.sum()
        tok = self.peek()
        if tok and tok[0] == "op" and tok[1] in COMPARE:
            self.i += 1
            node = Node("cmp", tok[1], node, self.sum())
            nxt = self.peek()
            if nxt and nxt[0] == "op" and nxt[1] in COMPARE:
                raise ExprError(f"chained comparison in '{self.text}'; use 'and'")
        return node

    def sum(self) -> Node:
        node = self.term()
        while True:
            op = self.accept("+", "-")
            if not op:
                return node
            node = Node("bin", op, node, self.term())

    def term(self) -> Node:
        node = self.unary()
        while True:
            op = self.accept("*", "/", "%")
            if not op:
                return node
            node = Node("bin", op, node, self.unary())

    def unary(self) -> Node:
        if self.accept("-"):
            return Node("neg", left=self.unary())
        return self.atom()

    def atom(self) -> Node:
        tok = self.peek()
        if tok is None:
            raise ExprError(f"expression ends early: '{self.text}'")
        kind, value = tok
        if kind == "num":
            self.i += 1
            return Node("num", value)
        if kind == "kw" and value in ("true", "false"):
            self.i += 1
            return Node("bool", value)
        if kind == "name":
            self.i += 1
            return Node("name", value)
        if self.accept("("):
            node = self.or_()
            self.expect(")")
            return node
        raise ExprError(f"unexpected '{value}' in '{self.text}'")

    def assign(self) -> Assign:
        tok = self.peek()
        if not tok or tok[0] != "name":
            raise ExprError(f"an action must start with the name it assigns: '{self.text}'")
        self.i += 1
        op_tok = self.peek()
        if not op_tok or op_tok[1] not in ASSIGN:
            raise ExprError(f"expected an assignment (=, +=, -=, *=, /=) after '{tok[1]}' "
                            f"in '{self.text}'")
        self.i += 1
        return Assign(tok[1], op_tok[1], self.or_())


def parse_guard(text: str) -> Node:
    p = _Parser(text)
    node = p.or_()
    p.done()
    return node


def parse_actions(text: str | None) -> list[Assign]:
    if not text or not text.strip():
        return []
    out = []
    for part in text.split(";"):
        if part.strip():
            p = _Parser(part.strip())
            out.append(p.assign())
            p.done()
    return out


def names(node: Node) -> list[str]:
    """Every name the expression reads, in order."""
    if node.kind == "name":
        return [node.value]
    out = []
    for child in (node.left, node.right):
        if child is not None:
            out += names(child)
    return out


# -- C++ printing -------------------------------------------------------------

Resolver = Callable[[str], str]


def _prec(node: Node) -> int:
    if node.kind in ("num", "bool", "name"):
        return PREC["atom"]
    if node.kind == "cmp":
        return PREC["cmp"]
    if node.kind in ("not", "neg"):
        return PREC[node.kind]
    return PREC[node.value]


def to_cpp(node: Node, resolve: Resolver) -> str:
    def emit(n: Node, parent: int, right_side: bool = False) -> str:
        if n.kind == "num":
            text = n.value
        elif n.kind == "bool":
            text = n.value
        elif n.kind == "name":
            text = resolve(n.value)
        elif n.kind == "not":
            # C++ `!` binds tighter than any binary operator, so anything but
            # an atom needs parentheses: not a == b  ->  !(a == b).
            text = "!" + emit(n.left, PREC["atom"])
        elif n.kind == "neg":
            text = "-" + emit(n.left, PREC["atom"])
        else:
            p = _prec(n)
            op = CPP_OP.get(n.value, n.value)
            # Left-associative: the right operand needs parentheses at equal
            # precedence (a - (b - c)); comparisons never chain.
            # `and` inside `or` is parenthesised for the reader (and for gcc's
            # -Wparentheses), though C++ precedence would not need it.
            def side(child: Node, prec: int) -> str:
                if n.value == "or" and child.kind == "bin" and child.value == "and":
                    return f"({emit(child, 0)})"
                return emit(child, prec)

            text = f"{side(n.left, p)} {op} {side(n.right, p + 1)}"
        return f"({text})" if _prec(n) < parent else text

    return emit(node, 0)


def assign_to_cpp(a: Assign, resolve: Resolver) -> str:
    return f"{resolve(a.target)} {a.op} {to_cpp(a.value, resolve)};"
