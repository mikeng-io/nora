# otel-queue-fixture

Disposable collector + Jaeger + synthetic-Langfuse for the durable
platform-bridge queue work (design/platform-bridges/04, issue #422). It
implements the routing the approved design REQUIRES (`docs/observability.md`
«How a span reaches a collector» describes the deployed counterpart this
proposal is derived from). The Jaeger/Langfuse legs are the TRANSITIONAL
route before deploying the implemented SigNoz backend (`docs/observability.md` «Accepted
direction: self-hosted SigNoz» / ADR 0034); this fixture attests the
transitional policy, not SigNoz, and nothing here implements it.

- the app sampler (`build_sampler()`) exports every root under ADR 0034;
  retention belongs to the collector;
- the Langfuse leg FIRST drops every `nora.trace_kind=queue` span
  (`filter/langfuse_queue`, explicit and ordered before the tail policy),
  then tail-samples: a whole surviving trace iff it carries a
  `gen_ai.operation.name` of `chat`/`invoke_agent`;
- the Jaeger leg drops only `chat` spans, retaining plumbing and queue roots.

A queue-only trace therefore lands in Jaeger and never reaches Langfuse —
and a queue span that somehow carries a `gen_ai` attribute still cannot,
because the kind filter runs before the tail policy. This fixture attests
the PROPOSED routing only; whether the deployed collector already excludes
queue-kind spans before its Langfuse policy is a separate prerequisite the
parent owns — no host readback is claimed here.

```sh
docker compose -f deploy/otel-queue-fixture/docker-compose.yml up -d --build
# ... send spans to http://localhost:4318/v1/traces ...
docker compose -f deploy/otel-queue-fixture/docker-compose.yml down -v
```

| endpoint | use |
| --- | --- |
| `http://localhost:4318/v1/traces` | OTLP/HTTP ingest — point `OTLPSpanExporter` here |
| `http://localhost:16686` | Jaeger UI + `/api/traces` query API |
| `http://localhost:5318/spans` | synthetic Langfuse: every span it accepted, decoded from OTLP/JSON |
| `http://localhost:5318/reset` | POST — clears the sink's store |
| `http://localhost:5318/healthz` | readiness |

`decision_wait` is 2s (vs the deployed 90s) for test latency — the policy,
not the timing, is what's under test. There are no credentials, no
persistence and no paid Langfuse calls; stopping `collector` mid-run loses
buffered spans and nothing else — the app's queue work does not depend on
it.

`tests/integration/test_bridge_observability.py` drives this fixture.
