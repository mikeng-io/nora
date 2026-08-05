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
    assert factory.build_limits(max_tokens=400, turns=4, total_tokens=24000) == {
        "turns": 4, "output_tokens": 400, "total_tokens": 24000
    }


def test_build_limits_uses_the_active_tier_max_tokens():
    limits = factory.build_limits(max_tokens=1200, turns=4, total_tokens=24000)
    assert limits["output_tokens"] == 1200


def test_create_agent_is_stateless_by_construction():
    from strands.agent.conversation_manager import NullConversationManager

    model = factory.create_model_provider(
        model=MODEL, max_tokens=MAX_TOKENS, temperature=TEMPERATURE, client=_client()
    )
    agent = factory.create_agent(
        model=model,
        system_prompt="<nora_persona/>",
        tools=[],
        pre_fill=[{"role": "user", "content": [{"text": "ctx"}]}],
        guard=_guard(),
        trace_attributes={"tag_id": "t1"},
    )
    assert isinstance(agent.conversation_manager, NullConversationManager)
    assert agent.system_prompt == "<nora_persona/>"


def test_create_agent_installs_the_prefill_as_conversation_history():
    model = factory.create_model_provider(
        model=MODEL, max_tokens=MAX_TOKENS, temperature=TEMPERATURE, client=_client()
    )
    agent = factory.create_agent(
        model=model, system_prompt="p", tools=[],
        pre_fill=[{"role": "user", "content": [{"text": "ctx"}]},
                  {"role": "assistant", "content": [{"text": "ok"}]}],
        guard=_guard(), trace_attributes={},
    )
    assert len(agent.messages) == 2


def test_create_agent_suppresses_the_default_callback_handler():
    from strands.handlers.callback_handler import null_callback_handler

    model = factory.create_model_provider(
        model=MODEL, max_tokens=MAX_TOKENS, temperature=TEMPERATURE, client=_client()
    )
    agent = factory.create_agent(
        model=model, system_prompt="p", tools=[], pre_fill=[],
        guard=_guard(), trace_attributes={},
    )
    # Agent.__init__ normalizes an explicit `callback_handler=None` into
    # null_callback_handler (a no-op) rather than storing literal None — see
    # strands.agent.agent.Agent.__init__: "If explicitly set to None,
    # null_callback_handler is used." What matters here is that the default
    # PrintingCallbackHandler (which writes to stdout) was NOT installed.
    assert agent.callback_handler is null_callback_handler


def test_two_agents_share_no_state():
    """D4: fresh agent per tag; continuity is SQLite + Honcho, never agent memory."""
    model = factory.create_model_provider(
        model=MODEL, max_tokens=MAX_TOKENS, temperature=TEMPERATURE, client=_client()
    )
    a = factory.create_agent(
        model=model, system_prompt="p", tools=[],
        pre_fill=[{"role": "user", "content": [{"text": "a"}]}],
        guard=_guard(), trace_attributes={},
    )
    b = factory.create_agent(
        model=model, system_prompt="p", tools=[], pre_fill=[],
        guard=_guard(), trace_attributes={},
    )
    assert a.messages is not b.messages
    assert len(b.messages) == 0
