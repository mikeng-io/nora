Archived synthetic probe scripts and raw receipts (2026-10-07)

These are the scripts actually used, not portable one-command benchmarks.
Their original environment and staging are prerequisites; no credentials are
included. Node 07 distinguishes measured inference from Nora replay accuracy.

Local norty probes:
  cp probe.py /tmp/nora-context-probe.py
  mkdir -p /tmp/nora-context-probe
  python /tmp/nora-context-probe.py
  python local_models.py laya
  python local_models.py strands
Use the original probe environment with laya, strands-decider and torch installed
through uv. The two local_models.py arms use CPU explicitly; neither measures the
Strands pre-ranker. probe.py requires Ollama 0.35.0 with tev1:0.8b and tev1:4b,
its /v1/systemone API, and the norty model store/binary paths embedded in the file.
It calls the resident server at port 11434 and temporarily starts CPU-only Ollama
at 11439. Verify backend/offload independently before comparing timings. Running
it changes model residency and writes results under /tmp/nora-context-probe.

Modal T4:
Requires the Modal Python package and an authenticated Modal account. From the
modal-t4 directory, run `modal run bench.py`; retain payload.json beside bench.py.
This provisions a paid T4 container and downloads the model. Reported warm timing
excludes provisioning, download and client-to-container transport.

DeepSeek Flash:
Run deepseek-flash/bench.py only on norty with the existing OmniRoute service at
127.0.0.1:20128. It reads the deployment config and .env at their embedded paths
to resolve the API key, and calls ollamacloud/deepseek-v4.1-flash. Keep payload.json
beside the script. It makes paid/subscription-backed model calls and overwrites
result.json. A rerun requires separate output storage to preserve these receipts.
The benchmark prompt allows depths 8/20/40; the later replay experiment deliberately
fixes depth to the available transcript length. replay-system-prompt.txt is the
exact experiment prompt, not the benchmark prompt.

Do not infer current package/model versions from these scripts alone. Hardware,
versions, backend evidence, cached-run exclusions and timing limits are in node 07
and the accompanying raw results. Any fresh run must record its own environment.
