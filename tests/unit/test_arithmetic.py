"""`nora.agent.arithmetic.evaluate` — the safe evaluator behind the `calculate` tool.

Each asserted number is a case where the obvious implementation is confidently
wrong rather than obviously broken:

  Decimal("2.005").quantize(Decimal("0.01"))  ->  2.00   # ROUND_HALF_EVEN
  Decimal(1485) / 7 -> 212.1428...; 212.14 * 7 -> 1484.98 (two cents short)
  Decimal(2) ** Decimal(9999999999) -> Overflow at once, but 2**9999999999
  over ints runs effectively forever

The tests are written so that reverting the code under test turns them
red: the banker's-rounding probe asserts the answer you get ONLY by choosing
ROUND_HALF_UP, and the remainder probe asserts a field that does not exist if
the shortfall is dropped.
"""

from decimal import Decimal

from nora.agent.arithmetic import MAX_EXPRESSION_CHARS, evaluate

# ---- the arithmetic is right -------------------------------------------------

def test_it_answers_ordinary_arithmetic():
    out = evaluate("(1200 + 285) / 5")
    assert out["status"] == "ok"
    assert out["result"] == "297"


def test_the_expression_comes_back_with_the_result():
    """DEC-31's shape: a wrong split has to be visible in the trace, not only
    in the group. `247.5` alone cannot be checked by anyone reading the log."""
    out = evaluate("1485/6")
    assert out["expression"] == "1485/6"
    assert out["result"] == "247.5"


def test_every_operator_the_docstring_promises_actually_works():
    """A fake that answered every expression the same way would pass a single
    happy-path assertion. These are seven different answers."""
    assert evaluate("2 + 3")["result"] == "5"
    assert evaluate("10 - 4")["result"] == "6"
    assert evaluate("7 * 6")["result"] == "42"
    assert evaluate("9 / 2")["result"] == "4.5"
    assert evaluate("9 // 2")["result"] == "4"
    assert evaluate("9 % 2")["result"] == "1"
    assert evaluate("2 ** 10")["result"] == "1024"
    assert evaluate("-(3 + 4)")["result"] == "-7"


def test_a_decimal_literal_is_not_read_through_a_float():
    """`ast` hands back `Constant(value=0.1)` — a FLOAT, already rounded at
    parse time. `Decimal(0.1)` is 0.1000000000000000055511151231257827…, so
    0.1 + 0.2 would not be 0.3. Reading the literal's SOURCE TEXT is the only
    exact form.

    The second case is the one `Decimal(str(value))` also gets wrong — the
    float is already 1.0 by the time `ast` hands it over, so `str()` of it is
    "1.0" and 22 digits are simply gone."""
    assert evaluate("0.1 + 0.2")["result"] == "0.3"
    assert evaluate("1.0000000000000000000001 + 0")["result"] == (
        "1.0000000000000000000001"
    )
    # Exactness has a stated ceiling rather than a silent one: a literal past
    # the magnitude bound is REFUSED, never quietly re-rounded to 28 digits.
    assert evaluate("123456789012345678901234567890.5 + 0")["status"] == "refused"


# ---- PROBE 1: banker's rounding is the wrong default, and it is the default --

def test_money_rounds_half_up_and_not_the_way_python_does_by_default():
    """`Decimal("2.005").quantize(Decimal("0.01"))` is **2.00**.

    Python's default context rounds ROUND_HALF_EVEN, which you get by NOT
    choosing. 2.005 -> 2.00 and 0.125 -> 0.12 are both defensible to a
    statistician and both wrong on a bill, and the group has no way to tell.
    An implementation that omits `rounding=ROUND_HALF_UP` fails here.
    """
    assert evaluate("2.005 * 1")["rounded_to_cents"] == "2.01"
    assert evaluate("0.125 * 1")["rounded_to_cents"] == "0.13"
    # The sign is not an escape hatch: half-up means away from zero.
    assert evaluate("-2.005 * 1")["rounded_to_cents"] == "-2.01"


def test_an_exact_answer_is_not_dressed_up_with_a_rounded_one():
    """`rounded_to_cents` appears only when rounding actually happened —
    otherwise every answer carries a second number that says nothing."""
    assert "rounded_to_cents" not in evaluate("1485 / 5")
    assert "rounded_to_cents" not in evaluate("2 + 2")


# ---- PROBE 2: the remainder must be visible ---------------------------------

def test_a_split_that_loses_money_says_where_the_money_went():
    """1485/7 = 212.1428…; 212.14 x 7 = 1484.98, two cents short.

    Reporting only the per-person figure leaves the group 0.02 short with
    nobody able to say why. `split` is what makes «who eats the extra 2 cents»
    an answerable question instead of an unanswerable one.
    """
    out = evaluate("1485/7")
    assert out["status"] == "ok"
    split = out["split"]
    assert split["parts"] == 7
    assert split["each"] == "212.14"
    assert split["leftover"] == "0.02"
    # Written as an expression rather than as prose so it can be handed
    # straight back to this same tool and checked: 2*212.15 + 5*212.14 = 1485.
    assert split["settles_as"] == "2 * 212.15 + 5 * 212.14"
    assert evaluate(split["settles_as"])["result"] == "1485.00"


def test_the_settlement_it_reports_adds_up_to_the_total_exactly():
    """The property, not the example. If `settles_as` did not reconcile, the
    tool would be replacing one wrong number with a more confident one."""
    for total, parts in ((1485, 7), (100, 3), (100, 6), (Decimal("99.99"), 7),
                         (1000, 998), (7, 2)):
        out = evaluate(f"{total}/{parts}")
        split = out.get("split")
        if split is None:  # divides exactly; nothing to reconcile
            continue
        each = Decimal(split["each"])
        higher, lower = split["settles_as"].split(" + ")
        hi_n, hi_v = higher.split(" * ")
        lo_n, lo_v = lower.split(" * ")
        assert int(hi_n) + int(lo_n) == parts
        assert Decimal(hi_v) * int(hi_n) + Decimal(lo_v) * int(lo_n) == Decimal(total)
        assert Decimal(total) - each * parts == Decimal(split["leftover"])


def test_the_service_charge_case_splits_on_the_computed_total():
    """The divisor and the dividend are read from the EXPRESSION, so
    `(1350 * 1.1) / 7` splits the total the model actually meant."""
    out = evaluate("(1350 * 1.1) / 7")
    assert out["split"]["parts"] == 7
    assert out["split"]["each"] == "212.14"
    assert out["split"]["leftover"] == "0.02"


def test_an_even_split_reports_no_remainder_block_at_all():
    out = evaluate("1485/5")
    assert "split" not in out
    assert out["result"] == "297"


def test_a_split_never_shows_a_second_money_figure_beside_the_settlement():
    """Two money answers that do not add up to the same total is the exact
    confusion this tool exists to remove."""
    out = evaluate("1485/7")
    assert "split" in out
    assert "rounded_to_cents" not in out


# ---- PROBE 3: Pow is a denial of service ------------------------------------

def test_a_huge_exponent_is_refused_rather_than_computed():
    """`2**9999999999` over ints runs unbounded. Over `Decimal` it raises
    `Overflow` at once — but only because the ambient context's Emax is 999999
    and the Overflow trap is on, neither of which this module sets, and
    «Overflow» is not a sentence a model can act on. The bound is checked
    here, explicitly, and the refusal says what to do instead.
    """
    out = evaluate("2**9999999999")
    assert out["status"] == "refused"
    assert "exponent" in out["note"].lower()
    assert out["expression"] == "2**9999999999"


def test_an_ordinary_power_still_works():
    """The bound must not cost the compound-interest question it exists beside."""
    assert evaluate("1.05 ** 30")["rounded_to_cents"] == "4.32"
    assert evaluate("2 ** 16")["result"] == "65536"


def test_a_number_too_large_to_state_honestly_is_refused_not_rendered():
    out = evaluate("99999999999999999999999999999999999999 * 10")
    assert out["status"] == "refused"


# ---- PROBE 4: the escapes are Attribute, Call and Name ----------------------

def test_attribute_access_is_refused():
    """`(1).__class__` is a bare `Attribute` over a `Constant`. It contains no
    `Name` and no `Call`, so an operator-level denylist waves it through."""
    out = evaluate("(1).__class__")
    assert out["status"] == "refused"
    assert "Attribute" in out["note"]


def test_a_call_is_refused():
    out = evaluate("__import__('os').system('x')")
    assert out["status"] == "refused"
    assert "Call" in out["note"]


def test_a_bare_name_is_refused():
    out = evaluate("x + 1")
    assert out["status"] == "refused"
    assert "Name" in out["note"]


def test_the_node_check_is_an_allowlist_and_not_a_list_of_todays_escapes():
    """The property that matters. None of these is `Attribute`, `Call` or
    `Name`; each is refused because it was never allowed, not because somebody
    thought of it. A denylist passes the three tests above and fails these."""
    for source in (
        "1 if 2 else 3",          # IfExp
        "[1, 2][0]",              # Subscript
        "(1, 2)",                 # Tuple
        "[1, 2]",                 # List
        "{1: 2}",                 # Dict
        "1 < 2",                  # Compare
        "1 and 2",                # BoolOp
        "not 1",                  # UnaryOp/Not
        "1 | 2",                  # BinOp/BitOr
        "1 << 2",                 # BinOp/LShift
        "~1",                     # UnaryOp/Invert
        "lambda: 1",              # Lambda
        "f'{1}'",                 # JoinedStr
        "'a' * 3",                # Constant, but a str one
        "1j + 1",                 # Constant, but a complex one
        "True + 1",               # Constant, but a bool one
    ):
        out = evaluate(source)
        assert out["status"] == "refused", f"{source!r} was allowed through"
        assert out["note"], f"{source!r} was refused with no reason"


# ---- PROBE 5: malformed input is a refusal, never an exception --------------

def test_a_malformed_expression_is_refused_and_does_not_raise():
    for source in ("1 +", "", "   ", "1 $ 2", "((1)", "1;2", "import os",
                   "1 2 3", "/ 5", "**", "1..2"):
        out = evaluate(source)
        assert out["status"] == "refused", f"{source!r}"
        assert out["note"], f"{source!r} was refused with no reason"
        assert out["expression"] == source


def test_dividing_by_zero_is_a_refusal_with_a_reason():
    for source in ("1/0", "0/0", "1 % 0", "1 // 0", "5 / (3 - 3)"):
        out = evaluate(source)
        assert out["status"] == "refused", f"{source!r}"
        assert "zero" in out["note"].lower(), f"{source!r}"


def test_an_over_long_expression_is_refused_before_it_is_parsed():
    """`ast.parse` is documented to be able to crash the interpreter on
    sufficiently nested input. CPython's own parser limit (a `SyntaxError` at
    depth 1000) is not ours and is not a promise. The length cap is the bound
    this module owns, and it also bounds recursion depth in the evaluator
    below it."""
    source = "(" * 5000 + "1" + ")" * 5000
    out = evaluate(source)
    assert out["status"] == "refused"
    assert str(MAX_EXPRESSION_CHARS) in out["note"]


def test_a_refusal_echoes_the_expression_but_never_a_result():
    out = evaluate("__import__('os')")
    assert out["expression"] == "__import__('os')"
    assert "result" not in out
    assert out["status"] == "refused"


def test_nothing_at_all_makes_it_raise():
    """The one hard rule: this is called from inside an agent turn, and an
    exception there costs the whole reply. Ten thousand assorted hostile
    strings, none of which may escape as an exception."""
    hostile = [
        "\x00", "\\", "1/0", "𝟙 + 1", "1" * 500, "0x" + "f" * 400,
        "1e999999999 * 1e999999999", "(-1) ** 0.5", "0 ** 0", "0 ** -1",
        "1_000 + 1", "0b1010 + 1", "0o17 + 1", "0xff + 1", ".5 + .5",
        "1.\n+1", "\t1+1", "1+1 # comment", "'''x'''", "None + 1",
    ]
    for source in hostile:
        out = evaluate(source)
        assert out["status"] in ("ok", "refused"), f"{source!r} -> {out}"


def test_it_only_ever_reports_two_states():
    """`unavailable` is the third state every other tool has and this one
    cannot: there is no clock, no socket and no injected collaborator here, so
    there is nothing that can be unreachable. A dead branch asserting
    otherwise would be a lie an operator would later act on."""
    seen = {evaluate(s)["status"] for s in
            ("1+1", "x", "1/0", "2**9999999999", "", "(1).__class__")}
    assert seen == {"ok", "refused"}
