from dataclasses import dataclass, field
from typing import List, Tuple, Optional

@dataclass
class ComponentSpec:
    refdes: str
    ci_id: str
    spice_value: str
    display_label: str
    x: float
    y: float

@dataclass
class WireSegment:
    index: int
    points: List[Tuple[float, float]] = field(default_factory=list)

@dataclass
class HarmonicTone:
    freq_hz: float
    amplitude_vp: float

@dataclass
class FuncGenConfig:
    freq_hz: float
    amplitude_vp: float = 1.4
    duty_cycle: float = 50.0
    offset_v: float = 0.0
    harmonics: List[HarmonicTone] = field(default_factory=list)
    injection_target: Optional[str] = None

@dataclass
class ScopeConfig:
    timebase_scale_s: float
    ch_a_scale_v: float = 1.0
    ch_b_scale_v: float = 1.0
    ch_a_pos: float = 0.0
    ch_b_pos: float = 0.0
    trigger_mode: int = 3

@dataclass
class BodeConfig:
    cursor_freq_hz: float
    f_start_hz: float = 20.0
    f_stop_hz: float = 200000.0
    db_min: float = -100.0
    db_max: float = 10.0
    control_mode: int = 0
    horiz_log: int = 0
    vert_log: int = 0

@dataclass
class TektronixConfig:
    name: str
    mode: str = "time"
    ch1_on: int = 1
    ch2_on: int = 1
    ch3_on: int = 0
    ch4_on: int = 0
    math_on: int = 0
    sec_per_div: str = "0.000025"
    ch1_volts: str = "1"
    ch1_pos: str = "2"
    ch2_volts: str = "1"
    ch2_pos: str = "-2"
    fft_source_ch: int = 0
    fft_hz_per_div: str = "5000"
    fft_center_hz: str = "25000"
    fft_db_per_div: str = "20"
    fft_db_pos: str = "20"
    menu_group_id: int = 9

@dataclass
class WindowRect:
    name: str
    left: int
    top: int
    width: Optional[int] = None
    height: Optional[int] = None

@dataclass
class BiquadStageSpec:
    stage_index: int
    order: int
    q_factor: float
    r1_ohm: float
    r2_ohm: float
    c1_farad: float
    c2_farad: float

@dataclass
class HighOrderFilterDesign:
    order: int
    fc_hz: float
    stages: List[BiquadStageSpec]
    pre_filter_spice_netlist: str
    final_stage: BiquadStageSpec
