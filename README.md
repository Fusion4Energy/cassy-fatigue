# cassy-fatigue

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/images/logo_full_dark.svg">
  <img src="docs/images/logo_full.svg" alt="cassy-fatigue" width="500"/>
</picture>

Design-code fatigue assessment at **every node** of an Ansys Mechanical solid model.

CASSY checks fatigue along linearized stress paths. For thermally cycled components the
critical point is often not on a path you defined. `cassy-fatigue` reads the nodal
stresses and temperatures of your `.rst`/`.rth` files with Ansys DPF and applies CASSY's
own fatigue rules (SDC-IC, RCC-MRx, RCC-MR) at each node. For every fatigue event you
define it gives the usage `Vj = ncycles / N`, and the total `U_total = Σ Vj`, as a map of
the whole model.

The assessment is set up and run in a graphical editor: add the result files, assign
CASSY materials, define loads (signed combinations of result sets, classified P or Q)
and fatigue events (sums of loads with a cycle count), then *Save and run*. The results
open in a built-in 3D viewer with hotspot tables and node reports.

## Install

Requirements: Ansys with DPF (and a license), Python ≥ 3.10, and CASSY (`cassy-f4e`)
with array support. From a checkout, in the environment where CASSY is installed:

```bash
python -m pip install -e ".[gui]"
```

## Use

```bash
cassy-fatigue gui                                  # new assessment
cassy-fatigue gui benchmarks/two_state.yaml        # open the bundled example
cassy-fatigue gui results/example/fatigue.vtu      # results viewer only
```

On Windows, `cassy-fatigue-gui` starts the GUI without a console window. A run writes
`fatigue.vtu` (usage, allowable cycles, stress ranges, temperatures and invalid flags at
every node), `configuration.yaml` and `traceability.json` (versions, file hashes,
units). A local DPF server is started and stopped automatically.

Advanced users can write the configuration file by hand and run it from the command line
(`cassy-fatigue run my_assessment.yaml --output results/`), see
[Configuration file](docs/configuration.md) and [Command line](docs/cli.md).

## Documentation

The user guide is in [`docs/`](docs/index.md). Build it as a website with
`python -m zensical build --strict` (needs the `docs` extra), then open `site/index.html`.

- [Getting started](docs/getting-started.md)
- [Setting up an assessment](docs/assessment.md) and [material assignment](docs/materials.md)
- [Results viewer](docs/viewer.md)
- [Results](docs/outputs.md) and [troubleshooting](docs/troubleshooting.md)
- [Scope and limitations](docs/limitations.md)

Developers: see [docs/development.md](docs/development.md) and [AGENTS.md](AGENTS.md).

## Status

Prototype. Results agree with CASSY's own scalar calculations for all three codes and
with an analytic two-material test model. The tool has not been qualified for
engineering use, and it has not yet been run on production-size models.
A successful run means the reports were written, not that the component passes.
