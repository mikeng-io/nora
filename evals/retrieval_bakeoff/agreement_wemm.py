"""Is the Q4_K_M GGUF on llama-server the same WeMM-Embedding-2B as the HF
checkpoint?

24 inputs from the locked set: 11 pool images (img-001 to img-010, and img-012,
the one-bowl near miss of img-001), 3 probe queries, the first 5 text_image
and the first 5 cantonese queries. Two phases, one model resident at a time:

1. `hf SCRATCH.json`: the HF checkpoint at the pinned commit, bf16 (its own
   dtype), CPU, transformers 5.2.0 as the card pins it, through
   sentence-transformers. Writes vectors and the token ids it fed the model to
   SCRATCH, which stays outside every checkout.
2. `gguf SCRATCH.json URL PID`: the same inputs through
   `adapters.LlamaServerEmbedder` (every check on), then one aggregates-only
   JSON on stdout: per-item cosine to HF at 2048 and 256 (21 items: the probes
   are reported apart), the probe rankings, the M2 guard, versions and hashes.
   No vector and no text from the set.

On the deploy host, from a checkout of this commit:

    nice -n 15 ~/nora-bakeoff-wemm/venv52/bin/python -I agreement_wemm.py hf /tmp/x.json
    # start llama-server (run_wemm.sh's flags), then
    nice -n 10 ~/nora-bakeoff-wemm/venv52/bin/python -I agreement_wemm.py gguf /tmp/x.json \\
        http://127.0.0.1:11439 <pid> > results/<stamp>-wemm-probe-agreement.json
"""

import json
import math
import resource
import sys
import time
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))

REPO_ID = "tencent/WeMM-Embedding-2B"
REV = "bbd6cd4bf52cfc6716f752a2df80b2706720bd95"
MEDIA = Path("/home/norty/nora-bakeoff/media")
IMAGES = ["img-001", "img-012", "img-002", "img-003", "img-004", "img-005", "img-006",
          "img-007", "img-008", "img-009", "img-010"]
#: Not set queries: written for the probe, in English and colloquial Cantonese.
PROBES = {"probe-en": ("two bowls of noodles", "en"), "probe-yue": ("兩碗麵", "yue"),
          "probe-yue2": ("有兩碗麵嗰張相", "yue")}
GGUF_SHA = "46b85372ad4f197cbdb00753e7ed0a1d12b251f20626f92865a044bb733e0dea"
MMPROJ_SHA = "c7271c975cab83ebd12ce2807e7b77e91134afd9f1658bd4dca986212a60560d"


def spec() -> list[dict]:
    media = yaml.safe_load((HERE / "media.yaml").read_text())["items"]
    slices = yaml.safe_load((HERE / "set.yaml").read_text())["slices"]
    out = [{"id": pid, "kind": "image", "value": str(MEDIA / f"{media[pid]['sha256']}.jpg")}
           for pid in IMAGES]
    out += [{"id": k, "kind": "text", "value": t} for k, (t, _) in PROBES.items()]
    out += [{"id": q["id"], "kind": "text", "value": q["text"]}
            for q in slices["text_image"]["queries"][:5] + slices["cantonese"]["queries"][:5]]
    return out


def unit(v):
    n = math.sqrt(sum(x * x for x in v))
    return [x / n for x in v]


def cos(a, b):
    return sum(x * y for x, y in zip(unit(a), unit(b), strict=True))


def hf(scratch: str) -> None:
    import sentence_transformers  # type: ignore[import-not-found]
    import torch  # type: ignore[import-not-found]
    import transformers  # type: ignore[import-not-found]

    if transformers.__version__ != "5.2.0":  # 5.19 keeps the newline WeMM's normaliser drops
        raise SystemExit(f"transformers {transformers.__version__}, not the card's 5.2.0")
    torch.set_num_threads(4)
    t0 = time.time()
    m = sentence_transformers.SentenceTransformer(
        REPO_ID, revision=REV, trust_remote_code=True, device="cpu",
        model_kwargs={"dtype": torch.bfloat16})
    load_s = time.time() - t0
    dtypes = {str(p.dtype) for p in m.parameters()}
    if dtypes != {"torch.bfloat16"}:
        raise SystemExit(f"loaded {dtypes}")
    items = {}
    for it in spec():
        x = it["value"] if it["kind"] == "text" else {"image": it["value"]}
        t = time.time()
        v = m.encode_document([x], normalize_embeddings=True, convert_to_numpy=True)[0]
        items[it["id"]] = {"vec": [float(a) for a in v], "s": round(time.time() - t, 3)}
    first = m[0]
    text_ids = first.preprocess([PROBES["probe-en"][0]])["input_ids"][0].tolist()
    img = first.preprocess([{"image": spec()[0]["value"]}])
    record = {
        "revision": REV, "dtype": "bfloat16", "load_s": round(load_s, 1), "items": items,
        "versions": {"torch": torch.__version__, "transformers": transformers.__version__,
                     "sentence_transformers": sentence_transformers.__version__},
        "text_ids": text_ids, "image_len": len(img["input_ids"][0]),
        "image_grid_thw": img["image_grid_thw"].tolist(),
        "peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
    }
    Path(scratch).write_text(json.dumps(record))


def gguf(scratch: str, url: str, pid: int) -> None:
    from evals.retrieval_bakeoff.adapters import LlamaServerEmbedder, http_json

    ref = json.loads(Path(scratch).read_text())
    emb = LlamaServerEmbedder("wemm-embedding-2b:Q4_K_M", native_dim=2048, base_url=url,
                              server_pid=pid, model_sha256=GGUF_SHA, mmproj_sha256=MMPROJ_SHA,
                              timeout=300)
    vecs, secs = {}, {}
    for it in spec():
        t = time.time()
        vecs[it["id"]] = (emb.embed_text([it["value"]], "query") if it["kind"] == "text"
                          else emb.embed_media([Path(it["value"])]))[0]
        secs[it["id"]] = round(time.time() - t, 3)
    probe_prompt = emb._text_prompt(PROBES["probe-en"][0])  # the exact string sent
    tok = http_json("POST", f"{url}/tokenize",
                    {"content": probe_prompt, "add_special": True, "with_pieces": True}, 30)
    llama_ids = [t["id"] for t in tok["tokens"]]

    def probe(src):
        pair = [cos(src[a], src[b]) for n, a in enumerate(IMAGES) for b in IMAGES[n + 1:]]
        rank = {f"{q}@{d}": [[i, round(s, 4)] for s, i in sorted(
            ((cos(src[q][:d], src[i][:d]), i) for i in IMAGES[:5]), reverse=True)]
            for d in (2048, 256) for q in PROBES}
        return {"m2_max_pairwise_cosine": round(max(pair), 4), "n_images": len(IMAGES),
                "rank": rank}

    hf_vecs = {k: v["vec"] for k, v in ref["items"].items()}
    kinds = {it["id"]: it["kind"] for it in spec()}
    spot = [k for k in vecs if k not in PROBES]
    items = {k: {"kind": kinds[k], "cos2048": round(cos(vecs[k], hf_vecs[k]), 4),
                 "cos256": round(cos(vecs[k][:256], hf_vecs[k][:256]), 4)} for k in spot}
    c2, c256 = [v["cos2048"] for v in items.values()], [v["cos256"] for v in items.values()]
    prov = emb.provenance()
    out = {
        "schema": 1, "kind": "wemm-probe-and-agreement", "script": "agreement_wemm.py",
        "model": {"gguf": "milkey/wemm-embedding-2b:Q4_K_M (Ollama blob)", "gguf_sha256": GGUF_SHA,
                  "mmproj": "DreamBlooms WeMM-Embedding-2B-GGUF projector, BF16",
                  "mmproj_sha256": MMPROJ_SHA, "server_cmdline": prov["server_cmdline"],
                  "build": http_json("GET", f"{url}/props", None, 30).get("build_info"),
                  "device": "CPU: 'no usable GPU found'; the norty user is not in the render "
                            "group", "server_memory": prov["server_memory"]},
        "reference": {"model": REPO_ID, "revision": ref["revision"], "dtype": ref["dtype"],
                      "device": "cpu, 4 threads", **ref["versions"],
                      "peak_rss_kib": ref["peak_rss_kib"], "load_s": ref["load_s"],
                      "item_s_mean": {k: round(sum(ref["items"][i]["s"] for i in spot
                                                   if kinds[i] == k)
                                               / sum(1 for i in spot if kinds[i] == k), 2)
                                      for k in ("image", "text")}},
        "input_format": {
            "text": "<|im_start|>user\\n{text}<|im_end|><embedding>",
            "image": "<|im_start|>user<|vision_start|><|image_pad|>*<|vision_end|>"
                     "<|im_end|><embedding>",
            "instruction": "none (config_sentence_transformers prompts = {})",
            "text_ids_equal_hf": llama_ids == ref["text_ids"], "text_ids_tail": llama_ids[-3:],
            "image_tokens_hf": ref["image_len"], "hf_grid_thw": ref["image_grid_thw"],
            "template_check": prov["template_check"]},
        "probe": {"pool": IMAGES[:5], "target": "img-001",
                  "langs": {k: lang for k, (_, lang) in PROBES.items()},
                  "q4": probe(vecs), "hf": probe(hf_vecs)},
        "agreement_q4_vs_hf": {
            "n": len(spot), "items": items,
            "cos2048_min": min(c2), "cos2048_mean": round(sum(c2) / len(c2), 4),
            "cos256_min": min(c256), "cos256_mean": round(sum(c256) / len(c256), 4)},
        "q4_item_s": secs,
    }
    print(json.dumps(out, indent=2, sort_keys=True, ensure_ascii=False))


if __name__ == "__main__":
    if sys.argv[1] == "hf":
        hf(sys.argv[2])
    else:
        gguf(sys.argv[2], sys.argv[3], int(sys.argv[4]))
