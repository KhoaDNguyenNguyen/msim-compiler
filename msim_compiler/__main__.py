import sys
import argparse
from msim_compiler.logger import get_logger
from msim_compiler.models import HarmonicTone
from msim_compiler.drm_guard import GLOBAL_DRM

def main():
    parser = argparse.ArgumentParser(description="Multisim 14 Zero-GUI Compiler & Vision Synthesizer")
    parser.add_argument("--gui", action="store_true", help="Launch the Desktop UI")
    parser.add_argument("--image", type=str, help="Run Optical Circuit Recognition on an image and compile to .ms14")
    parser.add_argument("--filter-order", type=int, default=7, help="Order of the Butterworth filter")
    parser.add_argument("--filter-fc", type=float, default=12000.0, help="Cutoff frequency in Hz")
    parser.add_argument("--out", type=str, default="dist/cli_output.ms14", help="Output .ms14 path")

    args = parser.parse_args()
    logger = get_logger("MSIM-BOOTSTRAP")

    if args.gui or len(sys.argv) == 1:
        logger.info("Launching MSimStudio Desktop GUI...")
        from msim_compiler.gui import launch_gui
        launch_gui()
        return

    if not GLOBAL_DRM.load_persisted_license():
        logger.warning("Unlicensed CLI invocation detected; writing watermarked schematic.")
        GLOBAL_DRM.export_unlicensed_schematic(args.out)
        return

    if args.image:
        logger.info(f"Running Vision-to-Multisim pipeline on '{args.image}'...")
        from msim_compiler.vision_parser import OpticalCircuitParser
        parser_eng = OpticalCircuitParser()
        parser_eng.compile_image_to_ms14(args.image, args.out, fc_hz=args.filter_fc)
    else:
        logger.info("Running Code-to-Multisim Active Filter Synthesizer...")
        from msim_compiler.compiler import MultisimHighOrderCompiler
        c = MultisimHighOrderCompiler()
        fc = args.filter_fc
        tones = [
            HarmonicTone(freq_hz=fc * 0.5, amplitude_vp=1.4),
            HarmonicTone(freq_hz=fc * 1.5, amplitude_vp=0.8),
            HarmonicTone(freq_hz=fc * 2.5, amplitude_vp=0.6)
        ]
        c.compile_high_order_filter(args.filter_order, fc, args.out, harmonics=tones)

if __name__ == "__main__":
    main()
