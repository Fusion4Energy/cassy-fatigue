"""Qt editor smoke tests (offscreen). Skipped without the optional ``gui`` extra."""

import os
import shutil
import time
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from cassy_fatigue.gui import pages
from cassy_fatigue.gui.app import LOGO, MainWindow
from cassy_fatigue.gui.session import DPFSession
from cassy_fatigue.probe import probe_result

BENCHMARKS = Path(__file__).resolve().parents[1] / "benchmarks"


class FakeSession(DPFSession):
    """Hands out a given server and never starts or stops one."""

    def __init__(self, server=None):
        super().__init__()
        self._fake = server
        self.closed = 0

    def server(self):
        if self._fake is None:
            raise RuntimeError("no DPF in this test")
        self.endpoint = "fake:0 (existing server)"
        return self._fake

    @property
    def active(self):
        return False

    def close(self):
        self.closed += 1


def make_window(qtbot, tmp_path, server=None):
    source = tmp_path / "two_state.yaml"
    shutil.copy(BENCHMARKS / "two_state.yaml", source)
    (tmp_path / "fixtures").mkdir()
    shutil.copy(BENCHMARKS / "fixtures" / "two_materials.rst", tmp_path / "fixtures")
    session = FakeSession(server)
    window = MainWindow(source, session=session)
    qtbot.addWidget(window)
    qtbot.waitUntil(
        lambda: (
            not str(
                window.probes.get(str(tmp_path / "fixtures" / "two_materials.rst"), "")
            ).startswith("probing")
        )
    )
    return window, session


def test_logo_is_packaged_and_loads_as_icon(qtbot):
    from PySide6.QtGui import QIcon

    assert LOGO.is_file()
    icon = QIcon(str(LOGO))
    assert not icon.isNull()
    assert not icon.pixmap(32, 32).isNull()


def test_editor_loads_validates_and_shows_every_page(qtbot, tmp_path):
    window, _ = make_window(qtbot, tmp_path)
    assert window.banner.text().startswith("✔")
    for row in range(window.nav.count()):
        window.nav.setCurrentRow(row)
    assert not hasattr(window, "preview")  # the YAML preview was removed
    assert "no DPF" in window.log.toPlainText()  # probe failure is reported, not raised
    assert window.pages[-1].run_button.isEnabled()


def test_invalid_edit_disables_run_and_valid_edit_saves(qtbot, tmp_path):
    window, _ = make_window(qtbot, tmp_path)
    window.project["fatigue_events"][0]["loads"] = ["missing"]
    window.changed()
    assert "unknown load" in window.banner.text()
    assert not window.pages[-1].run_button.isEnabled()
    assert window.windowTitle().endswith("*")
    window.project["fatigue_events"][0]["loads"] = ["hot"]
    window.changed()
    assert window.save()
    assert "loads: [hot]" in (tmp_path / "two_state.yaml").read_text(encoding="utf-8")


def test_run_uses_shared_pipeline_with_session_server(qtbot, tmp_path):
    server = object()
    window, session = make_window(qtbot, tmp_path, server)
    page = window.pages[-1]
    with patch.object(
        pages, "run_config", return_value={"vtu": tmp_path / "out" / "fatigue.vtu"}
    ) as run:
        page.run()
        qtbot.waitUntil(lambda: not page.running)
    args = run.call_args.args
    assert args[0].source == (tmp_path / "two_state.yaml").resolve()
    assert set(args[1]) == {1, 2}  # materials loaded before DPF use
    assert args[3] is server and args[4] == session.endpoint
    assert "Reports written" in page.result.text()
    assert page.show_results.isEnabled()  # the (fake) VTU is preloaded; failure only logged
    qtbot.waitUntil(lambda: "cannot open results" in window.log.toPlainText())
    window.close()
    assert session.closed >= 1


def test_run_shows_step_progress_and_can_be_cancelled(qtbot, tmp_path):
    window, _ = make_window(qtbot, tmp_path, object())
    page = window.pages[-1]
    assert not page.cancel_button.isEnabled() and not page.progress_bar.isVisible()

    def fake_run(*args):
        progress = args[-1]
        progress.plan(3)
        with progress.step("Reading stress struct set 1, material 1"):
            pass
        deadline = time.monotonic() + 30
        while not progress.cancelled and time.monotonic() < deadline:
            time.sleep(0.01)
        with progress.step("Reading stress struct set 2, material 1"):  # raises RunCancelled
            raise AssertionError("step after cancel must not start")

    with patch.object(pages, "run_config", side_effect=fake_run):
        page.run()
        qtbot.waitUntil(lambda: page.progress_bar.value() == 1)
        assert page.progress_bar.maximum() == 3
        assert "Step 1/3: Reading stress struct set 1, material 1 (done)" in page.step_label.text()
        assert "elapsed" in page.step_label.text() and "left" in page.step_label.text()
        assert page.cancel_button.isEnabled()
        page.cancel_button.click()
        assert not page.cancel_button.isEnabled()
        qtbot.waitUntil(lambda: not page.running)
    assert "Run cancelled" in page.result.text()
    assert not page.progress_bar.isVisible() and not page.show_results.isEnabled()
    assert "Cancelled:" in window.log.toPlainText()
    assert "[1/3] done in" in window.log.toPlainText()  # step log reaches the GUI log


def test_live_gui_run_matches_cli_example(qtbot, tmp_path, dpf_live_server):
    window, _ = make_window(qtbot, tmp_path, dpf_live_server)
    info = window.base_info()
    assert info is not None and info == probe_result(
        tmp_path / "fixtures" / "two_materials.rst", dpf_live_server
    )
    page = window.pages[-1]
    page.output.setText(str(tmp_path / "out"))
    page.run()
    qtbot.waitUntil(lambda: not page.running, timeout=120000)
    assert "Reports written" in page.result.text(), page.result.text()
    import numpy as np
    import pyvista as pv

    grid = pv.read(tmp_path / "out" / "fatigue.vtu")
    assert grid.n_points == 18 and grid.n_cells == 4
    assert np.count_nonzero(grid.point_data["invalid_code"]) == 0
    np.testing.assert_allclose(grid.point_data["U_total"].max(), 0.001551, rtol=1e-3)
    np.testing.assert_allclose(
        grid.point_data["U_total"], grid.point_data["Vj_pulse"] + grid.point_data["Vj_secondary"]
    )
    panel = window.results_panel  # the run preloads its VTU into the Results tab
    qtbot.waitUntil(lambda: panel.scene is not None, timeout=30000)
    assert panel.results.path == (tmp_path / "out" / "fatigue.vtu").resolve()
    assert panel.results.events == ["pulse", "secondary"]
    top = panel.hotspot_rows[0]
    np.testing.assert_allclose(panel.results.values("U_total")[top], 0.001551, rtol=1e-3)
    assert "Governing material: 1" in panel.results.format_report(top)


# ------------------------------------------------------------------ results tab
def results_window(qtbot, tmp_path):
    from test_gui_results import write_result

    path = write_result(tmp_path)
    window = MainWindow(None, session=FakeSession())
    qtbot.addWidget(window)
    panel = window.results_panel
    window.show_results(path)
    qtbot.waitUntil(lambda: panel.results is not None and panel.scene is not None)
    return window, panel, path


def test_results_tab_controls_hotspots_and_picking(qtbot, tmp_path):
    window, panel, _ = results_window(qtbot, tmp_path)
    assert window.tabs.currentWidget() is panel
    assert panel.plotter is None  # offscreen Qt: placeholder instead of the 3D widget
    assert "3D view unavailable" in panel.view.text()
    assert "1 invalid node(s)" in panel.source.text()
    assert panel.scene.field == "U_total" and panel.log.isChecked()
    assert panel.table.rowCount() == 15  # 16 nodes, one NaN excluded from "highest"

    panel.quantity.setCurrentIndex(panel.quantity.findData("N"))
    panel.event.setCurrentIndex(panel.event.findData("b"))
    panel.refresh()
    assert panel.scene.field == "N_b" and panel.mode.currentData() == "lowest"
    values = panel.results.values("N_b")
    assert values[panel.hotspot_rows[0]] == np.nanmin(values)

    panel.mode.setCurrentIndex(panel.mode.findData("invalid"))
    panel.refresh()
    assert panel.table.rowCount() == 1 and panel.table.item(0, 5).text() == "NONFINITE_RESULT"
    panel.table.cellClicked.emit(0, 1)
    assert "INVALID 16" in panel.details.toPlainText()

    panel.quantity.setCurrentIndex(panel.quantity.findData("invalid_code"))
    assert panel.event.itemText(0) == "(whole node)" and not panel.log.isEnabled()

    panel.threshold_min.setText("oops")
    panel.threshold.setChecked(True)
    panel.refresh()
    assert "⚠" in panel.status.text()
    panel.threshold.setChecked(False)

    panel.clip.setChecked(True)
    panel.clip_axis.setCurrentText("x")
    panel.clip_slider.setValue(500)  # middle of x in [0, 3]
    assert float(panel.clip_position.text()) == pytest.approx(1.5)
    panel.refresh()
    assert panel.scene.visible_nodes.sum() == 8
    panel.find.setText("101")
    panel.find.returnPressed.emit()
    assert panel.details.toPlainText().startswith("Node 101")
    panel.find.setText("5")
    panel.find.returnPressed.emit()
    assert "No node 5" in panel.details.toPlainText()


def test_results_screenshot_and_saved_view(qtbot, tmp_path):
    window, panel, path = results_window(qtbot, tmp_path)
    panel.quantity.setCurrentIndex(panel.quantity.findData("Vj"))
    panel.event.setCurrentIndex(panel.event.findData("b"))
    panel.auto_range.setChecked(False)
    panel.range_min.setText("1e-4")
    panel.range_max.setText("1e-2")
    panel.clip.setChecked(True)
    panel.clip_position.setText("2.0")
    panel.clip_position.editingFinished.emit()
    panel.image_w.setValue(300)
    panel.image_h.setValue(200)
    panel.refresh()
    wanted = panel.state()
    png = panel.screenshot(tmp_path / "view.png")
    assert png.is_file() and (tmp_path / "view.view.json").is_file()

    panel.quantity.setCurrentIndex(0)
    panel.clip.setChecked(False)
    panel.refresh()
    assert panel.state() != wanted
    panel.load_view(tmp_path / "view.view.json")
    assert panel.state() == wanted and panel.scene.field == "Vj_b"
    assert panel.clip_slider.value() == round(2 / 3 * 1000)

    # Reloading the same file keeps the view settings.
    panel.load(path)
    qtbot.waitUntil(lambda: "Loaded results" in window.log.toPlainText().split("Screenshot")[-1])
    assert panel.state() == wanted


def test_results_tab_reports_unreadable_files(qtbot, tmp_path):
    window, panel, _ = results_window(qtbot, tmp_path)
    bad = tmp_path / "not_results.vtu"
    import pyvista as pv

    pv.ImageData(dimensions=(2, 2, 2)).cast_to_unstructured_grid().save(bad)
    panel.load(bad)
    qtbot.waitUntil(lambda: "Cannot open" in panel.source.text())
    assert "not a cassy-fatigue result" in window.log.toPlainText()
    assert panel.results is not None  # the previous results stay loaded


def test_results_tab_drives_a_plotter(qtbot, tmp_path):
    """Same calls as on the QtInteractor, on an off-screen PyVista plotter."""
    import pyvista as pv
    from PySide6.QtWidgets import QLabel

    from cassy_fatigue.gui import viewer

    plotter = pv.Plotter(off_screen=True, window_size=[300, 200])
    with patch.object(viewer, "make_plotter", return_value=(plotter, QLabel("3D"))):
        window, panel, _ = results_window(qtbot, tmp_path)
    assert panel.plotter is plotter and "result" in plotter.actors
    plotter.view_xy()
    camera = plotter.camera_position
    panel.threshold.setChecked(True)
    panel.refresh()
    assert "context" in plotter.actors and plotter.camera_position == camera  # view kept
    corner = panel.results.grid.points[panel.results.hotspots("U_total", 1)[0]]
    panel._picked(corner + 1e-3)  # what the point-picking callback receives
    assert "marker" in plotter.actors
    assert panel.marker == panel.results.hotspots("U_total", 1)[0]
    panel.reset_camera()
    assert plotter.camera_position != camera
    png = panel.screenshot(tmp_path / "from_view.png")
    _, saved_camera, _ = viewer.rs.load_view(viewer.rs.view_file(png))
    assert saved_camera == [list(v) for v in plotter.camera_position]
    window.close()
    assert plotter._closed


def page_of(window, kind):
    return next(page for page in window.pages if isinstance(page, kind))


def test_sets_dialog_edits_signed_sets_with_probed_dropdowns(qtbot, tmp_path):
    from types import SimpleNamespace

    window, _ = make_window(qtbot, tmp_path)
    probed = SimpleNamespace(
        time_unit="s",
        set_label=lambda set_id: f"set {set_id} (?)",
        sets=[SimpleNamespace(set_id=i, label=lambda unit, i=i: f"set {i}") for i in (1, 2, 3)],
    )
    window.info = lambda analysis: probed if analysis == "other" else None
    dialog = pages.SetsDialog(window, window, ["struct", "other"], ["+struct:2", "-struct:1"])
    qtbot.addWidget(dialog)
    assert dialog.sets() == ["+struct:2", "-struct:1"]
    assert isinstance(dialog.table.cellWidget(0, 2), pages.QSpinBox)  # struct not probed
    dialog.table.cellWidget(0, 0).setCurrentText("-")
    dialog.table.cellWidget(1, 1).setCurrentText("other")  # keeps set 1, now a dropdown
    assert isinstance(dialog.table.cellWidget(1, 2), pages.QComboBox)
    dialog.table.cellWidget(1, 2).setCurrentIndex(2)
    assert dialog.sets() == ["-struct:2", "-other:3"]


def test_loads_page_edits_sets_and_scale(qtbot, tmp_path):
    window, _ = make_window(qtbot, tmp_path)
    page = page_of(window, pages.LoadsPage)
    page.load()
    assert page.table.item(1, 2).text() == "-struct:1"
    with (
        patch.object(pages.SetsDialog, "exec", return_value=pages.QDialog.Accepted),
        patch.object(pages.SetsDialog, "sets", return_value=["+struct:2", "-struct:1"]),
    ):
        page._edit(0)
    assert window.project["loads"]["hot"]["sets"] == ["+struct:2", "-struct:1"]
    page.table.cellWidget(0, 3).setValue(0.5)
    assert window.project["loads"]["hot"]["scale"] == 0.5
    page.table.cellWidget(0, 3).setValue(1)
    assert "scale" not in window.project["loads"]["hot"]
    assert window.banner.text().startswith("✔")
    assert window.save()  # no unsaved-changes prompt on close


def test_events_page_chooses_loads(qtbot, tmp_path):
    window, _ = make_window(qtbot, tmp_path)
    page = page_of(window, pages.EventsPage)
    page.load()
    assert page.table.item(0, 2).text() == "hot + cold_removed"
    with patch.object(pages, "choose", return_value=["cold_removed"]) as choose:
        page._edit(1)
    assert choose.call_args.args[3] == ["hot", "cold_removed"]
    assert choose.call_args.args[4] == {"hot"}
    assert window.project["fatigue_events"][1]["loads"] == ["cold_removed"]
    assert window.banner.text().startswith("✔")
    assert window.save()
    assert "loads: [cold_removed]" in (tmp_path / "two_state.yaml").read_text(encoding="utf-8")


def test_analyses_page_declares_units(qtbot, tmp_path):
    window, _ = make_window(qtbot, tmp_path)
    page = page_of(window, pages.AnalysesPage)
    page.load()
    assert page.table.item(0, 2).text() == "from result"
    page.table.selectRow(0)
    dialog = pages.UnitsDialog(window, "struct", {"stress": "MPa"})
    qtbot.addWidget(dialog)
    assert dialog.units() == {"length": None, "stress": "MPa", "temperature": None, "time": None}
    with (
        patch.object(pages.UnitsDialog, "exec", return_value=pages.QDialog.Accepted),
        patch.object(pages.UnitsDialog, "units", return_value={"length": "mm"}),
    ):
        page._units()
    assert window.project["analysis_units"] == {"struct": {"length": "mm"}}
    assert window.banner.text().startswith("✔")
    assert window.save()
    assert "length: mm" in (tmp_path / "two_state.yaml").read_text(encoding="utf-8")


def test_analyses_page_sets_temperature_source(qtbot, tmp_path):
    window, _ = make_window(qtbot, tmp_path)
    window.project["analyses"]["copy"] = window.project["analyses"]["struct"]
    page = page_of(window, pages.AnalysesPage)
    page.load()
    assert page.table.item(0, 3).text() == "own"
    page.table.selectRow(0)
    with patch.object(pages.QInputDialog, "getItem", return_value=("copy", True)) as ask:
        page._temperature()
    assert ask.call_args.args[3][0] == pages.AnalysesPage.OWN_TEMPERATURE
    assert window.project["analysis_temperature"] == {"struct": "copy"}
    assert window.save()
    assert "temperature: copy" in (tmp_path / "two_state.yaml").read_text(encoding="utf-8")
    page.table.selectRow(0)
    with patch.object(
        pages.QInputDialog, "getItem", return_value=(pages.AnalysesPage.OWN_TEMPERATURE, True)
    ):
        page._temperature()
    assert window.project["analysis_temperature"] == {}
    assert window.save()  # no unsaved changes, so closing does not ask
