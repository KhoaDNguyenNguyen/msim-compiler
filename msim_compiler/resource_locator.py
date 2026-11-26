import os
import sys

def get_bundle_root() -> str:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return getattr(sys, "_MEIPASS")
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def get_pkg_dir() -> str:
    return os.path.join(get_bundle_root(), "msim_compiler")

def get_config_asset(filename: str) -> str:
    return os.path.join(get_pkg_dir(), "config", filename)
