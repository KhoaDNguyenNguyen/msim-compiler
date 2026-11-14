import math
import os
import re
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
        if save_xml_path:
            os.makedirs(os.path.dirname(os.path.abspath(save_xml_path)), exist_ok=True)
            with open(save_xml_path, "wb") as f:
                f.write(self.xml_text.encode("utf-8"))
        self.codec.encode_from_string(self.xml_text, ms14_out_path)
        return self

    def discover_components(self) -> Dict[str, ComponentSpec]:
        comp_pat = re.compile(
            r'<Item\s+CiID="(\d+)"\s+Class="CiComponent">\s*<CiComponent\s+[^>]*LocalName="&amp;ASC([^"]+)"[^>]*>(.*?)</CiComponent>\s*</Item>',
            re.DOTALL
        )
        sym_pat = re.compile(
            r'<CIITSymbolComp\s+[^>]*Transformer-M20="([^"]+)"\s+Transformer-M21="([^"]+)"[^>]*CiComponent="(\d+)"[^>]*>(.*?)</CIITSymbolComp>',
            re.DOTALL
        )
        sym_map = {}
        for sm in sym_pat.finditer(self.xml_text):
            x_s, y_s, ci_id, sbody = sm.groups()
            val_m = re.search(r'<CIITSymTextCompValue\s+[^>]*Output="([^"]+)"', sbody)
            lbl = val_m.group(1) if val_m else ""
            sym_map[ci_id] = (float(x_s), float(y_s), lbl)

        result: Dict[str, ComponentSpec] = {}
        for cm in comp_pat.finditer(self.xml_text):
            ci_id, refdes, cbody = cm.groups()
            params = re.findall(r'<Item\s+Value="&amp;ASC([^"]+)"\s*/>', cbody)
            spice_val = params[1] if len(params) > 1 else ""
            x_v, y_v, lbl = sym_map.get(ci_id, (0.0, 0.0, ""))
            result[refdes] = ComponentSpec(refdes, ci_id, spice_val, lbl, x_v, y_v)
        return result

    def discover_wires(self) -> List[WireSegment]:
        w_pat = re.compile(r'<CODMultiLineComp\s+[^>]*>(.*?)</CODMultiLineComp>', re.DOTALL)
        wires: List[WireSegment] = []
        for idx, wm in enumerate(w_pat.finditer(self.xml_text), start=1):
            pts_raw = re.findall(r'<Item\s+X="([^"]+)"\s+Y="([^"]+)"\s*/>', wm.group(1))
            wires.append(WireSegment(idx, [(float(px), float(py)) for px, py in pts_raw]))
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
        comp_pat = re.compile(
            r'(<Item\s+CiID="(\d+)"\s+Class="CiComponent">\s*<CiComponent\s+[^>]*LocalName="&amp;ASC' + re.escape(refdes) + r'"[^>]*>.*?</CiComponent>\s*</Item>)',
            re.DOTALL
        )
        m = comp_pat.search(self.xml_text)
        if not m:
            self.logger.warning(f"Component '{refdes}' not found in XML DOM.")
            return self

        full_block, ci_id = m.group(1), m.group(2)

        def parse_si_num(v_s: str) -> float:
            s = v_s.strip().rstrip("FHOhm")
            m_n = re.match(r'^([+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)\s*([a-zA-Z]*)$', s)
            if not m_n:
                return 1000.0
            mult = {"G": 1e9, "M": 1e6, "k": 1e3, "K": 1e3, "": 1.0, "m": 1e-3, "u": 1e-6, "n": 1e-9, "p": 1e-12}.get(m_n.group(2), 1.0)
            return float(m_n.group(1)) * mult

        num_v = parse_si_num(str(spice_val))

        def replace_params(pm):
            p_block = pm.group(0)
            p_block = re.sub(
                r'(<doubles>\s*<Item\s+Value=")[^"]+("\s*/>)',
                rf'\g<1>{num_v:g}\g<2>',
                p_block, count=1
            )
            items = list(re.finditer(r'(<Item\s+Value="&amp;ASC)([^"]+)("\s*/>)', p_block))
            if len(items) >= 2:
                t_item = items[1]
                s_i, e_i = t_item.span()
                p_block = p_block[:s_i] + t_item.group(1) + str(spice_val) + t_item.group(3) + p_block[e_i:]
            return p_block

        new_block = re.sub(
            r'<CiaParamList\s+Class="CiaParamList">.*?</CiaParamList>',
            replace_params, full_block, count=1, flags=re.DOTALL
        )
        new_block = re.sub(
            r'<CiaProperDlg\s+Class="CiaProperDlg"[^>]*>.*?</CiaProperDlg>',
            replace_params, new_block, count=1, flags=re.DOTALL
        )
        self.xml_text = self.xml_text[:m.start()] + new_block + self.xml_text[m.end():]

        sym_pat = re.compile(
            r'(<CIITSymbolComp\s+[^>]*CiComponent="' + re.escape(ci_id) + r'"[^>]*>.*?</CIITSymbolComp>)',
            re.DOTALL
        )
        sm = sym_pat.search(self.xml_text)
        if sm:
            new_s_block = re.sub(
                r'(<CIITSymTextCompValue\s+[^>]*Output=")([^"]+)(")',
                r'\g<1>' + disp + r'\g<3>', sm.group(1)
            )
            self.xml_text = self.xml_text[:sm.start()] + new_s_block + self.xml_text[sm.end():]

        self.logger.info(f"Updated Component [{refdes}] (CiID={ci_id}) -> SPICE='{spice_val}', Label='{disp}'")
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

        comp_pat = re.compile(
            r'(<Item\s+CiID="\d+"\s+Class="CiComponent">\s*<CiComponent\s+[^>]*LocalName="&amp;ASCR1"[^>]*>.*?</CiComponent>\s*</Item>)',
            re.DOTALL
        )
        m = comp_pat.search(self.xml_text)
        if m:
            new_r1_block = re.sub(
                r'(<CiaSpiceTmpltExprt\s+Class="CiaSpiceTmpltExprt"\s+String="&amp;ASC)([^"]+)(")',
                lambda tm: tm.group(1) + design.pre_filter_spice_netlist + "\r\n" + tm.group(3),
                m.group(1), count=1
            )
            self.xml_text = self.xml_text[:m.start()] + new_r1_block + self.xml_text[m.end():]
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

        if not cfg.harmonics:
            f_spice = cfg.freq_hz * 1e-6
            self.xml_text, _ = re.subn(
                r'(sin\([0-9.eE+-]+\s+)[0-9.eE+-]+(\s+)[0-9.eE+-]+(\s+0\))',
                rf'\g<1>{cfg.amplitude_vp:.18e}\g<2>{f_spice:.18e}\g<3>',
                self.xml_text
            )
        else:
            pos_lines: List[str] = []
            neg_lines: List[str] = []
            n_tones = len(cfg.harmonics)
            for idx, tone in enumerate(cfg.harmonics):
                p_in = "3" if idx == 0 else f"n_hp_{idx}"
                p_out = "2" if idx == n_tones - 1 else f"n_hp_{idx+1}"
                n_in = "2" if idx == 0 else f"n_hn_{idx}"
                n_out = "1" if idx == n_tones - 1 else f"n_hn_{idx+1}"
                ac_mag = cfg.amplitude_vp if idx == 0 else 0.0
                f_sp = tone.freq_hz * 1e-6
                pos_lines.append(f"vfgen_pos_{idx+1} {p_in} {p_out} dc 0.000000e+00 ac {ac_mag:.18e} sin(0.000000000000000000e+00 {tone.amplitude_vp:.18e} {f_sp:.18e} 0)")
                neg_lines.append(f"vfgen_neg_{idx+1} {n_in} {n_out} dc 0.000000e+00 ac {ac_mag:.18e} sin(0.000000000000000000e+00 {tone.amplitude_vp:.18e} {f_sp:.18e} 0)")

            subckt_body = "\r\n".join(pos_lines + neg_lines)
            self.xml_text, cnt = re.subn(
                r'(&amp;ASC\.subckt\s+XXFG1_\d+\s+1\s+2\s+3\s+).*?(\s*\.ends)',
                lambda m: m.group(1) + "\r\n" + subckt_body + "\r\n" + m.group(2),
                self.xml_text, flags=re.DOTALL
            )

            if len(cfg.harmonics) > 1:
                extra_tones = cfg.harmonics[1:]
                ext_lines: List[str] = []
                for idx, tone in enumerate(extra_tones):
                    nin = "n_xfg_raw_%p" if idx == 0 else f"n_xfg_h{idx}_%p"
                    nout = "%t1" if idx == len(extra_tones) - 1 else f"n_xfg_h{idx+1}_%p"
                    f_sp = tone.freq_hz * 1e-6
                    ext_lines.append(f"v_xfg_ext_{idx+1}_%p {nout} {nin} dc 0 ac 0 sin(0 {tone.amplitude_vp:.6f} {f_sp:.12e} 0)")
                ext_prefix = "\r\n".join(ext_lines)
                self.xml_text = re.sub(
                    r'(<CiaSpiceTmpltExprt\s+Class="CiaSpiceTmpltExprt"\s+String="&amp;ASC)(?:v_xfg_ext_.*?\r\n)*x%p\s+(?:%t1|n_xfg_raw_%p)(\s+%t2\s+%t3\s+)',
                    lambda m: m.group(1) + ext_prefix + "\r\nx%p n_xfg_raw_%p" + m.group(2),
                    self.xml_text,
                    count=1,
                    flags=re.DOTALL
                )

            tones_desc = ", ".join(f"{t.freq_hz/1000.0:.1f}kHz({t.amplitude_vp}Vp)" for t in cfg.harmonics)
            self.logger.info(f"Configured XFG1 Multi-Harmonic Source [{tones_desc}] (updated={cnt})")
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
        return self

    def configure_tektronix_scopes(self, configs: List[TektronixConfig], trigger_freq_hz: float = 1000.0) -> "MS14Document":
        self.set_cdata_key("NI_EWB_TKSCOPE_POWERBUTTONON", "1")
        self.set_cdata_key("NI_EWB_TKSCOPE_TRIGGERFREQUENCY", f"{trigger_freq_hz:.2f}")

        pat_db = re.compile(
            r'(<Element\s+Key="&amp;ASCNI_EWB_SCOPETK_DATABASEDATA">\s*<CDataElement\s+Class="CDataElement"\s+Type="5"\s+Data=")([^"]*)("\s*/>)',
            re.DOTALL
        )
        matches = list(pat_db.finditer(self.xml_text))
        for idx in range(min(len(matches), len(configs)) - 1, -1, -1):
            m = matches[idx]
            cfg = configs[idx]
            p = m.group(2).replace("&amp;ASC", "").split("[]]_")
            if len(p) >= 37:
                p[5] = "1"
                if cfg.mode.lower() == "fft":
                    fft_span_hz = max(500.0, float(cfg.fft_hz_per_div) * 10.0)
                    opt_acq_tb = min(TEK_TIME_STEPS, key=lambda s: abs(s - (25.0 / fft_span_hz)))
                    p[6] = "0"
                    p[7] = str(cfg.fft_source_ch)
                    p[8] = "1"
                    p[9] = "1"
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
                    p[29] = "1"
                    p[31] = "0"
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
                    p[29] = "1"
                    p[31] = "0"
                new_db = "&amp;ASC" + "[]]_".join(p)
                self.xml_text = self.xml_text[:m.start()] + m.group(1) + new_db + m.group(3) + self.xml_text[m.end():]
                self.logger.info(f"Configured Tektronix [{cfg.name}] -> Mode={cfg.mode.upper()}")

        pat_menu = re.compile(
            r'(<Element\s+Key="&amp;ASCNI_EWB_CURRENTMENUGROUPID">\s*<CDataElement\s+Class="CDataElement"\s+Type="1"\s+Data=")([^"]*)("\s*/>)',
            re.DOTALL
        )
        menu_matches = list(pat_menu.finditer(self.xml_text))
        for idx in range(min(len(menu_matches), len(configs)) - 1, -1, -1):
            mm = menu_matches[idx]
            self.xml_text = self.xml_text[:mm.start()] + mm.group(1) + str(configs[idx].menu_group_id) + mm.group(3) + self.xml_text[mm.end():]
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
        f_wave = fundamental_hz or fc_hz
        raw_tb = (3.0 / float(f_wave)) / 10.0
        opt_scope_tb = min(SCOPE_125_STEPS, key=lambda s: abs(math.log10(s / raw_tb)))
        opt_tek_tb = min(TEK_TIME_STEPS, key=lambda s: abs(math.log10(s / raw_tb)))

        max_f = max([t.freq_hz for t in harmonics], default=fc_hz * 2.5) if harmonics else fc_hz * 2.5
        raw_fft_step = float(max_f) / 7.5
        opt_fft_hz = min(TEK_FFT_HZ_STEPS, key=lambda s: abs(math.log10(s / max(10.0, raw_fft_step))))
        opt_fft_center = opt_fft_hz * 5

        self.configure_function_generator(FuncGenConfig(freq_hz=f_wave, amplitude_vp=vim_vp, harmonics=harmonics or []))
        self.configure_oscilloscope(ScopeConfig(timebase_scale_s=opt_scope_tb, ch_a_scale_v=1.0, ch_b_scale_v=1.0))
        self.configure_bode_plotter(BodeConfig(cursor_freq_hz=fc_hz))

        tek_configs = [
            TektronixConfig("XSC2_TimeDomain_InOut", mode="time", ch1_on=1, ch2_on=1, math_on=0, sec_per_div=f"{opt_tek_tb:g}", ch1_volts="1", ch1_pos="2", ch2_volts="1", ch2_pos="-2", menu_group_id=9),
            TektronixConfig("XSC3_FFT_Input_CH1", mode="fft", fft_source_ch=0, fft_hz_per_div=str(opt_fft_hz), fft_center_hz=str(opt_fft_center), fft_db_per_div="10", fft_db_pos="20", menu_group_id=0),
            TektronixConfig("XSC4_FFT_Output_CH2", mode="fft", fft_source_ch=1, fft_hz_per_div=str(opt_fft_hz), fft_center_hz=str(opt_fft_center), fft_db_per_div="10", fft_db_pos="20", menu_group_id=0)
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
