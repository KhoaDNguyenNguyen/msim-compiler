import os
import re
import time
from typing import List, Optional, Tuple
from .codec import MS14Codec
from .document import MS14Document
from .eda_engine import AffineGrid2D, NetConnectionSpec, PartPlacementSpec
from .filter_synthesizer import HighOrderButterworthSynthesizer
from .logger import get_logger
from .models import HarmonicTone, WindowRect
from .schematic_builder import MultisimSchematicBuilder

class MultisimHighOrderCompiler:
    def __init__(self):
        self.logger = get_logger("MSIM-COMPILER")
        self.codec = MS14Codec()
        self.synthesizer = HighOrderButterworthSynthesizer(base_resistance_ohm=2000.0)

    def compile_high_order_filter(
        self,
        order: int,
        fc_hz: float,
        output_ms14_path: str,
        harmonics: Optional[List[HarmonicTone]] = None,
        template_path: Optional[str] = None
    ) -> str:
        t0 = time.perf_counter()
        design = self.synthesizer.synthesize(order=order, fc_hz=fc_hz)

        builder = MultisimSchematicBuilder.from_testbench()

        parts: List[PartPlacementSpec] = []
        nets: List[NetConnectionSpec] = []
        gnd_terms: List[Tuple[str, str]] = []

        total_w = sum(108 if st.order == 1 else 198 for st in design.stages)
        col_x = AffineGrid2D.snap9(max(288, 630 - total_w // 2))
        row_series_y = 297
        row_shunt_y = 369
        row_fb_y = 198

        r_counter = 1
        c_counter = 1

        rin_ref = f"R{r_counter}"
        r_counter += 1
        parts.append(PartPlacementSpec(rin_ref, "RESISTOR_VIRTUAL", "100k", x=col_x - 36, y=row_shunt_y, rotation=90))
        gnd_terms.append((rin_ref, "2"))

        stage_out_terms: List[Tuple[str, str]] = [(rin_ref, "1")]
        stage_summaries: List[str] = []
        biquad_buffer_specs: List[Tuple[str, str, float]] = []

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
                c_val = MS14Document.format_si(st.c1_farad) + "F"

                parts.append(PartPlacementSpec(r_ref, "RESISTOR_VIRTUAL", r_val, x=col_x, y=row_series_y, rotation=0))
                parts.append(PartPlacementSpec(c_ref, "CAPACITOR_VIRTUAL", c_val, x=col_x + 36, y=row_shunt_y, rotation=90))

                stage_out_terms.append((r_ref, "1"))
                nets.append(NetConnectionSpec(in_net_name, stage_out_terms))

                stage_out_terms = [(r_ref, "2"), (c_ref, "1")]
                if is_last:
                    nets.append(NetConnectionSpec("VOUT", stage_out_terms))
                gnd_terms.append((c_ref, "2"))
                stage_summaries.append(f"Stage {s_num} (1st-Ord RC): {r_ref}={r_val}, {c_ref}={c_val}")
                col_x += 108
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
                csh_val = MS14Document.format_si(st.c1_farad) + "F"
                cfb_val = MS14Document.format_si(st.c2_farad) + "F"

                parts.append(PartPlacementSpec(ra_ref, "RESISTOR_VIRTUAL", ra_val, x=col_x, y=row_series_y, rotation=0))
                parts.append(PartPlacementSpec(rb_ref, "RESISTOR_VIRTUAL", rb_val, x=col_x + 90, y=row_series_y, rotation=0))
                parts.append(PartPlacementSpec(cfb_ref, "CAPACITOR_VIRTUAL", cfb_val, x=col_x + 54, y=row_fb_y, rotation=0))
                parts.append(PartPlacementSpec(csh_ref, "CAPACITOR_VIRTUAL", csh_val, x=col_x + 126, y=row_shunt_y, rotation=90))

                stage_out_terms.append((ra_ref, "1"))
                nets.append(NetConnectionSpec(in_net_name, stage_out_terms))

                nets.append(NetConnectionSpec(f"N_ST{s_num}_MID", [(ra_ref, "2"), (rb_ref, "1"), (cfb_ref, "1")]))

                stage_out_terms = [(rb_ref, "2"), (csh_ref, "1"), (cfb_ref, "2")]
                if is_last:
                    nets.append(NetConnectionSpec("VOUT", stage_out_terms))
                gnd_terms.append((csh_ref, "2"))
                biquad_buffer_specs.append((ra_ref, rb_ref, st.c1_farad))
                stage_summaries.append(f"Stage {s_num} (Biquad Q={st.q_factor:.3f}): {ra_ref}={rb_ref}={ra_val}, {cfb_ref}={cfb_val}, {csh_ref}={csh_val}")
                col_x += 198

        if gnd_terms:
            nets.append(NetConnectionSpec("0", gnd_terms))

        builder.synthesize_topology(parts=parts, nets=nets, auto_place=False)

        for ra_ref, rb_ref, c1_farad in biquad_buffer_specs:
            ra_pat = re.compile(
                r'(<Item\s+CiID="\d+"\s+Class="CiComponent">\s*<CiComponent\s+[^>]*LocalName="&amp;ASC' + re.escape(ra_ref) + r'"[^>]*>.*?</CiComponent>\s*</Item>)',
                re.DOTALL
            )
            m_ra = ra_pat.search(builder.xml_text)
            if m_ra:
                ra_sp = "e_in_%p n_in_%p 0 %t1 0 1.0&#xD;&#xA;r%p n_in_%p %t2 \x231&#xD;&#xA;"
                new_ra = re.sub(
                    r'(<CiaSpiceTmpltExprt\s+Class="CiaSpiceTmpltExprt"\s+String="&amp;ASC)([^"]+)(")',
                    lambda tm: tm.group(1) + ra_sp + tm.group(3),
                    m_ra.group(1), count=1
                )
                builder.xml_text = builder.xml_text[:m_ra.start()] + new_ra + builder.xml_text[m_ra.end():]

            rb_pat = re.compile(
                r'(<Item\s+CiID="\d+"\s+Class="CiComponent">\s*<CiComponent\s+[^>]*LocalName="&amp;ASC' + re.escape(rb_ref) + r'"[^>]*>.*?</CiComponent>\s*</Item>)',
                re.DOTALL
            )
            m_rb = rb_pat.search(builder.xml_text)
            if m_rb:
                rb_sp = f"r%p %t1 n_pls_%p \x231&#xD;&#xA;c_sk_%p n_pls_%p 0 {c1_farad:.6e}&#xD;&#xA;e_sk_%p %t2 0 n_pls_%p 0 1.0&#xD;&#xA;"
                new_rb = re.sub(
                    r'(<CiaSpiceTmpltExprt\s+Class="CiaSpiceTmpltExprt"\s+String="&amp;ASC)([^"]+)(")',
                    lambda tm: tm.group(1) + rb_sp + tm.group(3),
                    m_rb.group(1), count=1
                )
                builder.xml_text = builder.xml_text[:m_rb.start()] + new_rb + builder.xml_text[m_rb.end():]

        tones_str = ", ".join(f"{t.freq_hz/1000.0:.1f}kHz({t.amplitude_vp}Vp)" for t in (harmonics or []))
        banner_lines = [
            f"Topology: {order}th-Order Butterworth Cascade | Cutoff fc = {fc_hz/1000.0:.2f} kHz | Roll-off = -{20*order} dB/dec",
            f"Multi-Tone Input (VIN): {tones_str if tones_str else 'Swept AC/Sine'}"
        ] + stage_summaries
        builder.add_schematic_info_banner(
            title=f"=== SYNTHESIZED {order}TH-ORDER BUTTERWORTH ACTIVE FILTER SPECIFICATION ===",
            lines=banner_lines,
            x=261,
            y=495
        )

        audit = builder.eda.audit_schematic_dom(builder.xml_text)

        f_fund = harmonics[0].freq_hz if harmonics else fc_hz * 0.5
        doc = MS14Document(codec=self.codec)
        doc.xml_text = builder.xml_text
        doc.auto_tune_all_instruments(fc_hz=fc_hz, fundamental_hz=f_fund, harmonics=harmonics)
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
            f"Compiled Centered {order}th-Order Butterworth Schematic ({len(parts)} parts, Junctions={audit.total_junctions}, Bends={audit.total_bends}, WL={audit.total_wirelength}u) -> '{os.path.basename(output_ms14_path)}' in {dt_ms:.1f} ms"
        )
        for s_line in banner_lines:
            self.logger.info(f"  [SPEC] {s_line}")
        return output_ms14_path
