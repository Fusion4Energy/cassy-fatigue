# Configuration file

!!! info "Advanced"
    Most assessments are set up in the [GUI](assessment.md), which writes this file for
    you. This page is for users who write or generate configuration files by hand, for
    example for scripted or batch runs from the [command line](cli.md). The meaning of
    every entry is explained in [Setting up an assessment](assessment.md); this page only
    gives the file syntax.

An assessment is one YAML file. A complete example:

```yaml
code: SDC-IC
analyses:
  struct: model.rst
materials:
  1: SS316L(N)-IG_SDC-IC
loads:
  pressure: {classification: P, sets: [+struct:1]}
  heatup:   {classification: Q, sets: [+struct:3, -struct:2]}
fatigue_events:
  - {id: startup, ncycles: 30000, loads: [pressure, heatup]}
  - {id: thermal, ncycles: 1000,  loads: [heatup]}
regions:
  - {named_selection: WELD_A, welding_f: 1.5, dpa: 0}
```

| Entry | Meaning | GUI page |
| --- | --- | --- |
| `code` | Design code: `SDC-IC`, `RCC-MRx` or `RCC-MR` | *General* |
| `material_library` | Optional folder with additional or modified CASSY material files | *General* |
| `analyses` | Your result files, each under a short name you choose | *Analyses* |
| `materials` | Which CASSY material applies to which part of the mesh | *Materials* |
| `regions` | Optional welding factor and dpa for named selections | *Regions* |
| `loads` | Named stress ranges, each classified P or Q | *Loads* |
| `fatigue_events` | The events: which loads they sum and how many cycles | *Fatigue events* |

File paths are relative to the YAML file. The file is checked strictly: misspelled or
unknown keys, duplicate keys, references to undefined loads and duplicate event IDs
stop the run with a message, before any result is read.

## Analyses

```yaml
analyses:
  op: operation.rst
  start: startup.rst
  therm: thermal.rth
```

An entry can also be written in long form, to declare [units](assessment.md#units) or
a [temperature source](assessment.md#temperatures):

```yaml
analyses:
  mech:  {file: mechanical.rst, temperature: therm, units: {length: mm, stress: MPa}}
  therm: thermal.rth
```

`units` accepts `length`, `stress`, `temperature` and `time`, with the values listed in
[Units](assessment.md#units). `temperature` names another analysis; without it, each
`.rst` uses its own temperatures.

## Materials

By solver material ID (integer keys):

```yaml
materials:
  1: SS316L(N)-IG_SDC-IC
  2: CuCrZr-IG Tr B SDC-IC
```

By named selection (selection names as keys, spelled exactly as in the result file):

```yaml
materials:
  STEEL_BODY: SS316L(N)-IG_SDC-IC
  COPPER_BODY: CuCrZr-IG Tr B SDC-IC
```

The two forms cannot be mixed. In named-selection mode the assignment numbers in the
results follow the order of the entries. See [Material assignment](materials.md).

## Regions

```yaml
regions:
  - {named_selection: WELD_A, welding_f: 1.5, dpa: 0}
  - {named_selection: FIRST_WALL, welding_f: 1, dpa: 2.5}
```

## Loads

```yaml
loads:
  heatup:
    classification: Q              # P (primary) or Q (secondary)
    sets: [+struct:3, -struct:2]   # stress of set 3 minus stress of set 2
    scale: 1                       # optional factor, default 1
```

`struct:3` means *result set 3 of analysis `struct`*. Every set needs an explicit sign.
Sets of different analyses can be combined (`[+op:3, -start:3]`); each set at most once
per load. `scale` must be finite and non-zero.

## Fatigue events

```yaml
fatigue_events:
  - id: startup
    ncycles: 30000
    loads: [pressure, heatup]
```

Loads are listed without signs, each at most once.
