import copy
import os
import re
import uuid
import xml.etree.ElementTree as ET
from typing import Callable, Dict, List, Optional, Tuple
from .codec import MS14Codec
from .dom_engine import MultisimDOMManipulator
from .logger import get_logger

class ComponentDomBuilder:
    def __init__(self, codec: Optional[MS14Codec] = None):
        self.logger = get_logger("DOM-BUILDER")
        self.codec = codec or MS14Codec()
        self.pkg_dir = os.path.dirname(os.path.abspath(__file__))
        self.config_dir = os.path.join(self.pkg_dir, "config")
        self.schema_path = os.path.join(self.config_dir, "ci_schema_skeletons.xml")
        self._skeletons: Dict[str, ET.Element] = {}
        self._load_or_build_schema_skeletons()

    @staticmethod
    def unesc(val: str) -> str:
        return val.replace("&amp;", "&")

    @staticmethod
    def serialize_element(elem: ET.Element) -> str:
        cloned = copy.deepcopy(elem)
        ET.indent(cloned, space="")
        raw = MultisimDOMManipulator.serialize_dom_restoring_multiline_attrs(cloned)
        raw = re.sub(r'\s+/>', '/>', raw)
        for tag in ("Ports", "Nodes", "ConnectList", "Links", "Models", "Components", "HiddenPins"):
            raw = raw.replace(f"<{tag}/>", f"<{tag}>\n</{tag}>")
        return raw

    def _load_or_build_schema_skeletons(self) -> None:
        os.makedirs(self.config_dir, exist_ok=True)
        if not os.path.exists(self.schema_path):
            self._extract_schema_from_reference_template()
        if os.path.exists(self.schema_path):
            try:
                tree_root = MultisimDOMManipulator.parse_dom_preserving_multiline_attrs(
                    open(self.schema_path, "r", encoding="utf-8").read()
                )
                for child in tree_root:
                    key = child.attrib.get("SkeletonKey", "")
                    if key and len(child) > 0:
                        self._skeletons[key] = copy.deepcopy(child[0])
                self.logger.info(f"Loaded {len(self._skeletons)} ElementTree CArchive schema skeletons from '{os.path.basename(self.schema_path)}'")
                return
            except Exception as ex:
                self.logger.warning(f"Failed loading cached schema skeletons ({ex}); falling back to pure-DOM generator")
        self._build_pure_dom_fallback_skeletons()

    def _extract_schema_from_reference_template(self) -> None:
        base_tpl = os.path.join(self.pkg_dir, "templates", "base_sallen_key.ms14")
        if not os.path.exists(base_tpl):
            self._build_pure_dom_fallback_skeletons()
            return

        xml_str = self.codec.decode_to_string(base_tpl).replace("\r\n", "\n")
        root = MultisimDOMManipulator.parse_dom_preserving_multiline_attrs(xml_str)
        schema_root = ET.Element("MultisimCArchiveSchema")

        gnd_sym_id = ""
        for item in root.findall(".//CIITDiagram/Elements/Item[@Class='CiComponent']"):
            comp = item.find("CiComponent")
            if comp is None:
                continue
            lname = comp.attrib.get("LocalName", "")
            if lname == "&ASCR1":
                sk_res = copy.deepcopy(comp)
                self._sanitize_passive_skeleton(sk_res)
                wrap_r = ET.SubElement(schema_root, "Skeleton", {"SkeletonKey": "PASSIVE_RESISTOR"})
                wrap_r.append(sk_res)
            elif lname == "&ASCC1":
                sk_cap = copy.deepcopy(comp)
                self._sanitize_passive_skeleton(sk_cap)
                wrap_c = ET.SubElement(schema_root, "Skeleton", {"SkeletonKey": "PASSIVE_VIRTUAL"})
                wrap_c.append(sk_cap)
            elif lname == "&ASCU2":
                sk_active = copy.deepcopy(comp)
                self._sanitize_active_skeleton(sk_active)
                wrap = ET.SubElement(schema_root, "Skeleton", {"SkeletonKey": "ACTIVE_IC"})
                wrap.append(sk_active)
            elif lname == "&ASCVCC":
                sk_vcc = copy.deepcopy(comp)
                ports_el = sk_vcc.find("Ports")
                if ports_el is not None:
                    ports_el.clear()
                wrap_vcc = ET.SubElement(schema_root, "Skeleton", {"SkeletonKey": "VCC"})
                wrap_vcc.append(sk_vcc)
            elif lname == "&ASCVEE":
                sk_vee = copy.deepcopy(comp)
                ports_el = sk_vee.find("Ports")
                if ports_el is not None:
                    ports_el.clear()
                wrap_vee = ET.SubElement(schema_root, "Skeleton", {"SkeletonKey": "VEE"})
                wrap_vee.append(sk_vee)
            elif "GROUND" not in [c.attrib.get("SkeletonKey") for c in schema_root]:
                raw_sub = ET.tostring(comp, encoding="unicode")
                if "POWER_SOURCES" in raw_sub and "GROUND" in raw_sub:
                    gnd_sym_id = comp.attrib.get("SymCompID", "")
                    sk_gnd = copy.deepcopy(comp)
                    ports_el = sk_gnd.find("Ports")
                    if ports_el is not None:
                        ports_el.clear()
                    wrap = ET.SubElement(schema_root, "Skeleton", {"SkeletonKey": "GROUND"})
                    wrap.append(sk_gnd)

        if gnd_sym_id:
            for obj_item in root.findall(".//CODComposite/Objects/Item[@Class='CIITSymbolComp']"):
                if obj_item.attrib.get("ID") == gnd_sym_id:
                    sk_gnd_sym = copy.deepcopy(obj_item)
                    for cl in sk_gnd_sym.findall(".//ConnectList"):
                        cl.clear()
                    wrap_gs = ET.SubElement(schema_root, "Skeleton", {"SkeletonKey": "GROUND_SYM"})
                    wrap_gs.append(sk_gnd_sym)
                    break

        for obj_item in root.findall(".//CODComposite/Objects/Item[@Class='CVirtualConnector']"):
            vc_el = obj_item.find("CVirtualConnector")
            if vc_el is not None:
                vc_cid = vc_el.attrib.get("CiComponent", "")
                sk_vc_sym = copy.deepcopy(obj_item)
                self._sanitize_vconn_sym_skeleton(sk_vc_sym)
                wrap_sym = ET.SubElement(schema_root, "Skeleton", {"SkeletonKey": "VCONN_SYM"})
                wrap_sym.append(sk_vc_sym)
                for c_item in root.findall(".//CIITDiagram/Elements/Item[@Class='CiComponent']"):
                    if c_item.attrib.get("CiID") == vc_cid:
                        c_el = c_item.find("CiComponent")
                        if c_el is not None:
                            sk_vc_comp = copy.deepcopy(c_el)
                            self._sanitize_vconn_comp_skeleton(sk_vc_comp)
                            wrap_comp = ET.SubElement(schema_root, "Skeleton", {"SkeletonKey": "VCONN_COMP"})
                            wrap_comp.append(sk_vc_comp)
                        break
                break

        serialized = self.serialize_element(schema_root)
        with open(self.schema_path, "w", encoding="utf-8") as f:
            f.write(serialized)
        self.logger.info(f"Extracted sanitized CArchive DOM schema to '{self.schema_path}'")

    @staticmethod
    def _sanitize_passive_skeleton(comp_el: ET.Element) -> None:
        for k in list(comp_el.attrib.keys()):
            if k != "Class":
                del comp_el.attrib[k]
        ports_el = comp_el.find("Ports")
        if ports_el is not None:
            ports_el.clear()
        for el in comp_el.iter():
            if el.tag == "CiaSpiceTmpltExprt":
                el.set("String", "&ASC__MSIM_SPICE_TPL__")
            elif el.tag == "CiaDOUBLE" and el.attrib.get("Double") in ("200", "204", "208"):
                el.set("Double", "__MSIM_CIA_DBL__")
            elif el.tag == "Item" and "Value" in el.attrib:
                val = el.attrib["Value"]
                if val in ("&ASCCAPACITOR_VIRTUAL", "&ASCRESISTOR_VIRTUAL"):
                    el.set("Value", "&ASC__MSIM_COMP_NAME__")
                elif val in ("&ASCCAPACITOR", "&ASCRESISTOR"):
                    el.set("Value", "&ASC__MSIM_FAM_SHORT__")
                elif val in ("&ASCVIRTUAL_CAPACITANCE", "&ASCVIRTUAL_RESISTANCE"):
                    el.set("Value", "&ASC__MSIM_FUNC_DESC__")
                elif val in ("&ASCCapacitor", "&ASCResistor"):
                    el.set("Value", "&ASC__MSIM_TYPE_LABEL__")
                elif val in ("&ASCF", "&UNI_uc103a9"):
                    el.set("Value", "__MSIM_UNIT_SYM__")
                elif val in ("&ASC31", "&ASC29", "&ASC172", "&ASC173"):
                    el.set("Value", "&ASC__MSIM_FAM_ID__")
                elif val in ("&ASCC%d", "&ASCR%d"):
                    el.set("Value", "&ASC__MSIM_REF_TPL__")
                elif val in ("&ASC1.5nF", "&UNI6.8k_uc103a9"):
                    el.set("Value", "__MSIM_DISP_VAL__")
                elif val in ("&ASC1.5n", "&ASC6.8k"):
                    el.set("Value", "&ASC__MSIM_SPICE_VAL__")
                elif val.startswith("&ASCr%p") or val.startswith("&ASCc%p") or val.startswith("&ASCl%p"):
                    el.set("Value", "&ASC__MSIM_SPICE_TPL__")
        for plist in comp_el.findall(".//CiaParamList") + comp_el.findall(".//CiaProperDlg"):
            dbl_items = plist.findall(".//doubles/Item")
            if dbl_items and "Value" in dbl_items[0].attrib:
                dbl_items[0].set("Value", "__MSIM_NUM_VAL__")

    @staticmethod
    def _sanitize_active_skeleton(comp_el: ET.Element) -> None:
        for k in list(comp_el.attrib.keys()):
            if k != "Class":
                del comp_el.attrib[k]
        ports_el = comp_el.find("Ports")
        if ports_el is not None:
            ports_el.clear()
        for el in comp_el.iter():
            if el.tag == "CiaSpiceTmpltExprt":
                el.set("String", "&ASC__MSIM_SPICE_TPL__ ")
            elif el.tag == "Item" and "Value" in el.attrib:
                val = el.attrib["Value"]
                if val == "&ASCOPAMP":
                    el.set("Value", "&ASC__MSIM_FAM_NAME__")
                elif val == "&ASC741":
                    el.set("Value", "&ASC__MSIM_COMP_NAME__")
                elif val == "&ASCAnalog":
                    el.set("Value", "&ASC__MSIM_FAM_GROUP__")
                elif val == "&ASCOperational Amplifier":
                    el.set("Value", "&ASC__MSIM_FUNC_DESC__")
                elif val == "&ASC46":
                    el.set("Value", "&ASC__MSIM_FAM_ID__")
                elif val == "&ASCU%d":
                    el.set("Value", "&ASC__MSIM_REF_TPL__")
                elif val.startswith("&ASCx%p"):
                    el.set("Value", "&ASC__MSIM_SPICE_TPL__")
                elif val.startswith("&ASC.SUBCKT 741"):
                    el.set("Value", "&ASC__MSIM_SPICE_MODEL__")
                elif val.startswith("&ASCInput_Voffset="):
                    el.set("Value", "")
                elif val in ("&ASCDIP-8", "&ASC Default V4 5 Terminal Opamp "):
                    el.set("Value", "")

    @staticmethod
    def _sanitize_vconn_sym_skeleton(item_el: ET.Element) -> None:
        for el in item_el.iter():
            if el.tag == "CIITSymTextCompName":
                el.set("Output", "&ASC__MSIM_NET_NAME__")
            elif el.tag == "Item" and el.attrib.get("Value") in ("&ASCVIN", "&ASCVOUT", "&ASCVGEN", "&ASCVin", "&ASCVout"):
                el.set("Value", "&ASC__MSIM_NET_NAME__")
                if "Guid" in el.attrib:
                    el.set("Guid", "__MSIM_VCONN_MOD_GUID__")
            elif el.tag == "ConnectList":
                el.clear()

    @staticmethod
    def _sanitize_vconn_comp_skeleton(comp_el: ET.Element) -> None:
        for k in list(comp_el.attrib.keys()):
            if k != "Class":
                del comp_el.attrib[k]
        ports_el = comp_el.find("Ports")
        if ports_el is not None:
            ports_el.clear()
        for el in comp_el.iter():
            if el.tag == "Item" and el.attrib.get("Value") in ("&ASCVIN", "&ASCVOUT", "&ASCVGEN", "&ASCVin", "&ASCVout"):
                el.set("Value", "&ASC__MSIM_NET_NAME__")

    def _build_pure_dom_fallback_skeletons(self) -> None:
        pas = ET.Element("CiComponent", {"Class": "CiComponent"})
        attrs_p = ET.SubElement(pas, "Attributes")
        ET.SubElement(attrs_p, "Item")
        it_sp = ET.SubElement(attrs_p, "Item")
        ET.SubElement(it_sp, "CiaSpiceTmpltExprt", {"Class": "CiaSpiceTmpltExprt", "String": "&ASC__MSIM_SPICE_TPL__"})
        it_pl = ET.SubElement(attrs_p, "Item")
        pl = ET.SubElement(it_pl, "CiaParamList", {"Class": "CiaParamList"})
        ET.SubElement(pl, "doubles")
        ET.SubElement(pl, "strings")
        prms = ET.SubElement(pl, "parameters")
        ET.SubElement(prms, "Item", {"Value": "&ASC0"})
        ET.SubElement(prms, "Item", {"Value": "&ASC__MSIM_SPICE_VAL__"})
        ET.SubElement(pl, "paramindicators")
        ET.SubElement(attrs_p, "Item")
        it_cs = ET.SubElement(attrs_p, "Item")
        cs = ET.SubElement(it_cs, "CiaCollString", {"Class": "CiaCollString"})
        strs = ET.SubElement(cs, "strings")
        for tok in ("&ASC__MSIM_FAM_SHORT__", "&ASC__MSIM_COMP_NAME__", "&ASC__MSIM_FAM_SHORT__", "&ASC__MSIM_FUNC_DESC__", "&ASC__MSIM_TYPE_LABEL__", "&ASC__MSIM_FAM_ID__", "&ASC__MSIM_REF_TPL__", "&ASC__MSIM_SPICE_TPL__"):
            ET.SubElement(strs, "Item", {"Value": tok})
        it_dlg = ET.SubElement(attrs_p, "Item")
        dlg = ET.SubElement(it_dlg, "CiaProperDlg", {"Class": "CiaProperDlg"})
        ET.SubElement(dlg, "doubles")
        ET.SubElement(dlg, "strings")
        prms2 = ET.SubElement(dlg, "parameters")
        ET.SubElement(prms2, "Item", {"Value": "&ASC0"})
        ET.SubElement(prms2, "Item", {"Value": "&ASC__MSIM_SPICE_VAL__"})
        ET.SubElement(dlg, "paramindicators")
        it_dbl = ET.SubElement(attrs_p, "Item")
        ET.SubElement(it_dbl, "CiaDOUBLE", {"Class": "CiaDOUBLE", "Double": "__MSIM_CIA_DBL__"})
        ET.SubElement(pas, "Ports")
        self._skeletons["PASSIVE_VIRTUAL"] = pas
        self._skeletons["PASSIVE_RESISTOR"] = copy.deepcopy(pas)
        self._skeletons["ACTIVE_IC"] = copy.deepcopy(pas)
        self._skeletons["GROUND"] = copy.deepcopy(pas)

    def build_cicomponent_dom(
        self,
        part,
        refdes: str,
        ci_id: int,
        sym_id: int,
        circuit_id: int,
        comp_guid: str,
        port_ids: List[int],
        spice_val: str,
        disp_val: str,
        num_val: float,
        model_ci_id: Optional[int] = None,
        model_idx: int = 1
    ) -> Tuple[ET.Element, Optional[ET.Element], str, str, str]:
        comp_up = part.comp_name.upper()
        fam_up = part.family_name.upper()
        ref_up = refdes.upper()

        is_vcc = (comp_up == "VCC") or ref_up.startswith("VCC")
        is_vee = (comp_up == "VEE") or ref_up.startswith("VEE")
        is_ground = ("GROUND" in comp_up) or (ref_up == "0") or ref_up.startswith("GND")
        is_capacitor = ("CAPACITOR" in comp_up) or ("CAPACITOR" in fam_up) or (ref_up.startswith("C") and not is_ground and not is_vcc)
        is_inductor = ("INDUCTOR" in comp_up) or ("INDUCTOR" in fam_up) or (ref_up.startswith("L") and not is_ground)
        is_resistor = ("RESISTOR" in comp_up) or ("RESISTOR" in fam_up) or (ref_up.startswith("R") and not is_ground)
        is_passive_virtual = is_capacitor or is_inductor or is_resistor

        extra_model_item: Optional[ET.Element] = None

        if is_vcc:
            local_ref_name = "VCC"
            inst_ref_attr = ""
            lbl_sel_attr = "0"
            sk_comp = copy.deepcopy(self._skeletons.get("VCC", self._skeletons["GROUND"]))
        elif is_vee:
            local_ref_name = "VEE"
            inst_ref_attr = ""
            lbl_sel_attr = "0"
            sk_comp = copy.deepcopy(self._skeletons.get("VEE", self._skeletons["GROUND"]))
        elif is_ground:
            local_ref_name = "0"
            inst_ref_attr = ""
            lbl_sel_attr = "0"
            sk_comp = copy.deepcopy(self._skeletons.get("GROUND", self._skeletons["PASSIVE_VIRTUAL"]))
        elif is_passive_virtual:
            local_ref_name = refdes
            inst_ref_attr = ""
            lbl_sel_attr = "1"
            if is_resistor and "PASSIVE_RESISTOR" in self._skeletons:
                sk_comp = copy.deepcopy(self._skeletons["PASSIVE_RESISTOR"])
            else:
                sk_comp = copy.deepcopy(self._skeletons["PASSIVE_VIRTUAL"])

            if is_capacitor:
                eff_spice_tpl = f"c%p %t1 %t2 {num_val:.6e}"
                fam_id_str = str(part.family_id if part.family_id and part.family_id != 29 else 31)
                ref_tpl_str = "C%d"
                cia_dbl_str = "204"
                comp_name_str = part.comp_name if "CAPACITOR" in comp_up else "CAPACITOR_VIRTUAL"
                fam_short_str = "CAPACITOR"
                func_desc_str = "VIRTUAL_CAPACITANCE"
                type_lbl_str = "Capacitor"
                unit_sym_str = "&ASCF"
            elif is_inductor:
                eff_spice_tpl = f"l%p %t1 %t2 {num_val:.6e}"
                fam_id_str = str(part.family_id if part.family_id and part.family_id != 29 else 30)
                ref_tpl_str = "L%d"
                cia_dbl_str = "208"
                comp_name_str = part.comp_name if "INDUCTOR" in comp_up else "INDUCTOR_VIRTUAL"
                fam_short_str = "INDUCTOR"
                func_desc_str = "VIRTUAL_INDUCTANCE"
                type_lbl_str = "Inductor"
                unit_sym_str = "&ASCH"
            else:
                eff_spice_tpl = f"r%p %t1 %t2 {num_val:.6e}"
                fam_id_str = str(part.family_id or 29)
                ref_tpl_str = "R%d"
                cia_dbl_str = "200"
                comp_name_str = part.comp_name if "RESISTOR" in comp_up else "RESISTOR_VIRTUAL"
                fam_short_str = "RESISTOR"
                func_desc_str = "VIRTUAL_RESISTANCE"
                type_lbl_str = "Resistor"
                unit_sym_str = "&UNI_uc103a9"

            token_map = {
                "&ASC__MSIM_SPICE_TPL__": f"&ASC{eff_spice_tpl}",
                "__MSIM_CIA_DBL__": cia_dbl_str,
                "&ASC__MSIM_COMP_NAME__": f"&ASC{comp_name_str}",
                "&ASC__MSIM_FAM_SHORT__": f"&ASC{fam_short_str}",
                "&ASC__MSIM_FUNC_DESC__": f"&ASC{func_desc_str}",
                "&ASC__MSIM_TYPE_LABEL__": f"&ASC{type_lbl_str}",
                "__MSIM_UNIT_SYM__": unit_sym_str,
                "&ASC__MSIM_FAM_ID__": f"&ASC{fam_id_str}",
                "&ASC__MSIM_REF_TPL__": f"&ASC{ref_tpl_str}",
                "__MSIM_DISP_VAL__": self.unesc(disp_val),
                "&ASC__MSIM_SPICE_VAL__": f"&ASC{spice_val}",
                "__MSIM_NUM_VAL__": f"{num_val:g}"
            }
            for el in sk_comp.iter():
                for attr_k, attr_v in list(el.attrib.items()):
                    if attr_v in token_map:
                        el.set(attr_k, token_map[attr_v])
                if el.tag == "CiaSpiceTmpltExprt":
                    el.set("String", f"&ASC{eff_spice_tpl}")

            for plist in sk_comp.findall(".//CiaParamList") + sk_comp.findall(".//CiaProperDlg"):
                dbl_items = plist.findall(".//doubles/Item")
                if dbl_items and "Value" in dbl_items[0].attrib:
                    dbl_items[0].set("Value", f"{num_val:g}")
                prm_items = plist.findall("./parameters/Item")
                if len(prm_items) >= 2:
                    prm_items[1].set("Value", f"&ASC{spice_val}")
        else:
            local_ref_name = refdes
            inst_ref_attr = ""
            lbl_sel_attr = "1"
            sk_comp = copy.deepcopy(self._skeletons.get("ACTIVE_IC", self._skeletons["PASSIVE_VIRTUAL"]))

            if "DIODE" in fam_up or "1N4148" in comp_up or ref_up.startswith("D"):
                mod_local = f"D1N4148__DIODE__{model_idx}"
                mod_spice_line = f".MODEL {mod_local} D(Is=2.52e-9 Rs=0.568 N=1.752 Cjo=4e-12 M=0.4 tt=20e-9)"
                eff_spice_tpl = "d%p %tA %tK %m"
            elif "BJT" in fam_up or "2N2222" in comp_up or ref_up.startswith("Q"):
                mod_local = f"2N2222A__BJT_NPN__{model_idx}"
                mod_spice_line = f".MODEL {mod_local} NPN(Is=14.34e-15 Bf=255.9 Vaf=74.03 Ikf=0.2847 Ne=1.307 Br=6.092 Rc=1 Rb=10 Cjc=7.306e-12 Cje=22.01e-12 Tf=411.1e-12 Tr=46.91e-9)"
                eff_spice_tpl = "q%p %tC %tB %tE %m"
            elif "OPAMP" in fam_up or "741" in comp_up or ref_up.startswith("U"):
                mod_local = f"741__OPAMP__{model_idx}"
                mod_spice_line = f".SUBCKT {mod_local} 1 2 3 4 5__MSIM_CRLF__VC 5 15 DC 1.68573V__MSIM_CRLF__VE 12 4 DC 1.68573V__MSIM_CRLF__IEE 10 4 DC 1.516e-005A__MSIM_CRLF__R1  10  0 10Gohm__MSIM_CRLF__R6  11  0 100Kohm__MSIM_CRLF__R7   5  4 1Kohm__MSIM_CRLF__Rc1  6  5 5305.16ohm__MSIM_CRLF__Rc2  5  7 5305.16ohm__MSIM_CRLF__Re1  9 10 1839.19ohm__MSIM_CRLF__Re2  8 10 1839.19ohm__MSIM_CRLF__Ro1  1 14 37.5ohm__MSIM_CRLF__Ro2 14  0 37.5ohm__MSIM_CRLF__Ree 10  0 1.31926e+007ohm__MSIM_CRLF__Rcc  0 13 2.20906e-005ohm__MSIM_CRLF__Cee  0 10 1e-012__MSIM_CRLF__Cc  14 11 3e-011__MSIM_CRLF__C1   6  7 1e-016__MSIM_CRLF__GA  11  0  6  7 0.000188496__MSIM_CRLF__GC   0 13  1  0 45268.1__MSIM_CRLF__GB  14  0 11  0 282.942__MSIM_CRLF__GCM  0 11 10  0 5.96075e-009__MSIM_CRLF__D1  14 13 Dopamp1__MSIM_CRLF__D2  13 14 Dopamp1__MSIM_CRLF__D3   1 15 Dopamp2__MSIM_CRLF__D4  12  1 Dopamp2__MSIM_CRLF__Qt1  6 2 9 Qopamp1__MSIM_CRLF__Qt2  7 3 8 Qopamp2__MSIM_CRLF__.MODEL Dopamp1 D (Is=7.53769e-014A Rs=0 Cjo=0F Vj=750mV Tt=0s M=0)__MSIM_CRLF__.MODEL Dopamp2 D (Is=8e-016A Rs=0 Cjo=0F Vj=750mV Tt=0s M=0)__MSIM_CRLF__.MODEL Qopamp1 NPN (Is=8e-016A BF=83.3333 BR=960m__MSIM_CRLF__+       Rb=0ohm Re=0ohm Rc=0ohm Cjs=0F Cje=0F Cjc=0F__MSIM_CRLF__+       Vje=750m Vjc=750m Tf=0 Tr=0 mje=0 mjc=0 VA=50)__MSIM_CRLF__.MODEL Qopamp2 NPN (Is=8.30948e-016A BF=107.143 BR=960m__MSIM_CRLF__+       Rb=0ohm Re=0ohm Rc=0ohm Cjs=0F Cje=0F Cjc=0F__MSIM_CRLF__+       Vje=750m Vjc=750m Tf=0 Tr=0 mje=0 mjc=0 VA=50)__MSIM_CRLF__.ENDS"
                pin_names_set = {p.name for p in part.pins}
                if "IN+" in pin_names_set and "OUT" in pin_names_set:
                    eff_spice_tpl = "x%p %tOUT %tIN- %tIN+ %tVS- %tVS+ %m"
                else:
                    eff_spice_tpl = "x%p %t6 %t2 %t3 %t4 %t7 %m"
            else:
                mod_local = f"{part.comp_name}__{part.family_name}__{model_idx}"
                raw_sp = " ".join(part.spice_data.splitlines()).strip()
                mod_spice_line = raw_sp if raw_sp else f".MODEL {mod_local} D()"
                eff_spice_tpl = part.spice_template.strip() or "x%p %m"

            token_map = {
                "&ASC__MSIM_SPICE_TPL__ ": f"&ASC{eff_spice_tpl} ",
                "&ASC__MSIM_SPICE_TPL__": f"&ASC{eff_spice_tpl}",
                "&ASC__MSIM_FAM_NAME__": f"&ASC{part.family_name}",
                "&ASC__MSIM_COMP_NAME__": f"&ASC{part.comp_name}",
                "&ASC__MSIM_FAM_GROUP__": f"&ASC{part.family_group}",
                "&ASC__MSIM_FUNC_DESC__": f"&ASC{part.function_desc}",
                "&ASC__MSIM_FAM_ID__": f"&ASC{part.family_id}",
                "&ASC__MSIM_REF_TPL__": f"&ASC{part.ref_template}",
                "&ASC__MSIM_SPICE_MODEL__": f"&ASC{mod_spice_line}"
            }
            for el in sk_comp.iter():
                for attr_k, attr_v in list(el.attrib.items()):
                    if attr_v in token_map:
                        el.set(attr_k, token_map[attr_v])
                if el.tag == "CiaSpiceTmpltExprt":
                    el.set("String", f"&ASC{eff_spice_tpl} ")

            if model_ci_id is not None:
                extra_model_item = ET.Element("Item", {"CiID": str(model_ci_id), "Class": "CiModel"})
                ci_mod = ET.SubElement(extra_model_item, "CiModel", {
                    "Class": "CiModel",
                    "LocalName": f"&ASC{mod_local}",
                    "ChangedByUser": "0",
                    "Scope": str(circuit_id)
                })
                m_attrs = ET.SubElement(ci_mod, "Attributes")
                ET.SubElement(m_attrs, "Item")
                m_it1 = ET.SubElement(m_attrs, "Item")
                ET.SubElement(m_it1, "CiaCString", {"Class": "CiaCString", "String": f"&ASC{mod_spice_line}"})
                m_it2 = ET.SubElement(m_attrs, "Item")
                m_pl = ET.SubElement(m_it2, "CiaParamList", {"Class": "CiaParamList"})
                ET.SubElement(m_pl, "doubles")
                ET.SubElement(m_pl, "strings")
                ET.SubElement(m_pl, "parameters")
                ET.SubElement(m_pl, "paramindicators")

        sk_comp.set("Class", "CiComponent")
        sk_comp.set("LocalName", f"&ASC{local_ref_name}")
        if model_ci_id is not None:
            sk_comp.set("Model", str(model_ci_id))
        sk_comp.set("SymCompID", str(sym_id))
        sk_comp.set("InstanceID", comp_guid)
        sk_comp.set("Circuit", str(circuit_id))

        ports_el = sk_comp.find("Ports")
        if ports_el is None:
            ports_el = ET.SubElement(sk_comp, "Ports")
        ports_el.clear()
        for pid in port_ids:
            ET.SubElement(ports_el, "Item", {"CiID": str(pid)})

        comp_item = ET.Element("Item", {"CiID": str(ci_id), "Class": "CiComponent"})
        comp_item.append(sk_comp)

        return comp_item, extra_model_item, local_ref_name, self.unesc(inst_ref_attr), lbl_sel_attr

    @staticmethod
    def build_ciport_dom(part, p_spec, port_ci_id: int, comp_ci_id: int) -> ET.Element:
        item = ET.Element("Item", {"CiID": str(port_ci_id), "Class": "CiPort"})
        port = ET.SubElement(item, "CiPort", {
            "Class": "CiPort",
            "LocalName": f"&ASC{p_spec.name}",
            "Component": str(comp_ci_id)
        })
        attrs = ET.SubElement(port, "Attributes")
        for _ in range(4):
            ET.SubElement(attrs, "Item")

        eff_pin_fam = "NON" if part.pin_family in ("", "$ANALOG$") else part.pin_family
        it_cs1 = ET.SubElement(attrs, "Item")
        cs1 = ET.SubElement(it_cs1, "CiaCollString", {"Class": "CiaCollString"})
        strs1 = ET.SubElement(cs1, "strings")
        for val in (f"&ASC{part.family_name}", f"&ASC{part.comp_name}", f"&ASC{eff_pin_fam}", "", "&ASC1", ""):
            ET.SubElement(strs1, "Item", {"Value": val})

        it_cs2 = ET.SubElement(attrs, "Item")
        cs2 = ET.SubElement(it_cs2, "CiaCollString", {"Class": "CiaCollString"})
        strs2 = ET.SubElement(cs2, "strings")
        for val in ("", "", "", "", "", "", f"&ASC{p_spec.number}", "", f"&ASC{p_spec.pin_group}", "", f"&ASC{p_spec.pin_type}", f"&ASC{p_spec.pin_type}"):
            ET.SubElement(strs2, "Item", {"Value": val})

        ET.SubElement(attrs, "Item")
        it_nt = ET.SubElement(attrs, "Item")
        ET.SubElement(it_nt, "CiaNodeType", {"Class": "CiaNodeType", "DWORD": "256"})
        for _ in range(26):
            ET.SubElement(attrs, "Item")
        is_gnd_port = ("GROUND" in part.comp_name.upper()) or ("POWER_SOURCES" in part.family_name.upper())
        if is_gnd_port:
            s2_items = strs2.findall("Item")
            if len(s2_items) >= 12:
                s2_items[10].set("Value", "")
                s2_items[11].set("Value", "")
        it_d1 = ET.SubElement(attrs, "Item")
        ET.SubElement(it_d1, "CiaDOUBLE", {"Class": "CiaDOUBLE", "Double": "14" if is_gnd_port else "6"})
        for _ in range(3):
            ET.SubElement(attrs, "Item")
        it_d2 = ET.SubElement(attrs, "Item")
        ET.SubElement(it_d2, "CiaDOUBLE", {"Class": "CiaDOUBLE", "Double": "1"})
        ET.SubElement(attrs, "Item")
        it_d3 = ET.SubElement(attrs, "Item")
        ET.SubElement(it_d3, "CiaDOUBLE", {"Class": "CiaDOUBLE", "Double": "0"})
        ET.SubElement(port, "Nodes")
        return item

    @staticmethod
    def build_vconn_ciport_dom(port_ci_id: int, comp_ci_id: int) -> ET.Element:
        item = ET.Element("Item", {"CiID": str(port_ci_id), "Class": "CiPort"})
        port = ET.SubElement(item, "CiPort", {
            "Class": "CiPort",
            "LocalName": "",
            "Component": str(comp_ci_id)
        })
        attrs = ET.SubElement(port, "Attributes")
        for _ in range(40):
            ET.SubElement(attrs, "Item")
        it_d = ET.SubElement(attrs, "Item")
        ET.SubElement(it_d, "CiaDOUBLE", {"Class": "CiaDOUBLE", "Double": "0"})
        ET.SubElement(port, "Nodes")
        return item

    def build_virtual_connector_dom(
        self,
        net_name: str,
        box_x: int,
        box_y: int,
        is_input: bool,
        vc_sym_id: int,
        vc_ci_id: int,
        vc_port_id: int,
        vc_guid: str,
        circuit_id: int,
        alloc_id_fn: Callable[[], int]
    ) -> Tuple[ET.Element, ET.Element, ET.Element, int, int, int, str]:
        sym_item = copy.deepcopy(self._skeletons["VCONN_SYM"])
        comp_el = copy.deepcopy(self._skeletons["VCONN_COMP"])
        mod_guid = "{" + str(uuid.uuid4()).upper() + "}"

        loc_px, loc_py = 5, 0
        pc_el = sym_item.find(".//CIITPinConnectorComp")
        if pc_el is not None:
            loc_px = int(float(pc_el.attrib.get("ptCenterX", "5")))
            loc_py = int(float(pc_el.attrib.get("ptCenterY", "0")))

        id_map: Dict[str, int] = {}
        first_seen = True
        for el in sym_item.iter():
            if "ID" in el.attrib:
                old_id = el.attrib["ID"]
                if old_id not in id_map:
                    if first_seen:
                        id_map[old_id] = vc_sym_id
                        first_seen = False
                    else:
                        id_map[old_id] = alloc_id_fn()
                el.set("ID", str(id_map[old_id]))

        vc_root = sym_item.find("CVirtualConnector")
        if vc_root is not None:
            vc_root.set("CiComponent", str(vc_ci_id))
            vc_root.set("Transformer-M20", str(box_x))
            vc_root.set("Transformer-M21", str(box_y))

        pin_sym = sym_item.find(".//CIITPinSymbolComp")
        if pin_sym is not None:
            pin_sym.set("PortID", str(vc_port_id))

        txt_el = sym_item.find(".//CIITSymTextCompName")
        if txt_el is not None:
            txt_el.set("Output", f"&ASC{net_name}")
            if is_input:
                txt_el.set("Transformer-M20", "-18")
                txt_el.set("Transformer-M21", "-6")
                txt_el.set("HorizontalAlign", "2")
            else:
                txt_el.set("Transformer-M20", "18")
                txt_el.set("Transformer-M21", "-6")
                txt_el.set("HorizontalAlign", "0")

        for el in sym_item.iter():
            if el.tag == "Item":
                if el.attrib.get("Value") in ("&ASC__MSIM_NET_NAME__", "&ASCVIN", "&ASCVOUT", "&ASCVin", "&ASCVout"):
                    el.set("Value", f"&ASC{net_name}")
                if "Guid" in el.attrib:
                    el.set("Guid", mod_guid)
            elif el.tag == "ConnectList":
                el.clear()

        pin_conn_id = alloc_id_fn()
        for item_sub in sym_item.findall(".//Item[@Class='CIITPinConnectorComp']"):
            pin_conn_id = int(item_sub.attrib["ID"])
            break

        comp_el.set("Class", "CiComponent")
        comp_el.set("LocalName", f"&ASC{net_name}")
        comp_el.set("SymCompID", str(vc_sym_id))
        comp_el.set("InstanceID", vc_guid)
        comp_el.set("Circuit", str(circuit_id))
        for el in comp_el.iter():
            if el.tag == "Item" and el.attrib.get("Value") == "&ASC__MSIM_NET_NAME__":
                el.set("Value", f"&ASC{net_name}")

        ports_el = comp_el.find("Ports")
        if ports_el is None:
            ports_el = ET.SubElement(comp_el, "Ports")
        ports_el.clear()
        ET.SubElement(ports_el, "Item", {"CiID": str(vc_port_id)})

        comp_item = ET.Element("Item", {"CiID": str(vc_ci_id), "Class": "CiComponent"})
        comp_item.append(comp_el)

        port_item = self.build_vconn_ciport_dom(vc_port_id, vc_ci_id)

        return sym_item, comp_item, port_item, pin_conn_id, loc_px, loc_py, mod_guid
