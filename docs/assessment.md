# Setting up an assessment

An assessment is set up in the **Configuration** tab of the GUI. It needs the `gui`
extra (see [Installation](installation.md)).

```bash
cassy-fatigue gui                          # new, empty assessment
cassy-fatigue gui my_assessment.yaml       # continue an existing one
```

On Windows, `cassy-fatigue-gui` starts it without a console window.

## The editor

- **Pages** on the left: *General*, *Analyses*, *Materials*, *Regions*, *Loads*,
  *Fatigue events*, *Run*. Work through them from top to bottom.
- **Validation banner** at the top, updated after every edit. It shows the first problem
  in the configuration, or turns green when the configuration is complete. Checks that
  need the result data (meshes, units, material coverage) still run when the assessment
  starts.
- **Log** at the bottom: result reads and their duration, the DPF server, run progress
  and errors.
- **File menu**: *New*, *Open…*, *Save*, *Save As…*, *Open results…* (`Ctrl+R`).

The assessment is saved as a configuration file (`.yaml`). You can save an incomplete
one as a draft and continue later. File paths are stored relative to the configuration
file, so you can move a folder with the configuration and its result files together.

When you add a result file or open a configuration, the editor reads the file's
metadata in the background (busy indicator in the status bar) and uses it to fill its
dropdowns: result sets with their times, material IDs, named selections, whether the
file contains temperatures, element types and the unit system. It reads the whole mesh,
so this can take minutes for a large model.

## General

- **Design code**: SDC-IC, RCC-MRx or RCC-MR.
- **Material library**: optional folder with additional or modified CASSY material
  files. It follows CASSY's usual rules for overriding bundled materials.

## Analyses

*Add…* adds one or more Ansys result files (`.rst`, `.rth`). Each gets a short name,
used by the loads; double-click it to rename it. Select a row to see the file's mesh,
result sets and named selections below the table.

All analyses must use the same mesh: same node IDs and coordinates, and for structural
`.rst` files also the same elements and material IDs. This is checked when the run
starts.

The *Metadata* column summarises the file and flags problems: `NO UNITS RECORDED`,
`NO SOLID ELEMENTS`, non-solid elements that will be ignored, or a structural file
without stresses. *Re-read metadata* reads the file again, for example after a re-solve.

### Temperatures

The temperature used for an event at a node is the **maximum** over all result sets
used in its loads, also those subtracted. Temperatures are never added or subtracted.

By default each set takes the temperature stored in **its own** `.rst` file (the
structural body temperatures, BFE); the *Temperature* column shows `own`. In a load
`+op:3, −start:3` the temperature is therefore the maximum of `op` set 3 and `start`
set 3.

If a structural result does not contain the temperatures, typically because they come
from a separate thermal analysis, add the thermal `.rth` as an analysis too, select the
structural row and click *Temperature…* to choose it as the source. The column then
shows `from <analysis>`.

Each structural set is matched to the set of the source with **the same time in
seconds**. If no set or several sets of the source have that time, the run stops.
Matching by set number alone is never done. Sources without temperatures are marked in
the dialog, and the run stops at the start if one is chosen. `traceability.json`
records which source was used for each analysis.

### Units

Stresses, temperatures and coordinates are converted to Pa, °C and m, using the unit
system recorded in each result file; the *Units* column shows `from result`. If a unit
is unknown, the run stops rather than guessing.

A result solved without a unit system (for example MAPDL without `/UNITS`) records no
units and is flagged `NO UNITS RECORDED`. Select it, click *Units…* and choose the units
the model was solved in:

| Quantity | Allowed units |
| --- | --- |
| Length | `m`, `mm`, `cm`, `in`, `ft` |
| Stress | `Pa`, `kPa`, `MPa`, `GPa` |
| Temperature | `degC`, `C`, `K`, `degF` |
| Time | `s` |

Declarations only fill in units the result does not record. If the result records a
unit and your declaration differs, the run stops instead of overriding it. Declare the
units of each analysis separately, including a temperature source.
`traceability.json` records, for each analysis, the units used and whether they came
from the result or from your declaration.

## Materials

Assign a CASSY material to every part of the model, by solver material ID or by named
selection. See [Material assignment](materials.md).

## Regions

Optional named selections with a welding factor (`welding_f`) and a dose (`dpa`). Pick
the selection from the dropdown (nodal or elemental selections of the first structural
analysis) and enter the values.

Nodes outside every region use `welding_f = 1` and `dpa = 0`. Regions must not overlap.
The welding factor must be at least 1 and dpa non-negative. Regions are independent of
the material assignment.

## Loads

A load is **one stress range** that you have identified, for example the range between
cold shutdown and hot operation. Each row has:

- **Name**, used by the events (double-click to rename; events are updated).
- **Class**: P (primary) or Q (secondary). This is your engineering judgement; it is
  not derived from the results.
- **Sets**: the result sets, each added (`+`) or subtracted (`−`). Double-click (or
  *Edit sets…*) to edit them: each row is a sign, an analysis and a result set. The
  dropdown shows each set with its time and load step/substep, for example
  `set 2: t=2 s, LS2/SS1`.
- **Scale**: optional factor, default 1.

The stress tensor of the load is `scale × (σ(set a) − σ(set b) + …)`, the same structure
as a load on a CASSY path.

- Set numbers are the **result set numbers** as listed by Ansys (1, 2, 3, … over all
  load steps and substeps), not load step numbers.
- Sets from different analyses can be combined in one load. Each set may appear only
  once in a load.
- *Add one per set…* creates a `+analysis:set` load for every set of an analysis, a
  quick start when each result set is already a stress range from a reference state.

!!! note "Do not count a stress twice"
    Each load should be the range you intend. If a result set already contains, for
    example, the pressure stress, do not also add a separate pressure load to the same
    event.

## Fatigue events

An event is a cycle type that occurs `ncycles` times. Each row has an **ID**, the
number of cycles and the **loads** it combines (double-click, or *Choose loads…*, to
tick them). Its stress range is the **sum** of these loads.

For each event and node:

- the total range `Δσ` is the sum of all its loads, and the primary range `Δσ_P` the sum
  of its P loads only;
- the equivalent (von Mises) stress ranges are computed from these summed tensors;
- CASSY computes the allowable cycles `N`, and the usage is `Vj = ncycles / N`.

The total usage is `U_total = Σ Vj` over all events. Events are listed as you define
them: there is no automatic cycle counting or rainflow. Loads cannot be subtracted in an
event: to use a range the other way round, define a load with the opposite signs.

## Run

*Save and run* saves the configuration and runs the assessment. Reports go to the
*Output folder* shown (default: `<configuration name>_results` next to the
configuration file). Existing reports are replaced only if *Overwrite existing reports*
is ticked.

While the run is going, the page shows a progress bar, the current step, the elapsed
time and a rough estimate of the remaining time. The estimate assumes all steps take
equally long, and DPF cannot report progress within a single read, so treat it as an
indication only.

*Cancel* stops the run at the end of the current step. No reports are written and
existing reports are left as they were. The window cannot be closed while a run is
going: cancel it first.

When the run finishes, its results are loaded into the Results tab. *Show results*
switches there and *Open output folder* opens the reports. See
[Results viewer](viewer.md) and [Results](outputs.md).

### DPF server

DPF reads results through a server process. The *Run* page chooses the server for the
session:

- **Start a local server** (default): started on the first result read and shut down
  when you close the window or click *Release DPF server*. Starting takes a few seconds,
  plus the license checkout on the first read. *Ansys installation* selects the
  installation if DPF does not find the right one (see
  [Locating Ansys](installation.md#locating-ansys)).
- **Connect to an existing server** at an IP address and port. The GUI never shuts down
  a server it did not start.

The result files must be readable by the DPF server. The connection is unencrypted, so
only connect to servers on your own machine or a trusted network.
