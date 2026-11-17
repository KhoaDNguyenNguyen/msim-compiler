import os
import sys

repo_root = os.path.dirname(os.path.abspath(__file__))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from msim_compiler.schematic_builder import MultisimSchematicBuilder
from msim_compiler.document import MS14Document
from msim_compiler.models import HarmonicTone, WindowRect
from msim_compiler.eda_engine import PartPlacementSpec, NetConnectionSpec

def main():
    parts = [
        PartPlacementSpec("C1", "CAPACITOR_VIRTUAL", "2.2u", x=396, y=315, rotation=90),
        PartPlacementSpec("L1", "INDUCTOR_VIRTUAL", "1.5m", x=450, y=243, rotation=0),
        PartPlacementSpec("C2", "CAPACITOR_VIRTUAL", "4.7u", x=504, y=315, rotation=90),
        PartPlacementSpec("L2", "INDUCTOR_VIRTUAL", "1.5m", x=558, y=243, rotation=0),
        PartPlacementSpec("C3", "CAPACITOR_VIRTUAL", "2.2u", x=612, y=315, rotation=90),
        PartPlacementSpec("R1", "RESISTOR_VIRTUAL", "8", x=666, y=315, rotation=90)
    ]

    nets = [
        NetConnectionSpec("VIN", [("C1", "1"), ("L1", "1")]),
        NetConnectionSpec("N_LC_MID", [("L1", "2"), ("C2", "1"), ("L2", "1")]),
        NetConnectionSpec("VOUT", [("L2", "2"), ("C3", "1"), ("R1", "1")]),
        NetConnectionSpec("0", [("C1", "2"), ("C2", "2"), ("C3", "2"), ("R1", "2")])
    ]

    builder = MultisimSchematicBuilder.from_testbench()
    builder.synthesize_topology(parts=parts, nets=nets, auto_place=False)

    doc = MS14Document(codec=builder.codec)
    doc.xml_text = builder.xml_text

    harmonics = [
        HarmonicTone(freq_hz=1000.0, amplitude_vp=2.0),
        HarmonicTone(freq_hz=3000.0, amplitude_vp=2.0),
        HarmonicTone(freq_hz=7000.0, amplitude_vp=2.0)
    ]

    doc.auto_tune_all_instruments(
        fc_hz=3000.0,
        fundamental_hz=1000.0,
        harmonics=harmonics,
        vim_vp=2.0
    )

    doc.configure_dashboard_layout([
        WindowRect("XSC1_Oscilloscope",      1020,  30, 560, 430),
        WindowRect("XBP1_BodePlotter",        380,  30, 630, 290),
        WindowRect("XSC2_Tektronix_Time",      10, 520, 620, 420),
        WindowRect("XSC3_Tektronix_FFT_In",   510, 520, 620, 420),
        WindowRect("XSC4_Tektronix_FFT_Out", 1010, 520, 620, 420),
        WindowRect("XFG1_FunctionGenerator",   10,  30, 259, 301)
    ])

    os.makedirs("dist", exist_ok=True)
    out_ms14 = "dist/Passive_5thOrder_LC_Pi_Filter.ms14"
    doc.save(out_ms14)
    print(out_ms14)

if __name__ == "__main__":
    main()
