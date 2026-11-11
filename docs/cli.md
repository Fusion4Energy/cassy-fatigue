# Command line

!!! info "Advanced"
    The GUI's *Save and run* does the same as the command below. Use the command line
    for scripted or batch runs, with a [configuration file](configuration.md) written by
    the GUI or by hand.

```bash
cassy-fatigue run my_assessment.yaml --output results/my_assessment
```

`python -m cassy_fatigue` works in place of `cassy-fatigue` and takes the same
arguments.

The tool checks the configuration and the CASSY materials first, then starts a local
DPF server, reads the results, runs CASSY and writes the [three report files](outputs.md).
The log shows each step and its duration:

```text
[3/15] Reading stress struct set 2, material 1…
[3/15] done in 0.1 s
```

## Options

| Option | Meaning |
| --- | --- |
| `--output DIR` | Output folder (default: `<configuration name>_results` next to the configuration file) |
| `--overwrite` | Replace existing reports in the output folder; without it the run stops |
| `--ansys-path DIR` | Ansys installation used to start a local DPF server (default: found by DPF, see [Locating Ansys](installation.md#locating-ansys)) |
| `--dpf-ip`, `--dpf-port` | Connect to a DPF server that is already running instead of starting one |

## DPF server

- **By default** the tool starts a local server when it is needed (after the
  configuration has been checked) and always shuts it down at the end, also after an
  error or Ctrl-C.
- **To use a server that is already running**, give its address with `--dpf-ip` and/or
  `--dpf-port` (or the `DPF_IP`/`DPF_PORT` environment variables). Missing parts default
  to `127.0.0.1:50054`. The tool leaves that server running. `--ansys-path` cannot be
  combined with these options.

The result files must be readable by the DPF server. The connection is unencrypted, so
only connect to servers on your own machine or a trusted network.

## Exit codes

- `0`: the reports were written. This says nothing about the outcome of the assessment.
  Usage above one and invalid nodes are results to review, not errors.
- `1`: the run stopped because of a configuration or processing error, described in the
  log. No reports are written.

## Opening the GUI

```bash
cassy-fatigue gui [my_assessment.yaml | results/fatigue.vtu]
```

opens the editor on a configuration file, or only the results viewer on a
`fatigue.vtu`.
