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
    assert FAKE_LLM_DELAY <= llm < FAKE_LLM_DELAY + 0.3


async def test_overhead_is_recorded_even_when_nothing_is_sent(harness_factory):
    h = await harness_factory(script=[ScriptedTurn(final_text="")])
    await h.handler.on_event(group_event(body="nora.000 ?"))
    await h.drain()
    assert len(h.metrics.observations("nora_overhead_seconds")) == 1
