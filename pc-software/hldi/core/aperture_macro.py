"""
Aperture macro (AM) interpreter.

Port of the ``vMacro`` / ``vArithm`` machinery in ``INC/gerber.forth``.  In the
original, an ``%AM..*%`` body is stored as text and *executed* as a little
Forth program when the aperture is flashed: each primitive (circle, vector
line, rectangle, polygon, moiré, thermal) is a word that draws into the
bitmap, ``$1 $2 ...`` are substituted with the flash parameters, and an
arithmetic evaluator handles expressions like ``$1+$2`` / ``$3x0.5``.

This module reproduces that behaviour as data instead of code:

* :func:`parse_macro` turns an AM body into a list of :class:`MacroPrimitive`
  whose operands are arithmetic expression strings.
* :meth:`ApertureMacro.evaluate` substitutes the flash parameters, evaluates
  every operand and emits resolved primitives with concrete numbers.

The numeric primitive codes follow the RS-274X standard exactly as the
``vMacro`` words did: 1=circle, 2/20=vector line, 21=center line,
4=outline, 5=polygon, 6=moiré, 7=thermal.  ``%`` parameters and the
modal-comment primitive 0 are handled too.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List


# ---------------------------------------------------------------------------
# Arithmetic evaluator  (port of vArithm)
# ---------------------------------------------------------------------------
# Gerber AM expressions use:  + -  x (or *) multiply,  / divide, and ( ).
# ``$n`` references macro parameter n.  This is a small, safe shunting-yard
# evaluator -- it never touches Python ``eval``.
class ArithmError(Exception):
    pass


_token_re = re.compile(r"\s*(\$\d+|\d*\.?\d+|[-+xX*/()])")


def _tokenize(expr: str) -> List[str]:
    tokens: List[str] = []
    pos = 0
    while pos < len(expr):
        if expr[pos].isspace():
            pos += 1
            continue
        m = _token_re.match(expr, pos)
        if not m:
            raise ArithmError(f"bad token in {expr!r} at {pos}")
        tok = m.group(1)
        tokens.append("*" if tok in ("x", "X") else tok)
        pos = m.end()
    return tokens


def eval_expr(expr: str, params: Dict[int, float]) -> float:
    """Evaluate a Gerber AM arithmetic expression."""
    tokens = _tokenize(str(expr).strip())
    if not tokens:
        return 0.0
    output: List[float] = []
    ops: List[str] = []
    prec = {"+": 1, "-": 1, "*": 2, "/": 2}

    def apply(op: str) -> None:
        b = output.pop()
        a = output.pop()
        if op == "+":
            output.append(a + b)
        elif op == "-":
            output.append(a - b)
        elif op == "*":
            output.append(a * b)
        elif op == "/":
            output.append(a / b if b else 0.0)

    prev = None
    for tok in tokens:
        if tok.startswith("$"):
            output.append(float(params.get(int(tok[1:]), 0.0)))
        elif re.match(r"^\d*\.?\d+$", tok):
            output.append(float(tok))
        elif tok == "(":
            ops.append(tok)
        elif tok == ")":
            while ops and ops[-1] != "(":
                apply(ops.pop())
            if ops:
                ops.pop()
        else:  # operator
            # unary minus / plus at expression start or after another op/'('
            if tok in "+-" and (prev is None or prev in "+-*/("):
                output.append(0.0)
            while ops and ops[-1] != "(" and prec.get(ops[-1], 0) >= prec[tok]:
                apply(ops.pop())
            ops.append(tok)
        prev = tok
    while ops:
        apply(ops.pop())
    return output[-1] if output else 0.0


# ---------------------------------------------------------------------------
# Primitives
# ---------------------------------------------------------------------------
# Primitive codes (RS-274X), matching the original vMacro words.
PRIM_COMMENT = 0
PRIM_CIRCLE = 1
PRIM_VLINE = 2          # legacy vector line (= 20)
PRIM_VLINE2 = 20        # vector line
PRIM_CLINE = 21         # center line
PRIM_OUTLINE = 4
PRIM_POLYGON = 5
PRIM_MOIRE = 6
PRIM_THERMAL = 7


@dataclass
class MacroPrimitive:
    """One primitive line of an AM body, operands kept as expressions."""
    code: int
    operands: List[str]


@dataclass
class ResolvedPrimitive:
    """A primitive with operands evaluated to concrete numbers (mm)."""
    code: int
    values: List[float]
    exposure: bool = True          # on (1) / off (0)


@dataclass
class ApertureMacro:
    name: str
    primitives: List[MacroPrimitive] = field(default_factory=list)
    # equality variables ($n = expr) defined inside the macro body
    assignments: List[tuple] = field(default_factory=list)  # (index, expr)

    def evaluate(self, args: List[float]) -> List[ResolvedPrimitive]:
        """Substitute flash arguments and evaluate every primitive."""
        params: Dict[int, float] = {i + 1: v for i, v in enumerate(args)}
        # apply variable assignments in order ($4=$1+2 etc.)
        for idx, expr in self.assignments:
            params[idx] = eval_expr(expr, params)
        resolved: List[ResolvedPrimitive] = []
        for prim in self.primitives:
            if prim.code == PRIM_COMMENT:
                continue
            vals = [eval_expr(op, params) for op in prim.operands]
            exposure = True
            # primitives 6 (moiré) and 7 (thermal) have no exposure operand;
            # for all others the first operand is the on/off flag.
            if vals and prim.code not in (PRIM_MOIRE, PRIM_THERMAL):
                exposure = vals[0] != 0
            resolved.append(ResolvedPrimitive(prim.code, vals, exposure))
        return resolved


def parse_macro(name: str, body: str) -> ApertureMacro:
    """Parse an AM body (the text after ``AM<name>*``) into an ApertureMacro.

    The body is a sequence of ``*``-separated statements.  A statement is
    either a primitive (``code,op,op,...``) or a variable assignment
    (``$n=expr``).  Lines beginning with ``0`` are comments.
    """
    macro = ApertureMacro(name=name)
    for stmt in body.split("*"):
        stmt = stmt.strip()
        if not stmt:
            continue
        if stmt.startswith("$"):
            m = re.match(r"\$(\d+)\s*=\s*(.+)", stmt)
            if m:
                macro.assignments.append((int(m.group(1)), m.group(2)))
            continue
        parts = [p.strip() for p in stmt.split(",")]
        try:
            code = int(float(parts[0]))
        except ValueError:
            continue
        macro.primitives.append(MacroPrimitive(code=code, operands=parts[1:]))
    return macro
