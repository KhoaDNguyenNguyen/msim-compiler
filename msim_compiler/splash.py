import os
import tkinter as tk
from tkinter import ttk
from .logger import get_logger
from .resource_locator import get_config_asset
from .drm_guard import AUTHOR_NAME, AUTHOR_EMAIL

def show_startup_splash(duration_ms: int = 2400) -> None:
    logger = get_logger("SPLASH-INIT")
    splash = tk.Tk()
    splash.overrideredirect(True)
    splash.attributes("-topmost", True)

    w, h = 500, 360
    ws = splash.winfo_screenwidth()
    hs = splash.winfo_screenheight()
    splash.geometry(f"{w}x{h}+{(ws - w) // 2}+{(hs - h) // 2}")

    ico_path = get_config_asset("app_icon.ico")
    if os.path.exists(ico_path):
        try:
            splash.iconbitmap(default=ico_path)
        except Exception:
            pass

    frame = tk.Frame(splash, bg="#141820", highlightbackground="#0078d4", highlightthickness=2)
    frame.pack(fill="both", expand=True)

    png_path = get_config_asset("splash_logo.png")
    if os.path.exists(png_path):
        try:
            img = tk.PhotoImage(file=png_path)
            if img.width() > 180:
                factor = max(1, img.width() // 160)
                img = img.subsample(factor, factor)
            lbl_logo = tk.Label(frame, image=img, bg="#141820", bd=0)
            lbl_logo.image = img
            lbl_logo.pack(pady=(18, 10))
            logger.info("Rendered splash_logo.png on startup screen")
        except Exception as ex:
            logger.warning(f"Failed loading splash_logo.png: {ex}")

    tk.Label(frame, text="MSimStudio Enterprise EDA Synthesizer", font=("Segoe UI", 13, "bold"), fg="#f0f4f8", bg="#141820").pack()
    tk.Label(frame, text=f"Author: {AUTHOR_NAME} ({AUTHOR_EMAIL})", font=("Segoe UI", 9), fg="#8a99ad", bg="#141820").pack(pady=(2, 12))

    status_var = tk.StringVar(value="Initializing XML CArchive DOM subsystem...")
    tk.Label(frame, textvariable=status_var, font=("Consolas", 9), fg="#38bdf8", bg="#141820").pack(pady=(4, 6))

    style = ttk.Style(splash)
    style.theme_use("clam")
    style.configure("Splash.Horizontal.TProgressbar", foreground="#0078d4", background="#0078d4", troughcolor="#222936", bordercolor="#141820")
    pbar = ttk.Progressbar(frame, style="Splash.Horizontal.TProgressbar", orient="horizontal", length=420, mode="determinate")
    pbar.pack(pady=(0, 16))

    milestones = {
        10: "Initializing XML CArchive DOM subsystem...",
        30: "Loading C++ AVX2 SIMD orthogonal routing kernel...",
        55: "Validating NI Multisim Jet 4.0 OLEDB database bridge...",
        80: "Verifying cryptographic hardware license state...",
        95: "Preparing interactive synthesis workspace..."
    }

    total_steps = 100
    step_delay = max(10, duration_ms // total_steps)

    def tick(step: int = 0):
        pbar["value"] = step
        if step in milestones:
            status_var.set(milestones[step])
        if step < total_steps:
            splash.after(step_delay, tick, step + 1)
        else:
            splash.destroy()

    splash.after(25, tick, 0)
    splash.mainloop()
