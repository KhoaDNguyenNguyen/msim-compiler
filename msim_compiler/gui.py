import os
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from .compiler import MultisimHighOrderCompiler
from .vision_parser import OpticalCircuitParser
from .models import HarmonicTone
from .logger import get_logger
from .drm_guard import GLOBAL_DRM, AUTHOR_NAME, AUTHOR_EMAIL
from .splash import show_startup_splash
from .resource_locator import get_config_asset

class MultisimCompilerGUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.logger = get_logger("MSIM-GUI")
        lic_tag = "Licensed" if GLOBAL_DRM.is_session_authorized() else "Unlicensed Evaluation"
        self.title(f"MSimStudio Enterprise [{lic_tag}] - Author: {AUTHOR_NAME}")
        self.geometry("800x540")

        ico_path = get_config_asset("app_icon.ico")
        if os.path.exists(ico_path):
            try:
                self.iconbitmap(default=ico_path)
            except Exception:
                pass

        self._build_ui()

    @staticmethod
    def _default_out_path(filename: str) -> str:
        cwd = os.path.abspath(os.getcwd())
        if os.path.basename(cwd).lower() == "dist":
            return os.path.join(cwd, filename)
        return os.path.abspath(os.path.join("dist", filename))

    def _build_ui(self):
        tab_control = ttk.Notebook(self)

        self.tab_code = ttk.Frame(tab_control, padding=16)
        tab_control.add(self.tab_code, text="Mode 1: Parametric / Code-to-Multisim")

        self.tab_vision = ttk.Frame(tab_control, padding=16)
        tab_control.add(self.tab_vision, text="Mode 2: Image-to-Multisim (Vision OCR)")

        tab_control.pack(expand=1, fill="both")

        ttk.Label(self.tab_code, text="Butterworth Filter Order (3 - 11):").grid(row=0, column=0, sticky="w", pady=8)
        self.val_order = tk.StringVar(value="7")
        ttk.Entry(self.tab_code, textvariable=self.val_order, width=25).grid(row=0, column=1, sticky="w", padx=10)

        ttk.Label(self.tab_code, text="Cutoff Frequency fc (Hz):").grid(row=1, column=0, sticky="w", pady=8)
        self.val_fc = tk.StringVar(value="12000")
        ttk.Entry(self.tab_code, textvariable=self.val_fc, width=25).grid(row=1, column=1, sticky="w", padx=10)

        ttk.Label(self.tab_code, text="Output .ms14 File Path:").grid(row=2, column=0, sticky="w", pady=8)
        self.val_code_out = tk.StringVar(value=self._default_out_path("Synthesized_Filter.ms14"))
        ttk.Entry(self.tab_code, textvariable=self.val_code_out, width=50).grid(row=2, column=1, sticky="w", padx=10)

        ttk.Button(
            self.tab_code,
            text="Compile Symmetrical Active Filter (.ms14)",
            command=self._run_synthesis
        ).grid(row=3, column=0, columnspan=2, pady=20)

        ttk.Label(self.tab_vision, text="Input Schematic Image (.png / .jpg / .bmp):").grid(row=0, column=0, sticky="w", pady=8)
        self.val_img = tk.StringVar(value=self._default_out_path("sample_input_circuit.png"))
        ttk.Entry(self.tab_vision, textvariable=self.val_img, width=45).grid(row=0, column=1, padx=8)
        ttk.Button(self.tab_vision, text="Browse...", command=self._browse_image).grid(row=0, column=2, padx=4)

        ttk.Label(self.tab_vision, text="Output .ms14 File Path:").grid(row=1, column=0, sticky="w", pady=8)
        self.val_vis_out = tk.StringVar(value=self._default_out_path("vision_synthesized_circuit.ms14"))
        ttk.Entry(self.tab_vision, textvariable=self.val_vis_out, width=45).grid(row=1, column=1, padx=8)

        ttk.Button(self.tab_vision, text="Generate Sample Schematic Image", command=self._gen_sample_image).grid(row=2, column=0, pady=16)
        ttk.Button(self.tab_vision, text="Extract Topology & Compile to .ms14", command=self._run_vision).grid(row=2, column=1, columnspan=2, pady=16)

        footer_text = f"Author: {AUTHOR_NAME} ({AUTHOR_EMAIL}) | HWID: {GLOBAL_DRM.get_hardware_id()}"
        self.status_var = tk.StringVar(value=footer_text)
        ttk.Label(self, textvariable=self.status_var, relief="sunken", anchor="w", padding=6).pack(side="bottom", fill="x")

    def _browse_image(self):
        pth = filedialog.askopenfilename(filetypes=[("Circuit Images", "*.png;*.jpg;*.jpeg;*.bmp")])
        if pth:
            self.val_img.set(pth)

    def _gen_sample_image(self):
        out_png = self.val_img.get().strip() or self._default_out_path("sample_input_circuit.png")
        OpticalCircuitParser.generate_synthetic_schematic_png(out_png)
        self.val_img.set(out_png)
        self.status_var.set(f"Generated sample circuit image: {out_png}")

    def _run_synthesis(self):
        def task():
            try:
                out = self.val_code_out.get()
                if not GLOBAL_DRM.is_session_authorized():
                    self.status_var.set("Exporting evaluation schematic...")
                    GLOBAL_DRM.export_unlicensed_schematic(out)
                    self.status_var.set(f"Evaluation export complete -> {out}")
                    messagebox.showwarning(
                        "Unlicensed Evaluation Export",
                        f"Exported file to:\n{out}\n\nOpen in NI Multisim or contact {AUTHOR_EMAIL} to purchase a license."
                    )
                    return

                self.status_var.set("Synthesizing filter schematic...")
                order = int(self.val_order.get())
                fc = float(self.val_fc.get())
                tones = [
                    HarmonicTone(freq_hz=fc * 0.5, amplitude_vp=1.4),
                    HarmonicTone(freq_hz=fc * 1.5, amplitude_vp=0.8),
                    HarmonicTone(freq_hz=fc * 2.5, amplitude_vp=0.6)
                ]
                c = MultisimHighOrderCompiler()
                c.compile_high_order_filter(order=order, fc_hz=fc, output_ms14_path=out, harmonics=tones)
                self.status_var.set(f"Compiled successfully -> {out}")
                messagebox.showinfo("Synthesis Complete", f"Compiled {order}th-order Butterworth circuit to:\n{out}")
            except Exception as e:
                self.logger.error(f"Synthesis failed: {e}")
                self.status_var.set(f"Error: {e}")
                messagebox.showerror("Compilation Error", str(e))
        threading.Thread(target=task, daemon=True).start()

    def _run_vision(self):
        def task():
            try:
                out = self.val_vis_out.get()
                if not GLOBAL_DRM.is_session_authorized():
                    self.status_var.set("Exporting evaluation schematic...")
                    GLOBAL_DRM.export_unlicensed_schematic(out)
                    self.status_var.set(f"Evaluation export complete -> {out}")
                    messagebox.showwarning(
                        "Unlicensed Evaluation Export",
                        f"Exported file to:\n{out}\n\nOpen in NI Multisim or contact {AUTHOR_EMAIL} to purchase a license."
                    )
                    return

                self.status_var.set("Running Vision OCR + Laplacian CG + A* Router...")
                parser = OpticalCircuitParser()
                out = parser.compile_image_to_ms14(self.val_img.get(), out)
                self.status_var.set(f"Vision compilation complete -> {out}")
                messagebox.showinfo("Vision OCR Complete", f"Compiled image to Multisim schematic:\n{out}")
            except Exception as e:
                self.logger.error(f"Vision OCR failed: {e}")
                self.status_var.set(f"Error: {e}")
                messagebox.showerror("Vision OCR Error", str(e))
        threading.Thread(target=task, daemon=True).start()

def launch_gui():
    show_startup_splash(duration_ms=2400)
    GLOBAL_DRM.prompt_license_dialog()
    app = MultisimCompilerGUI()
    app.mainloop()
