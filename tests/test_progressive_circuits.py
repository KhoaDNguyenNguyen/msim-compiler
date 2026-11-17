import os
import time
from typing import List
from msim_compiler import (
    get_logger,
    MS14Codec,
    MS14Document,
    MultisimDOMManipulator,
    MultisimSchematicBuilder,
    ICircuitSynthesizer,
    SynthesizedTopologyPlan,
    SynthesizerRegistry,
    PartPlacementSpec,
    NetConnectionSpec,
    HarmonicTone,
    WindowRect
)

logger = get_logger("PROGRESSIVE-GEN")

def make_multitone_resistor_spice(harmonics: List[HarmonicTone]) -> str:
    if len(harmonics) <= 1:
        return "r%p %t1 %t2 \x231\r\n"
    extra = harmonics[1:]
    lines: List[str] = []
    for idx, tone in enumerate(extra):
        n_in = "%t1" if idx == 0 else f"n_mt_{idx}_%p"
        n_out = f"n_mt_{idx + 1}_%p"
        f_sp = tone.freq_hz
        lines.append(f"v_mt_{idx + 1}_%p {n_in} {n_out} dc 0 ac 0 sin(0 {tone.amplitude_vp:.6f} {f_sp:.12e} 0)")
    last_node = f"n_mt_{len(extra)}_%p"
    lines.append(f"r%p {last_node} %t2 \x231")
    return "\r\n".join(lines) + "\r\n"

class Level1EasyRCFilter(ICircuitSynthesizer):
    def generate_topology(self, **kwargs) -> SynthesizedTopologyPlan:
        fc_hz = float(kwargs.get("fc_hz", 1590.0))
        tones = kwargs.get("harmonics", [])
        parts = [
            PartPlacementSpec("R1", "RESISTOR_VIRTUAL", "1k", x=414, y=243, rotation=0),
            PartPlacementSpec("C1", "CAPACITOR_VIRTUAL", "100n", x=504, y=315, rotation=90),
            PartPlacementSpec("R2", "RESISTOR_VIRTUAL", "100k", x=612, y=315, rotation=90)
        ]
        nets = [
            NetConnectionSpec("VIN", [("R1", "1")]),
            NetConnectionSpec("VOUT", [("R1", "2"), ("C1", "1"), ("R2", "1")]),
            NetConnectionSpec("0", [("C1", "2"), ("R2", "2")])
        ]
        return SynthesizedTopologyPlan(
            title="=== LEVEL 1 (EASY): 1ST-ORDER RC LOW-PASS FILTER & LOAD ===",
            banner_lines=[
                f"Topology: Passive RC Low-Pass Filter | Cutoff fc = {fc_hz:.1f} Hz | Slope = -20 dB/dec",
                "Components: R1 = 1 kOhm (Series), C1 = 100 nF (Shunt), R2 = 100 kOhm (Load)",
                "Input/Output: VIN On-Page Connector -> R1 -> VOUT On-Page Connector | Multi-Tone FFT Active"
            ],
            parts=parts,
            nets=nets,
            spice_overrides={},
            fc_hz=fc_hz
        )

class Level2MediumTwoStageDiodeClipper(ICircuitSynthesizer):
    def generate_topology(self, **kwargs) -> SynthesizedTopologyPlan:
        fc_hz = float(kwargs.get("fc_hz", 2000.0))
        tones = kwargs.get("harmonics", [])
        parts = [
            PartPlacementSpec("R1", "RESISTOR_VIRTUAL", "2.2k", x=396, y=243, rotation=0),
            PartPlacementSpec("C1", "CAPACITOR_VIRTUAL", "33n", x=486, y=315, rotation=90),
            PartPlacementSpec("R2", "RESISTOR_VIRTUAL", "4.7k", x=558, y=243, rotation=0),
            PartPlacementSpec("D1", "D1N4148", "1N4148", x=648, y=315, rotation=90),
            PartPlacementSpec("D2", "D1N4148", "1N4148", x=738, y=315, rotation=270),
            PartPlacementSpec("R3", "RESISTOR_VIRTUAL", "47k", x=828, y=315, rotation=90)
        ]
        nets = [
            NetConnectionSpec("VIN", [("R1", "1")]),
            NetConnectionSpec("N_STAGE1", [("R1", "2"), ("C1", "1"), ("R2", "1")]),
            NetConnectionSpec("VOUT", [("R2", "2"), ("D1", "A"), ("D2", "K"), ("R3", "1")]),
            NetConnectionSpec("0", [("C1", "2"), ("D1", "K"), ("D2", "A"), ("R3", "2")])
        ]
        return SynthesizedTopologyPlan(
            title="=== LEVEL 2 (MEDIUM): RC PRE-FILTER + ANTI-PARALLEL 1N4148 DIODE LIMITER ===",
            banner_lines=[
                "Topology: RC Pre-Filter Stage + Symmetrical Silicon Diode Clipper (+/-0.7V Clamp)",
                "Components: R1=2.2k, C1=33nF, R2=4.7k, D1=1N4148, D2=1N4148, R3=47k",
                "Behavior: Generates odd harmonics (3f0, 5f0) on XSC4 Output FFT due to symmetrical wave clipping"
            ],
            parts=parts,
            nets=nets,
            spice_overrides={},
            fc_hz=fc_hz
        )

class Level3HardCascadedSallenKey(ICircuitSynthesizer):
    def generate_topology(self, **kwargs) -> SynthesizedTopologyPlan:
        order = int(kwargs.get("order", 5))
        fc_hz = float(kwargs.get("fc_hz", 8000.0))
        tones = kwargs.get("harmonics", [])
        bw_plugin = SynthesizerRegistry.get("butterworth")
        tones_str = ", ".join(f"{t.freq_hz/1000.0:.1f}kHz({t.amplitude_vp}Vp)" for t in tones)
        plan = bw_plugin.generate_topology(order=order, fc_hz=fc_hz, tones_str=tones_str)

        return plan

class Level4UltraRLCBJTMatrix(ICircuitSynthesizer):
    def generate_topology(self, **kwargs) -> SynthesizedTopologyPlan:
        fc_hz = float(kwargs.get("fc_hz", 2500.0))
        tones = kwargs.get("harmonics", [])
        rlc_plugin = SynthesizerRegistry.get("rlc_bjt_matrix")
        plan = rlc_plugin.generate_topology(fc_hz=fc_hz)

        return plan

SynthesizerRegistry.register("lvl1_easy", Level1EasyRCFilter())
SynthesizerRegistry.register("lvl2_medium", Level2MediumTwoStageDiodeClipper())
SynthesizerRegistry.register("lvl3_hard", Level3HardCascadedSallenKey())
SynthesizerRegistry.register("lvl4_ultra", Level4UltraRLCBJTMatrix())

def compile_progressive_circuit(
    plugin_key: str,
    filename: str,
    fc_hz: float,
    harmonics: List[HarmonicTone],
    vim_vp: float = 1.4,
    extra_kwargs: dict = None
) -> None:
    t0 = time.perf_counter()
    kw = dict(extra_kwargs or {})
    kw["fc_hz"] = fc_hz
    kw["harmonics"] = harmonics

    plugin = SynthesizerRegistry.get(plugin_key)
    plan = plugin.generate_topology(**kw)

    builder = MultisimSchematicBuilder.from_testbench()
    builder.synthesize_topology(parts=plan.parts, nets=plan.nets, auto_place=False)

    for refdes, sp_tpl in plan.spice_overrides.items():
        builder.xml_text = MultisimDOMManipulator.update_spice_template_for_component(
            builder.xml_text, refdes, sp_tpl
        )

    ys_placed = [inst.world_bbox[3] for inst in builder.placed_components.values()]
    banner_y = max(477, ((max(ys_placed, default=396) + 54) // 9) * 9)
    builder.add_schematic_info_banner(
        title=plan.title,
        lines=plan.banner_lines,
        x=324,
        y=banner_y
    )

    audit = builder.eda.audit_schematic_dom(builder.xml_text)

    f_fund = harmonics[0].freq_hz if harmonics else fc_hz * 0.5
    doc = MS14Document(codec=builder.codec)
    doc.xml_text = builder.xml_text
    doc.auto_tune_all_instruments(
        fc_hz=fc_hz,
        fundamental_hz=f_fund,
        harmonics=harmonics,
        vim_vp=vim_vp
    )
    doc.configure_dashboard_layout([
        WindowRect("XSC1_Oscilloscope",      1020,  30, 560, 430),
        WindowRect("XBP1_BodePlotter",        380,  30, 630, 290),
        WindowRect("XSC2_Tektronix_Time",      10, 520, 620, 420),
        WindowRect("XSC3_Tektronix_FFT_In",   510, 520, 620, 420),
        WindowRect("XSC4_Tektronix_FFT_Out", 1010, 520, 620, 420),
        WindowRect("XFG1_FunctionGenerator",   10,  30, 259, 301)
    ])

    out_path = os.path.join("dist", filename)
    doc.save(out_path)
    dt_ms = (time.perf_counter() - t0) * 1000.0
    logger.info(
        f"Compiled [{filename}] -> Parts={len(plan.parts)}, Wires={audit.total_wires}, Junctions={audit.total_junctions}, Bends={audit.total_bends}, WL={audit.total_wirelength}u in {dt_ms:.1f} ms"
    )

if __name__ == "__main__":
    os.makedirs("dist", exist_ok=True)

    compile_progressive_circuit(
        "lvl1_easy",
        "01_Level1_Easy_RC_LowPass_Filter.ms14",
        fc_hz=1590.0,
        harmonics=[
            HarmonicTone(freq_hz=800.0, amplitude_vp=1.5),
            HarmonicTone(freq_hz=3200.0, amplitude_vp=0.9),
            HarmonicTone(freq_hz=6400.0, amplitude_vp=0.6)
        ],
        vim_vp=1.5
    )

    compile_progressive_circuit(
        "lvl2_medium",
        "02_Level2_Medium_RC_Diode_Clipper.ms14",
        fc_hz=2000.0,
        harmonics=[
            HarmonicTone(freq_hz=1000.0, amplitude_vp=3.5),
            HarmonicTone(freq_hz=4000.0, amplitude_vp=1.2)
        ],
        vim_vp=3.5
    )

    compile_progressive_circuit(
        "lvl3_hard",
        "03_Level3_Hard_5thOrder_Active_Butterworth.ms14",
        fc_hz=8000.0,
        harmonics=[
            HarmonicTone(freq_hz=4000.0, amplitude_vp=1.4),
            HarmonicTone(freq_hz=12000.0, amplitude_vp=0.9),
            HarmonicTone(freq_hz=20000.0, amplitude_vp=0.7)
        ],
        vim_vp=1.4,
        extra_kwargs={"order": 5}
    )

    compile_progressive_circuit(
        "lvl4_ultra",
        "04_Level4_Ultra_RLC_DualBJT_Clamp_Matrix.ms14",
        fc_hz=2500.0,
        harmonics=[
            HarmonicTone(freq_hz=1000.0, amplitude_vp=1.6),
            HarmonicTone(freq_hz=5000.0, amplitude_vp=0.8),
            HarmonicTone(freq_hz=9000.0, amplitude_vp=0.5)
        ],
        vim_vp=1.6
    )
