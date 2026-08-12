# Contributing to observix

## Setup

```bash
git clone https://github.com/Pranav-PA/observix
cd observix
uv venv && uv pip install -e ".[dev]"
```

Or with plain pip:

```bash
python -m venv .venv && source .venv/bin/activate   # .venv\Scripts\activate on Windows
pip install -e ".[dev]"
```

## The checks CI runs

```bash
pytest                    # 270+ tests, under two seconds
ruff check . && ruff format --check .
mypy                      # strict
```

Tests run with `OBSERVIX_STRICT=1`, which turns off the fail-open guard so internal errors surface instead of becoming a logged warning. Keep it that way — a bug that only shows up as a suppressed WARNING is a bug that ships.

## Where things live

Read [docs/flow.md](docs/flow.md) first — it walks execution through the codebase in the order it actually happens. [docs/decisions.md](docs/decisions.md) explains *why* the design is the way it is; check it before proposing a structural change, since the reasoning and the rejected alternatives are usually already written down.

```
src/observix/
  api.py           @observe, observe_block, start_span
  model/           canonical telemetry model
  semconv/         attribute vocabularies (ours + the four we target)
  dialects/        canonical → backend vocabulary  (pure functions)
  providers/       backend presets: endpoint, auth, default dialect
  pipeline/        redaction → translation → filtering → assembly
  cost/            token → USD
```

## Adding a backend

**You should not need to change core code.** See [docs/extending.md](docs/extending.md).

Built-in providers are for backends with a large user base and stable, documented OTLP ingestion. Everything else is better as a separate package with an entry point — it can release on its own schedule and does not tie its lifecycle to ours.

If you do contribute a built-in, it needs:

- A `Provider` subclass with endpoint resolution and auth from that vendor's own environment variables
- A `Dialect` if the backend has its own vocabulary
- Entry points in `pyproject.toml`
- Tests for endpoint resolution, header construction, and every attribute mapping
- A section in `docs/providers.md`

## Working on dialects

Dialects are the highest-value tests in the repo. A dialect regression is invisible in canonical attributes and only shows up as a degraded trace in someone's production backend.

Every dialect must satisfy the shared contract in `tests/test_dialects.py` — foreign attributes preserved, empty spans handled, no `None` values emitted, content suppressible. New dialects get added to the `ALL_DIALECTS` list so they inherit those checks automatically.

Cite a source for mappings. A link to the backend's documentation in the docstring is worth more than the mapping itself, because it is what lets the next person verify it is still true.

## Principles

Changes are measured against these. They are not decorative — [docs/decisions.md](docs/decisions.md) traces each one to concrete design choices.

1. **Never reinvent OpenTelemetry.** If OTel does it, delegate to it.
2. **Fail open.** Instrumentation must never break the application. Configuration errors are the one deliberate exception.
3. **Canonical in, native out.** Applications write our vocabulary; backends receive theirs.
4. **Policy belongs to the destination.** Redaction and sampling are per-exporter.
5. **Extensible without forking.** New backends are entry points, not core edits.
6. **Zero-config is a no-op.** No configuration means near-zero overhead.

## Performance

`@observe` runs on every instrumented call. Before adding work to `api.py` or `model/span.py`, know which side of the queue it lands on — see the three flows in [docs/flow.md](docs/flow.md). Work on the application thread needs justification; work on the exporter thread is usually fine.

## Pull requests

- One logical change per PR
- Tests for anything user-visible
- Update the relevant doc in `docs/` alongside the code
- Add a `CHANGELOG.md` entry under `Unreleased`
- Record a decision in `docs/decisions.md` if you made a real trade-off

## Reporting bugs

Include the observix version, Python version, your configuration (redacted), and what you expected versus what you saw. Output from `OBSERVIX_STRICT=1` with `logging.getLogger("observix").setLevel(logging.DEBUG)` is usually the fastest path to a diagnosis.

## Releasing

See [RELEASING.md](RELEASING.md).

## Licence

Contributions are accepted under Apache-2.0.
