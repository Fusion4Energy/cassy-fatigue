# Results

Every run writes three files to the output folder.

## `fatigue.vtu`

The solid mesh of the model, with the original node and element IDs, coordinates in
metres, and these values at every node:

| Field | Meaning |
| --- | --- |
| `U_total` | Total usage, the sum of `Vj` over all events |
| `invalid_code` | 0 if the node is valid, otherwise the [flags](#invalid-nodes) of all events combined |
| `material_id_U` | The material that governs `U_total` at this node |
| `Vj_<event>` | Usage of the event, `ncycles / N` |
| `N_<event>` | Allowable number of cycles |
| `delta_sigma_eq_Pa_<event>` | Equivalent (von Mises) total stress range, Pa |
| `delta_primary_eq_Pa_<event>` | Equivalent primary stress range (P loads only), Pa |
| `T_degC_<event>` | Temperature used, °C (maximum over the event's sets) |
| `de1_<event>` … `de4_<event>`, `de_total_<event>` | Strain-range components and total strain range from CASSY |
| `welding_f_<event>`, `dpa_<event>` | Welding factor and dpa used |
| `invalid_code_<event>`, `material_id_<event>` | Flags and governing material of this event |

`<event>` is the event `id`, for example `Vj_startup`. Open the file in the
[results viewer](viewer.md) or in ParaView.

For stress-based SDC-IC curves the strain fields are not applicable and may be empty
(NaN) even though `N` and `Vj` are valid.

## Invalid nodes

A node is *invalid* for an event when it cannot be assessed. Its `N`, `Vj` and
`U_total` are then NaN (shown magenta in the viewer), and the flags say why. Flags add
up: `3` means 1 + 2.

| Flag | Reason |
| --- | --- |
| 1 | Stress missing or not finite |
| 2 | Temperature missing or not finite |
| 16 | CASSY could not compute a positive `N`: typically a property outside its temperature range, or a strain or stress range above the fatigue curve |
| 32 | A material property needed by the code is missing |

If any event is invalid at a node, its `U_total` is NaN as well. An invalid event is
never left out of the sum, because that would understate the usage. Review invalid
nodes as part of the assessment: they often point to temperatures outside the material
data or to stress singularities.

## Nodes on a material interface

A node shared by two materials is assessed once per material (see
[Material assignment](materials.md#material-interfaces)). The file reports the material
with the **higher total usage**, and all per-event fields of that node come from the
same material, so the `Vj` values always add up to `U_total`. `material_id_U` tells you
which material that was. If one material is invalid at the node, the node is reported
invalid even if the other material has a finite result.

## `configuration.yaml`

The configuration exactly as it was used, with the file paths resolved. Keep it with the
results.

## `traceability.json`

Everything needed to audit or reproduce the run:

- versions of `cassy-fatigue`, CASSY and DPF, and hashes of the CASSY and
  `cassy-fatigue` source files;
- the material files used, with their hashes, and the material assignment (in
  named-selection mode: which assignment number is which selection);
- the units of each analysis and whether they came from the result or the
  configuration (`analysis_units`);
- the temperature source of each analysis (`temperature_sources`);
- non-solid elements that were skipped, per element type (`skipped_elements`);
- the DPF server used, the meaning of the invalid flags and the known limitations.

## Interpreting a run

A run that finishes has written its reports. Whether the component
passes is your judgement: check the maximum `U_total`, the invalid nodes, the material
assignment and the [limitations](limitations.md). Hotspots at sharp corners and other
stress singularities depend on the mesh. Judge them like any other peak stress.
