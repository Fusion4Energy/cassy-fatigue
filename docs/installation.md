# Installation

## Requirements

- **Ansys with DPF**, installed on the machine that runs the assessment, plus a license.
  `cassy-fatigue` reads results through DPF but does not need MAPDL.
- **Python 3.10 or later**.
- **CASSY** (`cassy-f4e`) in a version with *array support*, i.e. able to evaluate
  material properties and fatigue curves for many nodes at once. Older, scalar-only
  CASSY installations are rejected with an error message.

## Install

In the Python environment where CASSY is installed, from a checkout of the
`cassy-fatigue` repository:

```bash
python -m pip install -e ".[gui]"
```

The `gui` extra installs PySide6 and pyvistaqt for the graphical editor and results
viewer. Leave it out (`pip install -e .`) only if you will use the
[command line](cli.md) exclusively.

Check the installation by starting the GUI:

```bash
cassy-fatigue gui
```

On Windows, the `cassy-fatigue-gui` launcher starts it without a console window. If the
`cassy-fatigue` command is not found (for example after an editable install whose entry
points were not refreshed), `python -m cassy_fatigue gui` always works.

## Locating Ansys

When `cassy-fatigue` starts its own DPF server, DPF finds the Ansys installation by
itself; see [Manage multiple DPF Server installations](https://dpf.docs.pyansys.com/version/stable/getting_started/dpf_server.html)
in the DPF documentation. To use a specific installation, choose it under *Ansys
installation* on the *Run* page of the editor (command line: `--ansys-path`).

On Windows, installations on a network share are supported: the tool maps the share to
a temporary drive letter while the server runs and removes it afterwards.

See [Setting up an assessment](assessment.md#dpf-server) for connecting to a DPF server
that is already running instead.
