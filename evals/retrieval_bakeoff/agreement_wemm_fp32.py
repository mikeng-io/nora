"""Is the text-only fp32 load the model?

The bake-off's fp32 WeMM arms (`results/20261015T123946Z-stage2-wemm-fp32-*`)
load tencent/WeMM-Embedding-2B through `adapters._load_sentence_transformer`
with `text_only=True`: bf16 weights, `lm_head` and the vision tower replaced
by modules that raise, then cast to fp32. This embeds 24 set texts (the first
12 messages of texts.yaml, the first 12 cantonese queries) three ways and
prints one aggregates-only JSON (item ids, no text, no vector):

- text-only fp32 against the whole checkpoint at fp32: must be bitwise equal,
  since neither dropped part is reached by a text embedding;
- text-only fp32 against the whole checkpoint at bf16, its own dtype.

Needs about 20 GB of free memory (the whole fp32 model is 10.1 GiB of
weights), so it runs on a development machine, not the deploy host:

    HF_HUB_OFFLINE=1 python -I agreement_wemm_fp32.py > results/<stamp>-wemm-fp32-agreement.json

Result: `results/20261015T123946Z-wemm-fp32-agreement.json`.
"""

import json
import platform
import sys
from pathlib import Path

import sentence_transformers  # type: ignore[import-not-found]
import torch  # type: ignore[import-not-found]
import transformers  # type: ignore[import-not-found]
import yaml

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))

from evals.retrieval_bakeoff import adapters  # noqa: E402 - after the path line

REPO_ID = "tencent/WeMM-Embedding-2B"
REV = "bbd6cd4bf52cfc6716f752a2df80b2706720bd95"
THREADS = 8


def items() -> dict[str, str]:
    texts = yaml.safe_load((HERE / "texts.yaml").read_text())["items"]
    queries = yaml.safe_load((HERE / "set.yaml").read_text())["slices"]["cantonese"]["queries"]
    out = {pid: v["text"] for pid, v in list(texts.items())[:12]}
    out.update({q["id"]: q["text"] for q in queries[:12]})
    return out


def encode(st, xs: list[str]) -> torch.Tensor:
    return torch.tensor(st.encode(xs, normalize_embeddings=False, batch_size=1))


def whole(dtype) -> sentence_transformers.SentenceTransformer:
    return sentence_transformers.SentenceTransformer(
        REPO_ID, revision=REV, device="cpu", trust_remote_code=True,
        model_kwargs={"torch_dtype": dtype})


def main() -> None:
    torch.set_num_threads(THREADS)
    texts = items()
    ids, xs = list(texts), list(texts.values())
    text_only = adapters._load_sentence_transformer(REPO_ID, REV, "float32", text_only=True)
    dtypes = sorted({str(p.dtype) for p in text_only.parameters()})
    pieces = adapters._st_pieces(text_only)
    try:
        text_only[0].model.lm_head(torch.zeros(1, 2048))
        lm_head_raises = False
    except RuntimeError:
        lm_head_raises = True
    dropped = text_only._bakeoff_dropped
    v_text = encode(text_only, xs)
    del text_only
    m = whole(torch.float32)
    v_f32 = encode(m, xs)
    del m
    m = whole(torch.bfloat16)
    v_bf16 = encode(m, xs).float()
    del m
    cos = torch.nn.functional.cosine_similarity
    c32, cbf = cos(v_text, v_f32, dim=-1), cos(v_text, v_bf16, dim=-1)
    print(json.dumps({
        "schema": 1, "kind": "wemm-fp32-agreement", "script": "agreement_wemm_fp32.py",
        "model": REPO_ID, "revision": REV, "n": len(ids),
        "items": "the first 12 messages of texts.yaml and the first 12 cantonese queries",
        "text_only": {"param_dtypes": dtypes, "dropped_params": dropped,
                      "lm_head_raises_if_called": lm_head_raises,
                      "template_pieces_equal": tuple(pieces) == adapters.WEMM_PIECES},
        "text_only_fp32_vs_whole_fp32": {
            "bitwise_equal": bool(torch.equal(v_text, v_f32)),
            "max_abs_diff": float((v_text - v_f32).abs().max()),
            "cos_min": float(c32.min())},
        "text_only_fp32_vs_whole_bf16": {
            "cos_min": round(float(cbf.min()), 5), "cos_mean": round(float(cbf.mean()), 5),
            "per_item": {i: round(float(c), 5) for i, c in zip(ids, cbf, strict=True)}},
        "host": {"machine": platform.machine(), "system": platform.system(),
                 "torch": torch.__version__, "transformers": transformers.__version__,
                 "sentence_transformers": sentence_transformers.__version__,
                 "threads": THREADS},
    }, indent=2, sort_keys=True, ensure_ascii=False))


if __name__ == "__main__":
    main()
