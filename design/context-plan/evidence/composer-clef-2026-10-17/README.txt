Node 16: clef:27b on Ollama Cloud's /v1/systemone, 2026-10-17. Counts only; every turn,
state and per-turn answer stays on norty in ~/composer-labels/step2/out/{A,B}/clef_27b/.

run_cloud.py   ../composer-tev1-step2-2026-10-12/run.py with three changes: URL
               https://ollama.com/v1/systemone, a bearer key from OLLAMA_CLOUD_API_KEY,
               and a 5 s sleep instead of a local runner reload on HTTP 500.
               Ran: --arm B --model clef:27b --limit 200, then
                    --arm A --model clef:27b --ids sample.txt --limit 200. Both RC=0.
aggregate.json that bundle's analyze.py over every run in out/ (Tev1 runs included, unchanged);
               its text rendering stays on norty (aggregate.txt).
gate-base.txt  python -m evals relevance_gate --reasons, deployed nora-gate, summary block + rc.
gate-clef.txt  the same with NORA_SPIKE_GATE_SYSTEMONE_MODEL=clef:27b (spike branch 848228c0).
