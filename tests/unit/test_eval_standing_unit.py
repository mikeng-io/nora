"""The unit eval measures a saved Record's fold, never another agent turn."""



def test_unit_judge_reads_the_unit_contract_instead_of_a_room_recap():
    from evals.rebuilt.judge import _build_position_prompt

    prompt = _build_position_prompt(
        kind="faithful", prop="retain the scheduled check",
        evidence="generated completed session", position="generated unit state",
    )
    assert "UNIT STANDING" in prompt
    assert "Scheduling is not delivery" in prompt
    assert "wrote about one room" not in prompt
    assert "generated completed session" in prompt
    assert "generated unit state" in prompt

