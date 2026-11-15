import os
import re
import glob
import struct
import zlib
import tempfile
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

def test_phase1_zero_tempfile_db_bridge() -> None:
    logger.info("=== [TEST 1/6] Phase 1: Zero-Tempfile stdout DB Bridge & Defensive XOR Decryption ===")
    db = MultisimDatabaseBridge()
    assert len(db.password) > 0 and all(32 <= ord(c) <= 126 for c in db.password)
    logger.info(f"Verified Jet3 XOR decrypted password length={len(db.password)} (valid printable ASCII)")

    for leftover in glob.glob(os.path.join(db.bin_dir, "part_*.json")):
        os.remove(leftover)

    queries = ["RESISTOR_VIRTUAL", "CAPACITOR_VIRTUAL", "INDUCTOR_VIRTUAL", "D1N4148", "2N2222A", "GROUND"]
    for q in queries:
        db._part_cache.pop(q, None)
        spec = db.fetch_part(q)
        assert spec.comp_id >= 0, f"Invalid comp_id for {q}"
        assert len(spec.pins) >= 1, f"Missing pins for {q}"
        logger.info(
            f"  [DB-STDOUT-OK] Query='{q}' -> CompID={spec.comp_id}, Fam='{spec.family_name}', SpiceTpl='{spec.spice_template}', ParamLen={len(spec.param_data)}"
        )

    temp_files = glob.glob(os.path.join(db.bin_dir, "part_*.json"))
    assert len(temp_files) == 0, f"Tempfile leak detected in bin/: {temp_files}"
    logger.info("Confirmed 0 temporary part_*.json files created on disk during DB queries")

    caught_not_found = False
    try:
        db.fetch_part("__NON_EXISTENT_COMPONENT_XYZ_999__")
    except DatabaseBridgeError as ex:
        caught_not_found = True
        logger.info(f"  [DEFENSIVE-OK] Non-existent part cleanly raised DatabaseBridgeError: {ex}")
    assert caught_not_found, "Expected DatabaseBridgeError for non-existent component"

    with tempfile.NamedTemporaryFile(suffix=".PRD", delete=False) as bad_f:
        bad_f.write(b"\xff" * 256)
        bad_db_path = bad_f.name
    try:
        caught_decrypt = False
        try:
            MultisimDatabaseBridge(db_path=bad_db_path)
        except DatabaseDecryptionError as ex:
            caught_decrypt = True
            logger.info(f"  [DEFENSIVE-OK] Corrupted DB header cleanly raised DatabaseDecryptionError: {ex}")
        assert caught_decrypt, "Expected DatabaseDecryptionError on corrupted DB header"
    finally:
        if os.path.exists(bad_db_path):
            os.remove(bad_db_path)

def test_phase2_component_dom_builder_no_archetypes() -> None:
    logger.info("=== [TEST 2/6] Phase 2: Pure ElementTree ComponentDomBuilder & SPICE Archetype Accuracy ===")
    builder = MultisimSchematicBuilder.from_empty_sheet()
    assert not hasattr(builder, "_archetypes"), "Legacy self._archetypes attribute must not exist on MultisimSchematicBuilder"

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
    expected_map = {
        "&ASCR1": ("r%p", "4.7k", 4700.0),
        "&ASCC1": ("c%p", "22n", 2.2e-8),
        "&ASCL1": ("l%p", "15m", 0.015),
        "&ASCD1": ("d%p", None, None),
        "&ASCQ1": ("q%p", None, None)
    }
    for item in root.findall(".//CIITDiagram/Elements/Item[@Class='CiComponent']"):
        comp = item.find("CiComponent")
        if comp is None:
            continue
        lname = comp.attrib.get("LocalName", "")
        if lname in expected_map:
            exp_prefix, exp_str_val, exp_num = expected_map[lname]
            exprt = comp.find(".//CiaSpiceTmpltExprt")
            assert exprt is not None
            sp_str = exprt.attrib.get("String", "")
            assert exp_prefix in sp_str.lower(), f"Expected '{exp_prefix}' in {lname} SPICE template, got '{sp_str}'"
            assert not (lname in ("&ASCC1", "&ASCL1") and "r%p" in sp_str.lower()), f"Fatal resistor leak on {lname}: {sp_str}"
            if exp_str_val is not None:
                all_str_vals = [
                    it.attrib.get("Value", "")
                    for it in comp.findall(".//Item")
                    if "Value" in it.attrib
                ]
                assert f"&ASC{exp_str_val}" in all_str_vals, f"Expected '&ASC{exp_str_val}' in {lname} parameter strings, got {all_str_vals}"
            if exp_num is not None:
                dbl_items = comp.findall(".//doubles/Item")
                if dbl_items:
                    actual_val = float(dbl_items[0].attrib.get("Value", "0"))
                    assert abs(actual_val - exp_num) / exp_num < 1e-6, f"Value mismatch on {lname}: {actual_val} vs {exp_num}"
            logger.info(f"  [DOM-COMPONENT-OK] {lname[4:]}: SpiceTemplate='{sp_str[4:]}', SpiceVal='{exp_str_val}', NumericParam={exp_num}")

    out_empty_ms14 = os.path.join("dist", "Verify_EmptySheet_AllFamilies.ms14")
    builder.save_ms14(out_empty_ms14)
    doc = MS14Document(codec=builder.codec).load(out_empty_ms14)
    discovered = doc.discover_components()
    for ref in ("R1", "C1", "L1", "D1", "Q1"):
        assert ref in discovered, f"Missing '{ref}' after round-trip encode/decode"
    assert discovered["R1"].spice_value == "4.7k"
    assert discovered["C1"].spice_value == "22n"
    assert discovered["L1"].spice_value == "15m"
    logger.info(f"Verified Empty-Sheet round-trip binary compilation ({len(discovered)} components discovered)")

def test_phase3_virtual_connector_synchronization_and_isolation() -> None:
    logger.info("=== [TEST 3/6] Phase 3: Testbench VirtualConnector Synchronization & VIN/VOUT Isolation ===")
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
    report = builder.validate_dom_integrity()
    assert report["checked_vconns"] >= 6, f"Expected >= 6 synchronized virtual connectors, got {report['checked_vconns']}"
    logger.info(f"Verified {report['checked_vconns']} VirtualConnectors have 100% matching Output, NetModifier, LocalName, and CiNode bindings")

def test_phase4_pre_flight_negative_gate() -> None:
    logger.info("=== [TEST 4/6] Phase 4: Pre-Flight Verification Gate Negative Fault Injection ===")
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

    corrupted_xml = MultisimDOMManipulator.update_spice_template_for_component(
        builder.xml_text, "C1", "r%p %t1 %t2 \x231"
    )
    saved_xml = builder.xml_text
    builder.xml_text = corrupted_xml
    caught_spice_mismatch = False
    try:
        builder.validate_dom_integrity()
    except AssertionError as ex:
        caught_spice_mismatch = True
        logger.info(f"  [GATE-BLOCKED-SPICE-BUG] Pre-Flight Gate caught injected C1 resistor template: {ex}")
    finally:
        builder.xml_text = saved_xml
    assert caught_spice_mismatch, "Pre-Flight Gate failed to block SPICE archetype mismatch on C1"

def test_phase5_vision_ocr_morphological_opening() -> None:
    logger.info("=== [TEST 5/6] Phase 5: Vision OCR Morphological Opening & Blank-Image Rejection ===")
    parser = OpticalCircuitParser()
    sample_png = os.path.join("dist", "verify_vision_synthetic.png")
    OpticalCircuitParser.generate_synthetic_schematic_png(sample_png)
    parts, nets = parser.parse_image_to_topology(sample_png)
    assert len(parts) == 8, f"Expected 8 morphologically extracted components from synthetic PNG, got {len(parts)}"
    logger.info(f"Verified Morphological OCR extracted {len(parts)} components and {len(nets)} nets from '{os.path.basename(sample_png)}'")

    custom_png = os.path.join("dist", "verify_vision_custom_4comp.png")
    width, height = 600, 400
    canvas = [[255 for _ in range(width)] for _ in range(height)]
    for bx0, by0, bx1, by1 in [(120, 175, 180, 195), (280, 175, 340, 195), (220, 250, 240, 310), (380, 250, 400, 310)]:
        for yy in range(by0, by1 + 1):
            for xx in range(bx0, bx1 + 1):
                canvas[yy][xx] = 15

    raw_rows = bytearray()
    for row in canvas:
        raw_rows.append(0)
        for pix in row:
            raw_rows.extend((pix, pix, pix))
    def png_chunk(c_type: bytes, c_data: bytes) -> bytes:
        crc = zlib.crc32(c_type + c_data) & 0xFFFFFFFF
        return struct.pack(">I", len(c_data)) + c_type + c_data + struct.pack(">I", crc)
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    idat = zlib.compress(bytes(raw_rows), level=6)
    with open(custom_png, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + png_chunk(b"IHDR", ihdr) + png_chunk(b"IDAT", idat) + png_chunk(b"IEND", b""))

    c_parts, c_nets = parser.parse_image_to_topology(custom_png)
    assert len(c_parts) == 4, f"Expected 4 components from custom 4-component image, got {len(c_parts)}"
    logger.info(f"Verified custom 4-component schematic extracted {len(c_parts)} components (proving 0 hardcoded fallback)")

    blank_png = os.path.join("dist", "verify_vision_blank.png")
    blank_rows = bytearray()
    for _ in range(200):
        blank_rows.append(0)
        blank_rows.extend([255] * (300 * 3))
    ihdr_b = struct.pack(">IIBBBBB", 300, 200, 8, 2, 0, 0, 0)
    idat_b = zlib.compress(bytes(blank_rows), level=6)
    with open(blank_png, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + png_chunk(b"IHDR", ihdr_b) + png_chunk(b"IDAT", idat_b) + png_chunk(b"IEND", b""))

    caught_blank = False
    try:
        parser.parse_image_to_topology(blank_png)
    except ValueError as ex:
        caught_blank = True
        logger.info(f"  [DEFENSIVE-OCR-OK] Blank image cleanly raised ValueError: {ex}")
    assert caught_blank, "Expected ValueError on blank schematic image"

def test_phase6_end_to_end_filter_and_rlc_synthesis() -> None:
    logger.info("=== [TEST 6/6] Phase 6: End-to-End High-Order Filter & RLC-BJT Matrix Compilation ===")
    compiler = MultisimHighOrderCompiler()
    out_bw = os.path.join("dist", "Verify_7thOrder_Butterworth.ms14")
    compiler.compile_high_order_filter(
        order=7,
        fc_hz=12000.0,
        output_ms14_path=out_bw,
        harmonics=[
            HarmonicTone(freq_hz=6000.0, amplitude_vp=1.4),
            HarmonicTone(freq_hz=18000.0, amplitude_vp=0.8)
        ]
    )
    doc = MS14Document(codec=compiler.codec).load(out_bw)
    comps = doc.discover_components()
    assert len(comps) >= 15, f"Expected >= 15 components in 7th-order Butterworth, got {len(comps)}"
    logger.info(f"Verified 7th-Order Butterworth compilation: {len(comps)} components, {len(doc.discover_wires())} wires")

def run_all_verifications() -> None:
    test_phase1_zero_tempfile_db_bridge()
    test_phase2_component_dom_builder_no_archetypes()
    test_phase3_virtual_connector_synchronization_and_isolation()
    test_phase4_pre_flight_negative_gate()
    test_phase5_vision_ocr_morphological_opening()
    test_phase6_end_to_end_filter_and_rlc_synthesis()
    logger.info("ALL 6 MASTER PLAN VERIFICATION GATES PASSED SUCCESSFULLY.")

if __name__ == "__main__":
    run_all_verifications()
