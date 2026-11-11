"""The nodal fatigue run shared by the CLI and the GUI."""

from pathlib import Path

from cassy.general.material import Material

from cassy_fatigue.config import Config
from cassy_fatigue.kernel import MaterialSource, evaluate
from cassy_fatigue.output import write_outputs
from cassy_fatigue.progress import Progress
from cassy_fatigue.reader import DPFReader


def default_output_dir(config: Config) -> Path:
    return config.source.parent / f"{config.source.stem}_results"


def stress_sets(config: Config) -> set[tuple[str, int]]:
    """Distinct ``(analysis, set)`` stress reads used by the fatigue events."""
    return {
        (term.analysis, term.step)
        for event in config.fatigue_events
        for term in config.event_terms(event)
    }


def planned_steps(config: Config, reader: DPFReader | None = None) -> int:
    """Progress steps of a run; temperature sets are exact once ``reader`` is open."""
    stress = stress_sets(config)
    if reader is None:
        temperature = len(stress)  # upper bound until the time mapping is known
    else:
        temperature = len({reader.temperature_set(*key) for key in stress})
    materials = len(config.materials)
    return (
        DPFReader.open_steps(config)
        + materials * (len(stress) + temperature)
        + materials * len(config.fatigue_events)
        + 1
    )


def run_config(
    config: Config,
    materials: dict[int, Material],
    sources: dict[int, MaterialSource],
    server,
    endpoint: str,
    output_dir: Path | None = None,
    overwrite: bool = False,
    progress: Progress | None = None,
) -> dict[str, Path]:
    """Read, evaluate and write reports on an existing, caller-owned DPF server.

    Callers validate the configuration and load materials (``load_materials``) first, so
    no DPF server is needed to report configuration errors. ``progress`` receives one
    step per mesh read, per (set, material) read, per (event, material) assessment and
    for writing; :meth:`Progress.cancel` stops the run at the next step boundary
    (raising :class:`~cassy_fatigue.progress.RunCancelled`) without writing reports.
    """
    progress = progress or Progress()
    progress.plan(planned_steps(config))
    reader = DPFReader(config, server, progress)
    progress.plan(planned_steps(config, reader))
    results = evaluate(config, reader, materials, progress)
    with progress.step("Writing fatigue.vtu, configuration.yaml and traceability.json"):
        return write_outputs(
            config,
            reader,
            results,
            sources,
            output_dir or default_output_dir(config),
            overwrite,
            endpoint,
        )
