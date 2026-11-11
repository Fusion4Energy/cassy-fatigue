# Material assignment

The *Materials* page tells the tool which CASSY material applies to which elements.
There are two ways to do it; choose one at the top of the page. Switching clears the
current assignments.

## By solver material ID

Each row assigns a CASSY material to one material number (`mat`) of the solid elements
in the result file. *Fill from result…* adds a row for every number found in the file.
Every material number in the mesh needs a row, and numbers that do not occur in the mesh
are refused.

## By named selection

Useful when the solver material numbers do not match your fatigue materials, for
example when one solver material must be split into a base metal and a weld zone. Each
row assigns a CASSY material to a named selection of the first structural analysis.

- An **elemental** selection assigns its elements.
- A **nodal** selection assigns the elements whose nodes are *all* in the selection. A
  selection of surface nodes therefore assigns no element.
- Together, the selections must cover **every solid element exactly once**. Gaps and
  overlaps are errors. Neighbouring selections may share nodes on their boundary.
- A selection may span several solver material numbers or cover part of one.

In the results, material numbers are then **assignment numbers** 1, 2, … in the order
of the rows, not solver material numbers. The node report in the results viewer and
`traceability.json` (`material_assignment`) list which selection and CASSY material
each number stands for.

## CASSY materials

The dropdown lists CASSY's bundled materials and those of the material library chosen on
the *General* page. The material files used are hashed in `traceability.json`, so you
can tell later exactly which data a run used.

Some materials cannot be used for a nodal assessment. They are greyed out, with the
reason as a tooltip, and refused before any results are read:

- fatigue curves that need a mean stress or the special Inconel arguments;
- stress-based fatigue curves with RCC-MR or RCC-MRx (these codes need strain-based
  curves here).

## Material interfaces

Stresses and temperatures are averaged to the nodes **within each material**, never
across a material boundary. A node on the interface between two materials is therefore
assessed twice, once with each material's averaged stress and properties. The result
reports the material with the higher total usage. See
[Results](outputs.md#nodes-on-a-material-interface).
