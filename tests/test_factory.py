import pytest

from nora.agent import factory
from nora.agent.guards import ReplyGuard
from nora.agent.registry import ToolRegistry
from nora.obs.metrics import Metrics

BASE_URL = "http://localhost:20128/v1"
BUDGETS = ToolRegistry().budgets()
API_KEY = "test-key"
MODEL = "deepseek-v4-flash"
MAX_TOKENS = 400
TEMPERATURE = 0.7
RETRY = factory.build_retry_policy(timeout_seconds=30.0)


def _client() -> object:
    return factory.create_client(base_url=BASE_URL, api_key=API_KEY)


def _guard() -> ReplyGuard:
    return ReplyGuard(conversation_id="g1", budgets=BUDGETS, metrics=Metrics())


def test_create_client_is_a_plain_constructor_not_a_memoized_singleton():
    """DEC-13: `create_client` used to memoize onto a module-level `_CLIENT`
    global and return the same object on every call. That global is gone —
    "one client for the whole process" is now a property of composition-root
    *call discipline* (`build_dependencies` calls this exactly once and
    threads the result through `Dependencies.client`), not of this
    function, so two direct calls now legitimately return two different
    instances."""
    a = factory.create_client(base_url=BASE_URL, api_key=API_KEY)
    b = factory.create_client(base_url=BASE_URL, api_key=API_KEY)
    assert a is not b


def test_create_client_uses_the_given_base_url():
    client = factory.create_client(base_url=BASE_URL, api_key=API_KEY)
    assert client.base_url.host in ("localhost", "127.0.0.1")


def test_create_client_refuses_to_start_without_an_api_key():
    with pytest.raises(RuntimeError, match="api_key"):
        factory.create_client(base_url=BASE_URL, api_key="")


def test_model_provider_carries_the_tier_params():
    model = factory.create_model_provider(
        model=MODEL, max_tokens=MAX_TOKENS, temperature=TEMPERATURE, client=_client()
    )
    assert model.config["model_id"] == "deepseek-v4-flash"


def test_build_limits_matches_r8():
    assert factory.build_limits(output_tokens=12000, turns=4, total_tokens=64000) == {
        "turns": 4, "output_tokens": 12000, "total_tokens": 64000
    }


def test_build_limits_takes_the_loop_budget_and_never_the_per_call_ceiling():
    """R-24, at this function's own level.

    `output_tokens` used to BE `max_tokens` — the loop was allowed to emit
    exactly what one call was allowed to emit, and measured calls emitted
    99.8% of it (3,993 of 4,000). There is no longer a `max_tokens` parameter
    to pass, which is what makes the regression unspellable rather than merely
    unlikely: the only way back is to change the signature.
    """
    import inspect

    params = inspect.signature(factory.build_limits).parameters
    assert "max_tokens" not in params, "the per-call ceiling is not the loop's budget"
    assert set(params) == {"output_tokens", "turns", "total_tokens"}
    # And the value is passed through untouched, not derived from anything.
    assert factory.build_limits(
        output_tokens=999, turns=4, total_tokens=64000,
    )["output_tokens"] == 999


def test_create_agent_is_stateless_by_construction():
    from strands.agent.conversation_manager import NullConversationManager

    model = factory.create_model_provider(
        model=MODEL, max_tokens=MAX_TOKENS, temperature=TEMPERATURE, client=_client()
    )
    agent = factory.create_agent(
        model=model,
        system_prompt="<nora_doctrine/>",
        tools=[],
        pre_fill=[{"role": "user", "content": [{"text": "ctx"}]}],
        guard=_guard(),
        trace_attributes={"tag_id": "t1"},
        retry=RETRY,
    )
    assert isinstance(agent.conversation_manager, NullConversationManager)
    assert agent.system_prompt == "<nora_doctrine/>"


def test_create_agent_installs_the_prefill_as_conversation_history():
    model = factory.create_model_provider(
        model=MODEL, max_tokens=MAX_TOKENS, temperature=TEMPERATURE, client=_client()
    )
    agent = factory.create_agent(
        model=model, system_prompt="p", tools=[],
        pre_fill=[{"role": "user", "content": [{"text": "ctx"}]},
                  {"role": "assistant", "content": [{"text": "ok"}]}],
        guard=_guard(), trace_attributes={}, retry=RETRY,
    )
    assert len(agent.messages) == 2


def test_create_agent_suppresses_the_default_callback_handler():
    from strands.handlers.callback_handler import null_callback_handler

    model = factory.create_model_provider(
        model=MODEL, max_tokens=MAX_TOKENS, temperature=TEMPERATURE, client=_client()
    )
    agent = factory.create_agent(
        model=model, system_prompt="p", tools=[], pre_fill=[],
        guard=_guard(), trace_attributes={}, retry=RETRY,
    )
    # Agent.__init__ normalizes an explicit `callback_handler=None` into
    # null_callback_handler (a no-op) rather than storing literal None — see
    # strands.agent.agent.Agent.__init__: "If explicitly set to None,
    # null_callback_handler is used." What matters here is that the default
    # PrintingCallbackHandler (which writes to stdout) was NOT installed.
    assert agent.callback_handler is null_callback_handler


def test_the_retry_budget_fits_inside_the_invocation_timeout():
    """DEC-25, and the assertion that makes A8's `reason` label honest.

    Strands' default strategy sleeps 4+8+16+32+64 = 124s across six attempts.
    `nora.engagement.stages.invoke` wraps the whole invocation in
    `asyncio.wait_for(timeout=llm.timeout_seconds)` and the retry sleeps happen
    INSIDE it, so with the default a single throttle guarantees the timeout
    fires first — and the turn is then counted
    `nora_silent_failures_total{reason="invocation_timeout"}` when the cause was
    throttling. The invariant is the whole point: whatever the timeout, the
    worst-case backoff must leave room for the calls themselves.
    """
    for timeout in (5.0, 15.0, 30.0, 60.0, 120.0):
        policy = factory.build_retry_policy(timeout_seconds=timeout)
        assert policy.total_backoff_seconds < timeout * factory.RETRY_BACKOFF_FRACTION + 1
        assert policy.total_backoff_seconds < timeout
        assert policy.max_attempts >= 1


def test_the_retry_budget_at_todays_thirty_second_timeout():
    policy = factory.build_retry_policy(timeout_seconds=30.0)
    assert (policy.max_attempts, policy.initial_delay) == (4, 1)
    assert policy.total_backoff_seconds == 7          # 1 + 2 + 4


def test_a_timeout_too_short_for_even_one_retry_still_produces_a_usable_policy():
    """`max_attempts=1` is Strands' own "no retries" value (it is what
    `retry_strategy=None` becomes). A tier configured with a 1s timeout must
    still get an agent, not an exception at startup."""
    policy = factory.build_retry_policy(timeout_seconds=1.0)
    assert policy.max_attempts == 1
    assert policy.total_backoff_seconds == 0


def test_a_zero_initial_delay_is_clamped_rather_than_looping_forever():
    """`initial_delay=0` would never grow the accumulated backoff, so the ladder
    search would not terminate. It is clamped to one second."""
    policy = factory.build_retry_policy(timeout_seconds=30.0, initial_delay=0)
    assert policy.initial_delay == 1


def test_the_agent_carries_the_bounded_retry_strategy_not_the_sdk_default():
    from strands import ModelRetryStrategy

    model = factory.create_model_provider(
        model=MODEL, max_tokens=MAX_TOKENS, temperature=TEMPERATURE, client=_client()
    )
    agent = factory.create_agent(
        model=model, system_prompt="p", tools=[], pre_fill=[],
        guard=_guard(), trace_attributes={}, retry=RETRY,
    )
    strategy = agent._retry_strategy
    assert isinstance(strategy, ModelRetryStrategy)
    assert strategy._max_attempts == RETRY.max_attempts
    assert strategy._initial_delay == RETRY.initial_delay
    # The SDK defaults, spelled out so this fails if it silently reverts to them.
    assert (strategy._max_attempts, strategy._initial_delay, strategy._max_delay) != (6, 4, 240)


def test_each_agent_gets_its_own_retry_strategy_instance():
    """`ModelRetryStrategy` holds `_current_attempt`, mutated per invocation.
    Two overlapping tags sharing one instance would let one tag's throttle
    decide the other's backoff — the same per-tag-state rule `ReplyGuard`
    follows."""
    model = factory.create_model_provider(
        model=MODEL, max_tokens=MAX_TOKENS, temperature=TEMPERATURE, client=_client()
    )
    kwargs = {"model": model, "system_prompt": "p", "tools": [], "pre_fill": [],
              "trace_attributes": {}, "retry": RETRY}
    a = factory.create_agent(guard=_guard(), **kwargs)
    b = factory.create_agent(guard=_guard(), **kwargs)
    assert a._retry_strategy is not b._retry_strategy


def test_the_agent_is_named_so_a_trace_can_be_found():
    """DEC-26: Strands names the agent span `invoke_agent {agent.name}` and
    defaults `name` to "Strands Agents", so every span this process produced was
    identical and unsearchable."""
    model = factory.create_model_provider(
        model=MODEL, max_tokens=MAX_TOKENS, temperature=TEMPERATURE, client=_client()
    )
    agent = factory.create_agent(
        model=model, system_prompt="p", tools=[], pre_fill=[],
        guard=_guard(), trace_attributes={"tag_id": "t1"}, retry=RETRY,
    )
    assert agent.name == "nora"
    assert agent.agent_id == "nora"
    assert agent.trace_attributes["tag_id"] == "t1"


def test_two_agents_share_no_state():
    """D4: fresh agent per tag; continuity is SQLite + Honcho, never agent memory."""
    model = factory.create_model_provider(
        model=MODEL, max_tokens=MAX_TOKENS, temperature=TEMPERATURE, client=_client()
    )
    a = factory.create_agent(
        model=model, system_prompt="p", tools=[],
        pre_fill=[{"role": "user", "content": [{"text": "a"}]}],
        guard=_guard(), trace_attributes={}, retry=RETRY,
    )
    b = factory.create_agent(
        model=model, system_prompt="p", tools=[], pre_fill=[],
        guard=_guard(), trace_attributes={}, retry=RETRY,
    )
    assert a.messages is not b.messages
    assert len(b.messages) == 0
