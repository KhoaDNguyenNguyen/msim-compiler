import math
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, List, Tuple
from .document import MS14Document
from .eda_engine import AffineGrid2D, NetConnectionSpec, PartPlacementSpec
from .filter_synthesizer import HighOrderButterworthSynthesizer

@dataclass
class SynthesizedTopologyPlan:
    title: str
    banner_lines: List[str]
    parts: List[PartPlacementSpec]
    nets: List[NetConnectionSpec]
    spice_overrides: Dict[str, str] = field(default_factory=dict)
    fc_hz: float = 10000.0

class ICircuitSynthesizer(ABC):
    @abstractmethod
    def generate_topology(self, **kwargs) -> SynthesizedTopologyPlan:
        raise NotImplementedError

class ButterworthFilterPlugin(ICircuitSynthesizer):
    def __init__(self, base_resistance_ohm: float = 1000.0):
        self.base_r = base_resistance_ohm
        self.math_synth = HighOrderButterworthSynthesizer(base_resistance_ohm=base_resistance_ohm)

    def generate_topology(self, **kwargs) -> SynthesizedTopologyPlan:
        order = int(kwargs.get("order", 7))
        fc_hz = float(kwargs.get("fc_hz", 12000.0))
        tones_str = str(kwargs.get("tones_str", "Native XFG1 Source"))

        wc = 2.0 * math.pi * float(fc_hz)
        r0 = self.base_r

        parts: List[PartPlacementSpec] = []
        nets: List[NetConnectionSpec] = []
        gnd_terms: List[Tuple[str, str]] = []
        stage_summaries: List[str] = []

        pitch_x = 90
        total_w = (order + 1) * pitch_x
        col_x = AffineGrid2D.snap9(max(261, min(432, 585 - total_w // 2)))
        row_series_y = 243
        row_shunt_y = 315

        rs_val = MS14Document.format_si(r0)
        parts.append(PartPlacementSpec("R1", "RESISTOR_VIRTUAL", rs_val, x=col_x, y=row_series_y, rotation=0))
        nets.append(NetConnectionSpec("VIN", [("R1", "1")]))
        prev_series_ref = "R1"
        prev_series_pin = "2"
        col_x += pitch_x

        l_idx = 1
        c_idx = 1
        node_idx = 1

        for k in range(1, order + 1):
            gk = 2.0 * math.sin((2.0 * k - 1.0) * math.pi / (2.0 * order))
            if k % 2 == 1:
                c_farad = gk / (wc * r0)
                c_val = MS14Document.format_si(c_farad)
                c_ref = f"C{c_idx}"
                c_idx += 1
                parts.append(PartPlacementSpec(c_ref, "CAPACITOR_VIRTUAL", c_val, x=col_x, y=row_shunt_y, rotation=90))
                gnd_terms.append((c_ref, "2"))
                stage_summaries.append(f"Element {k} (Shunt Cap): {c_ref}={c_val}F")

                if k == order:
                    rl_ref = "R2"
                    parts.append(PartPlacementSpec(rl_ref, "RESISTOR_VIRTUAL", rs_val, x=col_x + pitch_x, y=row_shunt_y, rotation=90))
                    gnd_terms.append((rl_ref, "2"))
                    nets.append(NetConnectionSpec("VOUT", [(prev_series_ref, prev_series_pin), (c_ref, "1"), (rl_ref, "1")]))
                else:
                    l_next_gk = 2.0 * math.sin((2.0 * (k + 1) - 1.0) * math.pi / (2.0 * order))
                    l_henry = (l_next_gk * r0) / wc
                    l_val = MS14Document.format_si(l_henry)
                    l_ref = f"L{l_idx}"
                    l_idx += 1
                    col_x += pitch_x
                    parts.append(PartPlacementSpec(l_ref, "INDUCTOR_VIRTUAL", l_val, x=col_x, y=row_series_y, rotation=0))
                    nets.append(NetConnectionSpec(f"N_LAD_{node_idx}", [(prev_series_ref, prev_series_pin), (c_ref, "1"), (l_ref, "1")]))
                    node_idx += 1
                    prev_series_ref = l_ref
                    prev_series_pin = "2"
                    stage_summaries.append(f"Element {k+1} (Series Ind): {l_ref}={l_val}H")
                    col_x += pitch_x
            else:
                if k == order:
                    rl_ref = "R2"
                    parts.append(PartPlacementSpec(rl_ref, "RESISTOR_VIRTUAL", rs_val, x=col_x, y=row_shunt_y, rotation=90))
                    gnd_terms.append((rl_ref, "2"))
                    nets.append(NetConnectionSpec("VOUT", [(prev_series_ref, prev_series_pin), (rl_ref, "1")]))

        if gnd_terms:
            nets.append(NetConnectionSpec("0", gnd_terms))

        banner_lines = [
            f"Topology: {order}th-Order Pure Butterworth LC Ladder | Cutoff fc = {fc_hz/1000.0:.2f} kHz | Roll-off = -{20*order} dB/dec",
            f"Source Mode: {tones_str} | 100% Native Vanilla SPICE Components (Copy-Paste Ready)"
        ] + stage_summaries

        return SynthesizedTopologyPlan(
            title=f"=== SYNTHESIZED {order}TH-ORDER BUTTERWORTH FILTER SPECIFICATION ===",
            banner_lines=banner_lines,
            parts=parts,
            nets=nets,
            spice_overrides={},
            fc_hz=fc_hz
        )

class RLCLadderBJTPlugin(ICircuitSynthesizer):
    def generate_topology(self, **kwargs) -> SynthesizedTopologyPlan:
        fc_hz = float(kwargs.get("fc_hz", 2100.0))
        parts = [
            PartPlacementSpec("R10", "RESISTOR_VIRTUAL",  "150",     x=297, y=216, rotation=0),
            PartPlacementSpec("L1",  "INDUCTOR_VIRTUAL",  "18mH",    x=396, y=216, rotation=0),
            PartPlacementSpec("L2",  "INDUCTOR_VIRTUAL",  "27mH",    x=495, y=216, rotation=0),
            PartPlacementSpec("L3",  "INDUCTOR_VIRTUAL",  "18mH",    x=594, y=216, rotation=0),
            PartPlacementSpec("R11", "RESISTOR_VIRTUAL",  "180",     x=693, y=216, rotation=0),
            PartPlacementSpec("R12", "RESISTOR_VIRTUAL",  "150",     x=792, y=216, rotation=0),
            PartPlacementSpec("C10", "CAPACITOR_VIRTUAL", "470nF",   x=432, y=297, rotation=90),
            PartPlacementSpec("C11", "CAPACITOR_VIRTUAL", "680nF",   x=531, y=297, rotation=90),
            PartPlacementSpec("C12", "CAPACITOR_VIRTUAL", "470nF",   x=630, y=297, rotation=90),
            PartPlacementSpec("C13", "CAPACITOR_VIRTUAL", "220nF",   x=729, y=297, rotation=90),
            PartPlacementSpec("D1",  "D1N4148",           "1N4148",  x=792, y=306, rotation=0),
            PartPlacementSpec("R14", "RESISTOR_VIRTUAL",  "22k",     x=297, y=414, rotation=0),
            PartPlacementSpec("Q1",  "2N2222A",           "2N2222A", x=396, y=405, rotation=0),
            PartPlacementSpec("C14", "CAPACITOR_VIRTUAL", "100nF",   x=495, y=405, rotation=0),
            PartPlacementSpec("Q2",  "2N2222A",           "2N2222A", x=594, y=405, rotation=0),
            PartPlacementSpec("D2",  "D1N4148",           "1N4148",  x=693, y=414, rotation=0),
            PartPlacementSpec("R13", "RESISTOR_VIRTUAL",  "4.7k",    x=792, y=414, rotation=0),
            PartPlacementSpec("R15", "RESISTOR_VIRTUAL",  "10k",     x=495, y=495, rotation=0)
        ]
        nets = [
            NetConnectionSpec("VIN",        [("R10", "1"), ("R14", "1")]),
            NetConnectionSpec("N_LC1",      [("R10", "2"), ("L1", "1")]),
            NetConnectionSpec("N_LC2",      [("L1", "2"),  ("L2", "1"),  ("C10", "1")]),
            NetConnectionSpec("N_LC3",      [("L2", "2"),  ("L3", "1"),  ("C11", "1")]),
            NetConnectionSpec("N_LC4",      [("L3", "2"),  ("R11", "1"), ("C12", "1")]),
            NetConnectionSpec("N_RC5",      [("R11", "2"), ("R12", "1"), ("C13", "1")]),
            NetConnectionSpec("VOUT",       [("R12", "2"), ("D1", "K"),  ("R13", "2")]),
            NetConnectionSpec("N_Q1_BASE",  [("R14", "2"), ("Q1", "B")]),
            NetConnectionSpec("N_Q1_COLL",  [("Q1", "C"),  ("C14", "1")]),
            NetConnectionSpec("N_Q2_BASE",  [("C14", "2"), ("Q2", "B")]),
            NetConnectionSpec("N_Q2_COLL",  [("Q2", "C"),  ("D2", "A")]),
            NetConnectionSpec("N_Q1_EMIT",  [("Q1", "E"),  ("R15", "1")]),
            NetConnectionSpec("0", [
                ("C10", "2"), ("C11", "2"), ("C12", "2"), ("C13", "2"),
                ("D1", "A"), ("R13", "1"), ("D2", "K"), ("Q2", "E"), ("R15", "2")
            ])
        ]
        return SynthesizedTopologyPlan(
            title="=== ULTRA-COMPLEX RLC LADDER + DUAL-BJT 2N2222A + 1N4148 CLAMP MATRIX ===",
            banner_lines=[
                f"Row 1 (RLC Ladder): R10=150, L1=18mH, L2=27mH, L3=18mH, R11=180, R12=150 | fc = {fc_hz/1000.0:.2f} kHz",
                "Row 2 (Shunt Caps): C10=470nF, C11=680nF, C12=470nF, C13=220nF, D1=1N4148",
                "Row 3 (Active BJT): R14=22k, Q1=2N2222A, C14=100nF, Q2=2N2222A, D2=1N4148, R13=4.7k, R15=10k"
            ],
            parts=parts,
            nets=nets,
            fc_hz=fc_hz
        )

class SynthesizerRegistry:
    _plugins: Dict[str, ICircuitSynthesizer] = {
        "butterworth": ButterworthFilterPlugin(),
        "rlc_bjt_matrix": RLCLadderBJTPlugin()
    }

    @classmethod
    def register(cls, name: str, plugin: ICircuitSynthesizer) -> None:
        cls._plugins[name.lower()] = plugin

    @classmethod
    def get(cls, name: str) -> ICircuitSynthesizer:
        return cls._plugins[name.lower()]
