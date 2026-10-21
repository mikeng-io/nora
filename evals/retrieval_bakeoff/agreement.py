"""Are the HF and Ollama paths the same EmbeddingGemma 2?

The same prefixed texts and the same pool images, embedded by HF at float32
(sentence-transformers, the pinned commit) and by Ollama's Q8_0 tag; one
aggregates-only JSON on stdout: per-item cosines, ids as sha256, no vector
and no text. Images at the HF budget the scored run used (280, the card's
default) and at 70, the latter information only. Run on the deploy host
from the bake-off venv, with Ollama serving `embeddinggemma-2:latest`:

    nice -n 10 /home/<deploy-user>/nora-bakeoff/venv/bin/python -I agreement.py > out.json
"""

import base64
import datetime
import hashlib
import json
import math
import platform
import urllib.request
from pathlib import Path

import sentence_transformers  # type: ignore[import-not-found]
import torch  # type: ignore[import-not-found]
import transformers  # type: ignore[import-not-found]
from PIL import Image

REV = "914f7f89142e33e77833254d9c9b90c3cef7303b"
Q, D = "task: search result | query: ", "title: none | text: "
TEXTS = [
    Q + "打風嗰晚啲小巴停晒",
    D + "今晚七點喺旺角食飯，六個人",  # noqa: RUF001 - the text as hashed in the result
    Q + "the one with egg tarts on a metal tray",
    D + "ngo dei sing kei luk heui hang saan",
    Q + "上個月book咗嘅camping trip",
]
MEDIA = Path("/home/norty/nora-bakeoff/media")
BASE = "http://127.0.0.1:11434"


def call(path, body=None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(  # noqa: S310 - loopback Ollama
        BASE + path, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as resp:  # noqa: S310
        return json.load(resp)


def ollama(inp):
    return call("/api/embed", {"model": "embeddinggemma-2:latest", "input": inp,
                               "truncate": False})


def cos(a, b):
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    return dot / math.sqrt(sum(x * x for x in a) * sum(y * y for y in b))


def head(v, d):
    n = math.sqrt(sum(x * x for x in v[:d]))
    return [x / n for x in v[:d]]


def compare(h, o):
    cross = [cos(h[i], o[j]) for i in range(len(h)) for j in range(len(o)) if i != j]
    return {"same_item_768": [round(cos(a, b), 5) for a, b in zip(h, o, strict=True)],
            "same_item_256": [round(cos(head(a, 256), head(b, 256)), 5)
                              for a, b in zip(h, o, strict=True)],
            "different_items_768_max": round(max(cross), 4),
            "different_items_768_mean": round(sum(cross) / len(cross), 4)}


def main() -> None:
    imgs = sorted(MEDIA.glob("*.jpg"))[:4]
    o_t = ollama(TEXTS)["embeddings"]
    o_i_resp = [ollama([{"image": base64.b64encode(p.read_bytes()).decode()}]) for p in imgs]
    o_i = [r["embeddings"][0] for r in o_i_resp]
    tags = call("/api/tags")
    digest = next(m["digest"] for m in tags["models"] if m["name"] == "embeddinggemma-2:latest")
    st = sentence_transformers.SentenceTransformer(
        "google/embeddinggemma-2", revision=REV, device="cpu",
        model_kwargs={"torch_dtype": torch.float32})
    assert {str(p.dtype) for p in st.parameters()} == {"torch.float32"}
    assert st.model_card_data.base_model_revision == REV
    ip = st[0].processor.image_processor
    pil = [Image.open(p).convert("RGB") for p in imgs]
    h_t = st.encode(TEXTS, normalize_embeddings=False).tolist()
    images = {}
    for budget in (280, 70):  # 280: the scored run's; 70: information only
        ip.max_soft_tokens = budget
        soft = ip(pil, return_tensors="pt")["num_soft_tokens_per_image"].tolist()
        images[str(budget)] = {"hf_soft_tokens_per_image": soft,
                               **compare(st.encode(pil, normalize_embeddings=False).tolist(), o_i)}
    print(json.dumps({
        "schema": 1,
        "what": "EmbeddingGemma 2, HF float32 vs Ollama Q8_0, same prefixed texts and same "
                "images; aggregates only, no vector and no text",
        "at": datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds"),
        "host": platform.node(),
        "hf": {"model": "google/embeddinggemma-2", "revision": REV, "dtype": "float32",
               "torch": torch.__version__, "transformers": transformers.__version__,
               "sentence_transformers": sentence_transformers.__version__},
        "ollama": {"model": "embeddinggemma-2:latest", "digest": digest,
                   "version": call("/api/version")["version"], "quantization": "Q8_0",
                   "image_prompt_tokens": [r.get("prompt_eval_count") for r in o_i_resp]},
        "texts": {"n": len(TEXTS),
                  "roles": ["query" if t.startswith(Q) else "document" for t in TEXTS],
                  "sha256": [hashlib.sha256(t.encode()).hexdigest() for t in TEXTS],
                  **compare(h_t, o_t)},
        "images": {"n": len(imgs), "pool_sha256": [p.stem for p in imgs],
                   "by_hf_budget": images, "scored_run_budget": 280,
                   "note": "budget 70 is information only; the scored run is unchanged"},
    }, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
