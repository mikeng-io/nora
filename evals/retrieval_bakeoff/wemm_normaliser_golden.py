"""Goldens for `adapters.wemm_prompt` from WeMM-Embedding-2B's own tokenizer.

llama-server does not normalise, so the adapter must send the string the
official tokenizer would have produced. For a seeded fuzz corpus (every added
token, newlines, carriage returns, tabs, decomposed accents, CJK), each text is
rendered through the official sentence-transformers template and encoded by
the official tokenizer.json (normaliser, added-token split, post-processor).
The expected prompt is those ids decoded, minus the appended `<embedding>`,
and each one is checked by re-encoding it with the normaliser OFF: the ids
must be identical, so a prompt equal to the golden is the model's input as
llama-server tokenizes it. Writes the goldens to
`tests/fixtures/wemm_normaliser_golden.json`; `tests/evals/test_bakeoff_wemm.py`
compares the adapter against them.

    python -I wemm_normaliser_golden.py <snapshot dir of tencent/WeMM-Embedding-2B @ REV> OUT

Needs `tokenizers` and `jinja2`, which the gate never installs.
"""

import hashlib
import json
import random
import sys
from pathlib import Path

from jinja2 import Environment  # type: ignore[import-not-found]
from tokenizers import Tokenizer  # type: ignore[import-not-found]

REV = "bbd6cd4bf52cfc6716f752a2df80b2706720bd95"
TOKENIZER_SHA = "40e444c744512f423da4c8443c47c21e22ff76056ba4e9796a81c04c13a9daf0"
SEED, CASES, MAX_LEN = 0, 3000, 10


def main(snapshot: str, out: str) -> None:
    root = Path(snapshot)
    raw_json = (root / "tokenizer.json").read_bytes()
    if hashlib.sha256(raw_json).hexdigest() != TOKENIZER_SHA:
        raise SystemExit("tokenizer.json is not the pinned one")
    template_path = root / "additional_chat_templates" / "sentence_transformers.jinja"
    template = template_path.read_text()
    tok = Tokenizer.from_str(raw_json.decode())
    spec = json.loads(raw_json)
    spec["normalizer"] = None
    bare = Tokenizer.from_str(json.dumps(spec))
    added = [t["content"] for t in spec["added_tokens"]]
    embedding = tok.token_to_id("<embedding>")
    render = Environment().from_string(template).render  # noqa: S701 - text, not HTML

    alphabet = ["a", "b", " ", "\n", "\n", "\n", "\r", "\t", "é", "é", "打", "風",
                "Å", *added]
    rng = random.Random(SEED)  # noqa: S311 - a fuzz corpus, not a secret
    texts = ["", "a\n\nb", "a\n", "\n", "\n\n", "\nfoo"]
    while len(texts) < CASES:
        texts.append("".join(rng.choice(alphabet) for _ in range(rng.randint(0, MAX_LEN))))
    cases = []
    for text in texts:
        rendered = render(messages=[{"role": "user", "content": [{"type": "text", "text": text}]}])
        ids = tok.encode(rendered, add_special_tokens=True).ids
        if ids[-1] != embedding:
            raise SystemExit(f"{text!r}: the post-processor did not append <embedding>")
        prompt = tok.decode(ids[:-1], skip_special_tokens=False)
        if bare.encode(prompt, add_special_tokens=True).ids != ids:
            raise SystemExit(f"{text!r}: the decoded prompt does not re-encode to the same ids")
        cases.append({"text": text, "prompt": prompt})
    Path(out).write_text(json.dumps({
        "model": "tencent/WeMM-Embedding-2B", "revision": REV, "tokenizer_sha256": TOKENIZER_SHA,
        "template_sha256": hashlib.sha256(template.encode()).hexdigest(),
        "generator": "evals/retrieval_bakeoff/wemm_normaliser_golden.py", "seed": SEED,
        "added_tokens": added, "cases": cases,
    }, ensure_ascii=False, indent=1) + "\n")
    print(f"{len(cases)} cases, every prompt re-encodes to the official ids")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
