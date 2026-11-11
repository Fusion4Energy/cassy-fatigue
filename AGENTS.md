# AGENTS.md — cassy-fatigue

Notes for coding agents and developers adding features. User documentation lives in
`README.md` and `docs/` (Zensical). This file holds what the code does not make obvious:
invariants, service policy, environments, verified state and gotchas. The development
history (phases, reviews, per-session status) is in Git before 2026-11-12.

## Purpose

CASSY checks fatigue on linearized stress paths. For thermally cycled components the
critical point is often not on a predefined path, so this tool computes `Vj = ncycles / N`
for every analyst-defined fatigue event and `U = Σ Vj` **at every node** of an Ansys
Mechanical result.

- Results are read with PyAnsys DPF (`ansys-dpf-core>=0.16.3,<0.17`).
- Fatigue rules are **CASSY's own** `PATH_CODES[code].computeVj`, imported, never
  re-implemented, so paths and nodes cannot drift apart. CASSY stays FEM-agnostic; this
  package (`cassy-fatigue`, import `cassy_fatigue`) depends on `cassy-f4e`, which must
  include array support (`kernel.require_array_support` rejects scalar-only installs).
- Codes: SDC-IC, RCC-MRx, RCC-MR.

## Layout

| Path | Role |
| --- | --- |
| `src/cassy_fatigue/config.py` | Strict YAML (duplicate keys rejected) → frozen dataclasses; loads = signed `LoadTerm`s (from CASSY) × scale, events = load sums |
| `src/cassy_fatigue/reader.py` | DPF reads: material scopings, ElementalNodal→nodal averaging **per material**, unit normalization, caches, mesh checks, VTU grid |
| `src/cassy_fatigue/states.py` | Load ranges, event total/primary tensors; temperature envelope |
| `src/cassy_fatigue/kernel.py` | Material loading/support checks, von Mises, `assess_event`, `evaluate`, `InvalidCode` |
| `src/cassy_fatigue/output.py` | Interface projection, `fatigue.vtu`, `configuration.yaml`, `traceability.json` |
| `src/cassy_fatigue/pipeline.py` | `run_config` (read → evaluate → write on a supplied server, shared by CLI and GUI), `planned_steps` |
| `src/cassy_fatigue/probe.py` | Read-only result metadata for GUI dropdowns |
| `src/cassy_fatigue/progress.py` | Step counting, logging, cancellation (`Progress`, `RunCancelled`) |
| `src/cassy_fatigue/windrive.py` | Windows: temporary drive letter for a UNC Ansys install while a local DPF server runs |
| `src/cassy_fatigue/cli.py` | `run` and `gui` subcommands; DPF connect-or-start context manager |
| `src/cassy_fatigue/gui/` | PySide6 app (default dependency, with pyvistaqt): Qt-free `model.py` (project dict, legacy conversion) and `results.py` (viewer logic); `session.py` (one DPF server per session), `pages.py`, `viewer.py`, `app.py` |
| `.github/workflows/` | `tests.yml` (ruff + offline pytest, Linux + Windows, 3.11–3.14), `docs.yml` (build, Pages deploy on `main`), `publish.yml` (PyPI on GitHub release) |
| `tests/` | Offline unit tests + opt-in live tests (`conftest.py` provides `dpf_live_server`) |
| `benchmarks/` | Fixture generator/verifier, reader checks, DPF smoke benchmark, example YAMLs |
| `benchmarks/fixtures/two_materials.rst` | **Git-tracked** test result (binary via `.gitattributes`) |
| `docs/`, `zensical.toml` | User docs → `site/` (ignored) |
| `docs/images/logo*.svg` | Logo (CASSY's C and nut, wave instead of its stress line); `logo.svg` is copied to `gui/logo.svg` (window icon, package data) — keep both in sync |

## Core semantics (keep these invariants)

- **Loads and events.** A load is a P or Q stress range `scale × Σ sign × σ(set)`,
  written `sets: [+struct:3, -struct:2]` (a set at most once per load; `scale` finite,
  non-zero). An event is a plain **sum** of loads (no signs, no repeats) with `id` and
  `ncycles`. Signs belong only in loads. Tensors are combined *before* the equivalent
  stress. Legacy formats (single-set loads, `delta`/`state_a`/`state_b` events) are
  rejected by `Config` with a hint; `gui/model._convert_legacy` converts them exactly
  (`-load` → new `<load>_neg` with flipped set signs).
- **P/Q.** Tagged by the analyst, never inferred. `PQF = VM(Δσ_total)` (CASSY applies the
  welding factor), `tresca = VM(Δσ_P)`. Nodal primary is conservative (includes bending
  and peak). Volumetric only (no inertial upper-limit VM).
- **Temperature.** Envelope (max, not signed) over all sets of the event's loads. Each set
  reads the temperature of **its own analysis** (BFE of the `.rst`) unless
  `analyses.<name>: {file, temperature: <source>}` names another (`.rth` or `.rst`),
  matched by exactly one equal **physical time in seconds** on identical node
  IDs/coordinates — never by set number. `Config.temperature_sources`;
  `reader.temperature_set` → `(source, set)`. Global `temperature: {source: X}` only with
  one structural analysis. Sources without temperatures are rejected at reader start.
- **`set` is a one-based DPF result-set ID**, not a load step. DPF's
  `get_frequency`/`get_cumulative_index` are zero-based.
- **Units.** Stress → Pa, temperature → degC, coordinates → m. Unknown units raise; never
  infer. Results without a unit system report `''`; `analyses.<name>.units` declarations
  are fallbacks only (`DPFReader.unit`): recorded units win, a contradiction is an error.
  Resolved units and origin → `traceability.json` `analysis_units`. GUI keeps them in
  `project["analysis_units"]`, temperature sources in `project["analysis_temperature"]`,
  so `project["analyses"]` stays name → path.
- **Materials.** Positive solver IDs or named selections, never mixed. Selections must
  partition all solid elements without overlap; nodal selections include only elements
  whose every node is selected. Selection mode: output `material_id` = YAML-order
  assignment IDs (mapped in `traceability.json`).
- **Averaging is material-local**; interface nodes get one value per material. Every
  structural `.rst` (including a temperature-only one) must match the base mesh: element
  IDs, connectivity, types, `mat`.
- **Interface projection.** `U_total` = max over materials of each material's sum over
  events; all per-event fields come from that governing material, so Vj sum to
  `U_total`. An invalid material is never hidden by another's finite value.
- **Invalid flags (bitmask).** 1 stress, 2 temperature, 16 CASSY NaN/non-positive N,
  32 missing property; 4 and 8 retired. No scalar re-run: N/Vj/U are NaN. Any invalid
  event makes `U_total` NaN; events are never dropped.
- **Regions.** Named selections set `welding_f`/`dpa`; overlaps rejected; defaults
  `welding_f=1`, `dpa=0`.
- **Solids only.** `reader.solid_elements` on the base mesh; non-solids are removed from
  material scopings in both modes, averages use solids only, `fatigue.vtu` is the solid
  sub-mesh, counts → `traceability.json` `skipped_elements`. No shell/beam layers.
- **Rejected inputs:** no solid elements, multiple fields per material/set, curves needing
  mean-stress or Inconel arguments, stress-based curves for RCC-MR/RCC-MRx.
- **Protected outputs.** Existing files need `--overwrite`; writes go through temp files +
  `os.replace`; a failed or cancelled run leaves no partial outputs. Exit 0 = "reports
  written", not an engineering pass.
- **Progress.** Steps: base mesh, mesh check per other analysis, regions, stress and
  temperature read per (set, material), CASSY per (event, material), write. A new
  processing step must be added to `pipeline.planned_steps` too (the live test asserts
  done == planned). Cancel works only at step boundaries.

## Services: DPF and MAPDL

- **DPF:** agents may start their **own** DPF server (127.0.0.1, free port) when needed and
  must **always shut it down**, including on failure. Never stop or restart user-started
  servers. Verify afterwards that no own `Ans.Dpf.Grpc` process or listening port is left.
- **MAPDL:** user-owned. Launching one or clearing a model (`mapdl_fixture.py
  --allow-clear`) needs the user's explicit OK for that task; always exit it afterwards.
- All connections: plaintext legacy gRPC (Ansys 2024 R2),
  `dpf.ServerConfig(protocol="gRPC", legacy=True, grpc_mode="insecure")`. Local only.
- **CLI:** `--dpf-ip`/`--dpf-port` or `DPF_IP`/`DPF_PORT` → connect, leave running
  (defaults `127.0.0.1:50054`). Nothing → start locally *after* config/material
  validation (first free port from 50054) and shut down in `finally`; `--ansys-path`
  picks the install. `cli.ensure_mapdl_reader` checks the started server can read MAPDL
  results (loads `mapdlOperatorsCore` explicitly if needed).
- **UNC installs (Windows):** `cmd.exe` rejects a UNC working directory for
  `Ans.Dpf.Grpc.bat`; `windrive.drive_letter_path` maps the share (`net use`, Z: downwards)
  for the server's lifetime and repoints matching `AWP_ROOTxxx`. Mock-tested only.
- Benchmark scripts (`dpf_smoke.py`, `phase0_reader_checks.py`, `mapdl_fixture.py`) only
  **connect** to an existing server.

## Environments

**Linux workstation (current).** Ansys 2024 R2 at `/opt/ansys`; licenses from
`l1-w19-lic{1,2,3}.f4eda.local`. DPF starts in ~3 s. `cassyenv` has CASSY (editable from
`~/git_repos/CASSY`) but no DPF. Do not modify the user's envs; build a scratch venv:

```bash
~/.conda/envs/cassyenv/bin/python -m venv --system-site-packages "$SCRATCH/venv"
"$SCRATCH/venv/bin/python" -m pip install "ansys-dpf-core>=0.16.3,<0.17" "ansys-mapdl-core>=0.74.1"
"$SCRATCH/venv/bin/python" -m pip install -e . --no-deps
```

**Windows workstation.** `C:\F4EMiniforge3\envs\cassy-rst-bench\python.exe`, editable
`cassy-fatigue` and CASSY, Ansys via `AWP_ROOT242` on a network share. Do not recreate
that environment or build wheels for checks.

## Commands

```bash
python -m pytest -q                                   # offline (live tests skip)
CASSY_DPF_INTEGRATION=1 AWP_ROOT242=/opt/ansys python -m pytest -q
                                                      # + live; starts/stops one DPF server
                                                      # (DPF_IP/DPF_PORT to reuse one)
QT_QPA_PLATFORM=offscreen python -m pytest -q tests/test_gui_app.py tests/test_gui_results.py
python -m cassy_fatigue run benchmarks/two_state.yaml --output <dir>
python benchmarks/mapdl_fixture.py --verify-only      # needs a running DPF server
python -m zensical build --strict
ruff check . && ruff format --check .               # ruff==0.17.4 (pinned in [dev])
```

Reference result for both example configs: 18 nodes, 4 cells, 0 invalid, max
`U_total` = 0.001551, `U_total == Vj_pulse + Vj_secondary`, 15 progress steps.
217 tests pass with live DPF + GUI (offscreen), 2026-11-11.

## Test fixture

`benchmarks/fixtures/two_materials.rst`: 18 nodes, four SOLID185 hexahedra, two materials
(E 200/100 GPa, α 1e-5/5e-6, ν 0), two sets (UX strain 0.001 @ 20 degC, 0.002 @ 120 degC;
TREF 20), named selections `INTERFACE` (6 shared nodes, nodal), `MATERIAL_1`/`MATERIAL_2`
(elemental). Stresses are analytic; `mapdl_fixture.verify` checks components, BFE
temperatures and material-local averages. Regenerate only if its definition changes:
generate into `benchmarks/generated/two_materials/`, verify, copy over the tracked file.
It records MKS units and has no shells, so it cannot exercise declared units or skipping.

## Verified vs not yet verified

Verified on Linux with the fixture, fakes and in-memory DPF meshes: all core semantics
above, array/scalar CASSY parity for all three codes, the GUI offscreen (editor, run,
results viewer logic, off-screen screenshots).

Not yet verified — check these before relying on them, and when touching related code:

1. A real thermal `.rth` temperature source (fakes only), and two real `.rst` whose
   temperatures differ at the same time.
2. A real result without recorded units (declared-unit path), and a real `.rst` with
   shells/contacts (solid-only path).
3. Hotspot cross-check against a CASSY path ending at the node (nodal Vj should be ≥ the
   path end-point Vj).
4. Production scale: ~1e6 nodes × 10 events, 30–100 GB `.rst`. Suspected slow spots:
   per-material DPF operator calls, mesh read twice (GUI probe then run), `np.isin`
   region labelling, VTU size. Progress steps are not time-weighted.
5. Cluster deployment (DPF availability/licensing).
6. The real `QtInteractor` on a display (Linux and Windows): rendering, right-click
   picking, camera persistence, clean close. GUI on Windows (UNC drive letter,
   `cassy-fatigue-gui` launcher).
7. CASSY docs: link this tool from `docs/user_guide/`; `docs/limitations.md` there still
   claims stress-based path curves are unsupported.

Out of scope: immediate/ratcheting rules (need linearization), bolt Neuber fatigue,
automatic cycle extraction/rainflow. Singularity hotspots stay mesh-dependent.

## Gotchas

- Native DPF `mesh.grid` stalls importing the full VTK module set; `reader.grid` calls the
  `mesh_to_pyvista` operator directly and checks counts/coordinates.
- DPF takes the result key from the file extension verbatim and has no reader for `RST`
  (uppercase, common on Windows): it fails with `Source operator "result_info_provider"
  not found`, which looks like a missing plugin. Always open results via
  `reader.open_model` (lower-cased key).
- The DPF server needs absolute result paths.
- `dpf.start_local_server` auto-increments the port if 50054 is busy (even TIME-WAIT).
- PyMAPDL 0.74.1 + MAPDL 2024 R2 on Linux: `transport_mode="insecure"`, **no** `ip=`,
  `jobname="two_materials"` (the generator's `/FILNAME` is ignored), `start_instance=True`.
  The port lingers in TIME-WAIT after `exit()`.
- Never clean up processes with `pkill -f <pattern>`: it killed the agent's own shell.
  Filter `ps -u $USER -o pid=,comm=,args=` by executable name.
- GUI: DPF work runs on one `ThreadPoolExecutor` worker; results return via a `Signal`.
  Rebuild pages with `QTimer.singleShot(0, window.reload)`, never inside the signal
  handler of a widget being rebuilt. An `Ans.Dpf.Grpc.sh <defunct>` zombie stays until the
  Python process exits after `server.shutdown()` (harmless).
- `pyvistaqt.QtInteractor` cannot render on Qt `offscreen`/`vnc`/`minimalegl`/`eglfs` here
  and there is no Xvfb; `pv.Plotter(off_screen=True)` works (Mesa). `viewer.make_plotter`
  returns a placeholder on offscreen Qt; tests inject an off-screen plotter.
- dpf-core emits NumPy `DeprecationWarning`s (`data.shape = …`); not ours. IDE "import
  could not be resolved" diagnostics come from the editor interpreter.

## Working conventions

- Report verification honestly (earlier sessions had false-positive completion claims):
  state what ran, what was skipped and why; fixture checks are not engineering
  qualification.
- Commit only when asked, directly on `main`. Remote: `Fusion4Energy/cassy-fatigue` on
  GitHub; push only when asked.
- **Releases and versions.** Version from the Git tag via setuptools-scm (`vX.Y.Z`); a
  published GitHub release triggers `publish.yml` (PyPI trusted publishing). Requires
  `cassy-f4e>=3.3.2` (first array-aware release; 3.3.1 lacks it) and therefore Python
  ≥3.11 (pandas 3). CI installs CASSY from PyPI, so it cannot pass before 3.3.2 is
  published. Tracked text files use CRLF line endings: keep them when editing.
- README links are absolute (GitHub raw / Pages) because it is the PyPI page.
- Keep README, `docs/` and this file in sync when behaviour changes. The docs are written
  for analysts: describe what to do and what results mean; keep module names, test
  counts and environment paths here or in `docs/development.md`.
- Docs are **GUI-first**: analysts use the GUI almost always. Describe features by GUI
  page/button (`assessment.md`, `materials.md`, `viewer.md`); YAML syntax and the CLI live
  only under *Advanced* (`configuration.md`, `cli.md`). User docs name no specific Ansys
  version: DPF finds the installation (link to the DPF docs, don't explain its order).
- Prefer vectorized NumPy over per-node Python loops; production meshes are large.
- **Ruff is the only linter/formatter, pinned to `ruff==0.17.4`** (`[dev]` extra). Every change
  must leave `ruff check .` and `ruff format --check .` clean, imports sorted by ruff's `I`
  rules (config in `pyproject.toml`). Use the pinned version: other versions format and lint
  differently. Bump it deliberately, in its own commit, re-running `ruff check --fix .` and
  `ruff format .` over the whole repo. Use module loggers (`logger = logging.getLogger(__name__)`),
  not `logging.info(...)` (LOG015). A `noqa` needs a reason in the same comment.
