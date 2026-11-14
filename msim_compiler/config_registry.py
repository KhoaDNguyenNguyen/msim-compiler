import json
import os
from dataclasses import dataclass, field
from typing import Dict, List, Tuple

@dataclass(frozen=True)
class GridRoutingConfig:
    grid_pitch: int = 9
    grid_width: int = 180
    grid_height: int = 120
    step_cost: int = 10
    bend_penalty: int = 45
    cross_penalty: int = 160
    logistic_capacity: float = 4.0
    logistic_steepness: float = 1.35
    logistic_max_penalty: float = 240.0

@dataclass(frozen=True)
class SimulationProfileSpec:
    name: str
    f_start_hz: float = 20.0
    f_stop_hz: float = 200000.0
    db_min: float = -100.0
    db_max: float = 10.0
    default_vim_vp: float = 1.4
    scope_steps: Tuple[float, ...] = (
        1e-9, 2e-9, 5e-9, 1e-8, 2e-8, 5e-8, 1e-7, 2e-7, 5e-7,
        1e-6, 2e-6, 5e-6, 1e-5, 2e-5, 5e-5, 1e-4, 2e-4, 5e-4,
        1e-3, 2e-3, 5e-3, 1e-2, 2e-2, 5e-2, 0.1, 0.2, 0.5, 1.0
    )
    tek_time_steps: Tuple[float, ...] = (
        1e-9, 2.5e-9, 5e-9, 1e-8, 2.5e-8, 5e-8, 1e-7, 2.5e-7, 5e-7,
        1e-6, 2.5e-6, 5e-6, 1e-5, 2.5e-5, 5e-5, 1e-4, 2.5e-4, 5e-4,
        1e-3, 2.5e-3, 5e-3, 1e-2, 2.5e-2, 5e-2, 0.1, 0.25, 0.5, 1.0
    )
    tek_fft_steps: Tuple[int, ...] = (50, 100, 250, 500, 1000, 2500, 5000, 10000, 25000, 50000, 100000)

@dataclass
class EDAConfigRegistry:
    routing: GridRoutingConfig = field(default_factory=GridRoutingConfig)
    sim_profile: SimulationProfileSpec = field(default_factory=lambda: SimulationProfileSpec("Standard_AC_Transient"))
    part_spice_defaults: Dict[str, str] = field(default_factory=lambda: {
        "RESISTOR_VIRTUAL": "r%p %t1 %t2 \x231",
        "CAPACITOR_VIRTUAL": "c%p %t1 %t2 \x231",
        "INDUCTOR_VIRTUAL": "l%p %t1 %t2 \x231"
    })
    si_multipliers: Dict[str, float] = field(default_factory=lambda: {
        "T": 1e12, "G": 1e9, "M": 1e6, "MEG": 1e6, "k": 1e3, "K": 1e3,
        "": 1.0, "m": 1e-3, "u": 1e-6, "n": 1e-9, "p": 1e-12, "f": 1e-15
    })

    @classmethod
    def load(cls, config_json_path: str = "") -> "EDAConfigRegistry":
        reg = cls()
        if config_json_path and os.path.exists(config_json_path):
            with open(config_json_path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            if "routing" in raw:
                reg.routing = GridRoutingConfig(**raw["routing"])
        return reg

GLOBAL_CONFIG = EDAConfigRegistry.load()
