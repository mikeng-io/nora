"""Arithmetic that is actually arithmetic — the evaluator behind `calculate`.

WHY THIS IS NOT A PROMPT PROBLEM. 「每人 $247.50」 reads identically whether it
is right or wrong, and the group has no way to tell. That is R-5's family: a
failure indistinguishable from success at the point where somebody acts on it.
A model asked to divide 1485 by 6 in its head will usually be right and
occasionally be confidently wrong, and nothing downstream can catch the second
case. So the arithmetic moves out of the model.

WHAT THIS DOES NOT FIX, and the reason `expression` is returned beside the
result. **Constructing the expression is still reasoning.** She can divide by 5
when there were 6 people, add the service charge to the wrong subtotal, or
split a bill somebody had already paid half of. This removes ARITHMETIC error;
it does not remove MODELLING error, and the second kind is the one that will
actually happen. That is precisely why the expression has to reach the trace:
`247.5` alone is unreviewable, `1485/6` is a claim anybody can check.

GENERIC ON PURPOSE (DEC-24's argument). The entry point takes an EXPRESSION,
not `total` and `people`. A `split_bill(total, people)` tool would be a form
Nora designed for one question, and every other question — the 15% service
charge, how many days until the 20th, converting a price — would need another
one. An expression is a genuine model decision; `people=6` is Nora guessing in
advance which question will be asked.


THE FOUR CONSTRAINTS THIS MODULE ENFORCES
-----------------------------------------

1. **Python's default rounding is ROUND_HALF_EVEN, and you get it by NOT
   choosing.** `Decimal("2.005").quantize(Decimal("0.01"))` is **2.00**, not
   2.01; `0.125` goes to `0.12`. Banker's rounding is the right default for a
   statistician and the wrong one for a bill, and it is what an implementation
   that simply omits `rounding=` gets. Every quantize below passes
   `ROUND_HALF_UP` explicitly, and it is passed at the CALL rather than set on
   the context so that it cannot be undone by ambient state somewhere else in
   the process.

2. **A naive split loses money.** 1485/7 is 212.1428…; 212.14 x 7 is 1484.98,
   two cents short of 1485. Reporting only the per-person figure leaves the
   group short with nobody able to say why. See `_split` — the shortfall is
   reported, and so is an allocation that reconciles exactly.

3. **`Pow` is a denial of service before it is a safety question.**
   `2**9999999999` parses cleanly as `BinOp`/`Pow` and passes any
   operator-level whitelist. Over Python ints it runs effectively forever.
   Over `Decimal` it raises `Overflow` almost at once — a real mitigation, but
   one this module does not own: it is the ambient context's `Emax=999999` plus the `Overflow`
   trap being enabled, either of which another `localcontext` could change,
   and «Overflow» is not a sentence a model can act on. `Pow` is therefore
   KEPT — compound interest and percentages are ordinary group-chat questions
   — with the exponent bounded here, explicitly, and refused legibly.

4. **The escapes to reject are `Attribute`, `Call` and `Name`**, and a
   denylist of those three is not enough. `(1).__class__` is a bare
   `Attribute` over a `Constant`, containing neither a `Call` nor a `Name`;
   `__import__('os').system('x')` is `Call` + `Name`. But `1 if 2 else 3`,
   `[1,2][0]` and `lambda: 1` are none of the three and a denylist waves all
   of them through. `_eval` below is an ALLOWLIST — a recursive descent with
   one explicit branch per permitted node type and a refusal in the `else`.
   Validation and evaluation are the SAME walk, so they cannot disagree about
   what the tree contains; a separate `ast.walk` validation pass followed by a
   separate evaluation is two descriptions of one tree that drift.

`ast.parse(mode="eval")`, never `eval()`, and never `literal_eval` either:
the `ast` docs retract literal_eval's old "safe" claim outright ("a relatively
small input can lead to memory exhaustion or to C stack exhaustion, crashing
the process"), and it cannot do arithmetic anyway — `1+1` is a `ValueError` to
it.

TWO STATES, NOT THREE. Every other tool in `nora.agent.tools` has `ok`, a
refusal, and `unavailable`. **`unavailable` cannot occur here** and there is
no branch for it: this function reads no clock, opens no socket and holds no
injected collaborator, so there is nothing that can be unreachable. Its inputs
are a string and the CPU. A dead `unavailable` branch would be a state an
operator would eventually see in a dashboard and act on, and it would mean
nothing.
"""

from __future__ import annotations

import ast
from decimal import (
    ROUND_DOWN,
    ROUND_HALF_UP,
    Decimal,
    DecimalException,
    DivisionByZero,
    InvalidOperation,
    Overflow,
    localcontext,
)

# ---- the bounds this module owns --------------------------------------------

# Checked BEFORE `ast.parse`. The `ast` docs warn that parsing can crash the
# interpreter outright on sufficiently nested input ("stack depth limitations
# in Python's AST compiler"). CPython's parser happens to reject deeply nested
# parens with a `SyntaxError`, but that is an implementation detail and not a
# promise, and it is the wrong layer anyway: this cap is also what bounds the node count and
# therefore `_eval`'s recursion depth (a 200-character expression cannot nest
# deeper than ~100, against a default recursion limit of 1000). 200 characters
# is far more than any arithmetic a group chat contains.
MAX_EXPRESSION_CHARS = 200

# `Pow`'s exponent, per constraint 3 above. 1000 is generous by orders of
# magnitude against what this exists for — `1.05 ** 30` is thirty years of
# compound interest, `1.15 ** 1` is a service charge — while being far below
# anything that can cost real time.
MAX_POW_EXPONENT = 1000

# Every operand AND every intermediate result must satisfy
# `abs(value.adjusted()) <= this`, i.e. sit between 1e-20 and roughly 1e21.
# Two jobs. (a) It is the RENDER bound: `f"{Decimal('1E+999998'):f}"` is a
# megabyte of digits, and a tool result is copied into the model's context.
# (b) It keeps every value clear of the 28-digit context precision, so a number
# is never silently re-rounded behind the answer — a total that would be
# mangled is refused instead, which is the whole point of not using floats.
MAX_MAGNITUDE_DIGITS = 20

# Above this many parts, "who eats the extra cent" has stopped being a question
# anyone is asking, and the settlement string stops being readable.
MAX_SPLIT_PARTS = 1000

_CENT = Decimal("0.01")


class _RefusalError(Exception):
    """A refusal the model can act on, raised internally and never escaping.

    Carries the sentence rather than an error code because the recipient is a
    language model mid-turn: «`Attribute` is not arithmetic» is actionable and
    `ERR_NODE_TYPE` is not.
    """

    def __init__(self, note: str) -> None:
        super().__init__(note)
        self.note = note


# ---- reading a literal without going through a float ------------------------

def _constant(node: ast.Constant, source: str) -> Decimal:
    """One numeric literal, exactly.

    `ast` hands back `Constant(value=0.1)` — a Python FLOAT, already rounded at
    parse time. `Decimal(0.1)` is
    0.1000000000000000055511151231257827021181583404541015625, so `0.1 + 0.2`
    would not be `0.3` and the tool would be wrong in exactly the way it exists
    to prevent. `Decimal(str(0.1))` fixes that one case but not the general one
    — `str(123456789012345678901234567890.5)` is already `1.2345678901234568E+29`.

    So a float literal is re-read from its SOURCE TEXT, which is the only form
    that never lost anything. `Decimal` accepts the underscores Python allows
    in literals (`Decimal("1_000")` is 1000), so `1_000.5` survives.

    INT literals take the other path deliberately: `Decimal(int)` is exact by
    construction and needs no text, and it is the only form that reads `0xff`,
    `0b1010` and `0o17` correctly — `Decimal("0xff")` is an error.

    `type(...) is int`, never `isinstance`: **`bool` is a subclass of `int`**,
    so `True + 1` would otherwise be arithmetic that silently answers 2.
    """
    value = node.value
    if type(value) is int:
        return Decimal(value)
    if type(value) is float:
        text = ast.get_source_segment(source, node)
        try:
            return Decimal(text if text is not None else str(value))
        except DecimalException:
            raise _RefusalError(f"«{text or value}» is not a number I can work with.") from None
    # str, bytes, complex, bool, None, Ellipsis. `'a' * 3` is a real Python
    # expression and it is not arithmetic.
    raise _RefusalError(
        f"«{type(value).__name__}» is not a number. This only works on numbers."
    )


def _guard(value: Decimal) -> Decimal:
    """Refuse a value too large or too small to state honestly. See MAX_MAGNITUDE_DIGITS."""
    if not value.is_finite():
        raise _RefusalError("That does not come out to a finite number.")
    if value != 0 and abs(value.adjusted()) > MAX_MAGNITUDE_DIGITS:
        raise _RefusalError(
            "The numbers in that are too big or too small for me to give an exact "
            f"answer (over 1e{MAX_MAGNITUDE_DIGITS}). Work in smaller steps."
        )
    return value


# ---- the allowlist ----------------------------------------------------------
#
# One branch per permitted node type; anything else falls to the `else`. This
# is the shape constraint 4 argues for, and the `else` is why `IfExp`,
# `Subscript`, `Lambda`, `ListComp`, `JoinedStr`, `Compare`, `BoolOp`,
# `NamedExpr` and every construct added to Python later are refused without
# being enumerated: they are not allowed.
#
# Bitwise and shift operators (`|`, `&`, `^`, `<<`, `>>`) and `not`/`~` are
# absent on purpose rather than by oversight. `Decimal` does not implement any
# of them, nobody splitting a bill means them, and `1 | 2` silently meaning
# something in a money tool is not a behaviour worth having.

def _binop(node: ast.BinOp, source: str) -> Decimal:
    left = _eval(node.left, source)
    right = _eval(node.right, source)
    op = type(node.op)

    if op is ast.Add:
        return left + right
    if op is ast.Sub:
        return left - right
    if op is ast.Mult:
        return left * right
    if op in (ast.Div, ast.FloorDiv, ast.Mod):
        # Checked HERE rather than caught afterwards. `decimal` signals a zero
        # divisor as `DivisionByZero` for `/` but as `InvalidOperation` for
        # `x % 0`, and `0/0` is `InvalidOperation` too — three different
        # signals for one thing the model needs told once, in a sentence.
        if right == 0:
            raise _RefusalError("That divides by zero, so there is no answer to give.")
        if op is ast.Div:
            return left / right
        # `//` and `%` on Decimal TRUNCATE toward zero, where Python's ints
        # floor: `Decimal(-9) // Decimal(2)` is -4 and `-9 // 2` is -5. Stated
        # because it is a real difference and this module is the only place it
        # is visible; not corrected, because truncation is what "how many whole
        # ones fit" means and that is the question these get asked.
        return left // right if op is ast.FloorDiv else left % right
    if op is ast.Pow:
        # Constraint 3. The bound is checked before the operation, not
        # inferred from whatever the ambient context's Emax happens to be.
        if abs(right) > MAX_POW_EXPONENT:
            raise _RefusalError(
                f"That exponent is too large (over {MAX_POW_EXPONENT}). Raising to a "
                "power that big has no answer I can give in a chat message."
            )
        return left**right
    raise _RefusalError(
        f"«{op.__name__}» is not arithmetic. I can only do + - * / // % and **."
    )


def _eval(node: ast.AST, source: str) -> Decimal:
    if isinstance(node, ast.Constant):
        return _guard(_constant(node, source))
    if isinstance(node, ast.BinOp):
        return _guard(_binop(node, source))
    if isinstance(node, ast.UnaryOp):
        op = type(node.op)
        if op is ast.UAdd:
            return _guard(+_eval(node.operand, source))
        if op is ast.USub:
            return _guard(-_eval(node.operand, source))
        # `not 1` and `~1`.
        raise _RefusalError(f"«{op.__name__}» is not arithmetic. I can only do + and - here.")
    raise _RefusalError(
        f"«{type(node).__name__}» is not arithmetic. Write plain numbers and "
        "brackets with + - * / // % and **, nothing else."
    )


# ---- the remainder, made visible --------------------------------------------

def _split(total: Decimal, parts: Decimal) -> dict | None:
    """Where the money went when a division does not come out evenly.

    Measurement 2 is the whole reason this exists. 1485/7 is 212.1428…; round
    it and report only that, and seven people pay 1484.98 for a 1485 dinner
    while everyone believes the number. The rounding error is not the problem
    — it is two cents — the problem is that it is INVISIBLE, so «who is short»
    is a question with no answer anywhere in the system.

    The allocation is computed in whole cents and reconciles EXACTLY:
    `each` is the share floored to the cent, `leftover` is what that leaves
    unpaid, and `settles_as` distributes the leftover one cent at a time.
    ROUND_DOWN rather than half-up for `each` deliberately — flooring makes
    `leftover` non-negative, so the answer is always "somebody pays a cent
    MORE", never the harder-to-explain "two of you are owed a cent".

    `settles_as` is written as an EXPRESSION («2 * 212.15 + 5 * 212.14») and
    not as prose, so it is something the model — or a human reading the trace
    — can hand straight back to this same tool and check. A settlement nobody
    can verify would just be a more confident version of the number this
    function exists to distrust.

    Returns None whenever the shape is not a split anybody is asking about; a
    speculative block on every division would train the model to ignore it.
    """
    if parts != parts.to_integral_value() or not 2 <= parts <= MAX_SPLIT_PARTS:
        return None
    if total <= 0:
        return None
    # A total that is not itself a whole number of cents is not money, and the
    # cent-by-cent allocation below would not be honest about it.
    if total != total.quantize(_CENT, rounding=ROUND_HALF_UP):
        return None
    quotient = total / parts
    if quotient == quotient.quantize(_CENT, rounding=ROUND_HALF_UP):
        return None  # divides evenly to the cent; there is no remainder to show

    count = int(parts)
    each = quotient.quantize(_CENT, rounding=ROUND_DOWN)
    leftover = total - each * count
    # Exact by construction: `leftover` is a whole number of cents because
    # `total` is and `each * count` is.
    extra = int(leftover / _CENT)
    return {
        "parts": count,
        "each": _render(each),
        "leftover": _render(leftover),
        "settles_as": (
            f"{extra} * {_render(each + _CENT)} + {count - extra} * {_render(each)}"
        ),
    }


def _render(value: Decimal) -> str:
    """A Decimal as the model should read it.

    `f"{...:f}"` rather than `str()` so `1e3` reads as `1000` instead of
    `1E+3` — `Decimal` keeps the literal's exponent and `str()` shows it. Safe
    to expand only because `_guard` has already bounded the magnitude; without
    that bound this line is the render bomb (`f"{Decimal('1E+999998'):f}"` is a
    megabyte).
    """
    return f"{value:f}"


# ---- the one public entry point ---------------------------------------------

def evaluate(expression: str) -> dict:
    """Evaluate one arithmetic expression exactly. NEVER raises.

    That last word is the hard requirement: this runs inside an agent turn, and
    an exception there costs the entire reply — R-5 again, since a turn that
    dies is silent rather than wrong. Every failure leaves here as
    `{"status": "refused", "note": ...}` with a sentence the model can act on.

    Returns `status`, the `expression` as given, and on success `result` (the
    exact value), plus at most one of:

      * `rounded_to_cents` — when the exact value has more precision than a
        currency does. ROUND_HALF_UP, per measurement 1.
      * `split` — when the whole expression is a division that does not come
        out evenly. See `_split`.

    Never both, and that is deliberate: two money figures side by side that do
    not add up to the same total is the exact confusion this tool exists to
    remove. Neither key appears when the answer is already exact.
    """
    if not isinstance(expression, str):
        # The model supplies this and a schema is a request, not a guarantee.
        return {"status": "refused", "expression": str(expression),
                "note": "Give me the calculation as text, like «(1200 + 285) / 6»."}
    if len(expression) > MAX_EXPRESSION_CHARS:
        return {
            "status": "refused", "expression": expression[:MAX_EXPRESSION_CHARS],
            "note": f"That is longer than {MAX_EXPRESSION_CHARS} characters. Work it "
                    "out in steps and calculate each one.",
        }

    try:
        tree = ast.parse(expression, mode="eval")
    except (SyntaxError, ValueError, MemoryError, RecursionError):
        # SyntaxError covers malformed input AND, on 3.13, embedded null
        # bytes; ValueError is what older versions raise for the latter. The
        # last two are the crash modes the `ast` docs warn about and
        # MAX_EXPRESSION_CHARS should already have prevented — caught anyway,
        # because "should already have" is not the same as "cannot".
        return {
            "status": "refused", "expression": expression,
            "note": "I could not read that as a calculation. Write it as a plain "
                    "expression like «(1200 + 285) / 6»: numbers, brackets, and "
                    "+ - * / // % ** only.",
        }

    try:
        # An EXPLICIT context, so the answer never depends on ambient state
        # some other part of the process left behind. prec=28 is the
        # documented default, restated rather than inherited; the three traps
        # are on by default and are restated for the same reason — with
        # DivisionByZero untrapped, `1/0` would quietly return `Infinity` and
        # this function would report it as a result.
        with localcontext() as ctx:
            ctx.prec = 28
            ctx.rounding = ROUND_HALF_UP
            for signal in (DivisionByZero, InvalidOperation, Overflow):
                ctx.traps[signal] = True
            value = _eval(tree.body, expression)
            split = None
            if isinstance(tree.body, ast.BinOp) and isinstance(tree.body.op, ast.Div):
                # Re-evaluated rather than threaded out of `_eval`: it keeps
                # `_eval` a plain recursion returning one Decimal, and the
                # cost is two extra walks of a tree that cannot exceed 200
                # characters. `(1350 * 1.1) / 7` therefore splits the total
                # the model actually meant, not just a literal.
                split = _split(_eval(tree.body.left, expression),
                               _eval(tree.body.right, expression))
    except _RefusalError as refusal:
        return {"status": "refused", "expression": expression, "note": refusal.note}
    except DecimalException:
        # `0 ** 0`, `(-1) ** 0.5`, an Overflow the explicit bounds did not
        # already catch. Legible, and never an exception into the turn.
        return {
            "status": "refused", "expression": expression,
            "note": "That calculation has no answer. Check the numbers and try a "
                    "simpler form of it.",
        }

    # Annotated because the values are not homogeneous: `split` is a nested
    # dict beside four strings, and an unannotated literal infers `dict[str, str]`
    # and then rejects it.
    out: dict = {"status": "ok", "expression": expression, "result": _render(value)}
    if split is not None:
        out["split"] = split
    else:
        rounded = value.quantize(_CENT, rounding=ROUND_HALF_UP)
        # `!=` compares VALUE, so `Decimal("297")` against `Decimal("297.00")`
        # is equal and no second number is added. A rounded figure appears
        # only where rounding actually changed something.
        if rounded != value:
            out["rounded_to_cents"] = _render(rounded)
    return out
