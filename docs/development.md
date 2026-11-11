# Development

This page is for people changing `cassy-fatigue`. Design invariants, environment notes and
known pitfalls are in `AGENTS.md` at the repository root. Read it before adding features.

## Setup

In an environment with an array-aware CASSY, from the repository root:

```bash
python -m pip install -e ".[dev,mapdl,docs]"
```

`mapdl` is needed to regenerate the test fixture and by the benchmark-script tests,
`docs` only to build this site.

## Code structure

| Module | Role |
| --- | --- |
| `config.py` | Strict YAML validation into immutable dataclasses |
| `reader.py` | DPF reads: material scopings, material-local nodal averaging, units, mesh checks |
| `states.py` | Load ranges and event tensors, temperature envelope |
| `kernel.py` | CASSY material loading and the nodal call of `PATH_CODES[code].computeVj` |
| `output.py` | Interface projection and the three report files |
| `pipeline.py` | Read → evaluate → write, shared by the CLI and the GUI |
| `probe.py` | Result metadata for the GUI dropdowns |
| `cli.py` | Command line and DPF connect-or-start |
| `gui/` | PySide6 editor and results viewer (`model.py` and `results.py` are Qt-free) |

CASSY's fatigue rules are imported, never re-implemented.

## Tests

```bash
python -m pytest -q                        # offline; live tests are skipped
CASSY_DPF_INTEGRATION=1 python -m pytest -q
```

With `CASSY_DPF_INTEGRATION=1` the live tests start one local DPF server for the session
and shut it down at the end, or connect to `DPF_IP`/`DPF_PORT` if set. They read the
tracked fixture `benchmarks/fixtures/two_materials.rst`. GUI tests need `pytest-qt` and
run headless with `QT_QPA_PLATFORM=offscreen`.

`benchmarks/README.md` describes the fixture generator (needs MAPDL), the reader checks
and the DPF smoke benchmark.

## Lint and format

Ruff is pinned (`ruff==0.17.4`, installed by the `dev` extra) and is the only linter and
formatter. Before committing:

```bash
ruff check --fix .    # lint, including import sorting
ruff format .
```

Both must be clean (`ruff check . && ruff format --check .`). Use the pinned version: other
ruff versions apply different rules and formatting. Change the pin deliberately and
re-run both commands over the whole repository in the same commit.

## Continuous integration

GitHub Actions (`.github/workflows/`) run on every push to and pull request against
`main`:

- `tests.yml`: ruff (pinned version) and the offline test suite, including the GUI tests
  off-screen, on Linux and Windows for Python 3.11–3.14, as in CASSY. The runners
  have no Ansys, so the live DPF tests are skipped there. Run them locally before a
  release.
- `docs.yml`: builds this site with `--strict`; on `main` it deploys it to GitHub Pages
  (repository *Settings → Pages → Source: GitHub Actions*).

## Releases

The version comes from the Git tag (setuptools-scm); there is no version number in the
code. To publish version `X.Y.Z` on PyPI, create a GitHub release with the tag `vX.Y.Z`
on `main`. `publish.yml` builds the sdist and wheel, checks them with `twine`, and uploads
them by PyPI trusted publishing. One-time setup: on PyPI, add the repository as a trusted
publisher for `cassy-fatigue` (workflow `publish.yml`, environment `pypi`), and create
the `pypi` environment in the GitHub repository settings.

To check a build locally:

```bash
python -m build && python -m twine check --strict dist/*
```

## Documentation

```bash
python -m zensical build --strict
```

The site is written to `site/` (Git-ignored); open `site/index.html` directly.
`python -m zensical serve` starts a live preview. Pages are in `docs/`, the navigation in
`zensical.toml`.

The user pages are written for analysts. Describe what to do and what the results mean,
and keep implementation details on this page or in `AGENTS.md`. When behaviour changes,
update `README.md`, `docs/` and `AGENTS.md` together.
