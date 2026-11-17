import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from .config_registry import GLOBAL_CONFIG
from .models import HarmonicTone, TektronixConfig

CR = chr(13)
LF = chr(10)
CRLF = CR + LF
CRLF_TOKEN = "__MSIM_CRLF__"
LF_TOKEN = "__MSIM_LF__"

@dataclass
class UniversalPinNode:
    comp_ref: str
    pin_name: str

@dataclass
class UniversalNetGraph:
    parts: List[Dict[str, object]] = field(default_factory=list)
    nets: Dict[str, List[Tuple[str, str]]] = field(default_factory=dict)

    def build_laplacian_matrices(self) -> Tuple[List[str], List[float], List[float], List[float], List[float]]:
        ref_list = [str(p["refdes"]) for p in self.parts]
        ref_to_idx = {r: i for i, r in enumerate(ref_list)}
        n = len(ref_list)
        adj = [0.0] * (n * n)
        anchor_w = [1.5] * n
        anchor_x = [float(p.get("x") or (351 + i * 99)) for i, p in enumerate(self.parts)]
        anchor_y = [float(p.get("y") or 243) for i, p in enumerate(self.parts)]

        for net_name, terms in self.nets.items():
            valid_indices = [ref_to_idx[c] for c, _ in terms if c in ref_to_idx]
            k = len(valid_indices)
            if k >= 2:
                weight = 2.0 / float(k - 1)
                for a_i in range(k):
                    u = valid_indices[a_i]
                    for b_i in range(a_i + 1, k):
                        v = valid_indices[b_i]
                        if u != v:
                            adj[u * n + v] += weight
                            adj[v * n + u] += weight
            if net_name == "VIN":
                for u in valid_indices:
                    anchor_w[u] += 4.0
            elif net_name == "VOUT":
                for u in valid_indices:
                    anchor_w[u] += 4.0
        return ref_list, adj, anchor_w, anchor_x, anchor_y

class MultisimDOMManipulator:
    @staticmethod
    def parse_si_value(val_str: str) -> float:
        s = val_str.strip()
        for unit in ("Ohm", "ohm", "F", "H", "V", "A", "Hz"):
            if s.endswith(unit) and len(s) > len(unit):
                s = s[:-len(unit)].strip()
        m = re.match(r"^([+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)\s*([a-zA-Z]*)$", s)
        if not m:
            return 1000.0
        num = float(m.group(1))
        suf = m.group(2)
        return num * GLOBAL_CONFIG.si_multipliers.get(suf, 1.0)

    @classmethod
    def parse_dom_preserving_multiline_attrs(cls, xml_text: str) -> ET.Element:
        def protect_attr(m):
            val = (
                m.group(2)
                .replace("&#xD;&#xA;", CRLF_TOKEN)
                .replace("&#xd;&#xa;", CRLF_TOKEN)
                .replace("&#13;&#10;", CRLF_TOKEN)
                .replace("&#xA;", LF_TOKEN)
                .replace("&#10;", LF_TOKEN)
                .replace(CRLF, CRLF_TOKEN)
                .replace(LF, LF_TOKEN)
            )
            return m.group(1) + val + m.group(3)
        protected_xml = re.sub(r'(\b(?:String|Value)=")([^"]*)(")', protect_attr, xml_text)
        return ET.fromstring(protected_xml.encode("utf-8"))

    @classmethod
    def serialize_dom_restoring_multiline_attrs(cls, root: ET.Element) -> str:
        raw_xml = ET.tostring(root, encoding="unicode")
        return raw_xml.replace(CRLF_TOKEN, CRLF).replace(LF_TOKEN, CRLF)

    @staticmethod
    def validate_xml_tree(xml_text: str) -> ET.Element:
        return ET.fromstring(xml_text.encode("utf-8"))

    @classmethod
    def update_spice_template_for_component(cls, xml_text: str, refdes: str, new_spice_template: str) -> str:
        root = cls.parse_dom_preserving_multiline_attrs(xml_text)
        target_name = f"&ASC{refdes}"
        encoded_tpl = "&ASC" + new_spice_template.replace(CRLF, CRLF_TOKEN).replace(LF, CRLF_TOKEN)
        for item in root.findall(".//Elements/Item[@Class='CiComponent']"):
            comp = item.find("CiComponent")
            if comp is not None and comp.attrib.get("LocalName") == target_name:
                exprt = comp.find(".//CiaSpiceTmpltExprt")
                if exprt is not None:
                    exprt.set("String", encoded_tpl)
                break
        return cls.serialize_dom_restoring_multiline_attrs(root)

    @classmethod
    def configure_xfg1_harmonics_dom(
        cls,
        xml_text: str,
        freq_hz: float,
        amplitude_vp: float,
        harmonics: List[HarmonicTone],
        injection_target: Optional[str] = None
    ) -> str:
        extra_tones = harmonics[1:] if len(harmonics) > 1 else []
        if not extra_tones:
            return xml_text

        root = cls.parse_dom_preserving_multiline_attrs(xml_text)
        elements_el = root.find(".//CIITDiagram/Elements")
        objects_el = root.find(".//CODComposite/Objects")
        if elements_el is None or objects_el is None:
            return xml_text

        vin_port_ids = set()
        for n_it in elements_el.findall("Item[@Class='CiNode']"):
            cn = n_it.find("CiNode")
            if cn is not None and cn.attrib.get("LocalName") == "&ASCVIN":
                for p_ref in cn.findall("./Ports/Item"):
                    vin_port_ids.add(p_ref.attrib.get("CiID", ""))

        comp_port_on_vin = {}
        for p_it in elements_el.findall("Item[@Class='CiPort']"):
            pid = p_it.attrib.get("CiID", "")
            if pid in vin_port_ids:
                cp = p_it.find("CiPort")
                if cp is not None:
                    cid = cp.attrib.get("Component", "")
                    pname = cp.attrib.get("LocalName", "")
                    if pname.startswith("&ASC"):
                        pname = pname[4:]
                    if cid and pname:
                        comp_port_on_vin[cid] = pname

        target_comp_el = None
        target_pin_name = "1"
        for c_it in elements_el.findall("Item[@Class='CiComponent']"):
            cid = c_it.attrib.get("CiID", "")
            cc = c_it.find("CiComponent")
            if cc is None:
                continue
            lname = cc.attrib.get("LocalName", "")
            ref = lname[4:] if lname.startswith("&ASC") else lname
            if injection_target and ref == injection_target:
                target_comp_el = cc
                target_pin_name = comp_port_on_vin.get(cid, "1")
                break
            if cid in comp_port_on_vin and re.match(r"^[RCL]\d+$", ref, re.IGNORECASE):
                target_comp_el = cc
                target_pin_name = comp_port_on_vin[cid]
                break

        if target_comp_el is None:
            return xml_text

        vfg_node_id = "490443665"
        xfg_ports = {"495909056", "495902920"}
        existing_vfg = None
        for n_it in elements_el.findall("Item[@Class='CiNode']"):
            cn = n_it.find("CiNode")
            if cn is not None and cn.attrib.get("LocalName") == "&ASCVIN":
                p_container = cn.find("Ports")
                if p_container is not None:
                    for p_el in list(p_container.findall("Item")):
                        if p_el.attrib.get("CiID") in xfg_ports:
                            p_container.remove(p_el)
            elif n_it.attrib.get("CiID") == vfg_node_id:
                existing_vfg = n_it

        if existing_vfg is None:
            for n_it in elements_el.findall("Item[@Class='CiNode']"):
                cn = n_it.find("CiNode")
                if cn is not None and cn.attrib.get("LocalName") == "&ASCVIN":
                    import copy
                    vfg_it = copy.deepcopy(n_it)
                    vfg_it.set("CiID", vfg_node_id)
                    vfg_cn = vfg_it.find("CiNode")
                    vfg_cn.set("LocalName", "&ASCVFG")
                    vfg_ports = vfg_cn.find("Ports")
                    vfg_ports.clear()
                    for xpid in sorted(xfg_ports):
                        ET.SubElement(vfg_ports, "Item", {"CiID": xpid})
                    elements_el.append(vfg_it)
                    break

        for c_it in elements_el.findall("Item[@Class='CiCircuit']"):
            nodes_el = c_it.find("CiCircuit/Nodes")
            if nodes_el is not None:
                if not any(it.attrib.get("CiID") == vfg_node_id for it in nodes_el.findall("Item")):
                    ET.SubElement(nodes_el, "Item", {"CiID": vfg_node_id})

        for p_it in elements_el.findall("Item[@Class='CiPort']"):
            if p_it.attrib.get("CiID") in xfg_ports:
                n_el = p_it.find("CiPort/Nodes")
                if n_el is not None:
                    n_el.clear()
                    ET.SubElement(n_el, "Item", {"CiID": vfg_node_id})

        for lk_it in objects_el.findall("Item[@Class='CIITLinkComp']"):
            if lk_it.attrib.get("ID") in ("477615600", "477617544"):
                lk = lk_it.find("CIITLinkComp")
                if lk is not None:
                    lk.set("Node", vfg_node_id)
                    for mod_it in lk.findall(".//Element[@NetModifier='&ASCNI_EWB_NET_AUTONAMED']/Item"):
                        w_guid = mod_it.attrib.get("Guid", "")
                        if w_guid:
                            for nl in root.findall(f".//CDesignNetContainer//CSchDesignNetLabel[@ID='{w_guid}']"):
                                nl.set("LastNetName", "&ASCVFG")

        for vc_it in objects_el.findall("Item[@Class='CVirtualConnector']"):
            if vc_it.attrib.get("ID") == "192713976":
                vc = vc_it.find("CVirtualConnector")
                if vc is not None:
                    txt_el = vc.find(".//CIITSymTextCompName")
                    if txt_el is not None:
                        txt_el.set("Output", "&ASCVFG")
                    for mod_it in vc.findall(".//Element[@NetModifier='&ASCNI_EWB_NET_VIRTUAL']/Item"):
                        mod_it.set("Value", "&ASCVFG")
                        m_guid = mod_it.attrib.get("Guid", "")
                        if m_guid:
                            for nl in root.findall(f".//CDesignNetContainer//CSchDesignNetLabel[@ID='{m_guid}']"):
                                nl.set("LastNetName", "&ASCVFG")
                                nl.set("LastLocalName", "&ASCVFG")
                                nl.set("LastOverruleName", "&ASCVFG")
                    vc_cid = vc.attrib.get("CiComponent", "")
                    for c_it in elements_el.findall("Item[@Class='CiComponent']"):
                        if c_it.attrib.get("CiID") == vc_cid:
                            cc = c_it.find("CiComponent")
                            if cc is not None:
                                cc.set("LocalName", "&ASCVFG")

        exprt = target_comp_el.find(".//CiaSpiceTmpltExprt")
        if exprt is not None:
            raw_s = exprt.attrib.get("String", "")
            base_s = raw_s[4:] if raw_s.startswith("&ASC") else raw_s
            base_s = base_s.replace(CRLF_TOKEN, LF).replace(LF_TOKEN, LF)
            clean_lines = [ln for ln in base_s.splitlines() if ln.strip() and not ln.strip().startswith("v_xfg_s")]
            mt_lines = []
            for idx, tone in enumerate(extra_tones):
                n_in = "VFG" if idx == 0 else f"n_xfg_s{idx}"
                n_out = f"%t{target_pin_name}" if idx == len(extra_tones) - 1 else f"n_xfg_s{idx + 1}"
                mt_lines.append(
                    f"v_xfg_s{idx + 1} {n_in} {n_out} dc 0 ac 0 sin(0 {tone.amplitude_vp:.6g} {tone.freq_hz:.6g} 0)"
                )
            full_tpl = CRLF_TOKEN.join(clean_lines + mt_lines) + CRLF_TOKEN
            exprt.set("String", "&ASC" + full_tpl)

        return cls.serialize_dom_restoring_multiline_attrs(root)
