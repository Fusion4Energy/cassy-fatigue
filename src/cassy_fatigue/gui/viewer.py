"""Results tab: 3D view of ``fatigue.vtu`` with field/range/threshold/clip controls,
node picking, a hotspot table and reproducible screenshots.

The scene logic lives in the Qt-free :mod:`cassy_fatigue.gui.results`. The 3D widget is
a ``pyvistaqt.QtInteractor``; when it cannot be created (no OpenGL, Qt ``offscreen``
platform) the tab still works without it: tables, node reports and screenshots (rendered
off-screen) remain available.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
import pyvista as pv
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont, QGuiApplication
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from cassy_fatigue.gui import results as rs
from cassy_fatigue.gui.pages import item, make_table

if TYPE_CHECKING:
    from cassy_fatigue.gui.app import MainWindow

WHOLE_NODE = "(whole node)"
SLIDER_STEPS = 1000


def make_plotter(parent) -> tuple[object | None, QWidget]:
    """``(plotter, widget)``: a QtInteractor (which is its own widget), or ``None`` and a
    label saying why the 3D view is unavailable."""
    platform = QGuiApplication.platformName()
    reason = ""
    if platform in ("offscreen", "minimal"):
        reason = f"3D view unavailable on the Qt '{platform}' platform."
    else:
        try:
            from pyvistaqt import QtInteractor

            plotter = QtInteractor(parent)
        except Exception as error:  # noqa: BLE001 - any OpenGL/import failure: degrade
            reason = f"3D view unavailable: {error}"
        else:
            return plotter, plotter
    label = QLabel(reason + "\nTables, node reports and screenshots still work.")
    label.setAlignment(Qt.AlignCenter)
    label.setWordWrap(True)
    return None, label


def number(text: str, what: str = "value") -> float:
    try:
        value = float(text.strip())
    except ValueError:
        raise ValueError(f"{what}: enter a number (got {text.strip()!r})") from None
    if not math.isfinite(value):
        raise ValueError(f"{text!r} is not a finite number")
    return value


class ResultsPanel(QWidget):
    def __init__(self, window: MainWindow):
        super().__init__()
        self.window = window
        self.results: rs.ResultSet | None = None
        self.scene: rs.Scene | None = None
        self.marker: int | None = None
        self.hotspot_rows: list[int] = []
        self._updating = False
        self._camera_set = False
        self._timer = QTimer(self, singleShot=True, interval=200)
        self._timer.timeout.connect(self.refresh)

        # ------------------------------------------------------------- top bar
        self.open_button = QPushButton("Open results…")
        self.open_button.clicked.connect(self.open)
        self.reload_button = QPushButton("Reload")
        self.reload_button.clicked.connect(lambda: self.load(self.results.path))
        self.source = QLabel("No results loaded. Open a fatigue.vtu, or run an assessment.")
        self.source.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.source.setWordWrap(True)
        top = QHBoxLayout()
        top.addWidget(self.open_button)
        top.addWidget(self.reload_button)
        top.addWidget(self.source, 1)

        # ------------------------------------------------------------ controls
        controls = QWidget()
        form = QVBoxLayout(controls)
        field_box = QGroupBox("Field")
        field_form = QFormLayout(field_box)
        self.quantity = QComboBox()
        self.event = QComboBox()
        self.log = QCheckBox("Log scale")
        self.auto_range = QCheckBox("Automatic range (whole model)")
        self.auto_range.setChecked(True)
        self.range_min, self.range_max = QLineEdit(), QLineEdit()
        self.cmap = QComboBox()
        self.cmap.addItems(rs.COLORMAPS)
        field_form.addRow("Quantity", self.quantity)
        field_form.addRow("Event", self.event)
        field_form.addRow("", self.log)
        field_form.addRow("", self.auto_range)
        field_form.addRow("Min", self.range_min)
        field_form.addRow("Max", self.range_max)
        field_form.addRow("Colour map", self.cmap)
        form.addWidget(field_box)

        self.threshold = QGroupBox("Threshold (cells with any node in range)")
        self.threshold.setCheckable(True)
        self.threshold.setChecked(False)
        threshold_form = QFormLayout(self.threshold)
        self.threshold_min, self.threshold_max = QLineEdit(), QLineEdit()
        threshold_form.addRow("Min", self.threshold_min)
        threshold_form.addRow("Max", self.threshold_max)
        form.addWidget(self.threshold)

        self.clip = QGroupBox("Clip plane (capped)")
        self.clip.setCheckable(True)
        self.clip.setChecked(False)
        clip_form = QFormLayout(self.clip)
        self.clip_axis = QComboBox()
        self.clip_axis.addItems(["x", "y", "z"])
        self.clip_slider = QSlider(Qt.Horizontal)
        self.clip_slider.setRange(0, SLIDER_STEPS)
        self.clip_slider.setValue(SLIDER_STEPS // 2)
        self.clip_position = QLineEdit()
        self.clip_above = QCheckBox("Keep the side above the plane")
        self.clip_whole = QCheckBox("Whole cells (exact nodal values, jagged cut)")
        clip_form.addRow("Normal axis", self.clip_axis)
        clip_form.addRow("Position", self.clip_slider)
        clip_form.addRow("Coordinate [m]", self.clip_position)
        clip_form.addRow("", self.clip_above)
        clip_form.addRow("", self.clip_whole)
        form.addWidget(self.clip)

        display = QGroupBox("Display")
        display_form = QFormLayout(display)
        self.context = QCheckBox("Transparent context mesh when filtered")
        self.context.setChecked(True)
        self.opacity = QDoubleSpinBox(minimum=0.0, maximum=1.0, singleStep=0.05, value=0.15)
        self.edges = QCheckBox("Show element edges")
        self.image_w = QSpinBox(minimum=200, maximum=8000, value=1600)
        self.image_h = QSpinBox(minimum=200, maximum=8000, value=1000)
        size_row = QHBoxLayout()
        size_row.addWidget(self.image_w)
        size_row.addWidget(QLabel("×"))
        size_row.addWidget(self.image_h)
        display_form.addRow("", self.context)
        display_form.addRow("Context opacity", self.opacity)
        display_form.addRow("", self.edges)
        display_form.addRow("Screenshot size", size_row)
        form.addWidget(display)

        actions = QHBoxLayout()
        self.reset_button = QPushButton("Reset view")
        self.reset_button.clicked.connect(self.reset_camera)
        self.shot_button = QPushButton("Save screenshot…")
        self.shot_button.clicked.connect(self.screenshot)
        self.view_button = QPushButton("Load view…")
        self.view_button.clicked.connect(self.open_view)
        for widget in (self.reset_button, self.shot_button, self.view_button):
            actions.addWidget(widget)
        form.addLayout(actions)
        self.status = QLabel()
        self.status.setWordWrap(True)
        form.addWidget(self.status)
        form.addStretch()
        scroll = QScrollArea()
        scroll.setWidget(controls)
        scroll.setWidgetResizable(True)
        scroll.setMinimumWidth(290)

        # ---------------------------------------------------------------- view
        self.plotter, self.view = make_plotter(self)
        if self.plotter is not None:
            self.plotter.set_background("white")
            self.plotter.enable_point_picking(
                callback=self._picked, show_message=False, show_point=False, left_clicking=False
            )
            self.view.setToolTip("Right-click (or press P over the model) to pick a node.")

        # --------------------------------------------------- hotspots and nodes
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        hot_row = QHBoxLayout()
        self.mode = QComboBox()
        self.mode.addItem("Highest values", "highest")
        self.mode.addItem("Lowest values", "lowest")
        self.mode.addItem("Invalid nodes", "invalid")
        self.count = QSpinBox(minimum=1, maximum=100000, value=25)
        self.visible_only = QCheckBox("Displayed only")
        hot_row.addWidget(QLabel("Hotspots"))
        hot_row.addWidget(self.mode)
        hot_row.addWidget(self.count)
        hot_row.addWidget(self.visible_only)
        self.export_button = QPushButton("Export CSV…")
        self.export_button.clicked.connect(self.export)
        hot_row.addWidget(self.export_button)
        self.table: QTableWidget = make_table(
            ["#", "Node", "Value", "U_total", "Material (U)", "Invalid"]
        )
        self.table.cellClicked.connect(lambda row, _col: self.select(self.hotspot_rows[row]))
        find_row = QHBoxLayout()
        self.find = QLineEdit(placeholderText="node ID")
        self.find.returnPressed.connect(self._find)
        find_row.addWidget(QLabel("Find node"))
        find_row.addWidget(self.find)
        self.details = QPlainTextEdit(readOnly=True)
        self.details.setFont(QFont("monospace"))
        self.details.setPlaceholderText("Pick a node, click a hotspot or enter a node ID.")
        right_layout.addLayout(hot_row)
        right_layout.addWidget(self.table, 3)
        right_layout.addLayout(find_row)
        right_layout.addWidget(self.details, 2)

        splitter = QSplitter()
        splitter.addWidget(scroll)
        splitter.addWidget(self.view)
        splitter.addWidget(right)
        splitter.setStretchFactor(1, 3)
        splitter.setStretchFactor(2, 2)
        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(splitter, 1)

        self.quantity.currentIndexChanged.connect(self._quantity_changed)
        for signal in (
            self.event.currentIndexChanged,
            self.log.toggled,
            self.auto_range.toggled,
            self.cmap.currentIndexChanged,
            self.threshold.toggled,
            self.clip.toggled,
            self.clip_axis.currentIndexChanged,
            self.clip_above.toggled,
            self.clip_whole.toggled,
            self.context.toggled,
            self.opacity.valueChanged,
            self.edges.toggled,
            self.mode.currentIndexChanged,
            self.count.valueChanged,
            self.visible_only.toggled,
        ):
            signal.connect(self.schedule)
        for edit in (self.range_min, self.range_max, self.threshold_min, self.threshold_max):
            edit.editingFinished.connect(self.schedule)
        self.auto_range.toggled.connect(self._range_enabled)
        self.event.currentIndexChanged.connect(self._event_changed)
        self.clip_axis.currentIndexChanged.connect(self._slider_moved)
        self.clip_slider.valueChanged.connect(self._slider_moved)
        self.clip_position.editingFinished.connect(self._position_edited)
        self._set_enabled(False)

    # ---------------------------------------------------------------- loading
    def open(self):
        start = str(self.results.path.parent if self.results else self.window.directory)
        name, _ = QFileDialog.getOpenFileName(
            self, "Open results", start, "VTU results (*.vtu);;All files (*)"
        )
        if name:
            self.load(Path(name))

    def load(self, path: Path, on_loaded=None):
        """Read a VTU in the background; ``on_loaded`` runs on the GUI thread afterwards."""
        path = Path(path)
        self.source.setText(f"Loading {path}…")
        self.window.statusBar().showMessage(f"Loading {path.name}…")

        def finished(future):
            self.window.statusBar().clearMessage()
            try:
                loaded = future.result()
            except Exception as error:  # noqa: BLE001 - shown in the tab and the log
                self.source.setText(f"Cannot open {path}: {error}")
                self.window.append_log(f"ERROR: cannot open results {path}: {error}")
                return
            self.set_results(loaded)
            if on_loaded is not None:
                on_loaded()

        self.window.submit(lambda: rs.ResultSet.load(path), finished, io=True)

    def set_results(self, results: rs.ResultSet):
        same_model = self.results is not None and self.results.path == results.path
        previous = self.state() if same_model else None
        self.results, self.marker, self.scene = results, None, None
        if not same_model:
            self._camera_set = False
        self.source.setText(
            f"<b>{results.path}</b><br>{results.summary()}"
            + (
                ""
                if results.traceability
                else "<br>(no traceability.json next to it: generic flag names)"
            )
        )
        self.window.append_log(f"Loaded results {results.path}: {results.summary()}")
        self._updating = True
        try:
            self.quantity.clear()
            for quantity in results.quantities():
                self.quantity.addItem(rs.LABELS.get(quantity, quantity), quantity)
        finally:
            self._updating = False
        self._set_enabled(True)
        self._slider_moved()  # a clip coordinate for this model before the clip is enabled
        if previous is not None:
            try:
                self.apply_state(previous)
                return
            except ValueError:
                pass
        self.quantity.setCurrentIndex(0)
        self._quantity_changed()

    def _set_enabled(self, enabled: bool):
        for widget in (
            self.reload_button,
            self.quantity,
            self.event,
            self.log,
            self.auto_range,
            self.cmap,
            self.threshold,
            self.clip,
            self.reset_button,
            self.shot_button,
            self.export_button,
            self.find,
            self.mode,
            self.count,
            self.visible_only,
        ):
            widget.setEnabled(enabled)
        self.view_button.setEnabled(True)
        self._range_enabled()

    def _range_enabled(self, *_):
        manual = self.results is not None and not self.auto_range.isChecked()
        self.range_min.setEnabled(manual)
        self.range_max.setEnabled(manual)

    # ------------------------------------------------------------ widget state
    def _quantity_changed(self, *_):
        if self._updating or self.results is None:
            return
        quantity = self.quantity.currentData()
        self._updating = True
        try:
            self.event.clear()
            if self.results.has_global(quantity):
                self.event.addItem(WHOLE_NODE, None)
            if self.results.is_event_quantity(quantity):
                for event in self.results.events:
                    self.event.addItem(event, event)
            self.event.setEnabled(self.event.count() > 1)
            if self.event.count() == 0:
                self.event.addItem("—", None)
            categorical = quantity in rs.CATEGORICAL
            self.log.setEnabled(not categorical)
            self.log.setChecked(self.results.default_log(quantity) and not categorical)
            self.mode.setCurrentIndex(self.mode.findData(self.results.default_mode(quantity)))
            self.auto_range.setChecked(True)
        finally:
            self._updating = False
        self._event_changed()

    def _event_changed(self, *_):
        """Fill range/threshold boxes with the field's range as a starting point."""
        if self._updating or self.results is None:
            return
        name = self.field_name()
        full = self.results.finite_range(name)
        colour = self.results.finite_range(name, positive=self.log.isChecked()) or full
        self._updating = True
        try:
            if self.auto_range.isChecked():
                self.range_min.setText("" if colour is None else f"{colour[0]:.6g}")
                self.range_max.setText("" if colour is None else f"{colour[1]:.6g}")
            if full is not None:
                self.threshold_min.setText(f"{full[0]:.6g}")
                self.threshold_max.setText(f"{full[1]:.6g}")
        finally:
            self._updating = False
        self.schedule()

    def _slider_moved(self, *_):
        if self.results is None or self._updating:
            return
        lo, hi = self.results.axis_bounds(self.clip_axis.currentText())
        position = lo + (hi - lo) * self.clip_slider.value() / SLIDER_STEPS
        self._updating = True
        try:
            self.clip_position.setText(f"{position:.6g}")
        finally:
            self._updating = False
        self.schedule()

    def _position_edited(self):
        if self.results is None:
            return
        try:
            position = number(self.clip_position.text(), "Clip coordinate")
        except ValueError as error:
            self.status.setText(f"⚠ {error}")
            return
        lo, hi = self.results.axis_bounds(self.clip_axis.currentText())
        self._updating = True
        try:
            fraction = 0.5 if hi == lo else min(max((position - lo) / (hi - lo), 0.0), 1.0)
            self.clip_slider.setValue(round(fraction * SLIDER_STEPS))
        finally:
            self._updating = False
        self.schedule()

    def field_name(self) -> str:
        return self.results.field_name(self.quantity.currentData(), self.event.currentData())

    def state(self) -> rs.ViewState:
        """The view settings from the widgets; ValueError on unparsable numbers."""
        quantity = self.quantity.currentData()
        clim = None
        if not self.auto_range.isChecked():
            clim = (
                number(self.range_min.text(), "Colour min"),
                number(self.range_max.text(), "Colour max"),
            )
        threshold = None
        if self.threshold.isChecked():
            threshold = (
                number(self.threshold_min.text(), "Threshold min"),
                number(self.threshold_max.text(), "Threshold max"),
            )
        clip = None
        if self.clip.isChecked():
            clip = rs.ClipState(
                axis=self.clip_axis.currentText(),
                position=number(self.clip_position.text(), "Clip coordinate"),
                keep_above=self.clip_above.isChecked(),
                whole_cells=self.clip_whole.isChecked(),
            )
        log = self.log.isChecked() if self.log.isEnabled() else False
        return rs.ViewState(
            quantity=quantity,
            event=self.event.currentData(),
            log=log,
            clim=clim,
            cmap=self.cmap.currentText(),
            threshold=threshold,
            clip=clip,
            context=self.context.isChecked(),
            context_opacity=round(self.opacity.value(), 4),
            edges=self.edges.isChecked(),
        )

    def apply_state(self, state: rs.ViewState):
        """Set the widgets from a view state (e.g. a saved screenshot view)."""
        if self.results is None:
            raise ValueError("Load results first")
        index = self.quantity.findData(state.quantity)
        if index < 0:
            raise ValueError(f"No quantity {state.quantity!r} in these results")
        self.results.field_name(state.quantity, state.event)  # raises if missing
        self._updating = True
        try:
            self.quantity.setCurrentIndex(index)
        finally:
            self._updating = False
        self._quantity_changed()
        self._updating = True
        try:
            self.event.setCurrentIndex(max(self.event.findData(state.event), 0))
            if state.log is not None:
                self.log.setChecked(state.log)
            self.auto_range.setChecked(state.clim is None)
            if state.clim is not None:
                self.range_min.setText(repr(state.clim[0]))
                self.range_max.setText(repr(state.clim[1]))
            self.cmap.setCurrentText(state.cmap)
            self.threshold.setChecked(state.threshold is not None)
            if state.threshold is not None:
                self.threshold_min.setText(repr(state.threshold[0]))
                self.threshold_max.setText(repr(state.threshold[1]))
            self.clip.setChecked(state.clip is not None)
            if state.clip is not None:
                self.clip_axis.setCurrentText(state.clip.axis)
                self.clip_position.setText(repr(state.clip.position))
                self.clip_above.setChecked(state.clip.keep_above)
                self.clip_whole.setChecked(state.clip.whole_cells)
            self.context.setChecked(state.context)
            self.opacity.setValue(state.context_opacity)
            self.edges.setChecked(state.edges)
        finally:
            self._updating = False
        if state.clip is not None:
            self._position_edited()
        self._range_enabled()
        self.refresh()

    # --------------------------------------------------------------- drawing
    def schedule(self, *_):
        if not self._updating and self.results is not None:
            self._timer.start()

    def refresh(self):
        self._timer.stop()
        if self.results is None:
            return
        try:
            state = self.state()
            QGuiApplication.setOverrideCursor(Qt.WaitCursor)
            try:
                scene = self.results.build_scene(state)
            finally:
                QGuiApplication.restoreOverrideCursor()
        except ValueError as error:
            self.status.setText(f"⚠ {error}")
            self.status.setStyleSheet("color:#7a1712;")
            return
        self.scene = scene
        self.status.setStyleSheet("")
        shown = int(np.count_nonzero(scene.visible_nodes))
        self.status.setText(
            f"{scene.field}: colour range {scene.clim[0]:.4g} … "
            f"{scene.clim[1]:.4g} ({'log' if scene.log else 'linear'}); "
            f"{shown} of {self.results.grid.n_points} nodes displayed. "
            + (scene.message or "")
            + " Magenta: NaN (invalid)."
        )
        if self.plotter is not None:
            camera = self.plotter.camera_position if self._camera_set else None
            rs.draw(self.plotter, scene, state, self.marker, self.results)
            if camera is None:
                self.plotter.view_isometric()
                self.plotter.reset_camera()
                self._camera_set = True
            else:
                self.plotter.camera_position = camera
            self.plotter.render()
        self.fill_hotspots()

    def reset_camera(self):
        if self.plotter is not None:
            self.plotter.view_isometric()
            self.plotter.reset_camera()
            self.plotter.render()

    def fill_hotspots(self):
        if self.results is None or self.scene is None:
            return
        name = self.scene.field
        visible = self.scene.visible_nodes if self.visible_only.isChecked() else None
        self.hotspot_rows = self.results.hotspots(
            name, self.count.value(), self.mode.currentData(), visible
        )
        values, total = self.results.values(name), self.results.values("U_total")
        invalid = self.results.values("invalid_code")
        material = (
            self.results.values("material_id_U")
            if "material_id_U" in self.results.grid.point_data
            else None
        )
        self.table.setHorizontalHeaderItem(2, item(name))
        self.table.setRowCount(len(self.hotspot_rows))
        for row, index in enumerate(self.hotspot_rows):
            cells = (
                row + 1,
                self.results.node_id(index),
                rs.fmt(_scalar(values[index])),
                rs.fmt(float(total[index])),
                "" if material is None else self.results.material_label(material[index]),
                ", ".join(rs.decode_flags(invalid[index], self.results.flag_names())) or "",
            )
            for column, text in enumerate(cells):
                self.table.setItem(row, column, item(text))

    # --------------------------------------------------------------- picking
    def _picked(self, point, *_):
        if self.results is None or point is None:
            return
        visible = self.scene.visible_nodes if self.scene is not None else None
        index = self.results.nearest_node(point, visible)
        if index is not None:
            self.select(index)

    def _find(self):
        if self.results is None:
            return
        try:
            self.select(self.results.index_of_node(int(self.find.text().strip())))
        except ValueError as error:
            self.details.setPlainText(f"⚠ {error}")

    def select(self, index: int):
        self.marker = index
        self.details.setPlainText(self.results.format_report(index))
        if self.plotter is not None:
            radius = 0.01 * max(self.results.grid.length, 1e-12)
            self.plotter.add_mesh(
                pv.Sphere(radius=radius, center=self.results.grid.points[index]),
                color="red",
                name="marker",
                pickable=False,
            )
            self.plotter.render()

    # -------------------------------------------------------- export/reports
    def export(self):
        if self.results is None or self.scene is None:
            return
        name, _ = QFileDialog.getSaveFileName(
            self,
            "Export hotspots",
            str(self.results.path.with_name(f"hotspots_{self.scene.field}.csv")),
            "CSV (*.csv)",
        )
        if name:
            self.results.export_csv(self.hotspot_rows, self.scene.field, Path(name))
            self.window.append_log(f"Hotspots written to {name}")

    def screenshot(self, path: Path | None = None) -> Path | None:
        if self.results is None:
            return None
        if path is None:
            default = self.results.path.with_name(f"{self.field_name()}.png")
            name, _ = QFileDialog.getSaveFileName(
                self, "Save screenshot", str(default), "PNG (*.png)"
            )
            if not name:
                return None
            path = Path(name)
        try:
            state = self.state()
        except ValueError as error:
            QMessageBox.warning(self, "Cannot save screenshot", str(error))
            return None
        camera = (
            self.plotter.camera_position if self.plotter is not None and self._camera_set else None
        )
        written = rs.save_screenshot(
            self.results,
            state,
            camera,
            path,
            (self.image_w.value(), self.image_h.value()),
            self.marker,
        )
        self.window.append_log(f"Screenshot {written} (view settings: {rs.view_file(written)})")
        return written

    def open_view(self):
        start = str(self.results.path.parent if self.results else self.window.directory)
        name, _ = QFileDialog.getOpenFileName(
            self, "Load view", start, "Saved views (*.view.json);;All files (*)"
        )
        if name:
            self.load_view(Path(name))

    def load_view(self, path: Path):
        try:
            state, camera, data = rs.load_view(path)
        except (OSError, ValueError, TypeError) as error:
            QMessageBox.warning(self, "Cannot load view", str(error))
            return

        def apply():
            if data.get("source_sha256") and data["source_sha256"] != self.results.sha256:
                self.window.append_log(
                    f"WARNING: view {path.name} was saved on a different "
                    f"file ({data.get('source')}); applying it anyway"
                )
            try:
                self.apply_state(state)
            except ValueError as error:
                QMessageBox.warning(self, "Cannot apply view", str(error))
                return
            node = data.get("marker_node_id")
            if node is not None:
                try:
                    self.select(self.results.index_of_node(node))
                except ValueError:
                    pass
            if camera and self.plotter is not None:
                self.plotter.camera_position = [tuple(v) for v in camera]
                self._camera_set = True
                self.plotter.render()

        if self.results is None and data.get("source"):
            self.load(Path(data["source"]), on_loaded=apply)
        elif self.results is not None:
            apply()

    def close_view(self):
        if self.plotter is not None:
            self.plotter.close()


def _scalar(value):
    value = np.asarray(value)
    return int(value) if np.issubdtype(value.dtype, np.integer) else float(value)
