# Installation

## Requirements

- **Ansys with DPF**, installed on the machine that runs the assessment, plus a license.
  `cassy-fatigue` reads results through DPF but does not need MAPDL.
- **Python 3.11 or later**.
- **CASSY** (`cassy-f4e`) 3.3.2 or later, the first version with *array support*
  (evaluating material properties and fatigue curves for many nodes at once). It is
  installed automatically; older, scalar-only CASSY installations are rejected with an
  error message.

## Install

```bash
python -m pip install cassy-fatigue
```

This installs the graphical editor and results viewer (PySide6, pyvistaqt) together with
the [command line](cli.md).

To work on a checkout of the repository instead, see [Development](development.md).

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
