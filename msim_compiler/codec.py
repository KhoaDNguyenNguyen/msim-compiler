import os
import subprocess
import tempfile
from .logger import get_logger

class MS14Codec:
    def __init__(self):
        self.logger = get_logger("MS14-CODEC")
        self.pkg_dir = os.path.dirname(os.path.abspath(__file__))
        self.bin_dir = os.path.join(self.pkg_dir, "bin")
        self.cs_path = os.path.join(self.bin_dir, "ms14_codec.cs")
        self.exe_path = os.path.join(self.bin_dir, "ms14_codec.exe")
        self.ensure_compiled()

    def ensure_compiled(self) -> None:
        if os.path.exists(self.exe_path) and os.path.getsize(self.exe_path) > 2048:
            return
        csc_candidates = [
            r"C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe",
            r"C:\Windows\Microsoft.NET\Framework\v4.0.30319\csc.exe"
        ]
        csc_bin = next((c for c in csc_candidates if os.path.exists(c)), None)
        if not csc_bin:
            raise FileNotFoundError("Microsoft .NET Framework csc.exe compiler not found.")
        self.logger.info(f"Compiling PKWare DCL binary codec -> {self.exe_path}")
        subprocess.run(
            [csc_bin, "/nologo", "/optimize+", f"/out:{self.exe_path}", self.cs_path],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )

    def decode_file(self, ms14_path: str, xml_out_path: str) -> int:
        subprocess.run([self.exe_path, "decode", ms14_path, xml_out_path], check=True)
        sz = os.path.getsize(xml_out_path)
        self.logger.info(f"Decoded '{os.path.basename(ms14_path)}' -> XML ({sz} bytes)")
        return sz

    def encode_file(self, xml_in_path: str, ms14_out_path: str) -> int:
        os.makedirs(os.path.dirname(os.path.abspath(ms14_out_path)), exist_ok=True)
        subprocess.run([self.exe_path, "encode", xml_in_path, ms14_out_path], check=True)
        sz = os.path.getsize(ms14_out_path)
        self.logger.info(f"Encoded XML -> '{os.path.basename(ms14_out_path)}' ({sz} bytes)")
        return sz

    def decode_to_string(self, ms14_path: str) -> str:
        with tempfile.NamedTemporaryFile(suffix=".xml", delete=False) as tf:
            temp_xml = tf.name
        try:
            self.decode_file(ms14_path, temp_xml)
            with open(temp_xml, "r", encoding="utf-8", errors="ignore") as f:
                return f.read()
        finally:
            if os.path.exists(temp_xml):
                os.remove(temp_xml)

    def encode_from_string(self, xml_text: str, ms14_out_path: str) -> int:
        with tempfile.NamedTemporaryFile(suffix=".xml", delete=False) as tf:
            temp_xml = tf.name
        try:
            with open(temp_xml, "wb") as f:
                f.write(xml_text.encode("utf-8"))
            return self.encode_file(temp_xml, ms14_out_path)
        finally:
            if os.path.exists(temp_xml):
                os.remove(temp_xml)
