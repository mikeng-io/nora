import statistics

from nora.testing import ScriptedTurn, group_event

FAKE_LLM_DELAY = 0.8      # E.5: a fixed 800ms model delay isolates our overhead


def _script():
    return [ScriptedTurn(tool_calls=[("send_reply", {"text": "ok"})], final_text="ok")]


async def test_overhead_stays_within_the_budget(harness_factory):
    h = await harness_factory(script=_script(), delay=FAKE_LLM_DELAY, debounce=0)
    for i in range(20):
        await h.handler.on_event(group_event(body=f"nora.000 q{i}", ts=i))
        await h.drain()

    overhead = h.metrics.observations("nora_overhead_seconds")
    assert len(overhead) == 20
    p95 = statistics.quantiles(overhead, n=20)[-1]
    assert p95 <= 0.3, f"non-LLM overhead p95 {p95:.3f}s exceeds the 300ms budget"


async def test_end_to_end_latency_includes_the_model(harness_factory):
    h = await harness_factory(script=_script(), delay=FAKE_LLM_DELAY)
    await h.handler.on_event(group_event(body="nora.000 ?"))
    await h.drain()
    total = h.metrics.observations("nora_tag_latency_seconds")[0]
    assert total >= FAKE_LLM_DELAY


async def test_llm_latency_is_recorded_separately(harness_factory):
    h = await harness_factory(script=_script(), delay=FAKE_LLM_DELAY)
    await h.handler.on_event(group_event(body="nora.000 ?"))
    await h.drain()
    llm = h.metrics.observations("nora_llm_latency_seconds")[0]
    total = h.metrics.observations("nora_tag_latency_seconds")[0]
    overhead = h.metrics.observations("nora_overhead_seconds")[0]

    # RELATIVE, not a wall-clock ceiling. This asserted `llm < DELAY + 0.3`,
    # which is a proxy for «the model's span, not the whole turn» that a busy
    # machine fails on its own — it went red once in a full-suite run and
    # passed alone, which is the signature of an absolute bound under load
    # rather than of a defect (same class as the compute-backend timing tests
    # fixed 2026-08-14).
    #
    # The real property is that the LLM span is STRICTLY INSIDE the turn, and
    # it holds at any speed. A regression that folded the whole turn into the
    # LLM histogram breaks this exactly as it broke the old bound.
    #
    # Deliberately NOT asserting `total == llm + overhead`: `pipeline.py:621`
    # computes overhead as `max(0.0, total - llm_elapsed)`, so that identity is
    # true by construction and would pass with any llm value at all. It reads
    # like a strong invariant and measures nothing — the trap this file's own
    # history is about.
    assert llm >= FAKE_LLM_DELAY, "the model's own wait must be inside its span"
    assert llm < total, "the LLM span must not be the whole turn"
    assert overhead > 0, "some of the turn must be attributed outside the model"


async def test_overhead_is_recorded_even_when_nothing_is_sent(harness_factory):
    h = await harness_factory(script=[ScriptedTurn(final_text="")])
    await h.handler.on_event(group_event(body="nora.000 ?"))
    await h.drain()
    assert len(h.metrics.observations("nora_overhead_seconds")) == 1
