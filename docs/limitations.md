# Scope and limitations

## What the tool does not do

- **Solid elements only.** Shell, beam, point/mass, contact/target and 2D elements are
  skipped: they are left out of the averaging and of `fatigue.vtu`, and counted in
  `traceability.json`. A node shared with a shell gets the value from the solid elements
  only. Shell and beam layers are not assessed.
- **No cycle counting.** Events are defined by you; there is no automatic cycle
  extraction or rainflow counting.
- **Fatigue only.** No stress linearization, no immediate or ratcheting rules, no bolt
  (Neuber) fatigue.
- **Conservative primary stress.** The nodal primary stress range includes bending and
  peak stress, which a linearized path would separate.
- **Volumetric loads only.** CASSY's inertial upper-limit equivalent stress is not
  applied.
- **Some fatigue curves are refused:** curves that need a mean stress or the special
  Inconel arguments, and stress-based curves with RCC-MR or RCC-MRx.
- **Singularities.** Hotspots at sharp corners and other singular points depend on the
  mesh, as with any peak stress evaluation.
