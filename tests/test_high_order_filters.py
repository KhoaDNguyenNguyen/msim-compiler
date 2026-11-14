import os
from msim_compiler import (
    HarmonicTone,
    MS14Document,
    MultisimHighOrderCompiler,
    get_logger
)

def run_test_suite() -> None:
    logger = get_logger("TEST-SUITE")
    dist_dir = "D:/msim_binary_fuzzer/dist"
    os.makedirs(dist_dir, exist_ok=True)

    compiler = MultisimHighOrderCompiler()

    cases = [
        (
            5, 10000.0,
            "TestCase1_Butterworth_5thOrder_10kHz_MultiTone.ms14",
            [
                HarmonicTone(freq_hz=5000.0, amplitude_vp=1.4),
                HarmonicTone(freq_hz=15000.0, amplitude_vp=0.7),
                HarmonicTone(freq_hz=25000.0, amplitude_vp=0.5)
            ]
        ),
        (
            7, 12000.0,
            "TestCase2_Butterworth_7thOrder_12kHz_UltraSteep.ms14",
            [
                HarmonicTone(freq_hz=6000.0, amplitude_vp=1.4),
                HarmonicTone(freq_hz=18000.0, amplitude_vp=0.8),
                HarmonicTone(freq_hz=30000.0, amplitude_vp=0.6)
            ]
        ),
        (
            9, 8000.0,
            "TestCase3_Butterworth_9thOrder_8kHz_Extreme.ms14",
            [
                HarmonicTone(freq_hz=4000.0, amplitude_vp=1.4),
                HarmonicTone(freq_hz=12000.0, amplitude_vp=0.8),
                HarmonicTone(freq_hz=20000.0, amplitude_vp=0.6),
                HarmonicTone(freq_hz=28000.0, amplitude_vp=0.5)
            ]
        )
    ]

    for order, fc_hz, fname, tones in cases:
        out_path = os.path.join(dist_dir, fname)
        compiler.compile_high_order_filter(
            order=order,
            fc_hz=fc_hz,
            output_ms14_path=out_path,
            harmonics=tones
        )
        v_doc = MS14Document(codec=compiler.codec).load(out_path)
        for legacy in ("&amp;ASCU2", "&amp;ASCVCC", "&amp;ASCVEE", "495902816", "495898448"):
            assert legacy not in v_doc.xml_text, f"Legacy artifact {legacy} leaked into {fname}!"
        comps = v_doc.discover_components()
        logger.info(f"Verified [{fname}]: {len(comps)} clean components, 0 legacy Op-Amp/Sallen-Key remnants.")

if __name__ == "__main__":
    run_test_suite()
