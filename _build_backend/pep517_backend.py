import os
import csv
import base64
import hashlib
import zipfile
from pathlib import Path

PKG_NAME = "msim_compiler"
DIST_NAME = "msim-compiler"
VERSION = "1.0.0"
DIST_INFO = f"{PKG_NAME}-{VERSION}.dist-info"

METADATA_CONTENT = f"""Metadata-Version: 2.1
Name: {DIST_NAME}
Version: {VERSION}
Summary: Industrial Zero-GUI NI Multisim 14 (.ms14) Schematic Synthesizer, A* Router & Binary Compiler
Requires-Python: >=3.9
"""

WHEEL_CONTENT = """Wheel-Version: 1.0
Generator: msim-pep517-backend
Root-Is-Purelib: true
Tag: py3-none-any
"""

def _hash_bytes(data: bytes) -> str:
    digest = hashlib.sha256(data).digest()
    b64 = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return f"sha256={b64}"

def _write_dist_info(target_dir: Path) -> str:
    info_dir = target_dir / DIST_INFO
    info_dir.mkdir(parents=True, exist_ok=True)
    (info_dir / "METADATA").write_text(METADATA_CONTENT, encoding="utf-8")
    (info_dir / "WHEEL").write_text(WHEEL_CONTENT, encoding="utf-8")
    (info_dir / "top_level.txt").write_text(f"{PKG_NAME}\n", encoding="utf-8")
    return DIST_INFO

def _supported_features():
    return ["build_editable"]

def get_requires_for_build_wheel(config_settings=None):
    return []

def get_requires_for_build_editable(config_settings=None):
    return []

def prepare_metadata_for_build_wheel(metadata_directory, config_settings=None):
    return _write_dist_info(Path(metadata_directory))

def prepare_metadata_for_build_editable(metadata_directory, config_settings=None):
    return _write_dist_info(Path(metadata_directory))

def build_editable(wheel_directory, config_settings=None, metadata_directory=None):
    whl_name = f"{PKG_NAME}-{VERSION}-0.editable-py3-none-any.whl"
    whl_path = Path(wheel_directory) / whl_name
    repo_root = str(Path(__file__).resolve().parent.parent)
    pth_bytes = (repo_root + "\n").encode("utf-8")
    meta_bytes = METADATA_CONTENT.encode("utf-8")
    wheel_bytes = WHEEL_CONTENT.encode("utf-8")
    top_bytes = f"{PKG_NAME}\n".encode("utf-8")

    records = [
        (f"{PKG_NAME}.pth", _hash_bytes(pth_bytes), str(len(pth_bytes))),
        (f"{DIST_INFO}/METADATA", _hash_bytes(meta_bytes), str(len(meta_bytes))),
        (f"{DIST_INFO}/WHEEL", _hash_bytes(wheel_bytes), str(len(wheel_bytes))),
        (f"{DIST_INFO}/top_level.txt", _hash_bytes(top_bytes), str(len(top_bytes))),
        (f"{DIST_INFO}/RECORD", "", "")
    ]
    rec_lines = [f"{r[0]},{r[1]},{r[2]}" for r in records]
    rec_bytes = ("\n".join(rec_lines) + "\n").encode("utf-8")

    with zipfile.ZipFile(whl_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"{PKG_NAME}.pth", pth_bytes)
        zf.writestr(f"{DIST_INFO}/METADATA", meta_bytes)
        zf.writestr(f"{DIST_INFO}/WHEEL", wheel_bytes)
        zf.writestr(f"{DIST_INFO}/top_level.txt", top_bytes)
        zf.writestr(f"{DIST_INFO}/RECORD", rec_bytes)

    return whl_name

def build_wheel(wheel_directory, config_settings=None, metadata_directory=None):
    whl_name = f"{PKG_NAME}-{VERSION}-py3-none-any.whl"
    whl_path = Path(wheel_directory) / whl_name
    repo_root = Path(__file__).resolve().parent.parent
    pkg_dir = repo_root / PKG_NAME
    records = []

    with zipfile.ZipFile(whl_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for file_path in sorted(pkg_dir.rglob("*")):
            if file_path.is_file() and "__pycache__" not in file_path.parts:
                rel_path = file_path.relative_to(repo_root).as_posix()
                data = file_path.read_bytes()
                zf.writestr(rel_path, data)
                records.append((rel_path, _hash_bytes(data), str(len(data))))

        meta_bytes = METADATA_CONTENT.encode("utf-8")
        wheel_bytes = WHEEL_CONTENT.encode("utf-8")
        top_bytes = f"{PKG_NAME}\n".encode("utf-8")
        zf.writestr(f"{DIST_INFO}/METADATA", meta_bytes)
        zf.writestr(f"{DIST_INFO}/WHEEL", wheel_bytes)
        zf.writestr(f"{DIST_INFO}/top_level.txt", top_bytes)
        records.append((f"{DIST_INFO}/METADATA", _hash_bytes(meta_bytes), str(len(meta_bytes))))
        records.append((f"{DIST_INFO}/WHEEL", _hash_bytes(wheel_bytes), str(len(wheel_bytes))))
        records.append((f"{DIST_INFO}/top_level.txt", _hash_bytes(top_bytes), str(len(top_bytes))))
        records.append((f"{DIST_INFO}/RECORD", "", ""))

        rec_lines = [f"{r[0]},{r[1]},{r[2]}" for r in records]
        zf.writestr(f"{DIST_INFO}/RECORD", ("\n".join(rec_lines) + "\n").encode("utf-8"))

    return whl_name
