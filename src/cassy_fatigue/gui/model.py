"""Qt-free project model for the configuration GUI.

A project is a plain dict shaped like the YAML configuration, except that analysis and
material-library paths are kept absolute while editing, and declared analysis units and
temperature sources are kept apart in ``analysis_units`` (name -> {quantity: unit}) and
``analysis_temperature`` (name -> source analysis, only when not the analysis itself) so
``analyses`` always maps a name to a path. ``to_raw`` turns it back into
the YAML form for a given file location. Validation always goes through
:meth:`cassy_fatigue.config.Config.from_dict`, so the GUI never duplicates the rules.
"""

from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any

import yaml

from cassy_fatigue.config import Config, ConfigurationError, _UniqueLoader, format_term, parse_term

CODES = ("SDC-IC", "RCC-MRx", "RCC-MR")
CLASSIFICATIONS = ("P", "Q")
SECTION_ORDER = (
    "code",
    "analyses",
    "material_library",
    "materials",
    "regions",
    "loads",
    "fatigue_events",
)
LEGACY_SECTIONS = ("temperature",)  # converted on open, never written


def new_project() -> dict[str, Any]:
    return {
        "code": CODES[0],
        "analyses": {},
        "analysis_units": {},
        "analysis_temperature": {},
        "material_library": None,
        "materials": {},
        "regions": [],
        "loads": {},
        "fatigue_events": [],
    }


def materials_by_selection(project: dict) -> bool:
    """True if materials are keyed by named selection rather than by solver ``mat`` ID."""
    return any(isinstance(key, str) for key in project["materials"])


def _absolute(value, root: Path) -> str:
    return str((root / Path(value)).resolve()) if isinstance(value, str) and value else value


def _relative(value: str | None, root: Path) -> str | None:
    if not value:
        return value
    try:
        return Path(os.path.relpath(value, root)).as_posix()
    except ValueError:  # e.g. another drive on Windows
        return value


def _negate(term: str) -> str:
    return ("-" if term.startswith("+") else "+") + term[1:]


def _convert_legacy(project: dict) -> list[str]:
    """Convert the former signed-delta format in place; exact, returns notices.

    Former loads were single result sets and events signed sums of loads. Now a load is a
    signed set combination and an event a plain sum of loads, so a ``-load`` term becomes
    a new load ``<load>_neg`` with every set sign flipped (same classification and scale).
    """
    notices = []
    loads = project["loads"]
    for name, load in loads.items():
        if isinstance(load, dict) and "sets" not in load and ("analysis" in load or "set" in load):
            load["sets"] = [f"+{load.pop('analysis', '')}:{load.pop('set', '')}"]
            notices.append(f"Load {name!r}: analysis/set converted to sets {load['sets']}")
    for event in project["fatigue_events"]:
        if not isinstance(event, dict) or "loads" in event:
            continue
        if "state_a" in event or "state_b" in event:
            # delta = state_a - state_b: exact, since terms combine linearly and the
            # temperature envelope uses the same set of terms.
            event["delta"] = list(event.pop("state_a", []) or []) + [
                _negate(term)
                for term in (event.pop("state_b", []) or [])
                if isinstance(term, str) and term
            ]
        if "delta" not in event:
            continue
        names = []
        for term in event.pop("delta") or []:
            name = term[1:] if isinstance(term, str) and term[:1] in "+-" else term
            if isinstance(term, str) and term.startswith("-") and isinstance(loads.get(name), dict):
                negated = f"{name}_neg"
                if negated not in loads:
                    source = loads[name]
                    loads[negated] = {
                        **copy.deepcopy(source),
                        "sets": [_negate(text) for text in source.get("sets", [])],
                    }
                    notices.append(f"Load {negated!r} added: {name!r} with flipped signs")
                name = negated
            names.append(name)
        event["loads"] = names
        notices.append(
            f"Event {event.get('id')!r}: signed delta converted to the sum of loads {names}"
        )
    return notices


def load_project(path: Path) -> tuple[dict[str, Any], list[str]]:
    """Read a YAML file into a project, returning notices about converted content.

    The file does not need to be a valid configuration (it can be half finished), but it
    must be YAML without duplicate keys and must not contain unknown top-level keys.
    """
    path = Path(path).resolve()
    with path.open(encoding="utf-8") as stream:
        raw = yaml.load(stream, Loader=_UniqueLoader) or {}
    if not isinstance(raw, dict):
        raise ConfigurationError("The configuration file must contain a mapping")
    unknown = set(raw) - set(SECTION_ORDER) - set(LEGACY_SECTIONS)
    if unknown:
        raise ConfigurationError(f"Unknown configuration keys {sorted(map(str, unknown))}")
    project = new_project()
    raw = copy.deepcopy(raw)
    legacy_temperature = raw.pop("temperature", None)
    project.update(raw)
    root = path.parent
    analyses = {}
    for name, value in (project["analyses"] or {}).items():
        if isinstance(value, dict):
            if value.get("units"):
                project["analysis_units"][name] = dict(value["units"])
            if value.get("temperature"):
                project["analysis_temperature"][name] = value["temperature"]
            value = value.get("file", "")
        analyses[name] = _absolute(value, root)
    project["analyses"] = analyses
    project["material_library"] = _absolute(project.get("material_library"), root)
    for key, empty in (("materials", {}), ("loads", {}), ("regions", []), ("fatigue_events", [])):
        if project[key] is None:
            project[key] = empty
    notices = _convert_legacy(project)
    notices += _convert_temperature_source(project, legacy_temperature)
    return project, notices


def _convert_temperature_source(project: dict, legacy) -> list[str]:
    """The former global ``temperature.source`` as per-analysis sources; exact.

    It applied to every structural analysis, so each other ``.rst`` gets it explicitly
    (visible and editable instead of silently shared).
    """
    source = legacy.get("source") if isinstance(legacy, dict) else None
    if not source:
        return []
    store = project["analysis_temperature"]
    converted = [
        name
        for name, path in project["analyses"].items()
        if name != source and str(path).lower().endswith(".rst") and name not in store
    ]
    for name in converted:
        store[name] = source
    if not converted:
        return ["temperature.source removed: each analysis reads its own temperatures"]
    return [
        (
            f"temperature.source {source!r} converted to per-analysis temperature sources "
            f"of {', '.join(converted)}; check them on the Analyses page (by default each "
            "analysis reads its own BFE temperatures)"
        )
    ]


def to_raw(project: dict, yaml_path: Path) -> dict[str, Any]:
    """The YAML mapping for ``project`` saved at ``yaml_path`` (relative paths, no empties)."""
    root = Path(yaml_path).resolve().parent
    raw: dict[str, Any] = {}
    for key in SECTION_ORDER:
        value = copy.deepcopy(project.get(key))
        if key == "analyses":
            units = project.get("analysis_units") or {}
            sources = project.get("analysis_temperature") or {}
            entries = {}
            for name, path in value.items():
                entry = {"file": _relative(path, root)}
                if units.get(name):
                    entry["units"] = dict(units[name])
                if sources.get(name):
                    entry["temperature"] = sources[name]
                entries[name] = entry if len(entry) > 1 else entry["file"]
            value = entries
        elif key == "material_library":
            value = _relative(value, root)
        if key in ("material_library", "regions") and not value:
            continue
        raw[key] = value
    return raw


def to_yaml(project: dict, yaml_path: Path) -> str:
    return yaml.safe_dump(
        to_raw(project, yaml_path),
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=None,
        width=100,
    )


def validate(project: dict, yaml_path: Path) -> Config | str:
    """The validated :class:`Config`, or the first configuration error as text."""
    try:
        return Config.from_dict(to_raw(project, yaml_path), Path(yaml_path))
    except (ConfigurationError, TypeError, AttributeError, KeyError) as error:
        return str(error) or type(error).__name__


def save_project(project: dict, path: Path) -> None:
    """Write the project as YAML (atomically). Invalid projects can be saved as drafts."""
    path = Path(path).resolve()
    text = to_yaml(project, path)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def set_analysis_units(project: dict, name: str, units: dict[str, str | None]) -> None:
    """Declare fallback units for ``name``; empty values mean "as recorded in the result"."""
    declared = {quantity: unit for quantity, unit in units.items() if unit}
    store = project.setdefault("analysis_units", {})
    if declared:
        store[name] = declared
    else:
        store.pop(name, None)


def units_text(project: dict, name: str) -> str:
    declared = (project.get("analysis_units") or {}).get(name)
    if not declared:
        return "from result"
    return ", ".join(f"{quantity} {unit}" for quantity, unit in declared.items())


def set_analysis_temperature(project: dict, name: str, source: str | None) -> None:
    """Read the temperatures of ``name`` from ``source``; empty or itself means its own."""
    store = project.setdefault("analysis_temperature", {})
    if source and source != name:
        store[name] = source
    else:
        store.pop(name, None)


def temperature_text(project: dict, name: str) -> str:
    source = (project.get("analysis_temperature") or {}).get(name)
    if source:
        return f"from {source}"
    return "own" if str(project["analyses"].get(name, "")).lower().endswith(".rst") else "—"


def unique_name(base: str, taken) -> str:
    taken = set(taken)
    if base not in taken:
        return base
    index = 2
    while f"{base}_{index}" in taken:
        index += 1
    return f"{base}_{index}"


def rename_load(project: dict, old: str, new: str) -> None:
    """Rename a load, keeping its position and updating every event that uses it."""
    if old == new:
        return
    if new in project["loads"]:
        raise ConfigurationError(f"A load named {new!r} already exists")
    project["loads"] = {
        new if name == old else name: value for name, value in project["loads"].items()
    }
    for event in project["fatigue_events"]:
        event["loads"] = [new if name == old else name for name in event.get("loads", [])]


def remove_load(project: dict, name: str) -> None:
    """Remove a load and drop it from every event."""
    project["loads"].pop(name, None)
    for event in project["fatigue_events"]:
        event["loads"] = [other for other in event.get("loads", []) if other != name]


def load_analyses(load) -> list[str]:
    """Analyses named by the well-formed set terms of a (possibly unfinished) load."""
    terms = (
        [parse_term(text) for text in (load.get("sets") or [])] if isinstance(load, dict) else []
    )
    return list(dict.fromkeys(term.analysis for term in terms if term is not None))


def rename_sets(load: dict, old: str, new: str) -> None:
    """Point every set term of ``load`` on analysis ``old`` at ``new``."""
    renamed = []
    for text in load.get("sets", []):
        term = parse_term(text)
        if term is not None and term.analysis == old:
            text = format_term(type(term)(term.sign, new, term.step))
        renamed.append(text)
    load["sets"] = renamed


def rename_analysis(project: dict, old: str, new: str) -> None:
    if old == new:
        return
    if new in project["analyses"]:
        raise ConfigurationError(f"An analysis named {new!r} already exists")
    project["analyses"] = {
        new if name == old else name: value for name, value in project["analyses"].items()
    }
    units = project.setdefault("analysis_units", {})
    if old in units:
        units[new] = units.pop(old)
    sources = project.setdefault("analysis_temperature", {})
    project["analysis_temperature"] = {
        new if name == old else name: new if source == old else source
        for name, source in sources.items()
    }
    for load in project["loads"].values():
        rename_sets(load, old, new)


def remove_analysis(project: dict, name: str) -> None:
    project["analyses"].pop(name, None)
    project.setdefault("analysis_units", {}).pop(name, None)
    # Analyses that read this one's temperatures keep the now unknown name, so validation
    # reports them instead of silently switching them to their own temperatures.
    project.setdefault("analysis_temperature", {}).pop(name, None)
