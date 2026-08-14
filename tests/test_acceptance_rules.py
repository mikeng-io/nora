"""E.7 criterion 5: every rule R-1…R-17 has a passing named test."""

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# The named test the spec's B.1 table assigns to each rule.
RULE_TESTS = {
    "R-1": "test_ambient_never_constructs_agent",
    # R-1b: R-1's FIRST clause, revised. `nora.app.fire.FirePath` made "only the
    # tagging path may cause a send" false — a fire turn is a send not caused by
    # a tag. R-1 keeps its entry rather than losing it (the DEC-40 precedent
    # below): its test asserts BOTH clauses and the second one — ambient traffic
    # never reaches the agent — is untouched and still enforced. But it harnesses
    # only ambient input, so it stays green no matter how many non-tag send paths
    # exist, which makes it no brake at all on the revised clause. R-1b is that
    # brake: `Deliverer` is the only caller of `adapter.send`, it is constructed
    # only by `deliver.build`, and `deliver.build` has exactly two callers — the
    # tag pipeline and the fire path.
    "R-1b": "test_only_the_tag_path_and_the_fire_path_can_send",
    "R-2": "test_send_reply_past_the_cap_is_cancelled",
    "R-2b": "test_deliverer_latch_is_single_shot",
    "R-3": "test_send_to_other_group_cannot_escape_the_tagged_conversation",
    "R-4": "test_empty_reply_not_sent",
    "R-5": "test_silent_failure_matrix",
    "R-6": "test_leak_guard_blocks_canary",
    "R-6b": "test_leak_guard_blocks_doctrine_verbatim",
    "R-6c": "test_leak_guard_allows_normal_reply",
    "R-7": "test_tool_call_budget_enforced_for_load_transcript",
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
    "R-23": "test_a_budget_stop_is_distinguishable_from_a_silent_model",
    "R-24": "test_no_single_call_can_spend_the_whole_turn_budget",
    "R-27": "test_an_unknown_route_is_never_reported_as_no_service",
    # R-27's own rule on the place axis, not a new rule: «that place is not in
    # the stop data» and «that place exists but this route does not go there»
    # are different claims, and collapsing them is the same wrong-source-read-
    # as-nothing-found trap R-27 was registered for.
    "R-27b": "test_a_place_the_route_does_not_serve_is_never_no_such_place",
    # R-28: the OUTBOUND egress boundary for routing. A route query contains two
    # locations that leave the host to Google; a person's name or a resolved
    # position in origin/destination is a hard block, refused before any HTTP
    # call (design/14 §2.2, DEC-46). design/14 §2.3 reserved R-27/R-28 for
    # grounding; R-27 went to transit, so this is R-28 — search/fetch, when they
    # ship, take the next free number.
    "R-28": "test_a_route_naming_a_person_is_refused_before_egress",
    # R-2/R-2b keep their entries rather than gaining new ones: DEC-40 revised
    # the RULE (one message became `send_reply`'s budget) and both named tests
    # were rewritten in place to assert the revised invariant, which is what
    # `design/proposed/16-multi-message-replies.md` §6.7 asks for. A new key
    # would leave the old one pointing at a rule nobody enforces.
    "R-25": "test_a_turn_cut_off_between_messages_is_counted_as_partial",
    # R-29: a skill source is operator configuration only. `SkillStore.resolve`
    # is a pure name lookup, so a message can name a skill the operator shipped
    # (the intended activation) but can never introduce a new source — item 6
    # can never reach item 5 (design/12 §2, DEC-48).
    "R-29": "test_a_message_can_name_a_skill_but_never_load_one",
    # R-29: a second tag for a conversation already handling one is QUEUED, not
    # dropped — three people tagging at once each get a reply, in FIFO order,
    # while different conversations still run concurrently. Next free number
    # after R-28 (R-26 is reserved by design/proposed/18 for the unshipped
    # context-budget work), DEC-48. See `nora.engagement.queue`.
    "R-30": "test_three_tags_for_one_conversation_are_answered_in_fifo_order",
    # R-31: the presence disclosure seam is fail-closed (design/19 §5, DEC-54).
    # `whereis` refuses to disclose a circle member's position into a
    # conversation whose people are NOT a subset of the circle — a REFUSAL, not
    # a silent omission. This is the INVERSE of the room tools' R-21 and nothing
    # else in Nora enforced it; the reviews were explicit that «the same rule the
    # roster uses» was an overstatement. Next free number after R-30.
    "R-31": "test_whereis_refuses_to_disclose_outside_the_circle",
    # R-32: the collector is a change-recorder, not a sampler (design/19 §1,
    # DEC-55). A row is written only when a member's `ts` advances, so two reads
    # of an UNMOVED member produce ONE row — the property that makes presence a
    # time-series of distinct fixes rather than a duplication of every poll.
    "R-32": "test_an_unmoved_member_is_recorded_once",
    # R-33: media_fetch reads a stored image back through MiMo-V2.5, scoped to
    # THIS conversation's custody over a bounded window, cached by content hash
    # and screened on every serve, under its own vision timeout (derived from
    # llm.timeout_seconds, provably < the loop budget) and byte ceiling. The
    # MiMo description is untrusted egress — identifier-screened like `about`,
    # with the honest caveat that a bare local number is a residual risk equal
    # to R-18's. Image only; `not_an_image` for the rest; inert-by-config behind
    # [media] enabled. Next free number after the presence pair R-31/R-32
    # (R-26 is still reserved by design/proposed/18). See DEC-56.
    "R-33": "test_a_stored_image_is_read_only_within_scope_and_screened_on_serve",
    # R-34 RETIRED (DEC-73, 2026-08-08): it was the `fetch` fence — "may open only
    # a URL a search returned this turn" — defence-in-depth for the SSRF surface a
    # self-hosted Firecrawl created by fetching a model-chosen URL. `web_fetch` and
    # Firecrawl are gone (Tavily searches from its own servers and is never handed
    # a URL), so there is no fence and no rule; the number is not reused.
    # R-35: saved-place resolution is subset-scoped and falls through, never
    # refuses (design/23 §4.1, DEC-60). A room that is not a verified subset of
    # the circle resolves NO household place name — silently, so an outsider
    # cannot learn a guarded name EXISTS (the deliberate inverse of R-31's loud
    # refusal: there the subject is a person's position and silence would lie;
    # here the dictionary entry's existence is itself the secret).
    "R-35": "test_a_non_subset_room_resolves_no_saved_place",
    # R-36…R-39: reminders + the heartbeat (DEC-70). The next free numbers after
    # R-35; R-26 is still reserved by design/proposed/18.
    #
    # R-36: a reminder NEVER fires early. The tick's batch is `due_at <= now` and
    # nothing else — no rounding, no "close enough", no catch-up window. It is
    # the first rule because it is the one a scheduler gets wrong quietly: firing
    # a minute early is a message that arrives before the thing it is about, and
    # nobody reports it as a bug.
    "R-36": "test_a_row_not_yet_due_does_not_fire",
    # R-37: a row fires ONCE, ever. Taking a row is a compare-and-set on
    # `state = 'pending'` inside the statement, not a read-then-write: `due()` is
    # a query rather than a claim, and the tick awaits a turn per group before it
    # reaches the later rows, so a cancel landing in that window must win. With
    # `mark` doing the take it did not, and a cancelled row went
    # CANCELLED -> FIRING -> DONE and was spoken.
    "R-37": "test_claim_is_a_compare_and_set_from_pending",
    # R-37b: the same rule across a CRASH, where it is a decision rather than a
    # mechanism (§8.2). A row left FIRING resolves to FAILED and is never
    # re-queued: nothing links a row to the message it may already have
    # delivered, so re-queueing double-sends anything that died after egress.
    # At-most-once — losing one reminder visibly beats saying it twice.
    "R-37b": "test_sweep_resolves_firing_rows_to_failed",
    # R-38: only a MAPPED person may create a reminder (§12). `requester_person_id`
    # is what the fire-time policy re-check runs against, so an unmapped sender
    # has no owner to write and inventing one would put unprompted speech into a
    # room under nobody's name. Enforced at `remind()` and re-checked at fire
    # time, because identity.toml loads once at startup and the horizon is 30 days.
    "R-38": "test_remind_refuses_an_unmapped_requester",
    # R-39: `remind` refuses on a FIRE TURN — the structural bar on recurrence
    # (§7.1, §9). Without it Nora re-arms her own reminder each time one comes
    # due, and none of §9's caps can stop her: `max_pending_per_requester` counts
    # PENDING rows and the fire has already freed one before the replacement is
    # written. The flag is turn PROVENANCE (`fire_turn`), threaded from
    # `ToolRegistry.build`, because a fire turn is otherwise a well-formed turn
    # in the same conversation and no tool can tell the two apart.
    "R-39": "test_remind_refuses_on_a_fire_turn",
    # R-40: an MCP server's tools reach `Agent(tools=...)` for a tag only when
    # `policy.toml`'s per-conversation `tools` allowlist names that server —
    # resolved and enforced at ASSEMBLY (`EngagementPipeline._run_admitted`
    # calling `ToolRegistry.build(..., allowed_mcp=...)`), never at egress
    # (DEC-71).
    "R-40": "test_unauthorized_conversation_never_receives_pulse_tools",
    # R-41: matching an unlabeled entry in something looked up (a handle, an
    # id) to the person Nora is talking to requires an actual link — never
    # inferred from rank or list order (DEC-75). Behavioural, same shape as
    # R-14: asserted by conduct.md, measured by the adversarial corpus — NOT
    # a currently-passing gate (DEC-75's own text records the measured
    # failure rate; the test below only pins that the corpus exists).
    "R-41": "test_r41_is_covered_by_the_identity_grounding_corpus",
    # R-42: a version is announced AT MOST ONCE, per room, ever (DEC-97). The
    # next free number after R-41. Nora's second unprompted-speech occasion, and
    # the failure it exists to prevent is specific: a container crash-looping
    # every few seconds would otherwise post the same upgrade note into a group
    # chat on every boot. The claim is an INSERT on a primary key taken BEFORE
    # the turn, so a row EXISTING — announced, failed, or stranded mid-turn —
    # bars a second attempt. That is R-37b's at-most-once decision applied to a
    # second path, and for the same reason: nothing links a claim to a message
    # that may already have reached the group.
    #
    # The named test simulates the restart the way a restart works — everything
    # in-process rebuilt, only the database surviving — because an in-memory
    # claim (or a read-then-write) passes any single-process assertion.
    "R-42": "test_a_restart_never_re_announces_the_same_version",
    # R-43: a compute job is delivered AT MOST ONCE, ever — the same
    # discipline R-37/R-37b hold for reminders, applied to the third
    # Occasion (DEC-99). The next free number after R-42.
    "R-43": "test_a_job_never_delivered_twice",
    # R-45: a one-shot nudge, not a refusal — make_artifact called with
    # context empty after place_info/route already ran this turn (DEC-101).
    # R-44 was withdrawn 2026-08-14 (the prior occupant of this range); the
    # next free number is R-45, not a reuse.
    "R-45": "test_make_artifact_is_nudged_once_when_context_is_empty_after_gathering",
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
    """R-14 is behavioural: asserted by constraints.md, measured by the corpus."""
    assert (ROOT / "evals" / "adversarial" / "injections.yaml").exists()
    assert (ROOT / "evals" / "adversarial" / "transcript_injection.yaml").exists()


def test_r41_is_covered_by_the_identity_grounding_corpus():
    """R-41 is behavioural: asserted by conduct.md, measured by this corpus.

    Unlike R-14, this corpus is NOT currently a passing gate — DEC-75 records
    that the shipped doctrine wording still fabricates on 3/3 of its ambiguous
    cases (the grounded judge's number; the earlier "0/3" was the BLIND judge
    failing to detect it). This test pins that the measurement exists and is
    findable, not that the rule holds; see DEC-75 for the honest number.
    """
    assert (ROOT / "evals" / "adversarial" / "identity_grounding.yaml").exists()
