import os
from msim_compiler import (
    HarmonicTone,
    MS14Document,
    MultisimSchematicBuilder,
    NetConnectionSpec,
    PartPlacementSpec,
    WindowRect,
    get_logger
)

def test_declarative_matrix_synthesis() -> None:
    logger = get_logger("LIB-TEST")
    out_ms14 = r"D:\msim_binary_fuzzer\dist\ultra_complex_lab_circuit.ms14"
    out_xml = r"D:\msim_binary_fuzzer\dist\ultra_complex_lab_circuit.xml"

    builder = MultisimSchematicBuilder.from_testbench()

    parts = [
        PartPlacementSpec("R10", "RESISTOR_VIRTUAL",  "150",     x=297, y=216, rotation=0),
        PartPlacementSpec("L1",  "INDUCTOR_VIRTUAL",  "18mH",    x=396, y=216, rotation=0),
        PartPlacementSpec("L2",  "INDUCTOR_VIRTUAL",  "27mH",    x=495, y=216, rotation=0),
        PartPlacementSpec("L3",  "INDUCTOR_VIRTUAL",  "18mH",    x=594, y=216, rotation=0),
        PartPlacementSpec("R11", "RESISTOR_VIRTUAL",  "180",     x=693, y=216, rotation=0),
        PartPlacementSpec("R12", "RESISTOR_VIRTUAL",  "150",     x=792, y=216, rotation=0),

        PartPlacementSpec("C10", "CAPACITOR_VIRTUAL", "470nF",   x=432, y=297, rotation=90),
        PartPlacementSpec("C11", "CAPACITOR_VIRTUAL", "680nF",   x=531, y=297, rotation=90),
        PartPlacementSpec("C12", "CAPACITOR_VIRTUAL", "470nF",   x=630, y=297, rotation=90),
        PartPlacementSpec("C13", "CAPACITOR_VIRTUAL", "220nF",   x=729, y=297, rotation=90),
        PartPlacementSpec("D1",  "D1N4148",           "1N4148",  x=792, y=306, rotation=0),

        PartPlacementSpec("R14", "RESISTOR_VIRTUAL",  "22k",     x=297, y=414, rotation=0),
        PartPlacementSpec("Q1",  "2N2222A",           "2N2222A", x=396, y=405, rotation=0),
        PartPlacementSpec("C14", "CAPACITOR_VIRTUAL", "100nF",   x=495, y=405, rotation=0),
        PartPlacementSpec("Q2",  "2N2222A",           "2N2222A", x=594, y=405, rotation=0),
        PartPlacementSpec("D2",  "D1N4148",           "1N4148",  x=693, y=414, rotation=0),
        PartPlacementSpec("R13", "RESISTOR_VIRTUAL",  "4.7k",    x=792, y=414, rotation=0),

        PartPlacementSpec("R15", "RESISTOR_VIRTUAL",  "10k",     x=495, y=495, rotation=0)
    ]

    nets = [
        NetConnectionSpec("VIN",        [("R10", "1"), ("R14", "1")]),
        NetConnectionSpec("N_LC1",      [("R10", "2"), ("L1", "1")]),
        NetConnectionSpec("N_LC2",      [("L1", "2"),  ("L2", "1"),  ("C10", "1")]),
        NetConnectionSpec("N_LC3",      [("L2", "2"),  ("L3", "1"),  ("C11", "1")]),
        NetConnectionSpec("N_LC4",      [("L3", "2"),  ("R11", "1"), ("C12", "1")]),
        NetConnectionSpec("N_RC5",      [("R11", "2"), ("R12", "1"), ("C13", "1")]),
        NetConnectionSpec("VOUT",       [("R12", "2"), ("D1", "K"),  ("R13", "2")]),
        NetConnectionSpec("N_Q1_BASE",  [("R14", "2"), ("Q1", "B")]),
        NetConnectionSpec("N_Q1_COLL",  [("Q1", "C"),  ("C14", "1")]),
        NetConnectionSpec("N_Q2_BASE",  [("C14", "2"), ("Q2", "B")]),
        NetConnectionSpec("N_Q2_COLL",  [("Q2", "C"),  ("D2", "A")]),
        NetConnectionSpec("N_Q1_EMIT",  [("Q1", "E"),  ("R15", "1")]),
        NetConnectionSpec("0", [
            ("C10", "2"), ("C11", "2"), ("C12", "2"), ("C13", "2"),
            ("D1", "A"), ("R13", "1"), ("D2", "K"), ("Q2", "E"), ("R15", "2")
        ])
    ]

    builder.synthesize_topology(parts=parts, nets=nets, auto_place=False)
    builder.add_schematic_info_banner(
        title="=== ULTRA-COMPLEX RLC LADDER + DUAL-BJT 2N2222A + 1N4148 CLAMP MATRIX ===",
        lines=[
            "Row 1 (RLC Ladder): R10=150, L1=18mH, L2=27mH, L3=18mH, R11=180, R12=150 | fc = 2.10 kHz",
            "Row 2 (Shunt Caps): C10=470nF, C11=680nF, C12=470nF, C13=220nF, D1=1N4148",
            "Row 3 (Active BJT): R14=22k, Q1=2N2222A, C14=100nF, Q2=2N2222A, D2=1N4148, R13=4.7k, R15=10k"
        ],
        x=297,
        y=576
    )

    audit = builder.eda.audit_schematic_dom(builder.xml_text)
    for n_name, p_list in sorted(audit.net_summary.items()):
        logger.info(f"  [NET-TABLE] Net '{n_name:<12}' -> {p_list}")

    doc = MS14Document(codec=builder.codec)
    doc.xml_text = builder.xml_text
    doc.auto_tune_all_instruments(
        fc_hz=2100.0,
        fundamental_hz=1000.0,
        harmonics=[
            HarmonicTone(freq_hz=1000.0, amplitude_vp=1.4),
            HarmonicTone(freq_hz=5000.0, amplitude_vp=0.65),
            HarmonicTone(freq_hz=9000.0, amplitude_vp=0.45),
            HarmonicTone(freq_hz=15000.0, amplitude_vp=0.30)
        ],
        vim_vp=1.4
    )
    doc.configure_dashboard_layout([
        WindowRect("XSC1_Oscilloscope",      1020,  30, 560, 430),
        WindowRect("XBP1_BodePlotter",        380,  30, 630, 290),
        WindowRect("XSC2_Tektronix_Time",      10, 520, 620, 420),
        WindowRect("XSC3_Tektronix_FFT_In",   510, 520, 620, 420),
        WindowRect("XSC4_Tektronix_FFT_Out", 1010, 520, 620, 420),
        WindowRect("XFG1_FunctionGenerator",   10,  30, 259, 301)
    ])
    builder.xml_text = doc.xml_text
    builder.save_ms14(out_ms14, save_debug_xml=out_xml)

    verify_doc = MS14Document(codec=builder.codec).load(out_ms14)
    comps = verify_doc.discover_components()
    logger.info(f"Verified packaged synthesis: {len(comps)} active components in '{os.path.basename(out_ms14)}'")

if __name__ == "__main__":
    test_declarative_matrix_synthesis()
