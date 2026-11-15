from .dom_engine import MultisimDOMManipulator
from .component_builder import ComponentDomBuilder
import math
import os
import re
import xml.etree.ElementTree as ET
from typing import Dict, List, Optional
from .codec import MS14Codec
from .logger import get_logger
from .models import (
    BodeConfig,
    ComponentSpec,
    FuncGenConfig,
    HarmonicTone,
    HighOrderFilterDesign,
    ScopeConfig,
    TektronixConfig,
    WindowRect,
    WireSegment
)

SCOPE_125_STEPS = [
    1e-9, 2e-9, 5e-9, 1e-8, 2e-8, 5e-8, 1e-7, 2e-7, 5e-7,
    1e-6, 2e-6, 5e-6, 1e-5, 2e-5, 5e-5, 1e-4, 2e-4, 5e-4,
    1e-3, 2e-3, 5e-3, 1e-2, 2e-2, 5e-2, 0.1, 0.2, 0.5, 1.0
]

TEK_TIME_STEPS = [
    1e-9, 2.5e-9, 5e-9, 1e-8, 2.5e-8, 5e-8, 1e-7, 2.5e-7, 5e-7,
    1e-6, 2.5e-6, 5e-6, 1e-5, 2.5e-5, 5e-5, 1e-4, 2.5e-4, 5e-4,
    1e-3, 2.5e-3, 5e-3, 1e-2, 2.5e-2, 5e-2, 0.1, 0.25, 0.5, 1.0
]

TEK_FFT_HZ_STEPS = [50, 100, 250, 500, 1000, 2500, 5000, 10000, 25000, 50000, 100000]

class MS14Document:
    def __init__(self, codec: Optional[MS14Codec] = None):
        self.logger = get_logger("MS14-DOM")
        self.codec = codec or MS14Codec()
        self.xml_text: str = ""
        self.source_path: str = ""

    def load(self, ms14_path: str) -> "MS14Document":
        self.source_path = ms14_path
        self.xml_text = self.codec.decode_to_string(ms14_path)
        self.logger.info(f"Loaded XML DOM from '{os.path.basename(ms14_path)}' ({len(self.xml_text)} chars)")
        return self

    def save(self, ms14_out_path: str, save_xml_path: Optional[str] = None) -> "MS14Document":
        MultisimDOMManipulator.parse_dom_preserving_multiline_attrs(self.xml_text)
        if save_xml_path:
            os.makedirs(os.path.dirname(os.path.abspath(save_xml_path)), exist_ok=True)
            with open(save_xml_path, "wb") as f:
                f.write(self.xml_text.encode("utf-8"))
        self.codec.encode_from_string(self.xml_text, ms14_out_path)
        return self

    def discover_components(self) -> Dict[str, ComponentSpec]:
        root = MultisimDOMManipulator.parse_dom_preserving_multiline_attrs(self.xml_text)
        sym_map = {}
        for item in root.findall(".//CODComposite/Objects/Item[@Class='CIITSymbolComp']"):
            sym = item.find("CIITSymbolComp")
            if sym is None:
                continue
            ci_id = sym.attrib.get("CiComponent", "")
            x_s = float(sym.attrib.get("Transformer-M20", "0"))
            y_s = float(sym.attrib.get("Transformer-M21", "0"))
            val_el = sym.find(".//CIITSymTextCompValue")
            lbl = val_el.attrib.get("Output", "").replace("&", "&amp;") if val_el is not None else ""
            sym_map[ci_id] = (x_s, y_s, lbl)

        result: Dict[str, ComponentSpec] = {}
        for item in root.findall(".//CIITDiagram/Elements/Item[@Class='CiComponent']"):
            ci_id = item.attrib.get("CiID", "")
            comp = item.find("CiComponent")
            if comp is None:
                continue
            lname = comp.attrib.get("LocalName", "")
            refdes = lname[4:] if lname.startswith("&ASC") else lname
            if not refdes:
                continue
            spice_val = ""
            for plist in comp.findall(".//CiaParamList") + comp.findall(".//CiaProperDlg"):
                asc_items = [it for it in plist.findall(".//Item") if it.attrib.get("Value", "").startswith("&ASC")]
                if len(asc_items) >= 2:
                    raw_v = asc_items[1].attrib.get("Value", "")
                    cand_v = raw_v[4:] if raw_v.startswith("&ASC") else raw_v
                    if cand_v:
                        spice_val = cand_v
                        break
            x_v, y_v, lbl = sym_map.get(ci_id, (0.0, 0.0, ""))
            result[refdes] = ComponentSpec(refdes, ci_id, spice_val, lbl, x_v, y_v)
        return result

    def discover_wires(self) -> List[WireSegment]:
        root = MultisimDOMManipulator.parse_dom_preserving_multiline_attrs(self.xml_text)
        wires: List[WireSegment] = []
        idx = 1
        for lk in root.findall(".//CODComposite/Objects/Item[@Class='CIITLinkComp']/CIITLinkComp"):
            pts_el = lk.find("Points")
            if pts_el is not None:
                pts = [(float(pt.attrib.get("X", "0")), float(pt.attrib.get("Y", "0"))) for pt in pts_el.findall("Item")]
                wires.append(WireSegment(idx, pts))
                idx += 1
        return wires

    @staticmethod
    def format_si(value: float, unit_suffix: str = "") -> str:
        if value >= 1e6: return f"{value/1e6:.3g}M{unit_suffix}"
        if value >= 1e3: return f"{value/1e3:.3g}k{unit_suffix}"
        if value >= 1.0: return f"{value:.3g}{unit_suffix}"
        if value >= 1e-3: return f"{value*1e3:.3g}m{unit_suffix}"
        if value >= 1e-6: return f"{value*1e6:.3g}u{unit_suffix}"
        if value >= 1e-9: return f"{value*1e9:.3g}n{unit_suffix}"
        return f"{value*1e12:.3g}p{unit_suffix}"

    @staticmethod
    def format_display_label(refdes: str, spice_val: str) -> str:
        r_up = refdes.upper()
        if r_up.startswith("R"): return f"&amp;UNI{spice_val}_uc103a9"
        if r_up.startswith("C"):
            s_clean = spice_val if spice_val.upper().endswith("F") else f"{spice_val}F"
            return f"&amp;ASC{s_clean}"
        return f"&amp;ASC{spice_val}"

    def set_component(self, refdes: str, spice_val: str, display_label: Optional[str] = None) -> "MS14Document":
        disp = display_label or self.format_display_label(refdes, spice_val)
        disp_unesc = ComponentDomBuilder.unesc(disp)
        num_v = MultisimDOMManipulator.parse_si_value(str(spice_val))

        root = MultisimDOMManipulator.parse_dom_preserving_multiline_attrs(self.xml_text)
        target_ci_id = ""
        for item in root.findall(".//CIITDiagram/Elements/Item[@Class='CiComponent']"):
            comp = item.find("CiComponent")
            if comp is not None and comp.attrib.get("LocalName") == f"&ASC{refdes}":
                target_ci_id = item.attrib.get("CiID", "")
                for plist in comp.findall(".//CiaParamList") + comp.findall(".//CiaProperDlg"):
                    dbl = plist.find(".//doubles/Item")
                    if dbl is not None:
                        dbl.set("Value", f"{num_v:g}")
                    s_items = [it for it in plist.findall(".//Item") if it.attrib.get("Value", "").startswith("&ASC")]
                    if len(s_items) >= 2:
                        s_items[1].set("Value", f"&ASC{spice_val}")
                break

        if not target_ci_id:
            self.logger.warning(f"Component '{refdes}' not found in XML DOM.")
            return self

        for item in root.findall(".//CODComposite/Objects/Item[@Class='CIITSymbolComp']"):
            sym = item.find("CIITSymbolComp")
            if sym is not None and sym.attrib.get("CiComponent") == target_ci_id:
                val_el = sym.find(".//CIITSymTextCompValue")
                if val_el is not None:
                    val_el.set("Output", disp_unesc)
                break

        self.xml_text = ComponentDomBuilder.serialize_element(root)
        self.logger.info(f"Updated Component [{refdes}] (CiID={target_ci_id}) via DOM -> SPICE='{spice_val}', Label='{disp}'")
        return self

    def apply_high_order_filter_design(self, design: HighOrderFilterDesign) -> "MS14Document":
        fin = design.final_stage
        r1_str = self.format_si(fin.r1_ohm)
        r2_str = self.format_si(fin.r2_ohm)
        c1_str = self.format_si(fin.c1_farad)
        c2_str = self.format_si(fin.c2_farad)

        self.set_component("R1", r1_str, display_label=f"&amp;UNI{r1_str}_uc103a9 ({design.order}th-Order)")
        self.set_component("R2", r2_str)
        self.set_component("C1", c1_str)
        self.set_component("C2", c2_str)

        self.xml_text = MultisimDOMManipulator.update_spice_template_for_component(
            self.xml_text, "R1", design.pre_filter_spice_netlist + "\r\n"
        )
        self.logger.info(f"Injected {len(design.stages)-1} pre-filter SPICE cascade stage(s) into R1 template.")
        return self

    def set_cdata_key(self, key_name: str, new_val: str) -> int:
        pat = re.compile(
            r'(<Element\s+Key="&amp;ASC' + re.escape(key_name) + r'">\s*<CDataElement\s+Class="CDataElement"\s+Type="\d+"\s+Data=")([^"]*)("\s*/>)',
            re.DOTALL
        )
        self.xml_text, cnt = pat.subn(r'\g<1>' + str(new_val) + r'\g<3>', self.xml_text)
        return cnt

    def configure_function_generator(self, cfg: FuncGenConfig) -> "MS14Document":
        self.set_cdata_key("NI_EWB_WAVE_MODE", "0")
        self.set_cdata_key("NI_EWB_FREQUENCY_VALUE", f"{cfg.freq_hz:g}")
        self.set_cdata_key("NI_EWB_DEPUTY_CYCLE_VALUE", f"{cfg.duty_cycle:g}")
        self.set_cdata_key("NI_EWB_AMPLITUDE_VALUE", f"{cfg.amplitude_vp:g}")
        self.set_cdata_key("NI_EWB_OFFSET_VALUE", f"{cfg.offset_v:g}")
        self.xml_text = MultisimDOMManipulator.configure_xfg1_harmonics_dom(
            self.xml_text,
            freq_hz=cfg.freq_hz,
            amplitude_vp=cfg.amplitude_vp,
            harmonics=cfg.harmonics or []
        )
        if cfg.harmonics:
            tones_desc = ", ".join(f"{t.freq_hz/1000.0:.1f}kHz({t.amplitude_vp}Vp)" for t in cfg.harmonics)
            self.logger.info(f"Configured XFG1 Multi-Harmonic Source via DOM [{tones_desc}]")
        return self

    def configure_oscilloscope(self, cfg: ScopeConfig) -> "MS14Document":
        snapped_tb = min(SCOPE_125_STEPS, key=lambda s: abs(math.log10(s / max(1e-9, cfg.timebase_scale_s))))
        self.set_cdata_key("NI_EWB_TIMEBASE_MODE", "0")
        self.set_cdata_key("NI_EWB_TIMEBASE_POSITION_VALUE", "0")
        self.set_cdata_key("NI_EWB_TIMEBASE_RANGE_VALUE", "0.1")
        self.set_cdata_key("NI_EWB_TIMEBASE_SCALE_VALUE", f"{snapped_tb:g}")
        self.set_cdata_key("NI_EWB_A_CHANNEL_MODE", "2")
        self.set_cdata_key("NI_EWB_A_CHANNEL_SCALE_VALUE", f"{cfg.ch_a_scale_v:g}")
        self.set_cdata_key("NI_EWB_A_CHANNEL_POSITION_VALUE", f"{cfg.ch_a_pos:g}")
        self.set_cdata_key("NI_EWB_B_CHANNEL_MODE", "2")
        self.set_cdata_key("NI_EWB_B_CHANNEL_SCALE_VALUE", f"{cfg.ch_b_scale_v:g}")
        self.set_cdata_key("NI_EWB_B_CHANNEL_POSITION_VALUE", f"{cfg.ch_b_pos:g}")
        self.set_cdata_key("NI_EWB_TRIGGERBASE_TRIGGER_MODE", str(cfg.trigger_mode))
        self.set_cdata_key("NI_EWB_REVERSE_MODE", "1")
        self.set_cdata_key("NI_EWB_O_REVERSE_MODE", "1")
        self.set_cdata_key("NI_EWB_BACKGROUND_MODE", "1")
        return self

    def configure_bode_plotter(self, cfg: BodeConfig) -> "MS14Document":
        self.set_cdata_key("NI_EWB_BODE_CONTROL_MODE", str(cfg.control_mode))
        self.set_cdata_key("NI_EWB_HORIZONTAL_MODE", str(cfg.horiz_log))
        self.set_cdata_key("NI_EWB_VERTICAL_MODE", str(cfg.vert_log))
        self.set_cdata_key("NI_EWB_HORZ_INITIAL_VALUE", f"{cfg.f_start_hz:g}")
        self.set_cdata_key("NI_EWB_HORZ_FINAL_VALUE", f"{cfg.f_stop_hz:g}")
        self.set_cdata_key("NI_EWB_VERT_INITIAL_LOG_VALUE", f"{cfg.db_min:g}")
        self.set_cdata_key("NI_EWB_VERT_FINAL_LOG_VALUE", f"{cfg.db_max:g}")
        self.set_cdata_key("NI_EWB_CURRENT_DISPLAY_CURSOR_POS_0", f"{cfg.cursor_freq_hz:.2f}")
        self.set_cdata_key("NI_EWB_CURRENT_DISPLAY_CURSOR_POS_1", f"{cfg.cursor_freq_hz:.2f}")
        self.set_cdata_key("NI_EWB_BODE_REVERSE_MODE", "1")
        return self

    def configure_tektronix_scopes(self, configs: List[TektronixConfig], trigger_freq_hz: float = 1000.0) -> "MS14Document":
        self.set_cdata_key("NI_EWB_TKSCOPE_POWERBUTTONON", "1")
        self.set_cdata_key("NI_EWB_TKSCOPE_CURRENTSAMPLINGDATARUNNINGMODE", "2")
        self.set_cdata_key("NI_EWB_TKSCOPE_RUNMODE", "0")
        self.set_cdata_key("NI_EWB_TKSCOPE_TRIGGERFREQUENCY", f"{trigger_freq_hz:.2f}")

        node_pat = re.compile(
            r'(<CSourceSymbolCollectNode\s+Class="CSourceSymbolCollectNode"\s+CompLongName="&amp;ASCXSC[234][^"]*">.*?</CSourceSymbolCollectNode>)',
            re.DOTALL
        )
        node_matches = list(node_pat.finditer(self.xml_text))

        def set_local_key(blk: str, key_name: str, val_str: str) -> str:
            kpat = re.compile(
                r'(<Element\s+Key="&amp;ASC' + re.escape(key_name) + r'">\s*<CDataElement\s+Class="CDataElement"\s+Type="\d+"\s+Data=")([^"]*)("\s*/>)',
                re.DOTALL
            )
            return kpat.sub(r'\g<1>' + str(val_str) + r'\g<3>', blk)

        for idx in range(min(len(node_matches), len(configs)) - 1, -1, -1):
            nm = node_matches[idx]
            nblk = nm.group(1)
            cfg = configs[idx]

            nblk = set_local_key(nblk, "NI_EWB_SHOW_WINDOW_STATUS", "1")
            nblk = set_local_key(nblk, "NI_EWB_SIMULATION_ALLOW", "1")
            nblk = set_local_key(nblk, "NI_EWB_TKSCOPE_POWERBUTTONON", "1")
            nblk = set_local_key(nblk, "NI_EWB_TKSCOPE_RUNMODE", "0")
            nblk = set_local_key(nblk, "NI_EWB_TKSCOPE_CURRENTSAMPLINGDATARUNNINGMODE", "2")
            nblk = set_local_key(nblk, "NI_EWB_TKSCOPE_TRIGGERFREQUENCY", f"{trigger_freq_hz:.2f}")
            nblk = set_local_key(nblk, "NI_EWB_CURRENTMENUGROUPID", "9")
            nblk = set_local_key(nblk, "NI_EWB_CURRENTMEASUREBTID", "0")
            if cfg.mode.lower() == "fft":
                nblk = set_local_key(nblk, "NI_EWB_PRESELECTEDMATHGROUP", "4")
                nblk = set_local_key(nblk, "NI_EWB_SELCHANNELID", "4")
            else:
                nblk = set_local_key(nblk, "NI_EWB_PRESELECTEDMATHGROUP", "2")
                nblk = set_local_key(nblk, "NI_EWB_SELCHANNELID", "0")

            pat_db = re.compile(
                r'(<Element\s+Key="&amp;ASCNI_EWB_SCOPETK_DATABASEDATA">\s*<CDataElement\s+Class="CDataElement"\s+Type="5"\s+Data=")([^"]*)("\s*/>)',
                re.DOTALL
            )
            m = pat_db.search(nblk)
            if m:
                p = m.group(2).replace("&amp;ASC", "").split("[]]_")
                if len(p) >= 37:
                    p[5] = "1"
                    if cfg.mode.lower() == "fft":
                        fft_span_hz = max(500.0, float(cfg.fft_hz_per_div) * 10.0)
                        opt_acq_tb = min(TEK_TIME_STEPS, key=lambda s: abs(s - (25.0 / fft_span_hz)))
                        p[6] = "0"
                        p[7] = str(cfg.fft_source_ch)
                        p[8] = "1"
                        p[9] = "1" if int(cfg.fft_source_ch) == 1 else "0"
                        p[10] = "0"
                        p[11] = "0"
                        p[12] = "1"
                        p[13] = f"{opt_acq_tb:g}"
                        p[14] = "0"
                        p[15] = "1"
                        p[16] = "0"
                        p[17] = "1"
                        p[18] = "0"
                        p[23] = str(cfg.fft_hz_per_div)
                        p[24] = str(cfg.fft_center_hz)
                        p[25] = str(cfg.fft_db_per_div)
                        p[26] = str(cfg.fft_db_pos)
                    else:
                        p[6] = "1"
                        p[7] = "0"
                        p[8] = str(cfg.ch1_on)
                        p[9] = str(cfg.ch2_on)
                        p[10] = str(cfg.ch3_on)
                        p[11] = str(cfg.ch4_on)
                        p[12] = str(cfg.math_on)
                        p[13] = str(cfg.sec_per_div)
                        p[14] = "0"
                        p[15] = str(cfg.ch1_volts)
                        p[16] = str(cfg.ch1_pos)
                        p[17] = str(cfg.ch2_volts)
                        p[18] = str(cfg.ch2_pos)
                    new_db = "&amp;ASC" + "[]]_".join(p)
                    nblk = nblk[:m.start()] + m.group(1) + new_db + m.group(3) + nblk[m.end():]

            self.xml_text = self.xml_text[:nm.start()] + nblk + self.xml_text[nm.end():]
            self.logger.info(f"Configured Tektronix [{cfg.name}] -> Mode={cfg.mode.upper()}")

        return self

    def configure_dashboard_layout(self, rects: List[WindowRect]) -> "MS14Document":
        self.set_cdata_key("NI_EWB_SHOW_WINDOW_STATUS", "1")
        dlg_pat = re.compile(
            r'(<Element\s+Key="&amp;ASCNI_EWB_DIALOG_STATUS">\s*<CDataElement\s+Class="CDataElement"\s+Type="4"\s+Data="0">\s*<Elements>\s*)'
            r'<CDataElement\s+Class="CDataElement"\s+Type="1"\s+Data="([^"]*)"\s*/>\s*'
            r'<CDataElement\s+Class="CDataElement"\s+Type="1"\s+Data="([^"]*)"\s*/>\s*'
            r'<CDataElement\s+Class="CDataElement"\s+Type="1"\s+Data="([^"]*)"\s*/>\s*'
            r'<CDataElement\s+Class="CDataElement"\s+Type="1"\s+Data="([^"]*)"\s*/>'
            r'(\s*</Elements>\s*</CDataElement>\s*</Element>)',
            re.DOTALL
        )
        dlg_matches = list(dlg_pat.finditer(self.xml_text))
        for idx in range(min(len(dlg_matches), len(rects)) - 1, -1, -1):
            dm = dlg_matches[idx]
            r_cfg = rects[idx]
            w_val = r_cfg.width if r_cfg.width is not None else int(dm.group(4))
            h_val = r_cfg.height if r_cfg.height is not None else int(dm.group(5))
            new_block = (
                f'{dm.group(1)}'
                f'<CDataElement Class="CDataElement" Type="1" Data="{r_cfg.left}"/>\n'
                f'<CDataElement Class="CDataElement" Type="1" Data="{r_cfg.top}"/>\n'
                f'<CDataElement Class="CDataElement" Type="1" Data="{w_val}"/>\n'
                f'<CDataElement Class="CDataElement" Type="1" Data="{h_val}"/>'
                f'{dm.group(6)}'
            )
            self.xml_text = self.xml_text[:dm.start()] + new_block + self.xml_text[dm.end():]
        return self

    def auto_tune_all_instruments(
        self,
        fc_hz: float,
        fundamental_hz: Optional[float] = None,
        harmonics: Optional[List[HarmonicTone]] = None,
        vim_vp: float = 1.4
    ) -> "MS14Document":
        label_map = {
            "XSC1": "XSC1 (Time Scope)",
            "XSC2": "XSC2 (Time Domain)",
            "XSC3": "XSC3 (FFT Input)",
            "XSC4": "XSC4 (FFT Output)",
        }
        for old_lbl, new_lbl in label_map.items():
            self.xml_text = re.sub(
                r'(<CIITSymTextCompName\b[^>]*Output="&amp;ASC)' + old_lbl + r'(")',
                rf'\g<1>{new_lbl}\g<2>',
                self.xml_text
            )
        f_wave = fundamental_hz or fc_hz
        raw_tb = (3.0 / float(f_wave)) / 10.0
        opt_scope_tb = min(SCOPE_125_STEPS, key=lambda s: abs(math.log10(s / raw_tb)))
        opt_tek_tb = min(TEK_TIME_STEPS, key=lambda s: abs(math.log10(s / raw_tb)))

        max_f = max([t.freq_hz for t in harmonics], default=fc_hz * 2.5) if harmonics else fc_hz * 2.5
        raw_fft_step = float(max_f) / 6.0 if (harmonics and len(harmonics) > 1) else float(f_wave) / 5.0
        opt_fft_hz = min(TEK_FFT_HZ_STEPS, key=lambda s: abs(math.log10(s / max(10.0, raw_fft_step))))
        opt_fft_center = opt_fft_hz * 5

        self.configure_function_generator(FuncGenConfig(freq_hz=f_wave, amplitude_vp=vim_vp, harmonics=harmonics or []))
        self.configure_oscilloscope(ScopeConfig(timebase_scale_s=opt_scope_tb, ch_a_scale_v=1.0, ch_b_scale_v=1.0))
        self.configure_bode_plotter(BodeConfig(cursor_freq_hz=fc_hz))

        tek_configs = [
            TektronixConfig("XSC2_TimeDomain_InOut", mode="time", ch1_on=1, ch2_on=1, math_on=0, sec_per_div=f"{opt_tek_tb:g}", ch1_volts="1", ch1_pos="2", ch2_volts="1", ch2_pos="-2", menu_group_id=9),
            TektronixConfig("XSC3_FFT_Input_CH1", mode="fft", fft_source_ch=0, fft_hz_per_div=str(opt_fft_hz), fft_center_hz=str(opt_fft_center), fft_db_per_div="10", fft_db_pos="20", menu_group_id=9),
            TektronixConfig("XSC4_FFT_Output_CH2", mode="fft", fft_source_ch=1, fft_hz_per_div=str(opt_fft_hz), fft_center_hz=str(opt_fft_center), fft_db_per_div="10", fft_db_pos="20", menu_group_id=9)
        ]
        self.configure_tektronix_scopes(tek_configs, trigger_freq_hz=f_wave)

        default_rects = [
            WindowRect("XSC1_Oscilloscope", 925, 35, 660, 557),
            WindowRect("XBP1_BodePlotter", 270, 35, 646, 287),
            WindowRect("XSC2_Tektronix_Time", 10, 515, 961, 458),
            WindowRect("XSC3_Tektronix_FFT_In", 485, 515, 961, 458),
            WindowRect("XSC4_Tektronix_FFT_Out", 955, 515, 961, 458),
            WindowRect("XFG1_FunctionGenerator", 10, 35, 259, 301)
        ]
        self.configure_dashboard_layout(default_rects)
        return self
