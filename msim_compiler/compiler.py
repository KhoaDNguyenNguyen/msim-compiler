import os
import time
from typing import List, Optional
from .codec import MS14Codec
from .document import MS14Document
from .dom_engine import MultisimDOMManipulator
from .filter_synthesizer import HighOrderButterworthSynthesizer
from .logger import get_logger
from .models import HarmonicTone, WindowRect
from .schematic_builder import MultisimSchematicBuilder
from .synthesizers import SynthesizerRegistry, SynthesizedTopologyPlan

class MultisimHighOrderCompiler:
    def __init__(self):
        self.logger = get_logger("MSIM-COMPILER")
        self.codec = MS14Codec()
        self.synthesizer = HighOrderButterworthSynthesizer(base_resistance_ohm=2000.0)

    def compile_topology_plan(
        self,
        plan: SynthesizedTopologyPlan,
        output_ms14_path: str,
        harmonics: Optional[List[HarmonicTone]] = None,
        auto_place: bool = False
    ) -> str:
        t0 = time.perf_counter()
        builder = MultisimSchematicBuilder.from_testbench()
        if auto_place:
            builder.eda.solve_spectral_quadratic_placement(plan.parts, plan.nets)
        builder.synthesize_topology(parts=plan.parts, nets=plan.nets, auto_place=auto_place)

        for refdes, sp_tpl in plan.spice_overrides.items():
            builder.xml_text = MultisimDOMManipulator.update_spice_template_for_component(
                builder.xml_text, refdes, sp_tpl
            )

        builder.add_schematic_info_banner(
            title=plan.title,
            lines=plan.banner_lines,
            x=324,
            y=486
        )

        audit = builder.eda.audit_schematic_dom(builder.xml_text)
        f_fund = harmonics[0].freq_hz if harmonics else plan.fc_hz * 0.5
        doc = MS14Document(codec=self.codec)
        doc.xml_text = builder.xml_text
        doc.auto_tune_all_instruments(fc_hz=plan.fc_hz, fundamental_hz=f_fund, harmonics=harmonics)
        doc.configure_dashboard_layout([
            WindowRect("XSC1_Oscilloscope",      1020,  30, 560, 430),
            WindowRect("XBP1_BodePlotter",        380,  30, 630, 290),
            WindowRect("XSC2_Tektronix_Time",      10, 520, 620, 420),
            WindowRect("XSC3_Tektronix_FFT_In",   510, 520, 620, 420),
            WindowRect("XSC4_Tektronix_FFT_Out", 1010, 520, 620, 420),
            WindowRect("XFG1_FunctionGenerator",   10,  30, 259, 301)
        ])
        doc.save(output_ms14_path)

        dt_ms = (time.perf_counter() - t0) * 1000.0
        self.logger.info(
            f"Compiled Topology Plan ({len(plan.parts)} parts, Junctions={audit.total_junctions}, Bends={audit.total_bends}, WL={audit.total_wirelength}u) -> '{os.path.basename(output_ms14_path)}' in {dt_ms:.1f} ms"
        )
        for s_line in plan.banner_lines:
            self.logger.info(f"  [SPEC] {s_line}")
        return output_ms14_path

    def compile_high_order_filter(
        self,
        order: int,
        fc_hz: float,
        output_ms14_path: str,
        harmonics: Optional[List[HarmonicTone]] = None,
        template_path: Optional[str] = None
    ) -> str:
        tones_str = ", ".join(f"{t.freq_hz/1000.0:.1f}kHz({t.amplitude_vp}Vp)" for t in (harmonics or []))
        plugin = SynthesizerRegistry.get("butterworth")
        plan = plugin.generate_topology(
            order=order,
            fc_hz=fc_hz,
            tones_str=tones_str if tones_str else "Swept AC/Sine"
        )
        return self.compile_topology_plan(plan, output_ms14_path, harmonics=harmonics, auto_place=False)
