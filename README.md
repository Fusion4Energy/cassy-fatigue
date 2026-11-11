# cassy-fatigue

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/Fusion4Energy/cassy-fatigue/main/docs/images/logo_full_dark.svg">
  <img src="https://raw.githubusercontent.com/Fusion4Energy/cassy-fatigue/main/docs/images/logo_full.svg" alt="cassy-fatigue" width="500"/>
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

Requirements: Ansys with DPF (and a license) and Python ≥ 3.11. CASSY (`cassy-f4e`
3.3.2 or later) is installed automatically.

```bash
python -m pip install cassy-fatigue
```

## Use

```bash
cassy-fatigue gui                                  # new assessment
cassy-fatigue gui results/example/fatigue.vtu      # results viewer only
```

On Windows, `cassy-fatigue-gui` starts the GUI without a console window. A run writes
`fatigue.vtu` (usage, allowable cycles, stress ranges, temperatures and invalid flags at
every node), `configuration.yaml` and `traceability.json` (versions, file hashes,
units). A local DPF server is started and stopped automatically.

Advanced users can write the configuration file by hand and run it from the command line
(`cassy-fatigue run my_assessment.yaml --output results/`), see
[Configuration file](https://fusion4energy.github.io/cassy-fatigue/configuration.html) and [Command line](https://fusion4energy.github.io/cassy-fatigue/cli.html).

## Documentation

The user guide is published at <https://fusion4energy.github.io/cassy-fatigue/>.

- [Getting started](https://fusion4energy.github.io/cassy-fatigue/getting-started.html)
- [Setting up an assessment](https://fusion4energy.github.io/cassy-fatigue/assessment.html) and [material assignment](https://fusion4energy.github.io/cassy-fatigue/materials.html)
- [Results viewer](https://fusion4energy.github.io/cassy-fatigue/viewer.html)
- [Results](https://fusion4energy.github.io/cassy-fatigue/outputs.html) and [troubleshooting](https://fusion4energy.github.io/cassy-fatigue/troubleshooting.html)
- [Scope and limitations](https://fusion4energy.github.io/cassy-fatigue/limitations.html)

Developers: see [Development](https://fusion4energy.github.io/cassy-fatigue/development.html) and
[AGENTS.md](https://github.com/Fusion4Energy/cassy-fatigue/blob/main/AGENTS.md).

## Status

Prototype. Results agree with CASSY's own scalar calculations for all three codes and
with an analytic two-material test model. The tool has not been qualified for
engineering use, and it has not yet been run on production-size models.
A successful run means the reports were written, not that the component passes.
