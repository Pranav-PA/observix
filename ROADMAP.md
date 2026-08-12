# Roadmap

Where observix goes after 0.1.0, and why. Living document — reorder it freely,
but keep the reasoning attached to each item so the next person can disagree
with the reasoning rather than guess at it.

**The organising principle:** the product's entire claim is *"your telemetry
arrives natively shaped in every backend."* Anything that strengthens or
verifies that claim outranks anything that adds surface area.

---

## Where 0.1.0 actually stands

Being honest about this is what makes the rest of the plan legible.

| Dialect | Backend | Verified against a real instance? |
|---|---|---|
| `openinference` | Phoenix, Arize | ✅ Phoenix only |
| `mlflow` | MLflow | ✅ |
| `langfuse` | Langfuse | ❌ **doc-derived** |
| `otel_genai` | Datadog, Grafana, Honeycomb, SigNoz | ❌ **doc-derived** |
| `passthrough` | — | n/a |

**Two backends verified, two real bugs found.** Phoenix routed projects by a
resource attribute rather than the header we sent; MLflow had a native cost
field we never emitted. Both rendered *fine* — they just silently lost data.

That is a 100% hit rate on doc-derived mappings being wrong in some way. The
honest prior is that **Langfuse and the generic OTel path are also wrong in
ways we cannot currently see.**

`tests/test_conformance.py` closes half the gap cheaply — upstream renames now
fail CI. But conformance proves the *names* are right, not that the backend
*does something useful* with them.

---

## 0.2 — Finish the verification story

*Theme: earn the claim we already make.*

**This is the highest-value release and should not be skipped for features.**

### Langfuse live verification — the important one

Langfuse's null-input/output bug ([#12657](https://github.com/langfuse/langfuse/issues/12657))
is the headline justification for this entire project. If our `langfuse`
dialect has its own mapping bug, the pitch is hollow exactly where it matters
most.

Needs one of:
- Docker + `docker compose` for self-hosted Langfuse (preferred: no account, CI-friendly), or
- Langfuse Cloud keys as GitHub secrets `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY`.

Assert on what Langfuse *promotes into typed fields* — observation type,
`input`/`output`, `usage_details`, `cost_details`, session/user — not on raw
attributes. Same technique that caught the other two. **Use a model Langfuse
cannot price**, so a cost that appears must be the one we sent (see
[D32](docs/decisions.md)).

### Real foreign-span adoption

`observix.integrations.adopt` was written from specifications and has **never
seen a span produced by an actual instrumentation library.** It is the
"no re-instrumentation" on-ramp — most people's realistic first contact.

Install `traceloop-sdk` / `openinference-instrumentation-*`, have them emit
genuine spans, and assert we adopt them into the canonical model and fan them
out correctly. Doable offline with a stubbed vendor client.

### Generic OTLP path

Verify `otel_genai` against something free and self-hostable — Jaeger or SigNoz
in CI. Lower value than Langfuse (no AI-aware rendering to get wrong) but it is
the default dialect for the largest set of backends.

### Also in 0.2

- Repo description and topics (pending — see the end of this file).
- Coverage gaps: `model/span.py` (63%) and `providers/base.py` (63%) are mostly
  untested setter branches.
- Arize: same dialect as Phoenix, but different auth and endpoint. Cheap to
  cover once someone has a space id.

---

## 0.3 — Make onboarding cost nothing

*Theme: the gap between `pip install` and a useful trace.*

Today a user must call `record_llm_call` by hand for AI metadata. That is a
real barrier: OpenLLMetry and OpenInference give you spans by importing them.

Two candidate paths, and **they are not equivalent**:

**A. Lean on existing instrumentors (preferred).** Document
`adopt_foreign=True` + OpenLLMetry as the recommended zero-effort setup. Costs
almost nothing to build, respects the "don't reinvent auto-instrumentation"
non-goal, and immediately gives multi-backend fan-out to a large existing
userbase. Depends on 0.2's adoption verification being real.

**B. First-party instrumentors for `openai` and `anthropic`.** More control and
a better default experience, but it puts us on the treadmill of chasing vendor
SDK changes — which [D1](docs/decisions.md) explicitly refused.

Recommendation: **A first**, and only consider B if adoption proves genuinely
lossy for the common cases.

Also worth doing here:
- A `observix.instrument()` one-liner that configures *and* enables adoption.
- Framework context helpers (FastAPI/Starlette) for propagating session/user
  onto spans without threading arguments through call stacks.

---

## 0.4 — Beyond spans

*Theme: traces are one signal of three.*

- **Metrics.** OTel GenAI defines metric conventions
  (`gen_ai.client.token.usage`, `gen_ai.client.operation.duration`). Aggregates
  are what dashboards and alerts actually want; today every consumer derives
  them from spans.
- **Evaluation and feedback.** Langfuse and Phoenix both have score/annotation
  APIs, and they are *not* OTLP — they are REST. This breaks the "one canonical
  model, translated per destination" pattern and needs its own design. Probably
  a `Provider.record_score()` capability that only some providers implement.
- **Prompt management.** `observix.prompt.name` / `.version` exist but link to
  nothing. Both Langfuse and MLflow have prompt registries.

Metrics fit the existing architecture cleanly. Evaluation does not — do the
design work before the code.

---

## 0.5 — Operational hardening

*Theme: what breaks at scale.*

- **Tail sampling.** Per-destination head sampling exists
  ([D12](docs/decisions.md)); "keep every trace containing an error or costing
  over $1" needs buffering whole traces. Genuinely hard, frequently requested.
- **Collector parity.** Document honestly when an OTel Collector is the better
  answer than in-process fan-out — high span volume, many services, central
  policy. Being straight about our own limits builds more trust than pretending
  they do not exist.
- **Performance.** `benchmarks/README.md` already names the remaining hot
  spots: `ObservixSpan._set` calls `is_recording` per attribute, and every
  non-scalar goes through `to_json`. Neither has been optimised because neither
  has been shown to matter yet — measure before touching.
- **Resilience.** Behaviour when a backend is down for hours, queue saturation,
  and whether dropping spans is loud enough.

---

## 1.0 — Commit to stability

Ship 1.0 when **all five dialects are verified against real backends** and the
canonical namespace has survived contact with real users.

What 1.0 promises:
- `observix.*` attribute names are stable; renaming one is a major version.
- `Provider` and `Dialect` base classes are stable; third-party plugins do not
  break on a minor.
- Dialect *output* may still change within a minor when a backend changes what
  it reads — that is the whole point of the abstraction, and it goes in the
  changelog.

Do not ship 1.0 to signal confidence. Ship it when the namespace has stopped
moving.

---

## Backlog — real, unscheduled

- Hosted documentation (currently Markdown in-repo only).
- More providers: Honeycomb, SigNoz, Grafana Cloud, W&B Weave, Braintrust,
  Helicone. Each is cheap; each needs live verification to be worth trusting.
- Price book freshness — currently a hand-maintained snapshot
  ([D24](docs/decisions.md)). Possibly a scheduled job that flags drift.
- Redaction: the PII detectors are regexes and documented as *not* a compliance
  control. Either integrate a real detector or keep saying so loudly.
- GitHub issue/PR templates.
- Multi-language: the canonical model is language-agnostic; a TypeScript port
  is plausible but doubles maintenance. Not before 1.0.

---

## Explicit non-goals

Unchanged from [DESIGN.md §1.4](docs/DESIGN.md). Restated because roadmaps are
where scope creep enters:

- ❌ Reimplementing transport, batching, retries, propagation, or sampling primitives.
- ❌ Becoming a backend, UI, or storage layer.
- ❌ Owning auto-instrumentation for every vendor SDK.
- ❌ Being an evaluation framework. Recording eval *results* is in scope;
  running evals is not.

---

## How to decide what is next

In priority order:

1. **Does it verify a claim we already make?** Verification beats features.
   Two for two on finding real bugs.
2. **Does it reduce the distance between `pip install` and a useful trace?**
   Adoption is the binding constraint on a library nobody uses yet.
3. **Does it fit "canonical in, native out"?** If not, design first, code later.
4. **Would OpenTelemetry do it better?** Then delegate.

New backend requests are cheap to satisfy and easy to satisfy *badly*. A
provider without live verification is a guess with a version number.

---

## Immediate next steps

Concrete, in order:

1. Set the GitHub repo description and topics.
   > Provider-agnostic observability for Python and AI apps. Instrument once with @observe; export natively to Phoenix, Langfuse, MLflow, Datadog and any OTLP backend simultaneously.

   Topics: `observability` `opentelemetry` `llm` `genai` `tracing` `otlp`
   `langfuse` `phoenix` `mlflow` `python` `telemetry` `llmops`
2. Create the `pypi` GitHub environment with a required reviewer, so tag pushes
   need an approval before publishing.
3. Stand up Langfuse (Docker or Cloud keys) and write `tests/live/test_langfuse_live.py`.
4. Verify `adopt_foreign` against spans from a real instrumentation library.
5. Cut 0.2.0 once 3 and 4 land.
