# Troubleshooting

The run stops with a message in the log whenever something is unclear or inconsistent.
It does not guess. The validation banner of the editor catches most configuration
problems before you run. The most common messages:

## Configuration and materials

**`Output files already exist in …; use --overwrite explicitly`**
: A previous run wrote reports to the same folder. Choose another output folder, or tick
  *Overwrite existing reports* on the *Run* page (command line: `--overwrite`).

**`Unknown CASSY material '…'`**
: The name does not match a CASSY material exactly, typically in a configuration file
  edited by hand or a material library that is no longer selected. Pick the material
  from the dropdown on the *Materials* page, or check the *Material library* on the
  *General* page.

**`Material '…': …`** (refused material)
: The material's fatigue curve needs arguments a nodal assessment cannot provide (mean
  stress, Inconel options), or it is stress-based and the code is RCC-MR/RCC-MRx. Such
  materials are greyed out in the dropdown. See
  [Material assignment](materials.md#cassy-materials).

**`CASSY is not array-aware …`**
: The installed CASSY is too old. Install a CASSY version with array support in the same
  environment.

## Result files

**`Analysis X records no length unit …`** (or stress, temperature, time)
: The result was solved without a unit system (flagged `NO UNITS RECORDED` on the
  *Analyses* page). Declare the units it was solved in with *Units…*, see
  [Units](assessment.md#units).

**`Analysis X records stress unit 'MPa' but the configuration declares 'Pa'`**
: Your unit declaration contradicts the file. Reset it to *(from result)* with *Units…*:
  the units recorded in the file are used.

**`Analysis X has no result set N`**
: Set numbers are result set numbers over all load steps and substeps, starting at 1.
  The set dropdown of *Edit sets…* lists the existing sets with their times. If the
  result was re-solved, use *Re-read metadata*.

**`Analysis X (temperature source of Y) contains no BFE (structural) temperatures …`**
: The `.rst` has no temperatures. Take them from the thermal analysis with
  *Temperature…*, see [Temperatures](assessment.md#temperatures).

**`Temperature source X of Y must have exactly one set at time t; found 0`**
: Each structural set needs a thermal set with exactly the same time in seconds. Check
  that the thermal analysis saves results at the times of the structural one.

**`Analysis X does not have the same node IDs as …`**, **`… incompatible element connectivity/material IDs`**
: All analyses must use the same mesh. Check that the results come from the same model.

**`The mesh contains no solid elements`**
: Only solid elements are assessed. Shells, beams, contacts and other non-solid
  elements are skipped (and counted in `traceability.json`), but at least one solid
  element is needed.

## Material selections

**`Material named selections leave N solid elements unassigned`**,
**`… overlaps another assignment at N elements`**
: In named-selection mode the selections must cover every solid element exactly once.

**`Material selection '…' contains no complete elements`**
: A nodal selection only assigns elements whose nodes are all selected. A surface
  selection therefore assigns nothing. Use a body (elemental) selection.

## DPF server

**The server does not start**
: Check that Ansys with DPF is installed and licensed on this machine. If DPF does not
  find the installation, or finds the wrong one, choose it under *Ansys installation*
  on the *Run* page (command line: `--ansys-path`); see
  [Locating Ansys](installation.md#locating-ansys).

**Cannot connect to an existing server**
: Check that the server runs at the IP address and port given on the *Run* page (command
  line: `--dpf-ip`/`--dpf-port`) and that the result files are readable from the
  server's machine.

## Results

**Many invalid nodes (flag 16)**
: CASSY could not compute `N`, typically because the temperature is outside the range of
  the material data or the strain range is above the fatigue curve. Look at
  `T_degC_<event>` and the stress ranges of those nodes in the node report.

**Invalid nodes with flag 2**
: No temperature at those nodes for at least one set. Check the temperature source.
