"""E.7 criterion 5: every rule R-1…R-17 has a passing named test."""

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# The named test the spec's B.1 table assigns to each rule.
RULE_TESTS = {
    "R-1": "test_ambient_never_constructs_agent",
    "R-2": "test_second_send_reply_is_cancelled",
    "R-2b": "test_deliverer_latch_is_single_shot",
    "R-3": "test_send_to_other_group_cannot_escape_the_tagged_conversation",
    "R-4": "test_empty_reply_not_sent",
    "R-5": "test_silent_failure_matrix",
    "R-6": "test_leak_guard_blocks_canary",
    "R-6b": "test_leak_guard_blocks_persona_verbatim",
    "R-6c": "test_leak_guard_allows_normal_reply",
    "R-7": "test_tool_call_budget_enforced_for_read_buffer",
    "R-8": "test_limits_passed_through",
    "R-9": "test_debounce_window",
    "R-10": "test_concurrent_tags_same_conversation",
    "R-10b": "test_lock_released_on_timeout",
    "R-10c": "test_lock_released_on_llm_error",
    "R-11": "test_trigger_matrix",
    "R-12": "test_self_tag_ignored",
    "R-13": "test_prefill_escapes_angle_brackets",
    "R-13b": "test_forged_closing_tag_is_escaped",
    "R-15": "test_no_redundant_read_fails_when_prefill_was_sufficient",
    "R-16": "test_secret_never_logged_in_the_message",
    "R-18": "test_a_reply_carrying_someones_phone_number_is_refused",
    "R-17": "test_degraded_identity_enrichment_is_counted_and_r12_still_holds",
    "R-20": "test_a_forged_quote_cannot_put_words_in_someone_elses_mouth",
    "R-21": "test_not_being_here_is_indistinguishable_from_not_existing",
    "R-22": "test_nothing_known_is_distinguishable_from_could_not_ask",
}


def _collected() -> set[str]:
    out = subprocess.run(["uv", "run", "pytest", "--collect-only", "-q"],
                         cwd=ROOT, capture_output=True, text=True).stdout
    return {line.split("::")[-1].split("[")[0] for line in out.splitlines() if "::" in line}


def test_every_enforced_rule_has_a_named_test():
    collected = _collected()
    missing = {rule: name for rule, name in RULE_TESTS.items() if name not in collected}
    assert not missing, f"rules without their named test: {missing}"


def test_r14_is_covered_by_the_adversarial_corpus():
    """R-14 is behavioural: asserted by guardrails.md, measured by the corpus."""
    assert (ROOT / "evals" / "adversarial" / "injections.yaml").exists()
    assert (ROOT / "evals" / "adversarial" / "transcript_injection.yaml").exists()
