# cassy-fatigue

![cassy-fatigue](images/logo_full.svg#only-light){ width="500" }
![cassy-fatigue](images/logo_full_dark.svg#only-dark){ width="500" }

`cassy-fatigue` runs a design-code fatigue check at **every node** of an Ansys
Mechanical solid model, instead of only along stress paths. It is a plug-in of ``cassy-f4e`` (see doc [here](https://fusion4energy.github.io/CASSY/)).

CASSY  assesses fatigue on linearized stress classification lines. For thermally cycled
components the critical point may be somewhere you did not put a path. This tool
applies the same CASSY fatigue rules (SDC-IC, RCC-MRx or RCC-MR) to the nodal stresses
and temperatures of your result files. You get a usage map of the whole model and can
see where the hotspots are. Materials and rules are imported directly from cassy.

## How it works

1. **You describe the loading** in the graphical editor. You add your `.rst` (and
   optionally `.rth`) result files, assign a CASSY material to each part of the model and
   define *loads* and *fatigue events*. A load is a stress range built from result sets
   (for example "set 3 minus set 2") and classified as primary (P) or secondary (Q). An
   event is a sum of loads with a number of cycles.
2. **Results are read with Ansys DPF.** Stresses and temperatures are read from your
   result files and averaged to the nodes, separately for each material.
3. **CASSY computes the allowable cycles.** At each node and for each event, CASSY's
   own fatigue routines compute the allowable number of cycles `N`, the same code used
   for path assessments.
4. **You inspect the results.** For each event the usage fraction is
   `Vj = ncycles / N`, and the total is `U_total = Σ Vj`. Everything is written to a
   `fatigue.vtu` file, which opens in the built-in results viewer (or in ParaView).

Nodes that cannot be assessed (missing temperature, property out of range, stress above
the fatigue curve, …) are flagged and shown as invalid. They are never silently dropped.

## Where to go next

- [Installation](installation.md): what you need and how to install it.
- [Getting started](getting-started.md): open, run and inspect the bundled example in
  the GUI.
- [Setting up an assessment](assessment.md): the editor page by page: analyses,
  temperatures, units, regions, loads, events and running.
- [Material assignment](materials.md): by Ansys material ID or by named selection.
- [Results viewer](viewer.md) and [Results](outputs.md): what is in `fatigue.vtu` and how
  to read it.
- [Troubleshooting](troubleshooting.md): common error messages and what to do.
- [Scope and limitations](limitations.md): what the tool does not do and what has been
  verified.

Advanced users can also write the [configuration file](configuration.md) by hand and run
assessments from the [command line](cli.md), for example in scripts or batch jobs.

!!! warning "Prototype"
    `cassy-fatigue` is a prototype. It has been checked against analytic test cases and
    against CASSY's own scalar calculations, but not qualified for engineering use. A
    successful run means the reports were written, not that the component passes.
