"""Editor pages. Each page edits ``window.project`` in place and calls ``window.changed()``.

``load()`` rebuilds a page from the project. Structural edits (rename, add, remove,
analysis change) schedule ``window.reload()`` on the event loop instead of rebuilding
the widget whose signal is being handled.
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import TYPE_CHECKING

import yaml
from cassy.auxiliary.custom_errors import ConfigError
from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QStandardItemModel
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from cassy_fatigue.cli import DEFAULT_DPF_IP, DEFAULT_DPF_PORT
from cassy_fatigue.config import UNIT_CHOICES, Config, ConfigurationError, parse_term
from cassy_fatigue.gui import model
from cassy_fatigue.gui.session import ServerSettings
from cassy_fatigue.kernel import load_materials
from cassy_fatigue.pipeline import run_config
from cassy_fatigue.progress import Progress, RunCancelled, Update, format_seconds

if TYPE_CHECKING:
    from cassy_fatigue.gui.app import MainWindow

IDENTIFIER = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]*")


def identifier(text: str, fallback: str) -> str:
    text = re.sub(r"[^A-Za-z0-9_.-]", "_", text.strip())
    return text if IDENTIFIER.fullmatch(text) else fallback


def make_table(headers: list[str]) -> QTableWidget:
    table = QTableWidget(0, len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
    table.verticalHeader().setVisible(False)
    table.setSelectionBehavior(QAbstractItemView.SelectRows)
    table.setSelectionMode(QAbstractItemView.SingleSelection)
    return table


def item(text, editable=False) -> QTableWidgetItem:
    cell = QTableWidgetItem(str(text))
    if not editable:
        cell.setFlags(cell.flags() & ~Qt.ItemIsEditable)
    return cell


def buttons(*specs) -> QHBoxLayout:
    row = QHBoxLayout()
    for text, slot in specs:
        button = QPushButton(text)
        button.clicked.connect(slot)
        row.addWidget(button)
    row.addStretch()
    return row


def spin(value: float, minimum: float, maximum: float, decimals: int, on_change) -> QDoubleSpinBox:
    box = QDoubleSpinBox()
    box.setRange(minimum, maximum)
    box.setDecimals(decimals)
    box.setValue(float(value))
    box.valueChanged.connect(on_change)
    return box


def choose(
    parent, title: str, label: str, options: list[str], checked: set[str]
) -> list[str] | None:
    """Checklist dialog; returns the checked options or None if cancelled."""
    dialog = QDialog(parent)
    dialog.setWindowTitle(title)
    layout = QVBoxLayout(dialog)
    layout.addWidget(QLabel(label))
    listing = QListWidget()
    for option in options:
        entry = QListWidgetItem(option)
        entry.setFlags(entry.flags() | Qt.ItemIsUserCheckable)
        entry.setCheckState(Qt.Checked if option in checked else Qt.Unchecked)
        listing.addItem(entry)
    layout.addWidget(listing)
    box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
    box.accepted.connect(dialog.accept)
    box.rejected.connect(dialog.reject)
    layout.addWidget(box)
    if dialog.exec() != QDialog.Accepted:
        return None
    return [
        listing.item(i).text()
        for i in range(listing.count())
        if listing.item(i).checkState() == Qt.Checked
    ]


class Page(QWidget):
    title = ""
    hint = ""

    def __init__(self, window: MainWindow):
        super().__init__()
        self.window = window
        self.loading = False
        self.layout_ = QVBoxLayout(self)
        heading = QLabel(f"<h3>{self.title}</h3>")
        self.layout_.addWidget(heading)
        if self.hint:
            hint = QLabel(self.hint)
            hint.setWordWrap(True)
            hint.setStyleSheet("color: palette(placeholder-text);")
            self.layout_.addWidget(hint)

    @property
    def project(self) -> dict:
        return self.window.project

    def edited(self):
        if not self.loading:
            self.window.changed()

    def restructure(self):
        """An edit that changes other pages' options: refresh everything after this event."""
        if not self.loading:
            self.window.changed()
            QTimer.singleShot(0, self.window.reload)

    def load(self):
        self.loading = True
        try:
            self.populate()
        finally:
            self.loading = False

    def populate(self):
        raise NotImplementedError


# --------------------------------------------------------------------------- general
class GeneralPage(Page):
    title = "General"
    hint = (
        "Design code and an optional extra CASSY material folder. "
        "Bundled materials and ./additional_materials are always available."
    )

    def __init__(self, window):
        super().__init__(window)
        form = QFormLayout()
        self.code = QComboBox()
        self.code.addItems(model.CODES)
        self.code.currentTextChanged.connect(self._code)
        self.library = QLineEdit(readOnly=True, placeholderText="(none)")
        library_row = QHBoxLayout()
        library_row.addWidget(self.library)
        library_row.addLayout(buttons(("Browse…", self._browse), ("Clear", self._clear)))
        form.addRow("Design code", self.code)
        form.addRow("Material library", library_row)
        self.layout_.addLayout(form)
        self.layout_.addStretch()

    def populate(self):
        self.code.setCurrentText(self.project["code"])
        self.library.setText(self.project.get("material_library") or "")

    def _code(self, code):
        if not self.loading:
            self.project["code"] = code
            self.restructure()

    def _browse(self):
        folder = QFileDialog.getExistingDirectory(
            self, "CASSY material folder", str(self.window.directory)
        )
        if folder:
            self.project["material_library"] = str(Path(folder).resolve())
            self.restructure()

    def _clear(self):
        self.project["material_library"] = None
        self.restructure()


# -------------------------------------------------------------------------- analyses
class AnalysesPage(Page):
    title = "Analyses"
    hint = (
        "Ansys result files. Names are used by loads; double-click a name to rename it. "
        "Metadata is read with DPF in the background. Units come from the result; declare "
        "them with Units… only for a result that records none (e.g. solved without /UNITS). "
        "Each .rst reads its own BFE temperatures; use Temperature… to read them from "
        "another analysis (e.g. a thermal .rth, matched by physical time)."
    )

    def __init__(self, window):
        super().__init__(window)
        self.table = make_table(["Name", "File", "Units", "Temperature", "Metadata"])
        self.table.itemChanged.connect(self._renamed)
        self.table.itemSelectionChanged.connect(self._details)
        self.layout_.addWidget(self.table, 2)
        self.layout_.addLayout(
            buttons(
                ("Add…", self._add),
                ("Remove", self._remove),
                ("Units…", self._units),
                ("Temperature…", self._temperature),
                ("Re-read metadata", self._reprobe),
            )
        )
        self.details = QPlainTextEdit(readOnly=True)
        self.layout_.addWidget(self.details, 1)

    def populate(self):
        self.table.setRowCount(0)
        for name, path in self.project["analyses"].items():
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setItem(row, 0, item(name, editable=True))
            self.table.setItem(row, 1, item(path))
            self.table.setItem(row, 2, item(model.units_text(self.project, name)))
            self.table.setItem(row, 3, item(model.temperature_text(self.project, name)))
            self.table.setItem(row, 4, item(self._summary(path, name)))
        self._details()

    def _summary(self, path: str, name: str) -> str:
        result = self.window.probes.get(path)
        if result is None:
            return "not read yet"
        if isinstance(result, str):
            return result
        warnings = (
            []
            if result.solid_only
            else [
                "NO SOLID ELEMENTS"
                if not result.n_solid_elements
                else f"{result.n_elements - result.n_solid_elements} non-solid elements ignored"
            ]
        )
        if result.kind == "rst" and not result.has_stress:
            warnings.append("no stress")
        if not result.length_unit and not (self.project.get("analysis_units") or {}).get(name):
            warnings.append("NO UNITS RECORDED: declare them with Units…")
        return ", ".join(
            [
                f"{result.kind} {result.analysis_type}",
                f"{len(result.sets)} sets",
                f"{result.n_nodes} nodes",
                f"mat {list(result.material_ids)}",
                f"temperature {'yes' if result.has_temperature else 'no'}",
                *warnings,
            ]
        )

    def _selected(self) -> str | None:
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        if not rows:
            return None
        cell = self.table.item(rows[0].row(), 0)
        return None if cell is None else cell.text()

    def _details(self):
        name = self._selected()
        info = None if name is None else self.window.info(name)
        if info is None:
            self.details.setPlainText(
                "Select a read analysis to see its result sets and named selections."
            )
            return
        recorded = info.unit_system if info.length_unit else f"none recorded ({info.unit_system!r})"
        lines = [
            str(info.path),
            f"Units: {recorded}",
            f"Declared units: {model.units_text(self.project, name)}",
            f"Temperature: {model.temperature_text(self.project, name)}",
            f"Mesh: {info.n_nodes} nodes, {info.n_elements} elements",
            *(
                []
                if info.solid_only
                else [
                    f"  {info.n_solid_elements} solid elements assessed; non-solid elements "
                    "ignored: "
                    + ", ".join(f"{name} {count}" for name, count in info.skipped_elements.items())
                ]
            ),
            f"Material IDs (mat, solid elements): {list(info.material_ids)}",
            "",
            "Result sets:",
        ]
        lines += [f"  {entry.label(info.time_unit)}" for entry in info.sets]
        lines += ["", "Named selections:"]
        lines += [
            f"  {entry.name} ({entry.location}, {entry.size})" for entry in info.named_selections
        ]
        self.details.setPlainText("\n".join(lines))

    def _add(self):
        names, _ = QFileDialog.getOpenFileNames(
            self, "Add result files", str(self.window.directory), "Ansys results (*.rst *.rth)"
        )
        for name in names:
            path = str(Path(name).resolve())
            key = model.unique_name(
                identifier(Path(name).stem, "analysis"), self.project["analyses"]
            )
            self.project["analyses"][key] = path
            self.window.probe(path)
        if names:
            self.restructure()

    def _remove(self):
        name = self._selected()
        if name is None:
            return
        users = [
            load
            for load, data in self.project["loads"].items()
            if name in model.load_analyses(data)
        ]
        if (
            users
            and QMessageBox.question(
                self,
                "Remove analysis",
                f"Loads {', '.join(users)} use {name!r} and will become invalid. Remove?",
            )
            != QMessageBox.Yes
        ):
            return
        model.remove_analysis(self.project, name)
        self.restructure()

    def _units(self):
        name = self._selected()
        if name is None:
            return
        dialog = UnitsDialog(self, name, (self.project.get("analysis_units") or {}).get(name, {}))
        if dialog.exec() == QDialog.Accepted:
            model.set_analysis_units(self.project, name, dialog.units())
            self.restructure()

    OWN_TEMPERATURE = "(own BFE temperatures)"

    def _temperature(self):
        name = self._selected()
        if name is None:
            return
        if not str(self.project["analyses"][name]).lower().endswith(".rst"):
            QMessageBox.information(
                self, "Temperature source", "Only a structural .rst takes a temperature source."
            )
            return
        choices = [self.OWN_TEMPERATURE]
        for other in self.project["analyses"]:
            if other != name:
                info = self.window.info(other)
                missing = info is not None and not info.has_temperature
                choices.append(other + ("  — no temperature result" if missing else ""))
        current = (self.project.get("analysis_temperature") or {}).get(name)
        index = next(
            (i for i, text in enumerate(choices) if current and text.split("  — ")[0] == current), 0
        )
        text, ok = QInputDialog.getItem(
            self,
            f"Temperature of {name}",
            "Read the temperatures of this analysis's sets from (another analysis is "
            "matched by physical time):",
            choices,
            index,
            False,
        )
        if ok:
            source = None if text == self.OWN_TEMPERATURE else text.split("  — ")[0]
            model.set_analysis_temperature(self.project, name, source)
            self.restructure()

    def _reprobe(self):
        name = self._selected()
        if name is not None:
            self.window.probe(self.project["analyses"][name], force=True)

    def _renamed(self, cell: QTableWidgetItem):
        if self.loading or cell.column() != 0:
            return
        old = list(self.project["analyses"])[cell.row()]
        new = cell.text().strip()
        try:
            if not IDENTIFIER.fullmatch(new):
                raise ConfigurationError("Use letters, digits, _, . and - only")
            model.rename_analysis(self.project, old, new)
        except ConfigurationError as error:
            QMessageBox.warning(self, "Cannot rename", str(error))
        self.restructure()


class UnitsDialog(QDialog):
    """Fallback units of one analysis, used only where the result records none."""

    FROM_RESULT = "(from result)"

    def __init__(self, parent, name: str, declared: dict[str, str]):
        super().__init__(parent)
        self.setWindowTitle(f"Units of {name}")
        layout = QVBoxLayout(self)
        note = QLabel(
            "Units recorded in the result file always apply. Declare a unit only "
            "if the result records none; a declaration that contradicts a "
            "recorded unit stops the run."
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        form = QFormLayout()
        self.boxes = {}
        for quantity, choices in UNIT_CHOICES.items():
            box = QComboBox()
            box.addItem(self.FROM_RESULT, None)
            for unit in choices:
                box.addItem(unit, unit)
            index = box.findData(declared.get(quantity))
            box.setCurrentIndex(max(index, 0))
            form.addRow(quantity.capitalize(), box)
            self.boxes[quantity] = box
        layout.addLayout(form)
        box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        layout.addWidget(box)

    def units(self) -> dict[str, str | None]:
        return {quantity: box.currentData() for quantity, box in self.boxes.items()}


# ------------------------------------------------------------------------- materials
class MaterialsPage(Page):
    title = "Materials"
    hint = (
        "Assign a CASSY material to every solver material ID, or to named selections "
        "that partition all elements. Greyed materials cannot be used with the selected "
        "code (hover for the reason)."
    )

    def __init__(self, window):
        super().__init__(window)
        mode = QHBoxLayout()
        self.by_id = QRadioButton("By solver material ID (mat)")
        self.by_selection = QRadioButton("By named selection")
        group = QButtonGroup(self)
        group.addButton(self.by_id)
        group.addButton(self.by_selection)
        self.by_id.setChecked(True)
        self.by_id.toggled.connect(self._mode)
        mode.addWidget(self.by_id)
        mode.addWidget(self.by_selection)
        mode.addStretch()
        self.layout_.addLayout(mode)
        self.selection_mode = False
        self.table = make_table(["Key", "CASSY material"])
        self.layout_.addWidget(self.table)
        self.layout_.addLayout(
            buttons(
                ("Fill from result…", self._fill), ("Add…", self._add), ("Remove", self._remove)
            )
        )
        note = QLabel(
            "In named-selection mode, output material IDs are the YAML order of the "
            "assignments (1, 2, …); the mapping is written to traceability.json."
        )
        note.setWordWrap(True)
        self.layout_.addWidget(note)

    def populate(self):
        if self.project["materials"]:
            self.selection_mode = model.materials_by_selection(self.project)
        (self.by_selection if self.selection_mode else self.by_id).setChecked(True)
        catalogue = self.window.materials()
        self.table.setRowCount(0)
        for key, name in self.project["materials"].items():
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setItem(row, 0, item(key))
            box = QComboBox()
            items = QStandardItemModel()
            box.setModel(items)
            for material, reason in catalogue:
                box.addItem(material)
                if reason:
                    entry = items.item(box.count() - 1)
                    entry.setEnabled(False)
                    entry.setToolTip(reason)
            if name not in [material for material, _ in catalogue]:
                box.addItem(f"{name}" if name else "")
                items.item(box.count() - 1).setToolTip("Not in the CASSY material catalogue")
            box.setCurrentText(name or "")
            box.currentTextChanged.connect(lambda text, key=key: self._assign(key, text))
            self.table.setCellWidget(row, 1, box)

    def _suggestions(self) -> list[str]:
        info = self.window.base_info()
        if info is None:
            return []
        if self.selection_mode:
            return [entry.name for entry in info.named_selections]
        return [str(value) for value in info.material_ids]

    def _key(self, text: str):
        text = text.strip()
        if not self.selection_mode:
            if not text.isdigit() or int(text) <= 0:
                raise ConfigurationError("Material IDs must be positive integers")
            return int(text)
        if not text or text.isdigit():
            raise ConfigurationError("Enter a (non-numeric) named selection")
        return text

    def _default_material(self) -> str:
        for material, reason in self.window.materials():
            if reason is None and material.endswith(self.project["code"]):
                return material
        return ""

    def _mode(self, by_id: bool):
        if self.loading or by_id != self.selection_mode:
            return
        if (
            self.project["materials"]
            and QMessageBox.question(
                self, "Change assignment mode", "This clears the current assignments. Continue?"
            )
            != QMessageBox.Yes
        ):
            self.loading = True
            (self.by_selection if self.selection_mode else self.by_id).setChecked(True)
            self.loading = False
            return
        self.selection_mode = not by_id
        self.project["materials"] = {}
        self.restructure()

    def _fill(self):
        options = [
            option
            for option in self._suggestions()
            if (int(option) if not self.selection_mode else option) not in self.project["materials"]
        ]
        if not options:
            QMessageBox.information(
                self,
                "Fill from result",
                "Nothing to add (read the analyses first, or every entry is already assigned).",
            )
            return
        info = self.window.base_info()
        elemental = {entry.name for entry in info.named_selections if entry.location == "Elemental"}
        checked = set(options) if not self.selection_mode else elemental & set(options)
        chosen = choose(self, "Fill from result", "Add assignments for:", options, checked)
        for option in chosen or []:
            self.project["materials"][self._key(option)] = self._default_material()
        if chosen:
            self.restructure()

    def _add(self):
        text, ok = QInputDialog.getItem(
            self,
            "Add assignment",
            "Named selection:" if self.selection_mode else "Material ID:",
            self._suggestions(),
            0,
            True,
        )
        if not ok:
            return
        try:
            key = self._key(text)
            if key in self.project["materials"]:
                raise ConfigurationError(f"{key!r} is already assigned")
        except ConfigurationError as error:
            QMessageBox.warning(self, "Cannot add", str(error))
            return
        self.project["materials"][key] = self._default_material()
        self.restructure()

    def _remove(self):
        row = self.table.currentRow()
        if row >= 0:
            self.project["materials"].pop(list(self.project["materials"])[row])
            self.restructure()

    def _assign(self, key, name):
        if not self.loading:
            self.project["materials"][key] = name
            self.edited()


# --------------------------------------------------------------------------- regions
class RegionsPage(Page):
    title = "Regions"
    hint = (
        "Optional named selections with a welding factor and dose (dpa). They must not "
        "overlap; unselected nodes use welding_f = 1 and dpa = 0."
    )

    def __init__(self, window):
        super().__init__(window)
        self.table = make_table(["Named selection", "welding_f", "dpa"])
        self.layout_.addWidget(self.table)
        self.layout_.addLayout(buttons(("Add", self._add), ("Remove", self._remove)))

    def populate(self):
        info = self.window.base_info()
        selections = [entry.name for entry in info.named_selections] if info else []
        self.table.setRowCount(0)
        for index, region in enumerate(self.project["regions"]):
            self.table.insertRow(index)
            box = QComboBox(editable=True)
            box.addItems(selections)
            box.setCurrentText(str(region.get("named_selection", "")))
            box.currentTextChanged.connect(
                lambda text, i=index: self._set(i, "named_selection", text)
            )
            self.table.setCellWidget(index, 0, box)
            self.table.setCellWidget(
                index,
                1,
                spin(
                    region.get("welding_f", 1),
                    1,
                    100,
                    3,
                    lambda value, i=index: self._set(i, "welding_f", value),
                ),
            )
            self.table.setCellWidget(
                index,
                2,
                spin(
                    region.get("dpa", 0),
                    0,
                    1e4,
                    4,
                    lambda value, i=index: self._set(i, "dpa", value),
                ),
            )

    def _set(self, index, key, value):
        if not self.loading:
            self.project["regions"][index][key] = value
            self.edited()

    def _add(self):
        info = self.window.base_info()
        used = {region.get("named_selection") for region in self.project["regions"]}
        free = (
            [entry.name for entry in info.named_selections if entry.name not in used]
            if info
            else []
        )
        self.project["regions"].append(
            {"named_selection": free[0] if free else "", "welding_f": 1.0, "dpa": 0.0}
        )
        self.restructure()

    def _remove(self):
        row = self.table.currentRow()
        if row >= 0:
            del self.project["regions"][row]
            self.restructure()


# ----------------------------------------------------------------------------- loads
class SetsDialog(QDialog):
    """Edit the signed result sets of one load: Δσ_load = scale × Σ sign × σ(set)."""

    def __init__(self, parent, window, analyses: list[str], sets: list[str]):
        super().__init__(parent)
        self.setWindowTitle("Edit load sets")
        self.window_ = window
        self.analyses = analyses
        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel(
                "Load = scale × Σ sign × σ(result set), e.g. +hot − cold for a heat-up range.\n"
                "'Result set' is the one-based DPF result-set ID, not the load step."
            )
        )
        self.table = make_table(["Sign", "Analysis", "Result set"])
        layout.addWidget(self.table)
        layout.addLayout(
            buttons(
                ("Add set", lambda: self._row("+", analyses[0] if analyses else "", 1)),
                ("Remove set", self._remove),
            )
        )
        box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        layout.addWidget(box)
        for text in sets:
            term = parse_term(text)
            if term is None:
                self._row("+", analyses[0] if analyses else "", 1)
            else:
                self._row(term.sign, term.analysis, term.step)
        self.resize(560, 340)

    def _row(self, sign: str, analysis: str, set_id: int):
        row = self.table.rowCount()
        self.table.insertRow(row)
        signs = QComboBox()
        signs.addItems(["+", "-"])
        signs.setCurrentText(sign)
        names = QComboBox()
        names.addItems(self.analyses)
        if analysis not in self.analyses:
            names.addItem(analysis)
        names.setCurrentText(analysis)
        names.currentTextChanged.connect(lambda text, b=names: self._analysis_changed(b, text))
        self.table.setCellWidget(row, 0, signs)
        self.table.setCellWidget(row, 1, names)
        self.table.setCellWidget(row, 2, self._set_widget(analysis, set_id))

    def _set_widget(self, analysis: str, set_id: int) -> QWidget:
        info = self.window_.info(analysis)
        if info is None:
            box = QSpinBox()
            box.setRange(1, 10**9)
            box.setValue(set_id if set_id > 0 else 1)
            return box
        box = QComboBox()
        for entry in info.sets:
            box.addItem(entry.label(info.time_unit), entry.set_id)
        if box.findData(set_id) < 0:
            box.addItem(info.set_label(set_id), set_id)
        box.setCurrentIndex(box.findData(set_id))
        return box

    def _analysis_changed(self, names: QComboBox, text: str):
        for row in range(self.table.rowCount()):
            if self.table.cellWidget(row, 1) is names:
                self.table.setCellWidget(row, 2, self._set_widget(text, self._set_id(row)))

    def _set_id(self, row: int) -> int:
        widget = self.table.cellWidget(row, 2)
        return int(widget.value() if isinstance(widget, QSpinBox) else widget.currentData())

    def _remove(self):
        if self.table.currentRow() >= 0:
            self.table.removeRow(self.table.currentRow())

    def sets(self) -> list[str]:
        return [
            f"{self.table.cellWidget(row, 0).currentText()}"
            f"{self.table.cellWidget(row, 1).currentText()}:{self._set_id(row)}"
            for row in range(self.table.rowCount())
        ]


def sets_text(sets) -> str:
    return " ".join(str(text) for text in sets or []) or "(no sets)"


class LoadsPage(Page):
    title = "Loads"
    hint = (
        "A load is a stress range: a signed combination of result sets of structural "
        ".rst analyses (× scale), classified as primary (P) or secondary (Q). "
        "Double-click the sets to edit them, or a name to rename it (events are updated)."
    )

    def __init__(self, window):
        super().__init__(window)
        self.table = make_table(["Name", "Class", "Sets", "Scale"])
        self.table.itemChanged.connect(self._renamed)
        self.table.cellDoubleClicked.connect(self._double_clicked)
        self.layout_.addWidget(self.table)
        self.layout_.addLayout(
            buttons(
                ("Add…", self._add),
                ("Add one per set…", self._add_all),
                ("Edit sets…", self._edit),
                ("Remove", self._remove),
            )
        )

    def _structural(self) -> list[str]:
        return [
            name
            for name, path in self.project["analyses"].items()
            if str(path).lower().endswith(".rst")
        ]

    def populate(self):
        self.table.setRowCount(0)
        for row, (name, load) in enumerate(self.project["loads"].items()):
            self.table.insertRow(row)
            self.table.setItem(row, 0, item(name, editable=True))
            kind = QComboBox()
            kind.addItems(model.CLASSIFICATIONS)
            kind.setCurrentText(str(load.get("classification", "P")))
            kind.currentTextChanged.connect(
                lambda text, n=name: self._set(n, "classification", text)
            )
            self.table.setCellWidget(row, 1, kind)
            self.table.setItem(row, 2, item(sets_text(load.get("sets"))))
            scale = load.get("scale", 1)
            self.table.setCellWidget(
                row,
                3,
                spin(
                    scale if isinstance(scale, (int, float)) else 1,
                    -1e9,
                    1e9,
                    4,
                    lambda value, n=name: self._set(n, "scale", value),
                ),
            )

    def _set(self, name, key, value):
        if self.loading:
            return
        load = self.project["loads"][name]
        if key == "scale" and value == 1:
            load.pop("scale", None)  # the default; keep the YAML short
        else:
            load[key] = int(value) if key == "scale" and float(value).is_integer() else value
        self.edited()

    def _new_load(self, name: str, sets: list[str]):
        self.project["loads"][name] = {"classification": "P", "sets": sets}

    def _add(self):
        analyses = self._structural()
        if not analyses:
            QMessageBox.information(self, "Add load", "Add a structural .rst analysis first.")
            return
        name, ok = QInputDialog.getText(
            self, "Add load", "Load name:", text=model.unique_name("load", self.project["loads"])
        )
        if not ok:
            return
        if not IDENTIFIER.fullmatch(name) or name in self.project["loads"]:
            QMessageBox.warning(self, "Cannot add", f"{name!r} is not a new identifier")
            return
        self._new_load(name, [f"+{analyses[0]}:1"])
        self.window.changed()
        self.load()
        self._edit(list(self.project["loads"]).index(name))

    def _add_all(self):
        analyses = [name for name in self._structural() if self.window.info(name)]
        if not analyses:
            QMessageBox.information(
                self, "Add one per set", "No structural analysis has been read yet."
            )
            return
        analysis, ok = QInputDialog.getItem(
            self, "Add one load per set", "Analysis:", analyses, 0, False
        )
        if not ok:
            return
        existing = {tuple(load.get("sets") or ()) for load in self.project["loads"].values()}
        for entry in self.window.info(analysis).sets:
            sets = [f"+{analysis}:{entry.set_id}"]
            if tuple(sets) not in existing:
                name = model.unique_name(f"{analysis}_s{entry.set_id}", self.project["loads"])
                self._new_load(name, sets)
        self.restructure()

    def _double_clicked(self, row, column):
        if column == 2:
            self._edit(row)

    def _edit(self, row=None):
        row = self.table.currentRow() if row is None or row is False else row
        if row < 0:
            return
        load = self.project["loads"][list(self.project["loads"])[row]]
        dialog = SetsDialog(self, self.window, self._structural(), list(load.get("sets") or []))
        if dialog.exec() == QDialog.Accepted:
            load["sets"] = dialog.sets()
            self.restructure()

    def _remove(self):
        row = self.table.currentRow()
        if row < 0:
            return
        name = list(self.project["loads"])[row]
        users = [
            event.get("id")
            for event in self.project["fatigue_events"]
            if name in event.get("loads", [])
        ]
        if (
            users
            and QMessageBox.question(
                self,
                "Remove load",
                f"Events {', '.join(map(str, users))} use {name!r}; it is "
                "removed from them. Continue?",
            )
            != QMessageBox.Yes
        ):
            return
        model.remove_load(self.project, name)
        self.restructure()

    def _renamed(self, cell: QTableWidgetItem):
        if self.loading or cell.column() != 0:
            return
        old = list(self.project["loads"])[cell.row()]
        new = cell.text().strip()
        try:
            if not IDENTIFIER.fullmatch(new):
                raise ConfigurationError("Use letters, digits, _, . and - only")
            model.rename_load(self.project, old, new)
        except ConfigurationError as error:
            QMessageBox.warning(self, "Cannot rename", str(error))
        self.restructure()


# ---------------------------------------------------------------------------- events
def loads_text(names) -> str:
    return " + ".join(str(name) for name in names or []) or "(no loads)"


class EventsPage(Page):
    title = "Fatigue events"
    hint = (
        "Each event is the sum of one or more loads, with its number of cycles: "
        "Δσ = Σ loads, Δσ_P = Σ P loads, temperature = maximum over all their sets; "
        "Vj = ncycles / N and U = Σ Vj at every node. Double-click the loads to choose "
        "them, or an ID to rename it."
    )

    def __init__(self, window):
        super().__init__(window)
        self.table = make_table(["ID", "ncycles", "Loads"])
        self.table.itemChanged.connect(self._renamed)
        self.table.cellDoubleClicked.connect(self._double_clicked)
        self.layout_.addWidget(self.table)
        self.layout_.addLayout(
            buttons(("Add…", self._add), ("Choose loads…", self._edit), ("Remove", self._remove))
        )

    def populate(self):
        self.table.setRowCount(0)
        for row, event in enumerate(self.project["fatigue_events"]):
            self.table.insertRow(row)
            self.table.setItem(row, 0, item(event.get("id", ""), editable=True))
            self.table.setCellWidget(
                row,
                1,
                spin(
                    event.get("ncycles", 1),
                    0,
                    1e15,
                    2,
                    lambda value, i=row: self._set(i, "ncycles", value),
                ),
            )
            self.table.setItem(row, 2, item(loads_text(event.get("loads"))))

    def _set(self, index, key, value):
        if not self.loading:
            event = self.project["fatigue_events"][index]
            event[key] = int(value) if key == "ncycles" and float(value).is_integer() else value
            self.edited()

    def _add(self):
        ids = [event.get("id") for event in self.project["fatigue_events"]]
        name, ok = QInputDialog.getText(
            self, "Add event", "Event ID:", text=model.unique_name("event", ids)
        )
        if not ok:
            return
        if not IDENTIFIER.fullmatch(name) or name in ids:
            QMessageBox.warning(self, "Cannot add", f"{name!r} is not a new identifier")
            return
        self.project["fatigue_events"].append({"id": name, "ncycles": 1, "loads": []})
        self.window.changed()
        self.load()
        self._edit(len(self.project["fatigue_events"]) - 1)

    def _double_clicked(self, row, column):
        if column == 2:
            self._edit(row)

    def _edit(self, row=None):
        row = self.table.currentRow() if row is None or row is False else row
        if row < 0:
            return
        event = self.project["fatigue_events"][row]
        current = [str(name) for name in event.get("loads", [])]
        options = list(self.project["loads"]) + [
            n for n in current if n not in self.project["loads"]
        ]
        chosen = choose(
            self,
            f"Loads of {event.get('id')}",
            "The event is the sum of the checked loads:",
            options,
            set(current),
        )
        if chosen is not None:
            event["loads"] = chosen
            self.restructure()

    def _remove(self):
        row = self.table.currentRow()
        if row >= 0:
            del self.project["fatigue_events"][row]
            self.restructure()

    def _renamed(self, cell: QTableWidgetItem):
        if self.loading or cell.column() != 0:
            return
        new = cell.text().strip()
        others = [
            event.get("id")
            for i, event in enumerate(self.project["fatigue_events"])
            if i != cell.row()
        ]
        if IDENTIFIER.fullmatch(new) and new not in others:
            self.project["fatigue_events"][cell.row()]["id"] = new
        else:
            QMessageBox.warning(self, "Cannot rename", f"{new!r} is not a new identifier")
        self.restructure()


# ------------------------------------------------------------------------------- run
class RunPage(Page):
    title = "Run"
    hint = (
        "Saves the configuration, then reads the results with DPF and writes "
        "fatigue.vtu, configuration.yaml and traceability.json. Exit status 'written' "
        "means the reports exist, not that the component passes."
    )

    def __init__(self, window):
        super().__init__(window)
        self.running = False
        form = QFormLayout()
        self.output = QLineEdit()
        out_row = QHBoxLayout()
        out_row.addWidget(self.output)
        out_row.addLayout(buttons(("Browse…", self._browse_output)))
        self.overwrite = QCheckBox("Overwrite existing reports")
        form.addRow("Output folder", out_row)
        form.addRow("", self.overwrite)
        self.layout_.addLayout(form)

        server = QGroupBox("DPF server")
        grid = QFormLayout(server)
        self.start_local = QRadioButton("Start a local server (shut down on close)")
        self.connect = QRadioButton("Connect to an existing server (left running)")
        self.start_local.setChecked(True)
        self.ansys = QLineEdit(placeholderText="found by DPF")
        ansys_row = QHBoxLayout()
        ansys_row.addWidget(self.ansys)
        ansys_row.addLayout(buttons(("Browse…", self._browse_ansys)))
        self.ip = QLineEdit(DEFAULT_DPF_IP)
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.port.setValue(DEFAULT_DPF_PORT)
        grid.addRow(self.start_local)
        grid.addRow("Ansys installation", ansys_row)
        grid.addRow(self.connect)
        grid.addRow("IP", self.ip)
        grid.addRow("Port", self.port)
        self.server_state = QLabel()
        grid.addRow("Status", self.server_state)
        for widget in (self.start_local, self.connect):
            widget.toggled.connect(self._settings_changed)
        self.ansys.editingFinished.connect(self._settings_changed)
        self.ip.editingFinished.connect(self._settings_changed)
        self.port.valueChanged.connect(self._settings_changed)
        self.layout_.addWidget(server)

        row = QHBoxLayout()
        self.run_button = QPushButton("Save and run")
        self.run_button.clicked.connect(self.run)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setToolTip(
            "Stop at the next step; a DPF read that has started "
            "finishes first. No reports are written."
        )
        self.cancel_button.clicked.connect(self.cancel)
        self.cancel_button.setEnabled(False)
        self.release = QPushButton("Release DPF server")
        self.release.clicked.connect(self._release)
        self.open_folder = QPushButton("Open output folder")
        self.open_folder.clicked.connect(self._open_folder)
        self.open_folder.setEnabled(False)
        self.show_results = QPushButton("Show results")
        self.show_results.clicked.connect(self._show_results)
        self.show_results.setEnabled(False)
        for widget in (
            self.run_button,
            self.cancel_button,
            self.release,
            self.open_folder,
            self.show_results,
        ):
            row.addWidget(widget)
        row.addStretch()
        self.layout_.addLayout(row)
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        self.step_label = QLabel()
        self.step_label.setWordWrap(True)
        self.layout_.addWidget(self.progress_bar)
        self.layout_.addWidget(self.step_label)
        self._progress: Progress | None = None
        self._update: Update | None = None
        self._started = 0.0
        # Elapsed time keeps counting during a long DPF read (no updates arrive then).
        self._clock = QTimer(self)
        self._clock.setInterval(1000)
        self._clock.timeout.connect(self._show_progress)
        window.progress_signal.connect(self._on_progress)
        self.result = QLabel()
        self.result.setWordWrap(True)
        self.result.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.layout_.addWidget(self.result)
        self.layout_.addStretch()
        self._last_output: Path | None = None
        self._last_vtu: Path | None = None
        self._settings_changed()

    def populate(self):
        self.update_state()

    def settings(self) -> ServerSettings:
        text = self.ansys.text().strip()
        return ServerSettings(
            connect=self.connect.isChecked(),
            ip=self.ip.text().strip() or DEFAULT_DPF_IP,
            port=self.port.value(),
            ansys_path=Path(text) if text else None,
        )

    def _settings_changed(self, *_):
        local = self.start_local.isChecked()
        self.ansys.setEnabled(local)
        self.ip.setEnabled(not local)
        self.port.setEnabled(not local)
        settings = self.settings()
        # Reconfigure on the DPF worker so a running probe is never cut off.
        self.window.submit(
            lambda: self.window.session.configure(settings), lambda future: self.update_state()
        )

    def update_state(self):
        session = self.window.session
        if session.active:
            self.server_state.setText(f"in use: {session.endpoint}")
        else:
            self.server_state.setText("not started (starts on first metadata read or run)")
        self.release.setEnabled(session.active and not self.running)
        valid = not isinstance(self.window.validation, str)
        self.run_button.setEnabled(valid and not self.running)
        self.cancel_button.setEnabled(
            self.running and self._progress is not None and not self._progress.cancelled
        )
        if self.window.path is not None:
            # Same rule as pipeline.default_output_dir, without parsing the configuration.
            self.output.setPlaceholderText(
                str(self.window.path.parent / f"{self.window.path.stem}_results")
            )
        else:
            self.output.setPlaceholderText("<configuration name>_results next to the YAML")

    def _browse_output(self):
        folder = QFileDialog.getExistingDirectory(self, "Output folder", str(self.window.directory))
        if folder:
            self.output.setText(folder)

    def _browse_ansys(self):
        folder = QFileDialog.getExistingDirectory(self, "Ansys installation")
        if folder:
            self.ansys.setText(folder)
            self._settings_changed()

    def _release(self):
        self.window.submit(self.window.session.close, lambda future: self.update_state())

    def _open_folder(self):
        if self._last_output is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._last_output)))

    def _show_results(self):
        if self._last_vtu is not None:
            self.window.show_results(self._last_vtu)

    def run(self):
        if self.running or not self.window.save():
            return
        path = self.window.path
        output = Path(self.output.text()).resolve() if self.output.text().strip() else None
        overwrite = self.overwrite.isChecked()
        session = self.window.session
        self.running = True
        self.result.setText("Running…")
        progress = self._progress = Progress(self.window.progress_signal.emit)
        self._update, self._started = None, time.perf_counter()
        self.progress_bar.setRange(0, 0)  # busy until the first step is planned
        self.progress_bar.setVisible(True)
        self.step_label.setText("Validating configuration and materials…")
        self._clock.start()
        self.update_state()

        def work():
            # Same order as the CLI: validate config and materials before any DPF use.
            config = Config.read(path)
            materials, sources = load_materials(config)
            progress.check()
            server = session.server()
            return run_config(
                config, materials, sources, server, session.endpoint, output, overwrite, progress
            )

        def finished(future):
            self.running = False
            self._clock.stop()
            self._progress = None
            self.progress_bar.setVisible(False)
            elapsed = format_seconds(time.perf_counter() - self._started)
            self.step_label.setText(f"Finished after {elapsed}.")
            try:
                paths = future.result()
            except RunCancelled as error:
                self.window.append_log(f"Cancelled: {error}")
                self.result.setText(f"<b>Run cancelled</b> after {elapsed}; no reports written.")
                self.step_label.setText("")
            except (ValueError, RuntimeError, OSError, ConfigError, yaml.YAMLError) as error:
                self.window.append_log(f"ERROR: {error}")
                self.result.setText(f"<b>Run failed</b> (no reports written): {error}")
            except Exception as error:  # noqa: BLE001 - report instead of crashing the GUI
                self.window.append_log(f"ERROR: unexpected {type(error).__name__}: {error}")
                self.result.setText(f"<b>Run failed</b> unexpectedly: {error}")
            else:
                for name, written in paths.items():
                    self.window.append_log(f"{name}: {written}")
                self._last_output = next(iter(paths.values())).parent if paths else None
                self.open_folder.setEnabled(self._last_output is not None)
                self._last_vtu = paths.get("vtu")
                self.show_results.setEnabled(self._last_vtu is not None)
                if self._last_vtu is not None:  # preload; the user switches tab when ready
                    self.window.results_panel.load(self._last_vtu)
                self.result.setText(
                    "<b>Reports written</b> (not an engineering pass):<br>"
                    + "<br>".join(str(p) for p in paths.values())
                )
            self.update_state()

        self.window.submit(work, finished)

    def cancel(self):
        if self._progress is not None:
            self._progress.cancel()
            self.window.append_log("Cancelling: the run stops after the current step.")
            self.update_state()
            self._show_progress()

    def _on_progress(self, update: Update):
        if self._progress is None:  # a late update after the run finished
            return
        self._update = update
        if update.total:
            self.progress_bar.setRange(0, update.total)
            self.progress_bar.setValue(update.done)
        self._show_progress()

    def _show_progress(self):
        if not self.running:
            return
        elapsed = time.perf_counter() - self._started
        update = self._update
        if update is None:
            text = "Validating configuration and materials…"
        else:
            state = "" if update.running else " (done)"
            current = update.done + 1 if update.running else update.done
            text = f"Step {current}/{update.total}: {update.message}{state}"
            remaining = update.remaining()
            if remaining is not None:
                # From the mean step time so far; mesh reads and stress reads differ a lot.
                text += f" — roughly {format_seconds(remaining)} left"
        text += f" — elapsed {format_seconds(elapsed)}"
        if self._progress is not None and self._progress.cancelled:
            text += " — cancelling after this step…"
        self.step_label.setText(text)
