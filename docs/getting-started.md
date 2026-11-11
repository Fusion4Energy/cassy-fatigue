# Getting started

This page opens the bundled example in the GUI, runs it and shows what you get. It
assumes the [installation](installation.md) is done and that you work from a checkout of
the repository (the example files are in `benchmarks/`).

## The example

`benchmarks/fixtures/two_materials.rst` is a small test model: four hexahedral elements
(2 × 2), two materials (one per half), and two result sets with uniform stretching
and heating. Its material properties and P/Q classification are artificial. It is meant
to exercise the workflow, not to represent a real component.

`benchmarks/two_state.yaml` is a ready-made assessment of it.

## Open it in the editor

```bash
cassy-fatigue gui benchmarks/two_state.yaml
```

(or start `cassy-fatigue gui` and use *File → Open…*). The editor reads the result
file's metadata in the background; the status bar shows a busy indicator while it does.
Go through the pages on the left:

| Page | What the example contains |
| --- | --- |
| *General* | Design code SDC-IC |
| *Analyses* | One result file, named `struct` |
| *Materials* | Material ID 1 → `SS316L(N)-IG_SDC-IC`, ID 2 → `CuCrZr-IG Tr B SDC-IC` |
| *Regions* | Named selection `INTERFACE` with welding factor 1.5 |
| *Loads* | `hot` (Q, `+struct:2`) and `cold_removed` (P, `-struct:1`) |
| *Fatigue events* | `pulse` (100 cycles, `hot` + `cold_removed`) and `secondary` (10 cycles, `hot`) |

The banner at the top turns green when the configuration is complete.
[Setting up an assessment](assessment.md) explains every page.

## Run it

On the *Run* page, click *Save and run*. The tool checks the configuration and the
CASSY materials, starts a local DPF server, reads the results, runs CASSY and writes
three files to the output folder (by default `two_state_results` next to the
configuration file):

| File | Content |
| --- | --- |
| `fatigue.vtu` | The mesh with `U_total`, per-event `Vj`, `N`, stress ranges, temperatures and invalid flags at every node |
| `configuration.yaml` | The configuration as it was used, with resolved paths |
| `traceability.json` | Software versions, file hashes, units, material assignments: what you need to reproduce or audit the run |

The progress bar and the log at the bottom show each step and its duration. On a large
model the mesh and stress reads take most of the time. Existing reports are never
replaced unless you tick *Overwrite existing reports*, so a second run of the example
needs that box ticked or another output folder.

## Look at the results

When the run finishes, the results are loaded into the **Results** tab; *Show results*
switches there. For this example the maximum `U_total` is 0.00155, with no invalid
nodes.

You can switch to a single event's `Vj` or `N`, threshold, clip, list the hotspots and
click a node for its full report. See [Results viewer](viewer.md). To open earlier
results, use *File → Open results…* or `cassy-fatigue gui <folder>/fatigue.vtu`.

## Your own assessment

Start with *File → New*, add your result files on the *Analyses* page and work through
the pages from top to bottom. [Setting up an assessment](assessment.md) describes each
step.
