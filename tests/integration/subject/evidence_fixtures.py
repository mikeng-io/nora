"""Generated input metadata for consumer controls; no subject execution is claimed."""


def judge_trajectory(**fields):
    return {"model_calls": [{"model": "generated-subject", "messages": [
        {"role": "system", "content": "Generated subject instructions for a protocol control."},
        {"role": "user", "content": "Generated subject input."}]}],
        "effects": {"status": "observed", "items": [], "tools": []}, **fields}
