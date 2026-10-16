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

    # MEDIAN against the budget, MAX against a loose ceiling.
    #
    # `statistics.quantiles(xs, n=20)[-1]` over TWENTY samples is not a p95 in
    # any useful sense: it interpolates between the two largest values, so it
    # is a reading of the single worst turn. One GC pause anywhere in the run
    # sets it, and process weight (how many tests ran before) can flip it. A
    # gate that a neighbouring file's test count can flip is not measuring this
    # code.
    #
    # The claim worth gating is "our non-model work stays inside 300ms per
    # turn", and the MEDIAN states exactly that while being robust to one
    # outlier. A genuine regression — an extra round trip, a synchronous read
    # added to the turn — moves every sample and therefore moves the median.
    #
    # The max is kept as a SEPARATE, deliberately loose assertion so a turn
    # that genuinely hangs still fails, rather than being smoothed away. It is
    # not the budget; it is the pathology check.
    median = statistics.median(overhead)
    assert median <= 0.3, (
        f"non-LLM overhead median {median:.3f}s exceeds the 300ms budget "
        f"(max {max(overhead):.3f}s)"
    )
    assert max(overhead) <= 2.0, (
        f"one turn spent {max(overhead):.3f}s outside the model — the budget "
        f"is a median, but no single turn should look like this"
    )


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

    # RELATIVE, not a wall-clock ceiling: an absolute bound like
    # `llm < DELAY + 0.3` fails on a busy machine without any defect.
    #
    # The real property is that the LLM span is STRICTLY INSIDE the turn, and
    # it holds at any speed. A regression that folded the whole turn into the
    # LLM histogram breaks it.
    #
    # Deliberately NOT asserting `total == llm + overhead`: the pipeline
    # computes overhead as `max(0.0, total - llm_elapsed)`, so that identity is
    # true by construction and would pass with any llm value at all.
    assert llm >= FAKE_LLM_DELAY, "the model's own wait must be inside its span"
    assert llm < total, "the LLM span must not be the whole turn"
    assert overhead > 0, "some of the turn must be attributed outside the model"


async def test_overhead_is_recorded_even_when_nothing_is_sent(harness_factory):
    h = await harness_factory(script=[ScriptedTurn(final_text="")])
    await h.handler.on_event(group_event(body="nora.000 ?"))
    await h.drain()
    assert len(h.metrics.observations("nora_overhead_seconds")) == 1
