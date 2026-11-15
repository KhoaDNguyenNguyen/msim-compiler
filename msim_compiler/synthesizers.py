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
    def __init__(self, base_resistance_ohm: float = 2000.0):
        self.math_synth = HighOrderButterworthSynthesizer(base_resistance_ohm=base_resistance_ohm)

    def generate_topology(self, **kwargs) -> SynthesizedTopologyPlan:
        order = int(kwargs.get("order", 7))
        fc_hz = float(kwargs.get("fc_hz", 12000.0))
        tones_str = str(kwargs.get("tones_str", "Swept AC/Sine"))

        design = self.math_synth.synthesize(order=order, fc_hz=fc_hz)
        parts: List[PartPlacementSpec] = []
        nets: List[NetConnectionSpec] = []
        gnd_terms: List[Tuple[str, str]] = []
        spice_overrides: Dict[str, str] = {}

        rc_pitch = 81
        bq_pitch = 144 if order <= 9 else 126
        dx_rb = 63 if order <= 9 else 54
        total_w = sum(rc_pitch if st.order == 1 else bq_pitch for st in design.stages)
        cx_canvas = 585
        col_x = AffineGrid2D.snap9(max(243, min(450, cx_canvas - total_w // 2)))
        row_fb_y = 162
        row_series_y = 243
        row_shunt_y = 315

        r_counter = 1
        c_counter = 1

        rin_ref = f"R{r_counter}"
        r_counter += 1
        parts.append(PartPlacementSpec(rin_ref, "RESISTOR_VIRTUAL", "100k", x=col_x - 45, y=row_shunt_y, rotation=90))
        gnd_terms.append((rin_ref, "2"))

        stage_out_terms: List[Tuple[str, str]] = [(rin_ref, "1")]
        stage_summaries: List[str] = []

        for idx, st in enumerate(design.stages):
            s_num = idx + 1
            is_last = (idx == len(design.stages) - 1)
            in_net_name = "VIN" if idx == 0 else f"N_ST{idx}_OUT"

            if st.order == 1:
                r_ref = f"R{r_counter}"
                r_counter += 1
                c_ref = f"C{c_counter}"
                c_counter += 1
                r_val = MS14Document.format_si(st.r1_ohm)
                c_val = MS14Document.format_si(st.c1_farad)

                parts.append(PartPlacementSpec(r_ref, "RESISTOR_VIRTUAL", r_val, x=col_x, y=row_series_y, rotation=0))
                parts.append(PartPlacementSpec(c_ref, "CAPACITOR_VIRTUAL", c_val, x=col_x + 36, y=row_shunt_y, rotation=90))

                stage_out_terms.append((r_ref, "1"))
                nets.append(NetConnectionSpec(in_net_name, stage_out_terms))

                stage_out_terms = [(r_ref, "2"), (c_ref, "1")]
                if is_last:
                    nets.append(NetConnectionSpec("VOUT", stage_out_terms))
                gnd_terms.append((c_ref, "2"))
                stage_summaries.append(f"Stage {s_num} (1st-Ord RC): {r_ref}={r_val}, {c_ref}={c_val}F")
                col_x += rc_pitch
            else:
                ra_ref = f"R{r_counter}"
                r_counter += 1
                rb_ref = f"R{r_counter}"
                r_counter += 1
                cfb_ref = f"C{c_counter}"
                c_counter += 1
                csh_ref = f"C{c_counter}"
                c_counter += 1

                ra_val = MS14Document.format_si(st.r1_ohm)
                rb_val = MS14Document.format_si(st.r2_ohm)
                csh_val = MS14Document.format_si(st.c1_farad)
                cfb_val = MS14Document.format_si(st.c2_farad)

                parts.append(PartPlacementSpec(ra_ref, "RESISTOR_VIRTUAL", ra_val, x=col_x, y=row_series_y, rotation=0))
                parts.append(PartPlacementSpec(rb_ref, "RESISTOR_VIRTUAL", rb_val, x=col_x + dx_rb, y=row_series_y, rotation=0))
                parts.append(PartPlacementSpec(cfb_ref, "CAPACITOR_VIRTUAL", cfb_val, x=col_x + dx_rb, y=row_fb_y, rotation=0))
                parts.append(PartPlacementSpec(csh_ref, "CAPACITOR_VIRTUAL", csh_val, x=col_x + dx_rb + 36, y=row_shunt_y, rotation=90))

                stage_out_terms.append((ra_ref, "1"))
                nets.append(NetConnectionSpec(in_net_name, stage_out_terms))
                nets.append(NetConnectionSpec(f"N_ST{s_num}_MID", [(ra_ref, "2"), (rb_ref, "1"), (cfb_ref, "1")]))

                stage_out_terms = [(rb_ref, "2"), (csh_ref, "1"), (cfb_ref, "2")]
                if is_last:
                    nets.append(NetConnectionSpec("VOUT", stage_out_terms))
                gnd_terms.append((csh_ref, "2"))

                spice_overrides[ra_ref] = "e_in_%p n_in_%p 0 %t1 0 1.0\r\nr%p n_in_%p %t2 \x231"
                spice_overrides[rb_ref] = f"r%p %t1 n_pls_%p \x231\r\nc_sk_%p n_pls_%p 0 {st.c1_farad:.6e}\r\nr_dc_%p n_pls_%p 0 100Meg\r\ne_sk_%p %t2 0 n_pls_%p 0 1.0"
                stage_summaries.append(f"Stage {s_num} (Biquad Q={st.q_factor:.3f}): {ra_ref}={rb_ref}={ra_val}, {cfb_ref}={cfb_val}F, {csh_ref}={csh_val}F")
                col_x += bq_pitch

        if gnd_terms:
            nets.append(NetConnectionSpec("0", gnd_terms))

        banner_lines = [
            f"Topology: {order}th-Order Butterworth Cascade | Cutoff fc = {fc_hz/1000.0:.2f} kHz | Roll-off = -{20*order} dB/dec",
            f"Multi-Tone Input (VIN): {tones_str}"
        ] + stage_summaries

        return SynthesizedTopologyPlan(
            title=f"=== SYNTHESIZED {order}TH-ORDER BUTTERWORTH ACTIVE FILTER SPECIFICATION ===",
            banner_lines=banner_lines,
            parts=parts,
            nets=nets,
            spice_overrides=spice_overrides,
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
