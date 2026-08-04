import pytest

from nora.agent import factory
from nora.agent.guards import ReplyGuard
from tests.builders import make_limits, make_llm_config, make_llm_tier

TIER = make_llm_tier()
LIMITS = make_limits()


@pytest.fixture(autouse=True)
def _fake_key(monkeypatch):
    monkeypatch.setenv("OMNIROUTE_API_KEY", "test-key")
    factory.close_client_sync()
    yield
    factory.close_client_sync()


def _llm_cfg():
    return make_llm_config()


def test_create_client_is_a_process_wide_singleton():
    a = factory.create_client(_llm_cfg())
    b = factory.create_client(_llm_cfg())
    assert a is b


def test_create_client_reads_the_key_from_the_environment(monkeypatch):
    monkeypatch.setenv("OMNIROUTE_API_KEY", "secret-value")
    client = factory.create_client(_llm_cfg())
    assert client.base_url.host in ("localhost", "127.0.0.1")


def test_create_client_refuses_to_start_without_the_key(monkeypatch):
    monkeypatch.delenv("OMNIROUTE_API_KEY", raising=False)
    factory.close_client_sync()
    with pytest.raises(RuntimeError, match="OMNIROUTE_API_KEY"):
        factory.create_client(_llm_cfg())


def test_model_provider_carries_the_tier_params():
    model = factory.create_model_provider(TIER, factory.create_client(_llm_cfg()))
    assert model.config["model_id"] == "deepseek-v4-flash"


def test_build_limits_matches_r8():
    assert factory.build_limits(TIER, LIMITS) == {
        "turns": 4, "output_tokens": 400, "total_tokens": 24000
    }


def test_build_limits_uses_the_active_tier_max_tokens():
    deep = make_llm_tier(model="deepseek-v4-pro", max_tokens=1200, temperature=0.5)
    assert factory.build_limits(deep, LIMITS)["output_tokens"] == 1200


def test_create_agent_is_stateless_by_construction():
    from strands.agent.conversation_manager import NullConversationManager

    agent = factory.create_agent(
        model=factory.create_model_provider(TIER, factory.create_client(_llm_cfg())),
        system_prompt="<nora_persona/>",
        tools=[],
        pre_fill=[{"role": "user", "content": [{"text": "ctx"}]}],
        guard=ReplyGuard(conversation_id="g1"),
        trace_attributes={"tag_id": "t1"},
    )
    assert isinstance(agent.conversation_manager, NullConversationManager)
    assert agent.system_prompt == "<nora_persona/>"


def test_create_agent_installs_the_prefill_as_conversation_history():
    agent = factory.create_agent(
        model=factory.create_model_provider(TIER, factory.create_client(_llm_cfg())),
        system_prompt="p", tools=[],
        pre_fill=[{"role": "user", "content": [{"text": "ctx"}]},
                  {"role": "assistant", "content": [{"text": "ok"}]}],
        guard=ReplyGuard(conversation_id="g1"), trace_attributes={},
    )
    assert len(agent.messages) == 2


def test_create_agent_suppresses_the_default_callback_handler():
    from strands.handlers.callback_handler import null_callback_handler

    agent = factory.create_agent(
        model=factory.create_model_provider(TIER, factory.create_client(_llm_cfg())),
        system_prompt="p", tools=[], pre_fill=[],
        guard=ReplyGuard(conversation_id="g1"), trace_attributes={},
    )
    # Agent.__init__ normalizes an explicit `callback_handler=None` into
    # null_callback_handler (a no-op) rather than storing literal None — see
    # strands.agent.agent.Agent.__init__: "If explicitly set to None,
    # null_callback_handler is used." What matters here is that the default
    # PrintingCallbackHandler (which writes to stdout) was NOT installed.
    assert agent.callback_handler is null_callback_handler


def test_two_agents_share_no_state():
    """D4: fresh agent per tag; continuity is SQLite + Honcho, never agent memory."""
    model = factory.create_model_provider(TIER, factory.create_client(_llm_cfg()))
    a = factory.create_agent(model=model, system_prompt="p", tools=[],
                             pre_fill=[{"role": "user", "content": [{"text": "a"}]}],
                             guard=ReplyGuard(conversation_id="g1"), trace_attributes={})
    b = factory.create_agent(model=model, system_prompt="p", tools=[], pre_fill=[],
                             guard=ReplyGuard(conversation_id="g1"), trace_attributes={})
    assert a.messages is not b.messages
    assert len(b.messages) == 0
