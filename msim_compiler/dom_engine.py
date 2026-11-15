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
            val = m.group(2).replace(CRLF, CRLF_TOKEN).replace(LF, LF_TOKEN)
            return m.group(1) + val + m.group(3)
        protected_xml = re.sub(r'(\bString=")([^"]*)(")', protect_attr, xml_text)
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
        root = cls.parse_dom_preserving_multiline_attrs(xml_text)
        elements_node = root.find(".//CIITDiagram/Elements")
        if elements_node is None or len(harmonics) <= 1:
            return xml_text

        vgen_node_id = "490443665"
        vin_node_id = "490443664"
        xfg_plus_port_id = "495909056"
        xfg_vconn_port_id = "495902920"
        xfg_vconn_sym_id = "192713976"
        xfg_vconn_comp_id = "495902608"
        xfg_wire_ids = {"477615600", "477617544"}

        existing_vgen = None
        for item in elements_node.findall("Item[@Class='CiNode']"):
            if item.attrib.get("CiID") == vgen_node_id:
                existing_vgen = item
                break

        if existing_vgen is None:
            for item in elements_node.findall("Item[@Class='CiNode']"):
                if item.attrib.get("CiID") == vin_node_id:
                    vgen_item = ET.fromstring(ET.tostring(item, encoding="unicode"))
                    vgen_item.set("CiID", vgen_node_id)
                    cn = vgen_item.find("CiNode")
                    if cn is not None:
                        cn.set("LocalName", "&ASCVGEN")
                        ports_el = cn.find("Ports")
                        if ports_el is not None:
                            ports_el.clear()
                            ET.SubElement(ports_el, "Item", {"CiID": xfg_plus_port_id})
                            ET.SubElement(ports_el, "Item", {"CiID": xfg_vconn_port_id})
                    elements_node.append(vgen_item)
                    break

            for item in elements_node.findall("Item[@Class='CiNode']"):
                if item.attrib.get("CiID") == vin_node_id:
                    ports_el = item.find("CiNode/Ports")
                    if ports_el is not None:
                        for p_it in list(ports_el.findall("Item")):
                            if p_it.attrib.get("CiID") in (xfg_plus_port_id, xfg_vconn_port_id):
                                ports_el.remove(p_it)

            for item in elements_node.findall("Item[@Class='CiPort']"):
                if item.attrib.get("CiID") in (xfg_plus_port_id, xfg_vconn_port_id):
                    for n_it in item.findall(".//Nodes/Item"):
                        n_it.set("CiID", vgen_node_id)

            for item in elements_node.findall("Item[@Class='CiComponent']"):
                if item.attrib.get("CiID") == xfg_vconn_comp_id:
                    comp_el = item.find("CiComponent")
                    if comp_el is not None:
                        comp_el.set("LocalName", "&ASCVGEN")
                    for s_it in item.findall(".//CiaCollString/strings/Item"):
                        if s_it.attrib.get("Value") == "&ASCVIN":
                            s_it.set("Value", "&ASCVGEN")

            for item in elements_node.findall("Item[@Class='CiCircuit']"):
                nodes_el = item.find("CiCircuit/Nodes")
                if nodes_el is not None:
                    ET.SubElement(nodes_el, "Item", {"CiID": vgen_node_id})

            for obj_item in root.findall(".//CODComposite/Objects/Item"):
                oid = obj_item.attrib.get("ID", "")
                ocls = obj_item.attrib.get("Class", "")
                if ocls == "CVirtualConnector" and oid == xfg_vconn_sym_id:
                    for mod_it in obj_item.findall(".//Element[@NetModifier='&ASCNI_EWB_NET_VIRTUAL']/Item"):
                        mod_it.set("Value", "&ASCVGEN")
                    for txt_it in obj_item.findall(".//CIITSymTextCompName"):
                        txt_it.set("Output", "&ASCVGEN")
                elif ocls == "CIITLinkComp" and oid in xfg_wire_ids:
                    link_el = obj_item.find("CIITLinkComp")
                    if link_el is not None:
                        link_el.set("Node", vgen_node_id)

            net_items = root.find(".//CDesignNetContainer/NetLabels/Item/Items")
            if net_items is not None:
                ET.SubElement(net_items, "CSchDesignNetLabel", {
                    "Class": "CSchDesignNetLabel",
                    "Modifier": "8",
                    "Value": "",
                    "ID": "{1A94E944-0E79-4B74-AAA0-CCEB8D87E4DB}",
                    "Priority": "0",
                    "LastOverruleName": "&ASCVGEN",
                    "LastNetName": "&ASCVGEN",
                    "LastLocalName": "&ASCVGEN"
                })

        vin_ports_set = set()
        for item in elements_node.findall("Item[@Class='CiNode']"):
            cn = item.find("CiNode")
            if cn is not None and cn.attrib.get("LocalName") == "&ASCVIN":
                for p_it in cn.findall("./Ports/Item"):
                    pid = p_it.attrib.get("CiID", "")
                    if pid:
                        vin_ports_set.add(pid)

        comp_id_to_vin_pin = {}
        for item in elements_node.findall("Item[@Class='CiPort']"):
            pid = item.attrib.get("CiID", "")
            if pid in vin_ports_set:
                cp = item.find("CiPort")
                if cp is not None:
                    cid = cp.attrib.get("Component", "")
                    pname = cp.attrib.get("LocalName", "")
                    clean_pname = pname[4:] if pname.startswith("&ASC") else pname
                    if cid and clean_pname and clean_pname != "Dummy_VR_Pin":
                        comp_id_to_vin_pin[cid] = clean_pname

        target_comp_el = None
        target_pin_token = "%t1"

        if injection_target:
            wanted_lname = f"&ASC{injection_target}" if not injection_target.startswith("&ASC") else injection_target
            for item in elements_node.findall("Item[@Class='CiComponent']"):
                cid = item.attrib.get("CiID", "")
                comp_el = item.find("CiComponent")
                if comp_el is not None and comp_el.attrib.get("LocalName") == wanted_lname:
                    target_comp_el = comp_el
                    if cid in comp_id_to_vin_pin:
                        target_pin_token = f"%t{comp_id_to_vin_pin[cid]}"
                    break

        if target_comp_el is None:
            candidates = []
            for item in elements_node.findall("Item[@Class='CiComponent']"):
                cid = item.attrib.get("CiID", "")
                if cid not in comp_id_to_vin_pin:
                    continue
                comp_el = item.find("CiComponent")
                if comp_el is None:
                    continue
                lname = comp_el.attrib.get("LocalName", "")
                clean_ref = lname[4:] if lname.startswith("&ASC") else lname
                if clean_ref.upper().startswith(("XFG", "XSC", "XBP", "VIN", "VOUT", "VGEN", "0", "GND")):
                    continue
                exprt_el = comp_el.find(".//CiaSpiceTmpltExprt")
                if exprt_el is not None:
                    prio = 0 if clean_ref.upper().startswith("R") else (1 if clean_ref.upper().startswith(("L", "C")) else 2)
                    candidates.append((prio, clean_ref, comp_el, f"%t{comp_id_to_vin_pin[cid]}"))
            if candidates:
                candidates.sort(key=lambda x: (x[0], x[1]))
                target_comp_el = candidates[0][2]
                target_pin_token = candidates[0][3]

        if target_comp_el is not None:
            extra_tones = harmonics[1:]
            ext_lines: List[str] = []
            for idx, tone in enumerate(extra_tones):
                nin = "VGEN" if idx == 0 else f"n_harm_{idx}"
                nout = target_pin_token if idx == len(extra_tones) - 1 else f"n_harm_{idx+1}"
                ext_lines.append(
                    f"v_harm_{idx+1} {nout} {nin} dc 0 ac 0 sin(0 {tone.amplitude_vp:.6f} {tone.freq_hz:.12e} 0)"
                )
            exprt_el = target_comp_el.find(".//CiaSpiceTmpltExprt")
            if exprt_el is not None:
                raw_str = exprt_el.attrib.get("String", "&ASCr%p %t1 %t2 #1")
                body = raw_str[4:] if raw_str.startswith("&ASC") else raw_str
                body = body.replace(CRLF_TOKEN, LF).replace(LF_TOKEN, LF)
                clean_lines = [ln.strip() for ln in body.splitlines() if ln.strip() and not ln.strip().startswith("v_harm_")]
                final_lines = ext_lines + clean_lines
                exprt_el.set("String", "&ASC" + CRLF_TOKEN.join(final_lines) + CRLF_TOKEN)

        return cls.serialize_dom_restoring_multiline_attrs(root)
