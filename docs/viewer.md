# Results viewer

The **Results** tab of the GUI is a 3D viewer for `fatigue.vtu`. It opens automatically
after a run. To open earlier results, use *File → Open results…* (`Ctrl+R`), or open
only the viewer:

```bash
cassy-fatigue gui results/fatigue.vtu
```

If `traceability.json` is in the same folder, it is used for material names and flag
descriptions. The header shows the number of nodes, elements and events, the number of
invalid nodes, and the maximum `U_total` with its node.

## What is shown

*Quantity* selects the field: `U_total`, `invalid_code`, the governing material, or a
per-event field (`Vj`, `N`, stress ranges, temperature, strain ranges, dpa, welding
factor, flags), with *Event* selecting the event. See [Results](outputs.md) for their
meaning.

`U`, `Vj` and `N` use a logarithmic colour scale by default, all others a linear one.
The automatic range covers the whole model, so it stays the same while you threshold
or clip. Untick *Automatic range* to type your own limits. Invalid (NaN) nodes are
**magenta**.

## Threshold, clip and context

- **Threshold** keeps every element with at least one node whose value lies in the
  range you give. Invalid nodes never pass.
- **Clip plane** cuts the model normal to x, y or z at a position (slider or exact value
  in metres) and keeps one side. The cut face is closed and coloured. Values on the cut
  face are interpolated between nodes; tick *Whole cells* to keep cut elements whole and
  see only true nodal values.
- **Transparent context mesh** draws the full outline in grey while a threshold or clip
  is active, so you keep your bearings.

## Hotspots and node reports

The hotspot table lists the highest values of the shown field (the lowest for `N`, i.e.
the shortest life), or the invalid nodes. *Displayed only* restricts it to what is
currently visible. *Export CSV…* saves the table with full-precision values and
coordinates.

To inspect a node, click a row, type its ID in *Find node*, or right-click the model
(or press `P` over it). The node is marked red and its report lists coordinates,
`U_total`, flags, governing material and every event's values. Reports always show the
stored nodal values, never interpolated ones.

## Screenshots and saved views

*Save screenshot…* writes a PNG at a fixed size (default 1600 × 1000, white background)
and, next to it, a `.view.json` file with the view settings, camera and selected node.
*Load view…* restores such a view later, for example to produce the same picture after a
re-run. If the results file has changed since the view was saved, the log says so.

## Large models

On a 1-million-node model with 10 events, loading takes a few seconds, and thresholds
and clips about half a second. Without OpenGL (for example a remote session without a
display) the 3D view is replaced by a placeholder; tables, node reports and screenshots
still work.

`fatigue.vtu` also opens in ParaView.
