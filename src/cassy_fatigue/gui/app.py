"""PySide6 configuration editor: build, validate, save and run a cassy-fatigue YAML."""

from __future__ import annotations

import logging
import sys
import time
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QAction, QCloseEvent, QIcon, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QLabel,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QSplitter,
    QStackedWidget,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from cassy_fatigue.config import ConfigurationError
from cassy_fatigue.gui import model
from cassy_fatigue.gui.pages import (
    AnalysesPage,
    EventsPage,
    GeneralPage,
    LoadsPage,
    MaterialsPage,
    RegionsPage,
    RunPage,
)
from cassy_fatigue.gui.session import DPFSession
from cassy_fatigue.gui.viewer import ResultsPanel
from cassy_fatigue.kernel import list_materials
from cassy_fatigue.probe import ResultInfo, probe_result

UNTITLED = Path.cwd() / "untitled.yaml"


class _Bridge(QObject):
    """Delivers worker-thread results and log records to the GUI thread."""

    done = Signal(object, object)  # callback, future
    log = Signal(str)
    progress = Signal(object)  # cassy_fatigue.progress.Update of the running run


class _LogHandler(logging.Handler):
    def __init__(self, bridge: _Bridge):
        super().__init__(logging.INFO)
        self.bridge = bridge
        self.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))

    def emit(self, record):
        try:
            self.bridge.log.emit(self.format(record))
        except RuntimeError:  # window already destroyed
            pass


class MainWindow(QMainWindow):
    def __init__(self, path: Path | None = None, session: DPFSession | None = None):
        super().__init__()
        self.session = session or DPFSession()
        # One worker: DPF calls are serialized, and the GUI thread never blocks on them.
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="cassy-dpf")
        # Result files are read on their own worker, so viewing never waits for a DPF run.
        self._io_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="cassy-io")
        self._bridge = _Bridge()
        self._bridge.done.connect(lambda callback, future: callback(future))
        self._bridge.log.connect(self.append_log)
        self.progress_signal = self._bridge.progress  # emitted from the worker thread
        self._log_handler = _LogHandler(self._bridge)
        logging.getLogger().addHandler(self._log_handler)
        logging.getLogger().setLevel(logging.INFO)

        self.project = model.new_project()
        self.path: Path | None = None
        self.dirty = False
        self.validation: object = "not validated yet"  # Config, or the error text
        self.probes: dict[str, ResultInfo | str] = {}  # absolute path -> info or error text
        self._materials_cache: dict[tuple, list[tuple[str, str | None]]] = {}

        self.pages = [
            GeneralPage(self),
            AnalysesPage(self),
            MaterialsPage(self),
            RegionsPage(self),
            LoadsPage(self),
            EventsPage(self),
            RunPage(self),
        ]
        self.nav = QListWidget()
        self.nav.addItems([page.title for page in self.pages])
        self.nav.setFixedWidth(150)
        self.stack = QStackedWidget()
        for page in self.pages:
            self.stack.addWidget(page)
        self.nav.currentRowChanged.connect(self._show_page)

        self.banner = QLabel()
        self.banner.setWordWrap(True)
        self.banner.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.log = QPlainTextEdit(readOnly=True)
        self.log.setMaximumBlockCount(5000)
        pages = QWidget()
        layout = QVBoxLayout(pages)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.banner)
        layout.addWidget(self.stack, 1)
        top = QSplitter()
        top.addWidget(self.nav)
        top.addWidget(pages)
        top.setStretchFactor(1, 1)
        self.results_panel = ResultsPanel(self)
        self.tabs = QTabWidget()
        self.tabs.addTab(top, "Configuration")
        self.tabs.addTab(self.results_panel, "Results")
        outer = QSplitter(Qt.Vertical)
        outer.addWidget(self.tabs)
        outer.addWidget(self.log)
        outer.setStretchFactor(0, 4)
        outer.setStretchFactor(1, 1)
        self.setCentralWidget(outer)
        self._menus()
        # Busy indicator while result metadata is read (no progress inside one DPF read).
        self._probing = 0
        self.probe_bar = QProgressBar()
        self.probe_bar.setRange(0, 0)
        self.probe_bar.setMaximumWidth(160)
        self.probe_bar.setVisible(False)
        self.statusBar().addPermanentWidget(self.probe_bar)
        self.resize(1300, 800)
        if path is not None:
            self.open_path(Path(path))
        else:
            self.reload()
        self.nav.setCurrentRow(0)

    # ----------------------------------------------------------------- menus/files
    def _menus(self):
        menu = self.menuBar().addMenu("&File")
        for text, shortcut, slot in (
            ("&New", QKeySequence.New, self.new),
            ("&Open…", QKeySequence.Open, self.open),
            ("&Save", QKeySequence.Save, self.save),
            ("Save &As…", QKeySequence.SaveAs, self.save_as),
            ("Open &results…", "Ctrl+R", self.open_results),
            ("&Quit", QKeySequence.Quit, self.close),
        ):
            action = QAction(text, self, shortcut=shortcut)
            action.triggered.connect(slot)
            menu.addAction(action)

    def _confirm_discard(self) -> bool:
        if not self.dirty:
            return True
        answer = QMessageBox.question(
            self,
            "Unsaved changes",
            "Save changes to the configuration?",
            QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
        )
        if answer == QMessageBox.Save:
            return self.save()
        return answer == QMessageBox.Discard

    def new(self):
        if self._confirm_discard():
            self.project, self.path, self.dirty = model.new_project(), None, False
            self.reload()

    def open(self):
        if not self._confirm_discard():
            return
        name, _ = QFileDialog.getOpenFileName(
            self, "Open configuration", str(self.directory), "YAML (*.yaml *.yml);;All files (*)"
        )
        if name:
            self.open_path(Path(name))

    def open_path(self, path: Path):
        try:
            project, notices = model.load_project(path)
        except (OSError, ConfigurationError, ValueError) as error:
            QMessageBox.critical(self, "Cannot open configuration", str(error))
            return
        self.project, self.path, self.dirty = project, path.resolve(), False
        for notice in notices:
            self.append_log(f"NOTE: {notice}")
        if notices:
            self.dirty = True
        self.reload()
        for analysis in self.project["analyses"].values():
            self.probe(analysis)

    def save(self) -> bool:
        if self.path is None:
            return self.save_as()
        try:
            model.save_project(self.project, self.path)
        except OSError as error:
            QMessageBox.critical(self, "Cannot save", str(error))
            return False
        self.dirty = False
        self.append_log(f"Saved {self.path}")
        self.changed(mark_dirty=False)
        return True

    def save_as(self) -> bool:
        name, _ = QFileDialog.getSaveFileName(
            self,
            "Save configuration",
            str(self.path or self.directory / "fatigue.yaml"),
            "YAML (*.yaml *.yml)",
        )
        if not name:
            return False
        path = Path(name)
        if path.suffix.lower() not in (".yaml", ".yml"):
            path = path.with_suffix(".yaml")
        self.path = path.resolve()
        return self.save()

    def open_results(self):
        self.tabs.setCurrentWidget(self.results_panel)
        self.results_panel.open()

    def show_results(self, path: Path):
        """Load a fatigue.vtu into the Results tab and switch to it."""
        self.tabs.setCurrentWidget(self.results_panel)
        self.results_panel.load(Path(path))

    @property
    def directory(self) -> Path:
        return self.path.parent if self.path else Path.cwd()

    @property
    def yaml_path(self) -> Path:
        """Where relative paths are resolved; the current directory until first save."""
        return self.path or UNTITLED

    # --------------------------------------------------------------- state updates
    def _show_page(self, row: int):
        self.stack.setCurrentIndex(row)
        self.pages[row].load()

    def reload(self):
        """Rebuild every page from the project (after open/new/structural changes)."""
        for page in self.pages:
            page.load()
        self.changed(mark_dirty=False)

    def changed(self, mark_dirty: bool = True):
        """Refresh validation and the window title after an edit."""
        if mark_dirty:
            self.dirty = True
        result = self.validation = model.validate(self.project, self.yaml_path)
        if isinstance(result, str):
            self.banner.setText(f"⚠ {result}")
            self.banner.setStyleSheet("background:#fde2e1;color:#7a1712;padding:6px;")
        else:
            self.banner.setText(
                "✔ Configuration is valid (mesh, units and materials are "
                "checked again when the run reads the results)"
            )
            self.banner.setStyleSheet("background:#dff3e3;color:#14532d;padding:6px;")
        title = self.path.name if self.path else "untitled"
        self.setWindowTitle(f"cassy-fatigue — {title}{' *' if self.dirty else ''}")
        self.pages[-1].update_state()

    def append_log(self, text: str):
        self.log.appendPlainText(text)

    # ---------------------------------------------------------- catalogue / probes
    def materials(self) -> list[tuple[str, str | None]]:
        library = self.project.get("material_library") or None
        key = (self.project["code"], library)
        if key not in self._materials_cache:
            try:
                self._materials_cache[key] = list_materials(
                    self.project["code"], Path(library) if library else None
                )
            except (ConfigurationError, OSError, ValueError) as error:
                self.append_log(f"ERROR: material catalogue: {error}")
                self._materials_cache[key] = []
        return self._materials_cache[key]

    def info(self, analysis: str) -> ResultInfo | None:
        result = self.probes.get(self.project["analyses"].get(analysis, ""))
        return result if isinstance(result, ResultInfo) else None

    def base_info(self) -> ResultInfo | None:
        """Probe of the base mesh: the first load's first analysis, else the first .rst."""
        for load in self.project["loads"].values():
            for analysis in model.load_analyses(load)[:1]:
                if analysis in self.project["analyses"]:
                    return self.info(analysis)
        for name, path in self.project["analyses"].items():
            if str(path).lower().endswith(".rst"):
                return self.info(name)
        return None

    def submit(self, work, callback, io: bool = False):
        """Run ``work`` on the DPF worker (or the file worker); ``callback`` on the GUI thread."""
        future = (self._io_executor if io else self._executor).submit(work)
        future.add_done_callback(lambda done: self._bridge.done.emit(callback, done))
        return future

    def probe(self, path: str, force: bool = False):
        if not path or (path in self.probes and not force):
            return
        self.probes[path] = "probing…"
        self._probing += 1
        self.probe_bar.setVisible(True)
        self.statusBar().showMessage(
            f"Reading metadata of {Path(path).name} (mesh, result sets, named selections)…"
        )
        self.append_log(
            f"Reading metadata of {path} (the whole mesh is read; large models can take minutes)…"
        )

        def work():
            start = time.perf_counter()
            return probe_result(Path(path), self.session.server()), time.perf_counter() - start

        def finished(future: Future):
            self._probing -= 1
            self.probe_bar.setVisible(self._probing > 0)
            try:
                self.probes[path], seconds = future.result()
                self.append_log(
                    f"Read metadata of {path} in {seconds:.1f} s (DPF {self.session.endpoint})"
                )
            except Exception as error:  # noqa: BLE001 - shown in the analyses table and the log
                self.probes[path] = f"error: {error}"
                self.append_log(f"ERROR: cannot read {path}: {error}")
            if not self._probing:
                self.statusBar().clearMessage()
            self.reload()

        self.submit(work, finished)

    # --------------------------------------------------------------------- closing
    def closeEvent(self, event: QCloseEvent):
        if not self._confirm_discard():
            event.ignore()
            return
        if self.pages[-1].running:
            QMessageBox.warning(
                self, "Run in progress", "Wait for the run to finish, or cancel it on the Run page."
            )
            event.ignore()
            return
        self.statusBar().showMessage("Closing DPF session…")
        self._executor.shutdown(wait=True, cancel_futures=True)
        self._io_executor.shutdown(wait=True, cancel_futures=True)
        self.results_panel.close_view()
        self.session.close()
        logging.getLogger().removeHandler(self._log_handler)
        event.accept()


LOGO = Path(__file__).with_name("logo.svg")


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if sys.platform == "win32":
        # Own taskbar entry, so Windows shows the logo instead of the Python icon.
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("F4E.cassy-fatigue")
    app = QApplication.instance() or QApplication([sys.argv[0]])
    app.setWindowIcon(QIcon(str(LOGO)))
    path = Path(argv[0]) if argv else None
    if path is not None and path.suffix.lower() == ".vtu":
        window = MainWindow(None)
        window.show_results(path)
    else:
        window = MainWindow(path)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
