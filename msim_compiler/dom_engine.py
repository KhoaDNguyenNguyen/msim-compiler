import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from .config_registry import GLOBAL_CONFIG

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
        anchor_y = [float(p.get("y") or 243) for p in self.parts]

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
        m = re.match(r'^([+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)\s*([a-zA-Z]*)$', s)
        if not m:
            return 1000.0
        num = float(m.group(1))
        suf = m.group(2)
        return num * GLOBAL_CONFIG.si_multipliers.get(suf, 1.0)

    @staticmethod
    def validate_xml_tree(xml_text: str) -> ET.Element:
        return ET.fromstring(xml_text.encode("utf-8"))

    @classmethod
    def update_spice_template_for_component(cls, xml_text: str, refdes: str, new_spice_template: str) -> str:
        comp_pat = re.compile(
            r'(<Item\s+CiID="\d+"\s+Class="CiComponent">\s*<CiComponent\s+[^>]*LocalName="&amp;ASC' + re.escape(refdes) + r'"[^>]*>.*?</CiComponent>\s*</Item>)',
            re.DOTALL
        )
        m = comp_pat.search(xml_text)
        if not m:
            return xml_text
        updated_blk = re.sub(
            r'(<CiaSpiceTmpltExprt\s+Class="CiaSpiceTmpltExprt"\s+String="&amp;ASC)([^"]+)(")',
            lambda tm: tm.group(1) + new_spice_template + tm.group(3),
            m.group(1),
            count=1
        )
        return xml_text[:m.start()] + updated_blk + xml_text[m.end():]
