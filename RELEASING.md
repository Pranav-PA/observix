# Releasing

Everything here is automated by [`.github/workflows/release.yml`](.github/workflows/release.yml). The manual parts are the one-time setup and pushing a tag.

## One-time setup

### 1. Create the GitHub repository

Already done: <https://github.com/Pranav-PA/observix>.

```bash
git remote add origin https://github.com/Pranav-PA/observix.git
git push -u origin main
```

### 2. Claim the PyPI name

`observix` was unclaimed as of 2026-08-12. Names are first-come and **cannot be reused after deletion**, so claim it before announcing anything.

### 3. Configure Trusted Publishing

At <https://pypi.org/manage/account/publishing/>, add a pending publisher:

| Field | Value |
|---|---|
| PyPI project name | `observix` |
| Owner | your GitHub org or username |
| Repository name | `observix` |
| Workflow name | `release.yml` |
| Environment name | `pypi` |

Repeat at <https://test.pypi.org/manage/account/publishing/> with environment `testpypi`.

Trusted Publishing uses OIDC — PyPI verifies the workflow's identity directly, so there is **no API token to store, leak, or rotate**. Do not add a `PYPI_API_TOKEN` secret; it is not needed and is strictly worse.

### 4. Create the GitHub environments

Settings → Environments → New environment: `pypi` and `testpypi`. Adding a required reviewer to `pypi` means every production publish needs a human approval click, which is worth the friction.

## Releasing a version

### 1. Verify locally

```bash
pytest && ruff check . && ruff format --check . && mypy
```

And against a real backend:

```bash
phoenix serve                 # separate terminal
pytest tests/live -m live
```

### 2. Update the version and changelog

`src/observix/_version.py` is the single source of truth — `pyproject.toml` reads it via hatch, and the workflow fails the release if the tag disagrees with it.

Move the `Unreleased` items in `CHANGELOG.md` under the new version with today's date.

```bash
git commit -am "chore: release 0.2.0"
```

### 3. Dry-run to TestPyPI

Actions → Release → Run workflow, with **Publish to TestPyPI** checked. Then:

```bash
pip install --index-url https://test.pypi.org/simple/ \
            --extra-index-url https://pypi.org/simple/ observix
```

The extra index is needed because TestPyPI does not mirror `opentelemetry-*`.

### 4. Tag and push

```bash
git tag -a v0.2.0 -m "observix 0.2.0"
git push origin main --tags
```

The workflow then runs the full suite, checks the tag against `__version__`, builds, verifies the wheel installs clean in a fresh venv with entry points resolving, publishes to PyPI, and creates the GitHub release.

## Versioning

`0.x` minor releases may break APIs. From `1.0`:

- The canonical `observix.*` attribute namespace is stable — renaming a key is a major version.
- The `Provider` and `Dialect` base classes are stable; third-party plugins must not break on a minor release.
- Dialect *output* may change within a minor release when a backend changes what it reads. That is the point of the abstraction: your code does not move, the mapping does. Such changes are called out in the changelog.

## Release checklist

- [ ] `pytest`, `ruff check`, `ruff format --check`, `mypy` all clean
- [ ] Live tests pass against a real Phoenix
- [ ] All six examples run
- [ ] `_version.py` bumped
- [ ] `CHANGELOG.md` updated with the date
- [ ] Any new decision recorded in `docs/decisions.md`
- [ ] TestPyPI dry-run installs and imports
- [ ] Tag matches `__version__`
