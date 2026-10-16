"""Does the bake-off's adapter embed Qwen3-VL-Embedding-2B and
jina-embeddings-v5-omni-small the way their vendors do?

The scored runs go through `adapters.OfficialEmbedder` (sentence-transformers,
every sequence checked against `OfficialFormat`). This compares its vectors
with each vendor's OTHER published path, on the same inputs, at the same
pinned commit and dtype:

- Qwen: `scripts/qwen3_vl_embedding.py` from the model repository
  (`Qwen3VLEmbedder.process`, a query given the retrieval instruction the
  way the vendor's README passes it, everything else its default; it resizes
  images with qwen-vl-utils, then the processor does not);
- jina: raw transformers, as the card's quickstart writes it
  (`model.embed(**processor(text="Query: ..."))`, and an image as
  `processor(images=..., text="Document: " + IMAGE_RUN)`, with no chat
  template, where sentence-transformers wraps the image in one).

Inputs from the locked set: 11 pool images (img-001 to img-010, and img-012,
the one-bowl near miss of img-001), 3 probe queries, the first 5 text_image
and the first 5 cantonese queries, and the first 5 cantonese pool messages as
documents. Two phases, one model resident at a time; vectors stay in SCRATCH,
outside every checkout:

    python -I agreement_official.py adapter qwen3vl /tmp/q.json
    python -I agreement_official.py reference qwen3vl /tmp/q.json \\
        > results/<stamp>-qwen3vl-agreement.json

The second prints one aggregates-only JSON: per-item cosine at the native
dimension and 256, the M2 guard (largest cosine between two different
images) and the probe rankings for both paths, versions and hashes. No
vector and no text from the set.
"""

import datetime
import hashlib
import json
import math
import os
import platform
import resource
import sys
import time
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))

from evals.retrieval_bakeoff import adapters  # noqa: E402

MEDIA = Path("/home/norty/nora-bakeoff/media")
IMAGES = ["img-001", "img-012", "img-002", "img-003", "img-004", "img-005", "img-006",
          "img-007", "img-008", "img-009", "img-010"]
#: Not set queries: written for the probe, in English and colloquial Cantonese.
PROBES = {"probe-en": "two bowls of noodles", "probe-yue": "兩碗麵",
          "probe-yue2": "有兩碗麵嗰張相"}


def spec() -> list[dict]:
    media = yaml.safe_load((HERE / "media.yaml").read_text())["items"]
    texts = yaml.safe_load((HERE / "texts.yaml").read_text())["items"]
    slices = yaml.safe_load((HERE / "set.yaml").read_text())["slices"]
    out = [{"id": pid, "role": "image", "value": str(MEDIA / f"{media[pid]['sha256']}.jpg")}
           for pid in IMAGES]
    out += [{"id": k, "role": "query", "value": t} for k, t in PROBES.items()]
    out += [{"id": q["id"], "role": "query", "value": q["text"]}
            for q in slices["text_image"]["queries"][:5] + slices["cantonese"]["queries"][:5]]
    out += [{"id": pid, "role": "document", "value": texts[pid]["text"]}
            for pid in slices["cantonese"]["pool"][:5]]
    return out


def unit(v, d=None):
    v = list(v[:d] if d else v)
    n = math.sqrt(sum(x * x for x in v))
    return [x / n for x in v]


def cos(a, b, d=None):
    return sum(x * y for x, y in zip(unit(a, d), unit(b, d), strict=True))


def adapter_vectors(label: str) -> dict[str, list[float]]:
    emb = adapters.OfficialEmbedder(adapters.OFFICIAL[label], dtype="float32")
    out = {}
    for it in spec():
        if it["role"] == "image":
            out[it["id"]] = list(emb.embed_media([Path(it["value"])])[0])
        else:
            out[it["id"]] = list(emb.embed_text([it["value"]], it["role"])[0])
    return out, emb.provenance()


def reference_vectors(label: str) -> tuple[dict[str, list[float]], dict]:
    import torch  # type: ignore[import-not-found]
    import transformers  # type: ignore[import-not-found]
    from PIL import Image

    fmt = adapters.OFFICIAL[label]
    os.environ["HF_HUB_OFFLINE"] = "1"
    path = adapters._pinned_snapshot(fmt.repo, fmt.revision)
    assert Path(path).name == fmt.revision, path
    out = {}
    if label == "qwen3vl":
        sys.path.insert(0, str(Path(path) / "scripts"))
        from qwen3_vl_embedding import Qwen3VLEmbedder  # type: ignore[import-not-found]

        model = Qwen3VLEmbedder(model_name_or_path=path, dtype=torch.float32)
        params = model.model
        for it in spec():
            x = {"image": it["value"]} if it["role"] == "image" else {"text": it["value"]}
            if it["role"] == "query":  # the vendor's retrieval usage; else its default
                x["instruction"] = adapters.QWEN3VL_QUERY_INSTRUCTION
            out[it["id"]] = model.process([x], normalize=True)[0].tolist()
        how = ("scripts/qwen3_vl_embedding.py Qwen3VLEmbedder.process; queries with "
               "instruction=QWEN3VL_QUERY_INSTRUCTION, documents and images its default")
    else:
        model = transformers.AutoModel.from_pretrained(
            path, trust_remote_code=True, default_task="retrieval", modality="vision",
            dtype=torch.float32).eval()
        model.to(torch.float32)
        proc = transformers.AutoProcessor.from_pretrained(path, trust_remote_code=True)
        params = model
        for it in spec():
            if it["role"] == "image":
                inputs = proc(images=Image.open(it["value"]).convert("RGB"),
                              text="Document: " + adapters.IMAGE_RUN, return_tensors="pt")
            else:
                prefix = "Query: " if it["role"] == "query" else "Document: "
                inputs = proc(text=prefix + it["value"], return_tensors="pt")
            out[it["id"]] = model.embed(**inputs)[0].tolist()
        how = "raw transformers, the card's quickstart: model.embed(**processor(...))"
    dtypes = sorted({str(p.dtype) for p in params.parameters()})
    if dtypes != ["torch.float32"]:
        raise SystemExit(f"reference loaded {dtypes}")
    return out, {"how": how, "param_dtype": dtypes[0]}


def guard_and_probes(vecs: dict[str, list[float]], dims: tuple[int, int]) -> dict:
    pairs = [cos(vecs[a], vecs[b]) for i, a in enumerate(IMAGES) for b in IMAGES[i + 1:]]
    ranks = {}
    for d in dims:
        for p in PROBES:
            order = sorted(IMAGES, key=lambda i, p=p, d=d: -cos(vecs[p], vecs[i], d))
            ranks[f"{p}@{d}"] = order[:2]
    return {"m2_max_pairwise_cosine": round(max(pairs), 4), "probe_top2": ranks}


def main() -> None:
    phase, label, scratch = sys.argv[1], sys.argv[2], Path(sys.argv[3])
    fmt = adapters.OFFICIAL[label]
    dims = (fmt.native_dim, 256)
    t0 = time.time()
    if phase == "adapter":
        vecs, prov = adapter_vectors(label)
        scratch.write_text(json.dumps({
            "vectors": vecs, "provenance": prov, "seconds": round(time.time() - t0, 1),
            "peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}))
        return
    ref, ref_prov = reference_vectors(label)
    a = json.loads(scratch.read_text())
    vecs = a["vectors"]
    kinds = {it["id"]: it["role"] for it in spec()}
    items = {i: {d: round(cos(vecs[i], ref[i], d), 4) for d in dims} for i in sorted(vecs)}
    by_kind = {}
    for k in ("image", "query", "document"):
        xs = [items[i][fmt.native_dim] for i in items if kinds[i] == k]
        by_kind[k] = {"n": len(xs), "mean": round(sum(xs) / len(xs), 4), "min": round(min(xs), 4)}
    snap = adapters._pinned_snapshot(fmt.repo, fmt.revision)
    print(json.dumps({
        "what": f"{label}: adapters.OfficialEmbedder against the vendor's other path",
        "at": datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds"),
        "host": platform.node(), "model": fmt.repo, "revision": fmt.revision,
        "licence": fmt.licence, "dtype": "float32",
        "weights_sha256": hashlib.sha256((Path(snap) / "model.safetensors").read_bytes())
        .hexdigest(),
        "adapter": {k: a["provenance"].get(k) for k in (
            "torch", "transformers", "sentence_transformers", "param_dtype", "parameters",
            "format_checked")} | {"seconds": a["seconds"], "peak_rss_kib": a["peak_rss_kib"]},
        "reference": ref_prov | {"seconds": round(time.time() - t0, 1),
                                 "peak_rss_kib": resource.getrusage(
                                     resource.RUSAGE_SELF).ru_maxrss},
        "cosine": {"by_kind_at_native": by_kind, "items": items,
                   "dims": list(dims)},
        "adapter_checks": guard_and_probes(vecs, dims),
        "reference_checks": guard_and_probes(ref, dims),
    }, ensure_ascii=False, indent=1, sort_keys=True))


if __name__ == "__main__":
    main()
