"""Validated configuration: P/Q loads (signed set combinations) and fatigue events (load sums)."""

import math
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from cassy.auxiliary.types import LoadTerm


class ConfigurationError(ValueError):
    pass


class _UniqueLoader(yaml.SafeLoader):
    def construct_mapping(self, node, deep=False):
        result = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if key in result:
                raise ConfigurationError(
                    f"Duplicate YAML key {key!r} at line {key_node.start_mark.line + 1}"
                )
            result[key] = self.construct_object(value_node, deep=deep)
        return result


def _mapping(value, where: str, required=(), optional=()) -> dict:
    if not isinstance(value, dict):
        raise ConfigurationError(f"{where} must be a mapping")
    missing = set(required) - value.keys()
    unknown = value.keys() - set(required) - set(optional)
    problems = []
    if missing:
        problems.append(f"missing keys {sorted(map(str, missing))}")
    if unknown:
        problems.append(f"unknown keys {sorted(map(str, unknown))}")
    if problems:
        raise ConfigurationError(f"{where}: {', '.join(problems)}")
    return value


def _name(value, where: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]*", value):
        raise ConfigurationError(
            f"{where} must be a non-empty identifier (letters, digits, _, ., -)"
        )
    return value


def _text(value, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigurationError(f"{where} must be a non-empty string")
    return value


def _number(value, where: str, minimum: float = 0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigurationError(f"{where} must be a number")
    result = float(value)
    if not math.isfinite(result) or result < minimum:
        raise ConfigurationError(f"{where} must be finite and >= {minimum}")
    return result


def _path(value, root: Path, where: str) -> Path:
    path = Path(_text(value, where))
    return (root / path).resolve()


# Conversion factors to SI (Pa, m); temperatures convert with an offset (see reader).
STRESS_UNITS = {"Pa": 1.0, "kPa": 1e3, "MPa": 1e6, "GPa": 1e9}
LENGTH_UNITS = {"m": 1.0, "mm": 0.001, "cm": 0.01, "in": 0.0254, "ft": 0.3048}
TEMPERATURE_UNITS = ("degC", "C", "K", "degF")
TIME_UNITS = ("s",)
UNIT_CHOICES = {
    "length": tuple(LENGTH_UNITS),
    "stress": tuple(STRESS_UNITS),
    "temperature": TEMPERATURE_UNITS,
    "time": TIME_UNITS,
}


@dataclass(frozen=True)
class AnalysisUnits:
    """Units declared by the analyst for a result file that records none.

    Each entry is a fallback: it is used only where DPF reports an empty unit, and a
    declaration that contradicts a unit recorded in the result is an error.
    """

    length: str | None = None
    stress: str | None = None
    temperature: str | None = None
    time: str | None = None

    def declared(self) -> dict[str, str]:
        return {name: getattr(self, name) for name in UNIT_CHOICES if getattr(self, name)}


def _units(value, where: str) -> AnalysisUnits:
    data = _mapping(value, where, optional=tuple(UNIT_CHOICES))
    for quantity, unit in data.items():
        if unit not in UNIT_CHOICES[quantity]:
            raise ConfigurationError(
                f"{where}.{quantity} must be one of {list(UNIT_CHOICES[quantity])}, got {unit!r}"
            )
    return AnalysisUnits(**data)


TERM = re.compile(r"([+-])\s*([A-Za-z0-9_][A-Za-z0-9_.-]*)\s*:\s*(\d+)")


def parse_term(text) -> LoadTerm | None:
    """``"+analysis:set"`` as a CASSY :class:`LoadTerm`; ``None`` if malformed."""
    match = TERM.fullmatch(text.strip()) if isinstance(text, str) else None
    return None if match is None else LoadTerm(match[1], match[2], int(match[3]))


def format_term(term: LoadTerm) -> str:
    return f"{term.sign}{term.analysis}:{term.step}"


@dataclass(frozen=True)
class Load:
    """A primary (P) or secondary (Q) stress range: ``scale * sum(sign * stress(set))``.

    ``LoadTerm.step`` holds the one-based DPF result-set ID.
    """

    classification: str
    terms: tuple[LoadTerm, ...]
    scale: float = 1.0

    @property
    def analyses(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(term.analysis for term in self.terms))


@dataclass(frozen=True)
class FatigueEvent:
    """A reference event: the sum of one or more loads, with its number of cycles."""

    id: str
    ncycles: float
    loads: tuple[str, ...]


@dataclass(frozen=True)
class Region:
    named_selection: str
    welding_f: float = 1
    dpa: float = 0


def _temperature_sources(
    legacy, loads: dict[str, Load], declared: dict[str, str]
) -> dict[str, str]:
    """Temperature source of every structural analysis used by a load.

    By default each analysis reads its own BFE temperatures, so every set in an event's
    temperature envelope uses the temperature of the run it comes from;
    ``analyses.<name>.temperature`` names another analysis (e.g. a thermal ``.rth``).
    The former global ``temperature: {source: X}`` is only unambiguous with a single
    structural analysis: with several, sets of one run would silently take the
    temperature of another run's set at the same time, so it is rejected.
    """
    used = list(dict.fromkeys(name for load in loads.values() for name in load.analyses))
    if legacy is not None:
        data = _mapping(legacy, "temperature", ("source",))
        source = _text(data["source"], "temperature.source")
        if len(used) != 1:
            raise ConfigurationError(
                f"temperature.source is ambiguous with several structural analyses ({', '.join(used)}): "
                f"each analysis now reads its own BFE temperatures by default; to read another "
                f"analysis, declare analyses.<name>: {{file: ..., temperature: {source}}} "
                "(the GUI converts old files)"
            )
        if used[0] in declared and declared[used[0]] != source:
            raise ConfigurationError(
                f"temperature.source {source!r} contradicts analyses.{used[0]}.temperature "
                f"{declared[used[0]]!r}; keep only the latter"
            )
        declared = {**declared, used[0]: source}
    return {name: declared.get(name, name) for name in used}


@dataclass(frozen=True)
class Config:
    source: Path
    code: str
    analyses: dict[str, Path]
    # Structural analysis used by a load -> analysis whose temperatures its sets use.
    temperature_sources: dict[str, str]
    materials: dict[int, str]
    loads: dict[str, Load]
    fatigue_events: tuple[FatigueEvent, ...]
    regions: tuple[Region, ...]
    material_library: Path | None
    raw: dict
    material_selections: dict[int, str] = field(default_factory=dict)
    analysis_units: dict[str, AnalysisUnits] = field(default_factory=dict)

    def event_terms(self, event: FatigueEvent) -> tuple[LoadTerm, ...]:
        """Every signed set of every load of ``event`` (for reads and the temperature)."""
        return tuple(term for name in event.loads for term in self.loads[name].terms)

    @classmethod
    def read(cls, source: Path) -> "Config":
        source = source.resolve()
        with source.open(encoding="utf-8") as stream:
            raw = yaml.load(stream, Loader=_UniqueLoader)
        return cls.from_dict(raw, source)

    @classmethod
    def from_dict(cls, raw, source: Path) -> "Config":
        raw = _mapping(
            raw,
            "configuration",
            ("code", "analyses", "materials", "loads", "fatigue_events"),
            ("temperature", "regions", "material_library"),
        )
        source = source.resolve()
        root = source.parent
        code = _text(raw["code"], "code")
        if code not in ("SDC-IC", "RCC-MRx", "RCC-MR"):
            raise ConfigurationError(f"Unsupported fatigue code {code!r}")
        analyses_data = raw["analyses"]
        if not isinstance(analyses_data, dict) or not analyses_data:
            raise ConfigurationError("analyses must be a non-empty mapping")
        analyses = {}
        analysis_units = {}
        declared_sources = {}
        for name, entry in analyses_data.items():
            name = _name(name, "analysis name")
            path = entry
            if isinstance(entry, dict):
                entry = _mapping(entry, f"analysis {name}", ("file",), ("units", "temperature"))
                path = entry["file"]
                if entry.get("units"):
                    analysis_units[name] = _units(entry["units"], f"analysis {name}.units")
                if "temperature" in entry:
                    declared_sources[name] = _text(
                        entry["temperature"], f"analysis {name}.temperature"
                    )
            analyses[name] = _path(path, root, f"analysis {name}")
            if analyses[name].suffix.lower() not in (".rst", ".rth"):
                raise ConfigurationError(f"analysis {name} must be an .rst or .rth file")
        for name, thermal in declared_sources.items():
            if analyses[name].suffix.lower() != ".rst":
                raise ConfigurationError(
                    f"analysis {name}.temperature: only a structural .rst takes a temperature source"
                )
            if thermal not in analyses:
                raise ConfigurationError(
                    f"analysis {name}.temperature: unknown analysis {thermal!r}"
                )
        material_data = raw["materials"]
        if not isinstance(material_data, dict) or not material_data:
            raise ConfigurationError("materials must be a non-empty mapping")
        material_map = {}
        material_selections = {}
        by_id = all(
            isinstance(key, int) and not isinstance(key, bool) and key > 0 for key in material_data
        )
        by_selection = all(
            isinstance(key, str) and key.strip() and not key.strip().isdigit()
            for key in material_data
        )
        if not by_id and not by_selection:
            raise ConfigurationError(
                "materials must use either positive integer IDs or non-numeric named selection names, not both"
            )
        for index, (key, name) in enumerate(material_data.items(), 1):
            material_id = key if by_id else index
            if by_selection:
                material_selections[material_id] = _text(key, "material named selection")
            material_map[material_id] = _text(name, f"material {material_id}")
        load_data = raw["loads"]
        if not isinstance(load_data, dict) or not load_data:
            raise ConfigurationError("loads must be a non-empty mapping")
        loads = {}
        for name, data in load_data.items():
            name = _name(name, "load name")
            if isinstance(data, dict) and ("analysis" in data or "set" in data):
                raise ConfigurationError(
                    f"load {name}: 'analysis'/'set' were replaced by a signed set list, e.g. "
                    "sets: [+struct:2, -struct:1] (the GUI converts old files)"
                )
            data = _mapping(data, f"load {name}", ("classification", "sets"), ("scale",))
            classification = data["classification"]
            if classification not in ("P", "Q"):
                raise ConfigurationError(f"load {name}.classification must be P or Q")
            sets = data["sets"]
            if not isinstance(sets, list) or not sets:
                raise ConfigurationError(
                    f"load {name}.sets must be a non-empty list of "
                    "signed result sets such as +struct:2"
                )
            terms = []
            for text in sets:
                term = parse_term(text)
                if term is None:
                    raise ConfigurationError(
                        f"load {name}.sets: expected +analysis:set or -analysis:set, got {text!r}"
                    )
                if (
                    term.analysis not in analyses
                    or analyses[term.analysis].suffix.lower() != ".rst"
                ):
                    raise ConfigurationError(
                        f"load {name}: {term.analysis!r} is not a structural .rst analysis"
                    )
                if term.step <= 0:
                    raise ConfigurationError(
                        f"load {name}: result set IDs are one-based positive integers"
                    )
                if any(
                    (term.analysis, term.step) == (other.analysis, other.step) for other in terms
                ):
                    raise ConfigurationError(
                        f"load {name}: {term.analysis}:{term.step} appears more than once"
                    )
                terms.append(term)
            scale = data.get("scale", 1)
            if (
                isinstance(scale, bool)
                or not isinstance(scale, (int, float))
                or not math.isfinite(scale)
                or scale == 0
            ):
                raise ConfigurationError(f"load {name}.scale must be a finite non-zero number")
            loads[name] = Load(classification, tuple(terms), float(scale))
        temperature_sources = _temperature_sources(raw.get("temperature"), loads, declared_sources)
        for name, thermal in temperature_sources.items():
            if thermal not in analyses:
                raise ConfigurationError(f"Unknown temperature source {thermal!r} of {name}")

        events_data = raw["fatigue_events"]
        if not isinstance(events_data, list) or not events_data:
            raise ConfigurationError("fatigue_events must be a non-empty list")
        events = []
        event_ids = set()
        for data in events_data:
            if isinstance(data, dict) and {"delta", "state_a", "state_b"} & data.keys():
                raise ConfigurationError(
                    f"fatigue event {data.get('id')!r}: an event is now a sum of loads "
                    "(loads: [thermal, pressure]); signed set combinations belong in the "
                    "loads' sets (the GUI converts old files)"
                )
            data = _mapping(data, "fatigue event", ("id", "ncycles", "loads"))
            event_id = _name(data["id"], "event id")
            if event_id in event_ids:
                raise ConfigurationError(f"Duplicate fatigue event ID {event_id!r}")
            event_ids.add(event_id)
            ncycles = _number(data["ncycles"], f"{event_id}.ncycles")
            names = data["loads"]
            if not isinstance(names, list) or not names:
                raise ConfigurationError(f"{event_id}.loads must be a non-empty list of load names")
            for load in names:
                if not isinstance(load, str) or load not in loads:
                    raise ConfigurationError(f"{event_id}.loads: unknown load {load!r}")
            if len(set(names)) != len(names):
                raise ConfigurationError(f"{event_id}.loads: a load is listed more than once")
            events.append(FatigueEvent(event_id, ncycles, tuple(names)))
        regions_data = raw.get("regions", [])
        if not isinstance(regions_data, list):
            raise ConfigurationError("regions must be a list")
        regions = []
        selections = set()
        for data in regions_data:
            data = _mapping(data, "region", ("named_selection",), ("welding_f", "dpa"))
            selection = _text(data["named_selection"], "named_selection")
            if selection in selections:
                raise ConfigurationError(f"Duplicate region selection {selection!r}")
            selections.add(selection)
            factor = _number(data.get("welding_f", 1), f"{selection}.welding_f", 1)
            regions.append(
                Region(selection, factor, _number(data.get("dpa", 0), f"{selection}.dpa"))
            )
        library = raw.get("material_library")
        library_path = None if library is None else _path(library, root, "material_library")
        return cls(
            source,
            code,
            analyses,
            temperature_sources,
            material_map,
            loads,
            tuple(events),
            tuple(regions),
            library_path,
            raw,
            material_selections,
            analysis_units,
        )
