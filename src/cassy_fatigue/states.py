"""Event stress ranges (sum of P/Q loads) and temperature envelopes."""

from dataclasses import dataclass

import numpy as np

from cassy_fatigue.config import Config, FatigueEvent
from cassy_fatigue.reader import DPFReader, FloatArray


@dataclass(frozen=True)
class State:
    total: FloatArray
    primary: FloatArray


def load_range(reader: DPFReader, config: Config, name: str, material_id: int) -> FloatArray:
    """``scale * sum(sign * stress(set))`` of load ``name`` at the material's nodes."""
    load = config.loads[name]
    shape = (len(reader.regions[material_id].node_ids), 6)
    combined = np.zeros(shape)
    for term in load.terms:
        values = reader.stress(term.analysis, term.step)[material_id]
        if values.shape != shape:
            raise ValueError(
                f"Stress shape mismatch for load {name}: {values.shape}, expected {shape}"
            )
        if term.sign == "+":
            combined += values
        else:
            combined -= values
    return combined * load.scale


def build_state(reader: DPFReader, config: Config, event: FatigueEvent, material_id: int) -> State:
    """Total and primary stress ranges of ``event``: sums of its loads (P loads only)."""
    shape = (len(reader.regions[material_id].node_ids), 6)
    total = np.zeros(shape)
    primary = np.zeros(shape)
    for name in event.loads:
        contribution = load_range(reader, config, name, material_id)
        total += contribution
        if config.loads[name].classification == "P":
            primary += contribution
    return State(total, primary)


def event_temperature(
    reader: DPFReader, config: Config, event: FatigueEvent, material_id: int
) -> FloatArray:
    temperature = np.full(len(reader.regions[material_id].node_ids), -np.inf)
    # Each set's temperature comes from its own analysis's temperature source.
    keys = {reader.temperature_set(term.analysis, term.step) for term in config.event_terms(event)}
    for source, thermal_set in sorted(keys):
        values = reader.temperature(source, thermal_set)[material_id]
        if values.shape != temperature.shape:
            raise ValueError("Temperature shape does not match material node IDs")
        # Temperatures form an envelope, not a signed linear combination.
        temperature = np.maximum(temperature, values)
    return temperature
