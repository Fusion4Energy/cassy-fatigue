import logging
from pathlib import Path

import pytest

from cassy_fatigue.config import Config
from cassy_fatigue.progress import Progress, RunCancelled, format_seconds

BENCHMARKS = Path(__file__).resolve().parents[1] / "benchmarks"


def test_steps_count_log_and_report(caplog):
    updates = []
    progress = Progress(updates.append)
    progress.plan(2)
    with caplog.at_level(logging.INFO):
        with progress.step("Reading mesh"):
            pass
        with progress.step("Writing"):
            pass
    assert (progress.done, progress.total) == (2, 2)
    assert [(u.done, u.total, u.message, u.running) for u in updates] == [
        (0, 2, "Reading mesh", True),
        (1, 2, "Reading mesh", False),
        (1, 2, "Writing", True),
        (2, 2, "Writing", False),
    ]
    assert "[1/2] Reading mesh…" in caplog.text and "[2/2] done in" in caplog.text
    assert updates[-1].remaining() is None


def test_failed_step_is_not_counted():
    progress = Progress()
    with pytest.raises(ValueError), progress.step("Reading stress"):
        raise ValueError("bad")
    assert progress.done == 0


def test_cancel_stops_at_next_step_boundary():
    progress = Progress()
    with progress.step("first"):
        progress.cancel()  # the running step still finishes
    assert progress.done == 1 and progress.cancelled
    with pytest.raises(RunCancelled), progress.step("second"):
        raise AssertionError("not reached")
    assert isinstance(RunCancelled("x"), RuntimeError)  # CLI/GUI error handling applies


def test_unplanned_total_grows_and_remaining_estimate():
    progress = Progress()
    with progress.step("only"):
        pass
    assert progress.total == 1
    progress.plan(4)
    progress.plan(0)  # never below the completed steps
    assert progress.total == 1
    from cassy_fatigue.progress import Update

    assert Update(2, 6, "x", 10.0, False).remaining() == pytest.approx(20.0)


@pytest.mark.parametrize(
    ("seconds", "text"), [(4.4, "4 s"), (75, "1 min 15 s"), (3725, "1 h 02 min")]
)
def test_format_seconds(seconds, text):
    assert format_seconds(seconds) == text


def test_planned_steps_for_the_example_configuration():
    from cassy_fatigue.pipeline import planned_steps

    config = Config.read(BENCHMARKS / "two_state.yaml")
    # 1 analysis + regions; 2 materials x (2 stress + 2 temperature sets);
    # 2 materials x 2 events; writing.
    assert planned_steps(config) == 2 + 2 * (2 + 2) + 2 * 2 + 1


def test_live_run_reports_exactly_the_planned_steps(tmp_path, dpf_live_server):
    from cassy_fatigue.kernel import load_materials
    from cassy_fatigue.pipeline import planned_steps, run_config

    config = Config.read(BENCHMARKS / "two_state.yaml")
    materials, sources = load_materials(config)
    updates = []
    progress = Progress(updates.append)
    paths = run_config(
        config, materials, sources, dpf_live_server, "live", tmp_path / "out", progress=progress
    )
    assert paths["vtu"].is_file()
    assert progress.done == progress.total == planned_steps(config)
    messages = [u.message for u in updates if not u.running]
    assert messages[0].startswith("Reading mesh of struct")
    assert sum(m.startswith("Reading stress") for m in messages) == 4
    assert sum(m.startswith("Reading temperature") for m in messages) == 4
    assert sum(m.startswith("Assessing event") for m in messages) == 4
    assert messages[-1].startswith("Writing")
