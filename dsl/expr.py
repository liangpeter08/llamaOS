"""The DSL expression language: a small closed grammar, parsed to an AST.

    expr    := or
    or      := and ('or' and)*
    and     := not ('and' not)*
    not     := 'not' not | cmp
    cmp     := add ((>=|<=|>|<|==|!=) add | 'in' list | 'not' 'in' list)?
    add     := mul (('+'|'-') mul)*
    mul     := unary (('*'|'/') unary)*
    unary   := '-' unary | postfix
    postfix := primary ('[' INT ']')?
    primary := NUMBER | NUMBER% | true | false | NAME | NAME '(' args ')' | '(' expr ')'
    list    := '[' NAME (',' NAME)* ']'

No loops, no user functions, no attribute access beyond one dotted signal name.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .loader import DSLError


@dataclass(frozen=True)
class Node:
    src: str = field(default="", compare=False)


@dataclass(frozen=True)
class Num(Node):
    value: float = 0.0


@dataclass(frozen=True)
class BoolLit(Node):
    value: bool = False


@dataclass(frozen=True)
class Name(Node):
    id: str = ""


@dataclass(frozen=True)
class Call(Node):
    fn: str = ""
    args: tuple = ()


@dataclass(frozen=True)
class Index(Node):
    base: Node = None
    n: int = 0


@dataclass(frozen=True)
class Neg(Node):
    x: Node = None


@dataclass(frozen=True)
class BinOp(Node):
    op: str = ""
    l: Node = None
    r: Node = None


@dataclass(frozen=True)
class Cmp(Node):
    op: str = ""
    l: Node = None
    r: Node = None


@dataclass(frozen=True)
class In(Node):
    l: Node = None
    items: tuple = ()
    negate: bool = False


@dataclass(frozen=True)
class BoolOp(Node):
    op: str = ""  # and | or
    items: tuple = ()


@dataclass(frozen=True)
class Not(Node):
    x: Node = None


_TOKEN = re.compile(
    r"""\s*(?:
      (?P<num>\d+(?:_\d{3})*(?:\.\d+)?%?)
    | (?P<name>[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?)
    | (?P<op>>=|<=|==|!=|[-+*/<>()\[\],])
    )""",
    re.VERBOSE,
)
KEYWORDS = {"and", "or", "not", "in", "true", "false"}


def _tokenize(text: str) -> list[tuple[str, str, int, int]]:
    out, pos = [], 0
    text = text.rstrip()
    while pos < len(text):
        m = _TOKEN.match(text, pos)
        if not m or m.end() == pos:
            raise DSLError(f"unexpected character {text[pos:].strip()[:1]!r} in {text!r}")
        kind = m.lastgroup
        val = m.group(kind)
        start = m.start(kind)
        if kind == "name" and val in KEYWORDS:
            kind = "kw"
        out.append((kind, val, start, m.end()))
        pos = m.end()
    out.append(("eof", "", len(text), len(text)))
    return out


class _Parser:
    def __init__(self, text: str):
        self.text = text
        self.toks = _tokenize(text)
        self.i = 0

    def peek(self, k: int = 0):
        return self.toks[min(self.i + k, len(self.toks) - 1)]

    def take(self):
        t = self.toks[self.i]
        self.i += 1
        return t

    def accept(self, kind: str, val: str | None = None):
        t = self.peek()
        if t[0] == kind and (val is None or t[1] == val):
            return self.take()
        return None

    def expect(self, kind: str, val: str | None = None):
        t = self.accept(kind, val)
        if not t:
            got = self.peek()[1] or "end of expression"
            raise DSLError(f"expected {val or kind} but found {got!r} in {self.text!r}")
        return t

    def src(self, start: int) -> str:
        end = self.toks[self.i - 1][3]
        return self.text[start:end].strip()

    def parse(self) -> Node:
        node = self.or_()
        if self.peek()[0] != "eof":
            raise DSLError(f"unexpected {self.peek()[1]!r} in {self.text!r}")
        return node

    def or_(self):
        start = self.peek()[2]
        items = [self.and_()]
        while self.accept("kw", "or"):
            items.append(self.and_())
        return items[0] if len(items) == 1 else BoolOp(op="or", items=tuple(items), src=self.src(start))

    def and_(self):
        start = self.peek()[2]
        items = [self.not_()]
        while self.accept("kw", "and"):
            items.append(self.not_())
        return items[0] if len(items) == 1 else BoolOp(op="and", items=tuple(items), src=self.src(start))

    def not_(self):
        start = self.peek()[2]
        if self.peek()[0] == "kw" and self.peek()[1] == "not" and not (
            self.peek(1)[0] == "kw" and self.peek(1)[1] == "in"
        ):
            self.take()
            return Not(x=self.not_(), src=self.src(start))
        return self.cmp()

    def cmp(self):
        start = self.peek()[2]
        left = self.add()
        t = self.peek()
        if t[0] == "op" and t[1] in (">=", "<=", ">", "<", "==", "!="):
            self.take()
            right = self.add()
            return Cmp(op=t[1], l=left, r=right, src=self.src(start))
        negate = False
        if t[0] == "kw" and t[1] == "not" and self.peek(1)[1] == "in":
            self.take()
            negate = True
        if self.accept("kw", "in"):
            self.expect("op", "[")
            items = [self.expect("name")[1]]
            while self.accept("op", ","):
                items.append(self.expect("name")[1])
            self.expect("op", "]")
            return In(l=left, items=tuple(items), negate=negate, src=self.src(start))
        if negate:
            raise DSLError(f"expected 'in' after 'not' in {self.text!r}")
        return left

    def add(self):
        start = self.peek()[2]
        node = self.mul()
        while self.peek()[0] == "op" and self.peek()[1] in ("+", "-"):
            op = self.take()[1]
            node = BinOp(op=op, l=node, r=self.mul(), src=self.src(start))
        return node

    def mul(self):
        start = self.peek()[2]
        node = self.unary()
        while self.peek()[0] == "op" and self.peek()[1] in ("*", "/"):
            op = self.take()[1]
            node = BinOp(op=op, l=node, r=self.unary(), src=self.src(start))
        return node

    def unary(self):
        start = self.peek()[2]
        if self.accept("op", "-"):
            return Neg(x=self.unary(), src=self.src(start))
        return self.postfix()

    def postfix(self):
        start = self.peek()[2]
        node = self.primary()
        if self.accept("op", "["):
            t = self.expect("num")
            if "." in t[1] or "%" in t[1]:
                raise DSLError(f"bar offsets must be whole numbers in {self.text!r}")
            self.expect("op", "]")
            node = Index(base=node, n=int(t[1].replace("_", "")), src=self.src(start))
        return node

    def primary(self):
        start = self.peek()[2]
        t = self.take()
        kind, val = t[0], t[1]
        if kind == "num":
            if val.endswith("%"):
                return Num(value=float(val[:-1].replace("_", "")) / 100, src=val)
            return Num(value=float(val.replace("_", "")), src=val)
        if kind == "kw" and val in ("true", "false"):
            return BoolLit(value=val == "true", src=val)
        if kind == "name":
            if self.accept("op", "("):
                args = []
                if not self.accept("op", ")"):
                    args.append(self.or_())
                    while self.accept("op", ","):
                        args.append(self.or_())
                    self.expect("op", ")")
                return Call(fn=val, args=tuple(args), src=self.src(start))
            return Name(id=val, src=val)
        if kind == "op" and val == "(":
            node = self.or_()
            self.expect("op", ")")
            return node
        raise DSLError(f"unexpected {val or 'end of expression'!r} in {self.text!r}")


def parse(text) -> Node:
    if isinstance(text, bool):
        return BoolLit(value=text, src=str(text).lower())
    if isinstance(text, (int, float)):
        return Num(value=float(text), src=str(text))
    if not isinstance(text, str) or not text.strip():
        raise DSLError(f"expected an expression, got {text!r}")
    return _Parser(text).parse()


def walk(node: Node):
    """Yield every node in the tree."""
    yield node
    for child in children(node):
        yield from walk(child)


def children(node: Node) -> list[Node]:
    if isinstance(node, Call):
        return list(node.args)
    if isinstance(node, Index):
        return [node.base]
    if isinstance(node, (Neg, Not)):
        return [node.x]
    if isinstance(node, (BinOp, Cmp)):
        return [node.l, node.r]
    if isinstance(node, In):
        return [node.l]
    if isinstance(node, BoolOp):
        return list(node.items)
    return []


def names(node: Node) -> set[str]:
    return {n.id for n in walk(node) if isinstance(n, Name)}


def calls(node: Node) -> set[str]:
    return {n.fn for n in walk(node) if isinstance(n, Call)}
