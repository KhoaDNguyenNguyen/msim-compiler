import os
import time
from typing import Dict, Any, List
from .codec import MS14Codec
from .document import MS14Document
from .logger import get_logger

class CircuitBatchCompiler:
    def __init__(self, base_dir: str = "D:/msim_binary_fuzzer"):
        self.logger = get_logger("BATCH-COMPILER")
        self.base_dir = base_dir
        self.codec = MS14Codec()

    def compile_variant(
        self,
        template_ms14: str,
        output_ms14: str,
        component_updates: Dict[str, str],
        fc_hz: float,
        vim_vp: float = 1.4,
        save_xml_debug: bool = False
    ) -> str:
        t0 = time.perf_counter()
        doc = MS14Document(codec=self.codec).load(template_ms14)
        for refdes, val in component_updates.items():
            doc.set_component(refdes, val)
        doc.auto_tune_all_instruments(fc_hz=fc_hz, vim_vp=vim_vp)
        xml_dbg = os.path.splitext(output_ms14)[0] + ".xml" if save_xml_debug else None
        doc.save(output_ms14, save_xml_path=xml_dbg)
        dt_ms = (time.perf_counter() - t0) * 1000.0
        self.logger.info(f"Compiled '{os.path.basename(output_ms14)}' (fc={fc_hz:.2f} Hz) in {dt_ms:.1f} ms")
        return output_ms14

    def compile_lab_suite(self, jobs: List[Dict[str, Any]]) -> List[str]:
        outputs: List[str] = []
        for job in jobs:
            if not os.path.exists(job["template"]):
                self.logger.warning(f"Skipping missing template: {job['template']}")
                continue
            out = self.compile_variant(
                template_ms14=job["template"],
                output_ms14=job["output"],
                component_updates=job["components"],
                fc_hz=job["fc_hz"],
                vim_vp=job.get("vim_vp", 1.4),
                save_xml_debug=job.get("save_xml", False)
            )
            outputs.append(out)
        return outputs
