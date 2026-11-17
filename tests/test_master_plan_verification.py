import os
import re
import glob
import math
import struct
import zlib
import tempfile
from typing import Dict, List, Tuple
from msim_compiler import (
    ComponentDomBuilder,
    DatabaseBridgeError,
    DatabaseDecryptionError,
    HarmonicTone,
    MS14Codec,
    MS14Document,
    MultisimDOMManipulator,
    MultisimDatabaseBridge,
    MultisimHighOrderCompiler,
    MultisimSchematicBuilder,
    NetConnectionSpec,
    OpticalCircuitParser,
    PartPlacementSpec,
    get_logger
)

logger = get_logger("MASTER-VERIFY")

def solve_linear_system_complex(mat: List[List[complex]], rhs: List[complex]) -> List[complex]:
    n = len(rhs)
    a = [row[:] + [rhs[i]] for i, row in enumerate(mat)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(a[r][col]))
        if abs(a[pivot][col]) < 1e-18:
            raise RuntimeError(f"Singular Nodal Admittance Matrix detected at column {col} (Floating Node or Short-Circuit Loop)")
        a[col], a[pivot] = a[pivot], a[col]
        piv_val = a[col][col]
        for j in range(col, n + 1):
            a[col][j] /= piv_val
        for r in range(n):
            if r != col and abs(a[r][col]) > 0.0:
                factor = a[r][col]
                for j in range(col, n + 1):
                    a[r][j] -= factor * a[col][j]
    return [a[i][n] for i in range(n)]

def extract_and_solve_ac_transfer_function(xml_text: str, freq_hz: float, perturb_ref: str = "", perturb_scale: float = 1.0) -> float:
    root = MultisimDOMManipulator.parse_dom_preserving_multiline_attrs(xml_text)
    elements_el = root.find(".//CIITDiagram/Elements")
    assert elements_el is not None

    port_to_node: Dict[str, str] = {}
    for item in elements_el.findall("Item[@Class='CiNode']"):
        cn = item.find("CiNode")
        if cn is None:
            continue
        lname = cn.attrib.get("LocalName", "")
        nname = lname[4:] if lname.startswith("&ASC") else lname
        for p_it in cn.findall("./Ports/Item"):
            pid = p_it.attrib.get("CiID", "")
            if pid:
                port_to_node[pid] = nname

    comp_ports: Dict[str, Dict[str, str]] = {}
    for item in elements_el.findall("Item[@Class='CiPort']"):
        pid = item.attrib.get("CiID", "")
        cp = item.find("CiPort")
        if cp is not None:
            cid = cp.attrib.get("Component", "")
            pname = cp.attrib.get("LocalName", "")
            clean_p = pname[4:] if pname.startswith("&ASC") else pname
            if cid and pid in port_to_node:
                comp_ports.setdefault(cid, {})[clean_p] = port_to_node[pid]

    passive_branches: List[Tuple[str, str, str, str, float]] = []
    for item in elements_el.findall("Item[@Class='CiComponent']"):
        cid = item.attrib.get("CiID", "")
        comp = item.find("CiComponent")
        if comp is None:
            continue
        lname = comp.attrib.get("LocalName", "")
        ref = lname[4:] if lname.startswith("&ASC") else lname
        if not re.match(r'^[RCL]\d+$', ref, re.IGNORECASE):
            continue
        exprt = comp.find(".//CiaSpiceTmpltExprt")
        assert exprt is not None
        m_v = re.search(r'^[rcl]%p\s+%t\S+\s+%t\S+\s+([+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)', exprt.attrib.get("String", "")[4:].strip(), re.IGNORECASE)
        assert m_v is not None, f"Missing numeric value in CiaSpiceTmpltExprt on {ref}"
        val = float(m_v.group(1))
        if ref == perturb_ref:
            val *= perturb_scale
        pmap = comp_ports.get(cid, {})
        if "1" in pmap and "2" in pmap:
            passive_branches.append((ref, ref[0].upper(), pmap["1"], pmap["2"], val))

    unknown_nodes = sorted({n for _, _, n1, n2, _ in passive_branches for n in (n1, n2) if n not in ("0", "VIN")})
    assert "VOUT" in unknown_nodes, f"VOUT missing from active unknown nodes: {unknown_nodes}"
    node_idx = {n: i for i, n in enumerate(unknown_nodes)}
    dim = len(unknown_nodes)
    y_mat = [[0j for _ in range(dim)] for _ in range(dim)]
    rhs = [0j for _ in range(dim)]
    w = 2.0 * math.pi * freq_hz

    for _, kind, n1, n2, val in passive_branches:
        if kind == "R":
            adm = 1.0 / val
        elif kind == "C":
            adm = 1j * w * val
        else:
            adm = 1.0 / (1j * w * val)

        if n1 in node_idx:
            i1 = node_idx[n1]
            y_mat[i1][i1] += adm
            if n2 in node_idx:
                i2 = node_idx[n2]
                y_mat[i1][i2] -= adm
            elif n2 == "VIN":
                rhs[i1] += adm * 1.0

        if n2 in node_idx:
            i2 = node_idx[n2]
            y_mat[i2][i2] += adm
            if n1 in node_idx:
                i1 = node_idx[n1]
                y_mat[i2][i1] -= adm
            elif n1 == "VIN":
                rhs[i2] += adm * 1.0

    sol = solve_linear_system_complex(y_mat, rhs)
    vout_c = sol[node_idx["VOUT"]]
    return 20.0 * math.log10(max(1e-15, abs(vout_c)))

def test_phase1_zero_tempfile_db_bridge() -> None:
    logger.info("=== [GATE 1/7] Zero-Tempfile stdout DB Bridge & Defensive XOR Decryption ===")
    db = MultisimDatabaseBridge()
    assert len(db.password) > 0 and all(32 <= ord(c) <= 126 for c in db.password)
    for leftover in glob.glob(os.path.join(db.bin_dir, "part_*.json")):
        os.remove(leftover)

    for q in ["RESISTOR_VIRTUAL", "CAPACITOR_VIRTUAL", "INDUCTOR_VIRTUAL", "D1N4148", "2N2222A", "GROUND"]:
        db._part_cache.pop(q, None)
        spec = db.fetch_part(q)
        assert spec.comp_id >= 0 and len(spec.pins) >= 1
        logger.info(f"  [DB-STDOUT-OK] Query='{q}' -> CompID={spec.comp_id}, Fam='{spec.family_name}', SpiceTpl='{spec.spice_template}'")

    assert len(glob.glob(os.path.join(db.bin_dir, "part_*.json"))) == 0

    caught_not_found = False
    try:
        db.fetch_part("__NON_EXISTENT_COMPONENT_XYZ_999__")
    except DatabaseBridgeError:
        caught_not_found = True
    assert caught_not_found

    with tempfile.NamedTemporaryFile(suffix=".PRD", delete=False) as bad_f:
        bad_f.write(b"\xff" * 256)
        bad_db_path = bad_f.name
    try:
        caught_decrypt = False
        try:
            MultisimDatabaseBridge(db_path=bad_db_path)
        except DatabaseDecryptionError:
            caught_decrypt = True
        assert caught_decrypt
    finally:
        if os.path.exists(bad_db_path):
            os.remove(bad_db_path)

def test_phase2_deep_carchive_3way_sync() -> None:
    logger.info("=== [GATE 2/7] Pure ElementTree ComponentDomBuilder & Deep 3-Way GUI/SPICE Sync ===")
    builder = MultisimSchematicBuilder.from_empty_sheet()
    parts = [
        PartPlacementSpec("R1", "RESISTOR_VIRTUAL", "4.7k", x=306, y=216, rotation=0),
        PartPlacementSpec("C1", "CAPACITOR_VIRTUAL", "22nF", x=414, y=216, rotation=0),
        PartPlacementSpec("L1", "INDUCTOR_VIRTUAL", "15mH", x=522, y=216, rotation=0),
        PartPlacementSpec("D1", "D1N4148", "1N4148", x=630, y=216, rotation=0),
        PartPlacementSpec("Q1", "2N2222A", "2N2222A", x=738, y=216, rotation=0)
    ]
    nets = [
        NetConnectionSpec("VIN", [("R1", "1")]),
        NetConnectionSpec("N1", [("R1", "2"), ("C1", "1")]),
        NetConnectionSpec("N2", [("C1", "2"), ("L1", "1")]),
        NetConnectionSpec("N3", [("L1", "2"), ("D1", "A")]),
        NetConnectionSpec("N4", [("D1", "K"), ("Q1", "B")]),
        NetConnectionSpec("VOUT", [("Q1", "C")]),
        NetConnectionSpec("0", [("Q1", "E")])
    ]
    builder.synthesize_topology(parts=parts, nets=nets, auto_place=False)
    report = builder.validate_dom_integrity()
    assert report["checked_components"] == 5

    root = MultisimDOMManipulator.parse_dom_preserving_multiline_attrs(builder.xml_text)
    sym_val_by_cid: Dict[str, str] = {}
    for sym_it in root.findall(".//CODComposite/Objects/Item[@Class='CIITSymbolComp']"):
        sym_el = sym_it.find("CIITSymbolComp")
        if sym_el is not None:
            cid_ref = sym_el.attrib.get("CiComponent", "")
            v_el = sym_el.find(".//CIITSymTextCompValue")
            if cid_ref and v_el is not None:
                sym_val_by_cid[cid_ref] = v_el.attrib.get("Output", "")

    expected_map = {
        "&ASCR1": ("r%p %t1 %t2 4.700000e+03", "4.7k", 4700.0, "Resistor", "VIRTUAL_RESISTANCE", "&UNI4.7k_uc103a9"),
        "&ASCC1": ("c%p %t1 %t2 2.200000e-08", "22n", 2.2e-8, "Capacitor", "VIRTUAL_CAPACITANCE", "&ASC22nF"),
        "&ASCL1": ("l%p %t1 %t2 1.500000e-02", "15m", 0.015, "Inductor", "VIRTUAL_INDUCTANCE", "&ASC15mH")
    }
    for item in root.findall(".//CIITDiagram/Elements/Item[@Class='CiComponent']"):
        cid = item.attrib.get("CiID", "")
        comp = item.find("CiComponent")
        if comp is None:
            continue
        lname = comp.attrib.get("LocalName", "")
        if lname in expected_map:
            exp_tpl, exp_str_val, exp_num, exp_tlbl, exp_fdesc, exp_sym_lbl = expected_map[lname]
            sp_str = comp.find(".//CiaSpiceTmpltExprt").attrib.get("String", "")
            assert sp_str == f"&ASC{exp_tpl}", f"Expected '{exp_tpl}' on {lname}, got '{sp_str}'"
            all_vals = [it.attrib.get("Value", "") for it in comp.findall(".//Item") if "Value" in it.attrib]
            assert f"&ASC{exp_str_val}" in all_vals
            assert f"&ASC{exp_tlbl}" in all_vals
            assert f"&ASC{exp_fdesc}" in all_vals
            assert f"&ASC{exp_tpl}" in all_vals, f"CiaParamList internal template not synced with '{exp_tpl}' on {lname}"
            assert sym_val_by_cid.get(cid) == exp_sym_lbl, f"Symbol Value Label mismatch on {lname}: {sym_val_by_cid.get(cid)} vs {exp_sym_lbl}"
            assert f"&ASC{exp_str_val}" in all_vals
            assert abs(MultisimDOMManipulator.parse_si_value(exp_str_val) - exp_num) / exp_num < 1e-6
            logger.info(f"  [3WAY-SYNC-OK] {lname[4:]}: Template='{sp_str[4:]}', Type='{exp_tlbl}', SymLabel='{exp_sym_lbl}', Value={exp_num}")

def test_phase3_copy_paste_purity_and_instrument_cdata_preservation() -> None:
    logger.info("=== [GATE 3/7] Copy-Paste Purity Audit (Zero VGEN/v_harm/r_gnd), XFG1 Multi-Tone & Instrument CData Preservation ===")
    tb_path = os.path.join("msim_compiler", "templates", "instrument_testbench.ms14")
    if os.path.exists(tb_path):
        os.remove(tb_path)

    builder = MultisimSchematicBuilder.from_testbench()
    parts = [
        PartPlacementSpec("R1", "RESISTOR_VIRTUAL", "1k", x=414, y=243, rotation=0),
        PartPlacementSpec("C1", "CAPACITOR_VIRTUAL", "100nF", x=504, y=315, rotation=90)
    ]
    nets = [
        NetConnectionSpec("VIN", [("R1", "1")]),
        NetConnectionSpec("VOUT", [("R1", "2"), ("C1", "1")]),
        NetConnectionSpec("0", [("C1", "2")])
    ]
    builder.synthesize_topology(parts=parts, nets=nets, auto_place=False)

    doc = MS14Document(codec=builder.codec)
    doc.xml_text = builder.xml_text
    doc.auto_tune_all_instruments(
        fc_hz=1591.5,
        fundamental_hz=800.0,
        harmonics=[
            HarmonicTone(freq_hz=800.0, amplitude_vp=1.5),
            HarmonicTone(freq_hz=3200.0, amplitude_vp=0.8)
        ]
    )
    assert "VGEN" not in doc.xml_text, "Parasitic VGEN net leaked into schematic XML!"
    assert "v_harm_" not in doc.xml_text, "Parasitic v_harm_ source leaked into schematic XML!"
    assert "r_gnd_" not in doc.xml_text, "Parasitic r_gnd_ resistor leaked into schematic XML!"
    assert "v_xfg_s1" in doc.xml_text, "XFG1 native multi-tone harmonic source v_xfg_s1 missing from XFG1 template!"
    assert "{333F0FA6-F265-4C2B-9350-5DC5E5240A98}" not in doc.xml_text, "Stale VOUT Overrule GUID leaked into schematic XML!"

    cnt_bode_fstop = doc.set_cdata_key("NI_EWB_HORZ_FINAL_VALUE", "200000")
    cnt_bode_dbmin = doc.set_cdata_key("NI_EWB_VERT_INITIAL_LOG_VALUE", "-100")
    cnt_scope_tb = doc.set_cdata_key("NI_EWB_TIMEBASE_SCALE_VALUE", "0.0005")
    assert cnt_bode_fstop >= 1, "Bode Plotter NI_EWB_HORZ_FINAL_VALUE key was wiped during testbench pruning!"
    assert cnt_bode_dbmin >= 1, "Bode Plotter NI_EWB_VERT_INITIAL_LOG_VALUE key was wiped during testbench pruning!"
    assert cnt_scope_tb >= 1, "Oscilloscope NI_EWB_TIMEBASE_SCALE_VALUE key was wiped during testbench pruning!"
    logger.info(f"  [CDATA-KEYS-PRESERVED-OK] XBP1, XSC1 & XFG1 multi-tone verified (fstop={cnt_bode_fstop}, dbmin={cnt_bode_dbmin}, tb={cnt_scope_tb})")

def test_phase4_adversarial_trap_injections() -> None:
    logger.info("=== [GATE 4/7] Adversarial Trap Fault Injection Suite (7 Deadly Traps) ===")
    builder = MultisimSchematicBuilder.from_testbench()
    parts = [
        PartPlacementSpec("R1", "RESISTOR_VIRTUAL", "1k", x=414, y=243, rotation=0),
        PartPlacementSpec("C1", "CAPACITOR_VIRTUAL", "100nF", x=504, y=315, rotation=90),
        PartPlacementSpec("L1", "INDUCTOR_VIRTUAL", "10mH", x=594, y=243, rotation=0)
    ]
    nets = [
        NetConnectionSpec("VIN", [("R1", "1")]),
        NetConnectionSpec("N1", [("R1", "2"), ("C1", "1"), ("L1", "1")]),
        NetConnectionSpec("VOUT", [("L1", "2")]),
        NetConnectionSpec("0", [("C1", "2")])
    ]
    builder.synthesize_topology(parts=parts, nets=nets, auto_place=False)
    clean_xml = builder.xml_text

    builder.xml_text = MultisimDOMManipulator.update_spice_template_for_component(clean_xml, "C1", "r%p %t1 %t2 1000")
    caught_trap1 = False
    try:
        builder.validate_dom_integrity()
    except AssertionError as ex:
        caught_trap1 = True
        logger.info(f"  [TRAP-1-CAUGHT] Blocked SPICE archetype mismatch: {ex}")
    assert caught_trap1

    builder.xml_text = MultisimDOMManipulator.update_spice_template_for_component(
        clean_xml, "R1", "v_harm_1 %t1 VGEN dc 0 ac 0 sin(0 1 1000 0)\r\nr%p %t1 %t2 1000"
    )
    caught_trap2 = False
    try:
        builder.validate_dom_integrity()
    except AssertionError as ex:
        caught_trap2 = True
        logger.info(f"  [TRAP-2-CAUGHT] Blocked parasitic v_harm_ injection on R1: {ex}")
    assert caught_trap2

    builder.xml_text = MultisimDOMManipulator.update_spice_template_for_component(
        clean_xml, "C1", "c%p %t1 %t2 1e-7\r\nr_gnd_%p_2 %t2 0 1e-9"
    )
    caught_trap3 = False
    try:
        builder.validate_dom_integrity()
    except AssertionError as ex:
        caught_trap3 = True
        logger.info(f"  [TRAP-3-CAUGHT] Blocked parasitic r_gnd_ ground tie on C1: {ex}")
    assert caught_trap3

    root_t4 = MultisimDOMManipulator.parse_dom_preserving_multiline_attrs(clean_xml)
    for item in root_t4.findall(".//CIITDiagram/Elements/Item[@Class='CiComponent']"):
        comp = item.find("CiComponent")
        if comp is not None and comp.attrib.get("LocalName") == "&ASCL1":
            for it in comp.findall(".//Item"):
                if it.attrib.get("Value") == "&ASCInductor":
                    it.set("Value", "&ASCCapacitor")
    builder.xml_text = ComponentDomBuilder.serialize_element(root_t4)
    caught_trap4 = False
    try:
        builder.validate_dom_integrity()
    except AssertionError as ex:
        caught_trap4 = True
        logger.info(f"  [TRAP-4-CAUGHT] Blocked L1 carrying Capacitor GUI metadata: {ex}")
    assert caught_trap4

    root_t5 = MultisimDOMManipulator.parse_dom_preserving_multiline_attrs(clean_xml)
    for item in root_t5.findall(".//CIITDiagram/Elements/Item[@Class='CiComponent']"):
        comp = item.find("CiComponent")
        if comp is not None and comp.attrib.get("LocalName") == "&ASCL1":
            for it in comp.findall(".//Item"):
                if it.attrib.get("Value", "").startswith("&ASCl%p"):
                    it.set("Value", "&ASCc%p %t1 %t2 1e-6")
    builder.xml_text = ComponentDomBuilder.serialize_element(root_t5)
    caught_trap5 = False
    try:
        builder.validate_dom_integrity()
    except AssertionError as ex:
        caught_trap5 = True
        logger.info(f"  [TRAP-5-CAUGHT] Blocked CiaParamList cross-archetype c%p leak inside L1: {ex}")
    assert caught_trap5

    root_t6 = MultisimDOMManipulator.parse_dom_preserving_multiline_attrs(clean_xml)
    for item in root_t6.findall(".//CIITDiagram/Elements/Item[@Class='CiComponent']"):
        comp = item.find("CiComponent")
        if comp is not None and comp.attrib.get("LocalName") == "&ASCR1":
            exprt = comp.find(".//CiaSpiceTmpltExprt")
            if exprt is not None:
                exprt.set("String", "&ASCr%p %t1 %t2 9.990000e+05")
    builder.xml_text = ComponentDomBuilder.serialize_element(root_t6)
    caught_trap6 = False
    try:
        builder.validate_dom_integrity()
    except AssertionError as ex:
        caught_trap6 = True
        logger.info(f"  [TRAP-6-CAUGHT] Blocked CiaParamList vs CiaProperDlg value desync: {ex}")
    assert caught_trap6

    root_t7 = MultisimDOMManipulator.parse_dom_preserving_multiline_attrs(clean_xml)
    for vc_it in root_t7.findall(".//CODComposite/Objects/Item[@Class='CVirtualConnector']"):
        for mod_it in vc_it.findall(".//Element[@NetModifier='&ASCNI_EWB_NET_VIRTUAL']/Item"):
            mod_it.set("Guid", "{333F0FA6-F265-4C2B-9350-5DC5E5240A98}")
    builder.xml_text = ComponentDomBuilder.serialize_element(root_t7)
    caught_trap7 = False
    try:
        builder.validate_dom_integrity()
    except AssertionError as ex:
        caught_trap7 = True
        logger.info(f"  [TRAP-7-CAUGHT] Blocked duplicate NetModifier Guid across VIN and VOUT: {ex}")
    assert caught_trap7
    builder.xml_text = clean_xml

def test_phase5_nodal_ac_solver_and_perturbation_sensitivity() -> None:
    logger.info("=== [GATE 5/7] Built-in Nodal AC Solver & Component Perturbation Sensitivity Gate ===")
    compiler = MultisimHighOrderCompiler()
    out_bw5 = os.path.join("dist", "Verify_5thOrder_Pure_Butterworth.ms14")
    compiler.compile_high_order_filter(order=5, fc_hz=10000.0, output_ms14_path=out_bw5)

    doc = MS14Document(codec=compiler.codec).load(out_bw5)
    g_pass = extract_and_solve_ac_transfer_function(doc.xml_text, 100.0)
    g_fc = extract_and_solve_ac_transfer_function(doc.xml_text, 10000.0)
    g_stop = extract_and_solve_ac_transfer_function(doc.xml_text, 100000.0)
    logger.info(f"  [AC-NODAL-SOLVER] 5th-Order Butterworth: Gain(100Hz)={g_pass:.3f}dB, Gain(10kHz)={g_fc:.3f}dB, Gain(100kHz)={g_stop:.3f}dB")

    assert -6.15 <= g_pass <= -5.90, f"Unexpected passband gain {g_pass:.3f}dB (expected -6.02dB for doubly-terminated ladder)"
    assert -9.15 <= g_fc <= -8.90, f"Unexpected cutoff gain {g_fc:.3f}dB (expected -9.03dB = -6.02dB - 3.01dB)"
    assert g_stop <= -100.0, f"Unexpected stopband gain {g_stop:.3f}dB (expected <= -100dB)"

    for ref in ("R1", "R2", "C1", "L1", "C2", "L2", "C3"):
        g_perturbed = extract_and_solve_ac_transfer_function(doc.xml_text, 10000.0, perturb_ref=ref, perturb_scale=2.0)
        delta_db = abs(g_perturbed - g_fc)
        assert delta_db > 0.25, f"Dummy/Dead Component Detected: Perturbing '{ref}' by 2x changed H(j*wc) by only {delta_db:.6f} dB!"
        logger.info(f"  [SENSITIVITY-OK] Perturbing {ref} by 2x shifted cutoff gain by {delta_db:.3f} dB (100% Electrically Active)")

def test_phase6_vision_ocr_morphological_opening() -> None:
    logger.info("=== [GATE 6/7] Vision OCR Morphological Opening & Blank-Image Rejection ===")
    parser = OpticalCircuitParser()
    with tempfile.TemporaryDirectory() as tmp_dir:
        sample_png = os.path.join(tmp_dir, "verify_vision_synthetic.png")
        OpticalCircuitParser.generate_synthetic_schematic_png(sample_png)
        parts, nets = parser.parse_image_to_topology(sample_png)
        assert len(parts) == 8
        logger.info(f"  [OCR-SYNTHETIC-OK] Extracted {len(parts)} components and {len(nets)} nets")

        blank_png = os.path.join(tmp_dir, "verify_vision_blank.png")
        blank_rows = bytearray()
        for _ in range(200):
            blank_rows.append(0)
            blank_rows.extend([255] * (300 * 3))
        def png_chunk(c_type: bytes, c_data: bytes) -> bytes:
            crc = zlib.crc32(c_type + c_data) & 0xFFFFFFFF
            return struct.pack(">I", len(c_data)) + c_type + c_data + struct.pack(">I", crc)
        ihdr_b = struct.pack(">IIBBBBB", 300, 200, 8, 2, 0, 0, 0)
        idat_b = zlib.compress(bytes(blank_rows), level=6)
        with open(blank_png, "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\n" + png_chunk(b"IHDR", ihdr_b) + png_chunk(b"IDAT", idat_b) + png_chunk(b"IEND", b""))

        caught_blank = False
        try:
            parser.parse_image_to_topology(blank_png)
        except ValueError:
            caught_blank = True
        assert caught_blank
        logger.info("  [OCR-BLANK-REJECTED-OK] Blank image cleanly raised ValueError")

def test_phase7_set_component_live_update_integrity() -> None:
    logger.info("=== [GATE 7/7] Live Component Update (MS14Document.set_component) 3-Way Synchronization ===")
    builder = MultisimSchematicBuilder.from_testbench()
    parts = [
        PartPlacementSpec("R1", "RESISTOR_VIRTUAL", "1k", x=414, y=243, rotation=0),
        PartPlacementSpec("C1", "CAPACITOR_VIRTUAL", "100nF", x=504, y=315, rotation=90)
    ]
    nets = [
        NetConnectionSpec("VIN", [("R1", "1")]),
        NetConnectionSpec("VOUT", [("R1", "2"), ("C1", "1")]),
        NetConnectionSpec("0", [("C1", "2")])
    ]
    builder.synthesize_topology(parts=parts, nets=nets, auto_place=False)
    doc = MS14Document(codec=builder.codec)
    doc.xml_text = builder.xml_text
    doc.set_component("R1", "10k")
    doc.set_component("C1", "47n")
    out_updated = os.path.join("dist", "Verify_SetComponent_3WaySync.ms14")
    doc.save(out_updated)

    reloaded = MS14Document(codec=builder.codec).load(out_updated)
    comps = reloaded.discover_components()
    assert comps["R1"].spice_value == "10k"
    assert comps["C1"].spice_value == "47n"
    g_fc_new = extract_and_solve_ac_transfer_function(reloaded.xml_text, 1.0 / (2.0 * math.pi * 10000.0 * 47e-9))
    assert abs(g_fc_new - (-3.0103)) < 0.05
    logger.info(f"  [LIVE-UPDATE-OK] R1=10k, C1=47n updated cleanly; Nodal AC gain at new fc = {g_fc_new:.4f} dB")

def run_all_verifications() -> None:
    os.makedirs("dist", exist_ok=True)
    test_phase1_zero_tempfile_db_bridge()
    test_phase2_deep_carchive_3way_sync()
    test_phase3_copy_paste_purity_and_instrument_cdata_preservation()
    test_phase4_adversarial_trap_injections()
    test_phase5_nodal_ac_solver_and_perturbation_sensitivity()
    test_phase6_vision_ocr_morphological_opening()
    test_phase7_set_component_live_update_integrity()
    logger.info("ALL 7 ADVERSARIAL & ZERO-PARASITIC CORE VERIFICATION GATES PASSED 100%.")

if __name__ == "__main__":
    run_all_verifications()
