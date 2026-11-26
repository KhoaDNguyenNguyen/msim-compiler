import os
import sys
import hmac
import hashlib
import subprocess
import winsound
import tkinter as tk
from tkinter import ttk, messagebox
from typing import Optional
from .logger import get_logger
from .resource_locator import get_config_asset
from .schematic_builder import MultisimSchematicBuilder
from .document import MS14Document

AUTHOR_NAME = "Dang-Khoa N. Nguyen"
AUTHOR_EMAIL = "khoadnguyennguyen@gmail.com"

_MASK_A = bytes([0x9A, 0x43, 0xE1, 0x77, 0x12, 0xB8, 0x5C, 0x39, 0xD4, 0x6E, 0x81, 0x2F, 0xA9, 0x05, 0xF3, 0x4B, 0x7E, 0xC2, 0x19, 0x60, 0x8D, 0x34, 0xE7, 0x51, 0xB0, 0x28, 0x9F, 0x72, 0xD8, 0x46, 0x1B, 0xA4])
_MASK_B = bytes([0xD7, 0x10, 0xA8, 0x3A, 0x4D, 0xFB, 0x13, 0x6B, 0x81, 0x39, 0xC5, 0x76, 0xFA, 0x4E, 0xB1, 0x14, 0x21, 0x8F, 0x57, 0x27, 0xDA, 0x65, 0xB8, 0x0E, 0xE9, 0x7B, 0xCA, 0x3E, 0x93, 0x19, 0x55, 0xF0])

class SecurityLicenseManager:
    def __init__(self):
        self.logger = get_logger("DRM-SECURITY")
        self._session_token: Optional[str] = None
        self.license_path = self._resolve_license_store()

    @staticmethod
    def _resolve_license_store() -> str:
        appdata = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or os.path.expanduser("~")
        sec_dir = os.path.join(appdata, "MSimStudio")
        os.makedirs(sec_dir, exist_ok=True)
        return os.path.join(sec_dir, "license.key")

    @staticmethod
    def _derive_master_secret() -> bytes:
        return bytes(a ^ b for a, b in zip(_MASK_A, _MASK_B))

    @staticmethod
    def _query_wmic(cmd: str) -> str:
        try:
            si = subprocess.STARTUPINFO()
            si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            raw = subprocess.check_output(cmd, shell=True, startupinfo=si, stderr=subprocess.DEVNULL, timeout=3).decode("utf-8", errors="ignore")
            lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
            return lines[1] if len(lines) >= 2 else "FALLBACK"
        except Exception:
            return "FALLBACK"

    @classmethod
    def get_hardware_id(cls) -> str:
        mb_uuid = cls._query_wmic("wmic csproduct get uuid")
        cpu_id = cls._query_wmic("wmic cpu get processorid")
        vol_id = cls._query_wmic("wmic logicaldisk where \"DeviceID='C:'\" get VolumeSerialNumber")
        composite = f"MB:{mb_uuid}|CPU:{cpu_id}|VOL:{vol_id}"
        digest = hashlib.sha256(composite.encode("utf-8")).hexdigest().upper()
        return f"MSIM-{digest[:6]}-{digest[6:12]}-{digest[12:18]}"

    @classmethod
    def compute_license_key(cls, hwid: str) -> str:
        clean_hwid = hwid.strip().upper()
        mac = hmac.new(cls._derive_master_secret(), clean_hwid.encode("utf-8"), hashlib.sha256).hexdigest().upper()
        return f"{mac[0:4]}-{mac[4:8]}-{mac[8:12]}-{mac[12:16]}-{mac[16:20]}"

    def verify_key(self, candidate_key: str) -> bool:
        hwid = self.get_hardware_id()
        expected = self.compute_license_key(hwid)
        candidate = candidate_key.strip().upper()
        is_ok = hmac.compare_digest(candidate, expected)
        if is_ok:
            self._session_token = hmac.new(self._derive_master_secret(), f"AUTHORIZED::{hwid}::{expected}".encode("utf-8"), hashlib.sha256).hexdigest()
        else:
            self._session_token = None
        return is_ok

    def is_session_authorized(self) -> bool:
        if not self._session_token:
            return False
        hwid = self.get_hardware_id()
        expected_key = self.compute_license_key(hwid)
        expected_tok = hmac.new(self._derive_master_secret(), f"AUTHORIZED::{hwid}::{expected_key}".encode("utf-8"), hashlib.sha256).hexdigest()
        return hmac.compare_digest(self._session_token, expected_tok)

    def load_persisted_license(self) -> bool:
        if not os.path.exists(self.license_path):
            return False
        try:
            with open(self.license_path, "r", encoding="utf-8") as f:
                return self.verify_key(f.read().strip())
        except Exception:
            return False

    def save_license(self, valid_key: str) -> None:
        with open(self.license_path, "w", encoding="utf-8") as f:
            f.write(valid_key.strip().upper())

    def prompt_license_dialog(self) -> None:
        if self.load_persisted_license():
            return

        hwid = self.get_hardware_id()
        dlg = tk.Tk()
        dlg.title("MSimStudio Enterprise - License Activation")
        w, h = 500, 265
        ws = dlg.winfo_screenwidth()
        hs = dlg.winfo_screenheight()
        dlg.geometry(f"{w}x{h}+{(ws - w) // 2}+{(hs - h) // 2}")
        dlg.resizable(False, False)

        ico_path = get_config_asset("app_icon.ico")
        if os.path.exists(ico_path):
            try:
                dlg.iconbitmap(default=ico_path)
            except Exception:
                pass

        container = ttk.Frame(dlg, padding=18)
        container.pack(fill="both", expand=True)
        ttk.Label(container, text="Hardware-Locked License Activation Required", font=("Segoe UI", 11, "bold")).pack(anchor="w")
        ttk.Label(container, text=f"Author: {AUTHOR_NAME} ({AUTHOR_EMAIL})", font=("Segoe UI", 9)).pack(anchor="w", pady=(2, 10))

        hwid_frame = ttk.Frame(container)
        hwid_frame.pack(fill="x", pady=4)
        ttk.Label(hwid_frame, text="Hardware ID:", width=14).pack(side="left")
        ent_hwid = ttk.Entry(hwid_frame, font=("Consolas", 10))
        ent_hwid.insert(0, hwid)
        ent_hwid.configure(state="readonly")
        ent_hwid.pack(side="left", fill="x", expand=True, padx=(0, 6))

        ttk.Button(hwid_frame, text="Copy", width=8, command=lambda: [dlg.clipboard_clear(), dlg.clipboard_append(hwid)]).pack(side="right")

        key_frame = ttk.Frame(container)
        key_frame.pack(fill="x", pady=8)
        ttk.Label(key_frame, text="License Key:", width=14).pack(side="left")
        ent_key = ttk.Entry(key_frame, font=("Consolas", 10, "bold"))
        ent_key.pack(side="left", fill="x", expand=True)

        ttk.Label(container, text="Closing this dialog runs in evaluation mode with restricted schematic export.", font=("Segoe UI", 8, "italic")).pack(anchor="w", pady=(4, 12))

        btn_row = ttk.Frame(container)
        btn_row.pack(fill="x")

        def on_activate():
            entered = ent_key.get().strip().upper()
            if self.verify_key(entered):
                self.save_license(entered)
                messagebox.showinfo("License Activated", "Hardware license verified. Full synthesis unlocked.")
                dlg.destroy()
            else:
                messagebox.showerror("Invalid License Key", f"The entered key is invalid for Hardware ID:\n{hwid}\n\nContact: {AUTHOR_EMAIL}")

        ttk.Button(btn_row, text="Activate License", command=on_activate).pack(side="right", padx=(6, 0))
        ttk.Button(btn_row, text="Continue Unlicensed", command=dlg.destroy).pack(side="right")
        dlg.protocol("WM_DELETE_WINDOW", dlg.destroy)
        dlg.mainloop()

    def export_unlicensed_schematic(self, output_ms14_path: str) -> str:
        hwid = self.get_hardware_id()
        self.logger.warning(f"Intercepted unlicensed schematic synthesis request -> '{output_ms14_path}'")

        wav_path = get_config_asset("drm_alert.wav")
        if os.path.exists(wav_path):
            try:
                winsound.PlaySound(wav_path, winsound.SND_FILENAME | winsound.SND_ASYNC)
            except Exception:
                pass

        builder = MultisimSchematicBuilder.from_empty_sheet()

        phrases = [
            f"MUA B\u1ea2N QUY\u1ec0N \u0110I CHA !!! ({AUTHOR_EMAIL})",
            f"BUY THE LICENSE ALREADY !!! ({AUTHOR_EMAIL})",
            "\u8d76\u7d27\u4e70\u6b63\u7248\u6388\u6743\u5427\uff0c\u8001\u5144 !!!",
            "\u0e0b\u0e37\u0e49\u0e2d\u0e25\u0e34\u0e02\u0e2a\u0e34\u0e17\u0e18\u0e34\u0e4c\u0e40\u0e16\u0e2d\u0e30\u0e1e\u0e48\u0e2d\u0e2b\u0e19\u0e38\u0e48\u0e21 !!!",
            "\u041a\u0423\u041f\u0418 \u0423\u0416\u0415 \u041b\u0418\u0426\u0415\u041d\u0417\u0418\u042e, \u0411\u0420\u0410\u0422 !!!",
            "\u3044\u3044\u52a0\u6e1b\u30e9\u30a4\u30bb\u30f3\u30b9\u3092\u8cb7\u3048 !!!",
            "\ub77c\uc774\uc13c\uc2a4 \uc880 \uc0ac\ub77c\uace0 !!!",
            "\u00a1COMPRA LA LICENCIA YA, HOMBRE !!!"
        ]

        lines = []
        for i in range(45):
            row_str = "   |   ".join(phrases[(i + j) % len(phrases)] for j in range(3))
            lines.append(row_str)

        builder.add_schematic_info_banner(
            title=f"MUA B\u1ea2N QUY\u1ec0N \u0110I CHA !!! | LI\u00caN H\u1ec6 (CONTACT): {AUTHOR_EMAIL} | AUTHOR: {AUTHOR_NAME} | HWID: {hwid}",
            lines=lines,
            x=18,
            y=18
        )

        os.makedirs(os.path.dirname(os.path.abspath(output_ms14_path)), exist_ok=True)
        doc = MS14Document(codec=builder.codec)
        doc.xml_text = builder.xml_text
        doc.save(output_ms14_path)
        self.logger.info(f"Wrote multilingual watermarked schematic -> '{output_ms14_path}'")
        return output_ms14_path

GLOBAL_DRM = SecurityLicenseManager()
