"""Qt-free results-viewer logic on ``fatigue.vtu``: fields, view state, scene, hotspots.

Everything here works on PyVista objects and NumPy arrays so it is testable with an
off-screen :class:`pyvista.Plotter`. Filtering uses VTK's C++ filters (threshold, clip,
surface extraction); node-level queries (hotspots, picking, reports) always read the
original nodal values, never values interpolated on cut faces.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np
import pyvista as pv

from cassy_fatigue.kernel import InvalidCode

#: Whole-node fields (not per event), in menu order.
GLOBAL_FIELDS = ("U_total", "invalid_code", "material_id_U")
#: Per-event fields ``<base>_<event id>``, in menu order.
EVENT_FIELDS = (
    "Vj",
    "N",
    "delta_sigma_eq_Pa",
    "delta_primary_eq_Pa",
    "T_degC",
    "de_total",
    "de1",
    "de2",
    "de3",
    "de4",
    "dpa",
    "welding_f",
    "invalid_code",
    "material_id",
)
LABELS = {
    "U_total": "U total (sum of Vj)",
    "invalid_code": "Invalid code (bitmask)",
    "material_id_U": "Material governing U",
    "Vj": "Vj (usage fraction)",
    "N": "N (allowable cycles)",
    "delta_sigma_eq_Pa": "Δσ eq. total [Pa]",
    "delta_primary_eq_Pa": "Δσ eq. primary [Pa]",
    "T_degC": "Temperature [degC]",
    "de_total": "Δε total",
    "de1": "Δε1",
    "de2": "Δε2",
    "de3": "Δε3",
    "de4": "Δε4",
    "dpa": "dpa",
    "welding_f": "Welding factor",
    "material_id": "Material governing the event",
}
#: Fields shown on a log colour scale by default.
LOG_DEFAULT = {"U_total", "Vj", "N"}
#: Fields whose hotspots are the lowest values (shortest life).
LOW_IS_CRITICAL = {"N"}
#: Fields that are integer categories (no log scale, exact values in reports).
CATEGORICAL = {"invalid_code", "material_id_U", "material_id"}
#: Arrays that are bookkeeping, not results.
HIDDEN = {"node_id", "vtkOriginalPointIds", "vtkOriginalCellIds", "_node_index", "_display"}
AXES = {"x": (1.0, 0.0, 0.0), "y": (0.0, 1.0, 0.0), "z": (0.0, 0.0, 1.0)}
COLORMAPS = ("viridis", "plasma", "inferno", "turbo", "jet", "coolwarm", "Reds")
NAN_COLOR = "magenta"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def decode_flags(code: int, names: dict[str, int] | None = None) -> list[str]:
    """Names of the bits set in an invalid code (from traceability.json when given)."""
    names = names or {name: int(flag) for name, flag in InvalidCode.__members__.items()}
    code = int(code)
    found = [name for name, bit in names.items() if bit and code & bit == bit]
    rest = code & ~sum(bit for bit in names.values() if bit)
    if rest:
        found.append(f"UNKNOWN_{rest}")
    return found


@dataclass(frozen=True)
class ClipState:
    axis: str = "x"
    position: float = 0.0  # absolute coordinate in metres along ``axis``
    keep_above: bool = False  # keep the side with coordinate >= position
    whole_cells: bool = False  # crinkle clip: exact nodal values, jagged cut

    def normal(self) -> tuple[float, float, float]:
        return AXES[self.axis]


@dataclass(frozen=True)
class ViewState:
    """Everything that defines a picture except the camera (saved next to screenshots)."""

    quantity: str = "U_total"
    event: str | None = None
    log: bool | None = None  # None: field default
    clim: tuple[float, float] | None = None  # None: automatic (whole model)
    cmap: str = "viridis"
    threshold: tuple[float, float] | None = None
    clip: ClipState | None = None
    context: bool = True
    context_opacity: float = 0.15
    edges: bool = False

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> ViewState:
        known = {f for f in cls.__dataclass_fields__}
        unknown = set(data) - known
        if unknown:
            raise ValueError(f"Unknown view settings: {', '.join(sorted(unknown))}")
        data = dict(data)
        for key in ("clim", "threshold"):
            if data.get(key) is not None:
                lo, hi = (float(v) for v in data[key])
                data[key] = (lo, hi)
        if data.get("clip") is not None:
            data["clip"] = ClipState(**data["clip"])
        return cls(**data)


@dataclass
class Scene:
    """Meshes and options to draw one picture."""

    field: str
    label: str
    surface: pv.PolyData  # coloured result surface (clipped/thresholded)
    context: pv.PolyData | None  # transparent full outer surface
    clim: tuple[float, float]
    log: bool
    visible_nodes: np.ndarray  # bool per original node: in a displayed cell
    message: str = ""


@dataclass
class ResultSet:
    """A loaded ``fatigue.vtu`` with its optional ``traceability.json``."""

    path: Path
    grid: pv.UnstructuredGrid
    events: list[str]
    traceability: dict = field(default_factory=dict)
    sha256: str = ""
    _surface: pv.PolyData | None = None
    _ranges: dict = field(default_factory=dict)

    # ------------------------------------------------------------------ loading
    @classmethod
    def load(cls, path: Path) -> ResultSet:
        path = Path(path).resolve()
        grid = pv.read(path)
        if not isinstance(grid, pv.UnstructuredGrid) or "U_total" not in grid.point_data:
            raise ValueError(f"{path} is not a cassy-fatigue result (no U_total point field)")
        if grid.n_points == 0 or grid.n_cells == 0:
            raise ValueError(f"{path} has an empty mesh")
        grid.point_data["_node_index"] = np.arange(grid.n_points, dtype=np.int64)
        events = sorted(
            {name[3:] for name in grid.point_data if name.startswith("Vj_")},
            key=lambda e: list(grid.point_data).index(f"Vj_{e}"),
        )
        trace = {}
        sidecar = path.with_name("traceability.json")
        if sidecar.is_file():
            try:
                trace = json.loads(sidecar.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                trace = {}
        return cls(path=path, grid=grid, events=events, traceability=trace, sha256=_sha256(path))

    # ------------------------------------------------------------------- fields
    def quantities(self) -> list[str]:
        """Menu entries: global fields, per-event bases, then any unknown point arrays."""
        names = set(self.grid.point_data)
        found = [name for name in GLOBAL_FIELDS if name in names]
        found += [
            base
            for base in EVENT_FIELDS
            if any(f"{base}_{event}" in names for event in self.events)
        ]
        claimed = set(found) | {f"{base}_{event}" for base in EVENT_FIELDS for event in self.events}
        found += [
            name for name in self.grid.point_data if name not in claimed and name not in HIDDEN
        ]
        return list(dict.fromkeys(found))

    def is_event_quantity(self, quantity: str) -> bool:
        return quantity in EVENT_FIELDS and any(
            f"{quantity}_{event}" in self.grid.point_data for event in self.events
        )

    def has_global(self, quantity: str) -> bool:
        """True if the quantity also exists for the whole node (``invalid_code``)."""
        return quantity in GLOBAL_FIELDS and quantity in self.grid.point_data

    def field_name(self, quantity: str, event: str | None = None) -> str:
        """Array name; ``event=None`` means the whole-node field when one exists."""
        if event is None and self.has_global(quantity):
            return quantity
        if self.is_event_quantity(quantity):
            if event is None:
                event = self.events[0]
            name = f"{quantity}_{event}"
        else:
            name = quantity
        if name not in self.grid.point_data:
            raise ValueError(f"No field {name!r} in {self.path.name}")
        return name

    def values(self, name: str) -> np.ndarray:
        return np.asarray(self.grid.point_data[name])

    def finite_range(self, name: str, positive: bool = False) -> tuple[float, float] | None:
        key = (name, positive)
        if key not in self._ranges:
            values = self.values(name).astype(float, copy=False)
            mask = np.isfinite(values)
            if positive:
                mask &= values > 0
            self._ranges[key] = (
                (float(values[mask].min()), float(values[mask].max())) if mask.any() else None
            )
        return self._ranges[key]

    def flag_names(self) -> dict[str, int] | None:
        codes = self.traceability.get("invalid_codes")
        return {str(k): int(v) for k, v in codes.items()} if isinstance(codes, dict) else None

    def material_label(self, material_id: int) -> str:
        if int(material_id) < 0:
            return "none (invalid)"
        regions = self.traceability.get("material_assignment", {}).get("regions", {})
        entry = regions.get(str(int(material_id)))
        if not entry:
            return str(int(material_id))
        selection = entry.get("named_selection")
        detail = entry.get("material", "?") + (f", {selection}" if selection else "")
        return f"{int(material_id)} ({detail})"

    @property
    def surface(self) -> pv.PolyData:
        if self._surface is None:
            self._surface = self.grid.extract_surface(algorithm="dataset_surface")
        return self._surface

    def summary(self) -> str:
        total = self.values("U_total")
        invalid = np.count_nonzero(self.values("invalid_code"))
        text = (
            f"{self.grid.n_points} nodes, {self.grid.n_cells} cells, "
            f"{len(self.events)} event(s), {invalid} invalid node(s)"
        )
        if np.isfinite(total).any():
            index = int(np.nanargmax(total))
            text += f"; max U_total = {total[index]:.4g} at node {self.node_id(index)}"
        return text

    def node_id(self, index: int) -> int:
        if "node_id" in self.grid.point_data:
            return int(self.grid.point_data["node_id"][index])
        return int(index)

    def index_of_node(self, node_id: int) -> int:
        if "node_id" not in self.grid.point_data:
            return int(node_id)
        found = np.flatnonzero(self.grid.point_data["node_id"] == int(node_id))
        if not len(found):
            raise ValueError(f"No node {node_id}")
        return int(found[0])

    # -------------------------------------------------------------------- scene
    def default_log(self, quantity: str) -> bool:
        return quantity in LOG_DEFAULT

    def build_scene(self, state: ViewState) -> Scene:
        name = self.field_name(state.quantity, state.event)
        log = self.default_log(state.quantity) if state.log is None else bool(state.log)
        if state.quantity in CATEGORICAL:
            log = False
        clim = state.clim or self.finite_range(name, positive=log)
        if clim is None and log:  # nothing positive: fall back to linear
            log, clim = False, self.finite_range(name)
        message = ""
        if clim is None:
            clim, message = (0.0, 1.0), f"{name} has no finite values (all nodes invalid)"
        lo, hi = clim
        if log and lo <= 0:
            raise ValueError("A log colour range needs a positive minimum")
        if hi < lo:
            raise ValueError("Colour range maximum is below the minimum")
        if hi == lo:
            hi = lo * (1 + 1e-9) + (1e-30 if lo == 0 else 0.0)

        volume = self.grid
        visible = np.ones(self.grid.n_points, dtype=bool)
        filtered = state.threshold is not None or state.clip is not None
        if state.threshold is not None:
            t_lo, t_hi = state.threshold
            if t_hi < t_lo:
                raise ValueError("Threshold maximum is below the minimum")
            # A cell is kept if any of its nodes is in range; NaN never passes.
            volume = volume.threshold(value=(t_lo, t_hi), scalars=name, preference="point")
            visible = np.zeros(self.grid.n_points, dtype=bool)
            visible[np.asarray(volume.point_data["_node_index"])] = True
        if state.clip is not None and volume.n_cells:
            clip = state.clip
            volume = volume.clip(
                normal=clip.normal(),
                origin=self._plane_origin(clip),
                invert=not clip.keep_above,
                crinkle=clip.whole_cells,
            )
            if clip.whole_cells:
                kept = np.zeros(self.grid.n_points, dtype=bool)
                if volume.n_points:
                    kept[np.asarray(volume.point_data["_node_index"])] = True
            else:
                coordinate = self.grid.points[:, "xyz".index(clip.axis)]
                tolerance = 1e-9 * max(self.grid.length, 1.0)
                kept = (
                    (coordinate >= clip.position - tolerance)
                    if clip.keep_above
                    else (coordinate <= clip.position + tolerance)
                )
            visible &= kept
        if not filtered:
            surface = self.surface.copy(deep=False)  # cached outer surface; own _display array
        elif volume.n_cells:
            # Volume clip + surface extraction gives a capped cut face for solids.
            surface = volume.extract_surface(algorithm="dataset_surface")
        else:
            surface = pv.PolyData()
            visible[:] = False
            message = message or "No cells pass the threshold/clip"
        if surface.n_points:
            values = np.asarray(surface.point_data[name], dtype=float)
            if log:  # VTK's log lookup table cannot map non-positive values
                values = np.where(np.isfinite(values) & (values <= 0), lo, values)
            surface.point_data["_display"] = values
        context = self.surface if filtered and state.context else None
        label = LABELS.get(state.quantity, state.quantity)
        if name != state.quantity and name != "invalid_code":
            label += f" — {name[len(state.quantity) + 1 :]}"
        return Scene(
            field=name,
            label=label,
            surface=surface,
            context=context,
            clim=(lo, hi),
            log=log,
            visible_nodes=visible,
            message=message,
        )

    def _plane_origin(self, clip: ClipState) -> tuple[float, float, float]:
        origin = list(self.grid.center)
        origin["xyz".index(clip.axis)] = clip.position
        return tuple(origin)

    def axis_bounds(self, axis: str) -> tuple[float, float]:
        i = "xyz".index(axis)
        bounds = self.grid.bounds
        return float(bounds[2 * i]), float(bounds[2 * i + 1])

    # ------------------------------------------------------------- node queries
    def nearest_node(self, point, visible: np.ndarray | None = None) -> int | None:
        """Index of the original node closest to ``point`` among the visible ones."""
        candidates = np.flatnonzero(visible) if visible is not None else None
        points = self.grid.points if candidates is None else self.grid.points[candidates]
        if not len(points):
            return None
        nearest = int(np.argmin(np.sum((points - np.asarray(point, float)) ** 2, axis=1)))
        return nearest if candidates is None else int(candidates[nearest])

    @staticmethod
    def default_mode(quantity: str) -> str:
        return "lowest" if quantity in LOW_IS_CRITICAL else "highest"

    def hotspots(
        self, name: str, count: int, mode: str = "highest", visible: np.ndarray | None = None
    ) -> list[int]:
        """Node indices: ``highest``/``lowest`` finite values, or ``invalid`` nodes."""
        if mode not in ("highest", "lowest", "invalid"):
            raise ValueError(f"Unknown hotspot mode {mode!r}")
        candidates = np.arange(self.grid.n_points) if visible is None else np.flatnonzero(visible)
        if mode == "invalid":
            code = self._invalid_field_for(name)
            bad = candidates[self.values(code)[candidates] != 0]
            return [int(i) for i in bad[:count]]
        values = self.values(name).astype(float, copy=False)[candidates]
        finite = np.isfinite(values)
        candidates, values = candidates[finite], values[finite]
        if not len(values) or count <= 0:
            return []
        keys = -values if mode == "highest" else values
        count = min(count, len(values))
        top = np.argpartition(keys, count - 1)[:count]
        order = top[np.lexsort((candidates[top], keys[top]))]  # ties: lower node index first
        return [int(i) for i in candidates[order]]

    def _invalid_field_for(self, name: str) -> str:
        for event in self.events:
            if name.endswith(f"_{event}") and f"invalid_code_{event}" in self.grid.point_data:
                return f"invalid_code_{event}"
        return "invalid_code"

    def node_report(self, index: int) -> dict:
        data = self.grid.point_data

        def value(key):
            raw = data[key][index]
            return (
                int(raw) if np.issubdtype(np.asarray(data[key]).dtype, np.integer) else float(raw)
            )

        names = self.flag_names()
        report = {
            "node_id": self.node_id(index),
            "coordinates_m": [float(c) for c in self.grid.points[index]],
            "U_total": value("U_total"),
            "invalid_code": value("invalid_code"),
            "invalid_flags": decode_flags(value("invalid_code"), names),
            "events": {},
        }
        if "material_id_U" in data:
            report["material_U"] = self.material_label(value("material_id_U"))
        for event in self.events:
            entry = {}
            for base in EVENT_FIELDS:
                key = f"{base}_{event}"
                if key in data:
                    entry[base] = value(key)
            if "invalid_code" in entry:
                entry["invalid_flags"] = decode_flags(entry["invalid_code"], names)
            if "material_id" in entry:
                entry["material"] = self.material_label(entry["material_id"])
            report["events"][event] = entry
        return report

    def format_report(self, index: int) -> str:
        report = self.node_report(index)
        x, y, z = report["coordinates_m"]
        lines = [
            f"Node {report['node_id']}  at ({x:.6g}, {y:.6g}, {z:.6g}) m",
            f"U_total = {fmt(report['U_total'])}",
        ]
        if report["invalid_code"]:
            lines.append(f"INVALID {report['invalid_code']}: {', '.join(report['invalid_flags'])}")
        if "material_U" in report:
            lines.append(f"Governing material: {report['material_U']}")
        for event, entry in report["events"].items():
            lines.append("")
            lines.append(f"Event {event}")
            for base, val in entry.items():
                if base in ("invalid_flags", "material_id"):
                    continue
                if base == "invalid_code":
                    if val:
                        lines.append(f"  invalid: {val} ({', '.join(entry['invalid_flags'])})")
                    continue
                lines.append(
                    f"  {LABELS.get(base, base)}: {val if isinstance(val, str) else fmt(val)}"
                )
        return "\n".join(lines)

    def export_csv(self, indices: list[int], name: str, path: Path) -> None:
        with open(path, "w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(
                [
                    "rank",
                    "node_id",
                    name,
                    "U_total",
                    "invalid_code",
                    "material_id_U",
                    "x_m",
                    "y_m",
                    "z_m",
                ]
            )
            for rank, index in enumerate(indices, 1):
                material = (
                    int(self.values("material_id_U")[index])
                    if "material_id_U" in self.grid.point_data
                    else ""
                )
                writer.writerow(
                    [
                        rank,
                        self.node_id(index),
                        repr(float(self.values(name)[index])),
                        repr(float(self.values("U_total")[index])),
                        int(self.values("invalid_code")[index]),
                        material,
                        *(repr(float(c)) for c in self.grid.points[index]),
                    ]
                )


def fmt(value) -> str:
    if isinstance(value, int):
        return str(value)
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "NaN"
    return f"{value:.6g}"


# --------------------------------------------------------------------- drawing
def draw(
    plotter,
    scene: Scene,
    state: ViewState,
    marker: int | None = None,
    results: ResultSet | None = None,
) -> None:
    """Replace the plotter's actors with ``scene``; keeps the camera."""
    plotter.set_background("white")
    plotter.clear_actors()
    for title in list(plotter.scalar_bars.keys()):
        plotter.remove_scalar_bar(title)
    if scene.context is not None:
        plotter.add_mesh(
            scene.context,
            color="lightgrey",
            opacity=state.context_opacity,
            name="context",
            pickable=False,
            show_scalar_bar=False,
        )
    if scene.surface.n_points:
        plotter.add_mesh(
            scene.surface,
            scalars="_display",
            clim=scene.clim,
            log_scale=scene.log,
            cmap=state.cmap,
            nan_color=NAN_COLOR,
            show_edges=state.edges,
            name="result",
            scalar_bar_args={
                "title": scene.label,
                "fmt": "%.3g",
                "vertical": False,
                "position_x": 0.2,
                "position_y": 0.03,
                "width": 0.6,
                "color": "black",
            },
            copy_mesh=False,
        )
    if marker is not None and results is not None:
        radius = 0.01 * max(results.grid.length, 1e-12)
        plotter.add_mesh(
            pv.Sphere(radius=radius, center=results.grid.points[marker]),
            color="red",
            name="marker",
            pickable=False,
        )
    if scene.message:
        plotter.add_text(
            scene.message, position="upper_left", font_size=10, color="black", name="message"
        )


def save_screenshot(
    results: ResultSet,
    state: ViewState,
    camera,
    path: Path,
    size: tuple[int, int] = (1600, 1000),
    marker: int | None = None,
) -> Path:
    """Render off-screen at a fixed size and write ``<png>.json`` with the view settings."""
    path = Path(path)
    if path.suffix.lower() != ".png":
        path = path.with_suffix(".png")
    scene = results.build_scene(state)
    plotter = pv.Plotter(off_screen=True, window_size=list(size))
    try:
        draw(plotter, scene, state, marker, results)
        if camera is None:
            plotter.view_isometric()
        else:
            plotter.camera_position = [tuple(map(float, v)) for v in camera]
        plotter.screenshot(str(path))
        camera_used = [list(map(float, v)) for v in plotter.camera_position]
    finally:
        plotter.close()
    view_file(path).write_text(
        json.dumps(
            {
                "source": str(results.path),
                "source_sha256": results.sha256,
                "image_size": list(size),
                "camera": camera_used,
                "marker_node_id": None if marker is None else results.node_id(marker),
                "view": state.to_dict(),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return path


def view_file(png: Path) -> Path:
    return Path(png).with_suffix(".view.json")


def load_view(path: Path) -> tuple[ViewState, list | None, dict]:
    """View settings, camera and the raw sidecar from a ``.view.json`` file."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if "view" not in data:
        raise ValueError(f"{path} is not a saved view (no 'view' entry)")
    return ViewState.from_dict(data["view"]), data.get("camera"), data
