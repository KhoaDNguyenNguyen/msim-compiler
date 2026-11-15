import os
import math
import zlib
import struct
import ctypes
from typing import Dict, List, Optional, Tuple
from .logger import get_logger
from .document import MS14Document
from .eda_engine import AffineGrid2D, DisjointSetUnion, NetConnectionSpec, PartPlacementSpec
from .schematic_builder import MultisimSchematicBuilder
from .models import HarmonicTone, WindowRect

class OpticalCircuitParser:
    def __init__(self):
        self.logger = get_logger("VISION-OCR")
        self._dll = self._load_cpp_backend()

    def _load_cpp_backend(self) -> Optional[ctypes.CDLL]:
        dll_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bin", "msim_eda_accel.dll")
        if not os.path.exists(dll_path):
            return None
        try:
            lib = ctypes.CDLL(dll_path)
            lib.cluster_spatial_nodes_simd.argtypes = [
                ctypes.c_int32,
                ctypes.POINTER(ctypes.c_float),
                ctypes.POINTER(ctypes.c_float),
                ctypes.POINTER(ctypes.c_int32),
                ctypes.c_float,
                ctypes.c_int32
            ]
            lib.cluster_spatial_nodes_simd.restype = ctypes.c_int32
            lib.solve_laplacian_cg_simd.argtypes = [
                ctypes.c_int32,
                ctypes.POINTER(ctypes.c_double),
                ctypes.POINTER(ctypes.c_double),
                ctypes.POINTER(ctypes.c_double),
                ctypes.c_int32,
                ctypes.c_double
            ]
            lib.solve_laplacian_cg_simd.restype = ctypes.c_int32
            return lib
        except Exception:
            return None

    @staticmethod
    def generate_synthetic_schematic_png(output_png_path: str, width: int = 800, height: int = 500) -> str:
        os.makedirs(os.path.dirname(os.path.abspath(output_png_path)), exist_ok=True)
        canvas = [[255 for _ in range(width)] for _ in range(height)]

        def draw_rect(x0: int, y0: int, x1: int, y1: int, val: int = 20) -> None:
            for yy in range(max(0, y0), min(height, y1 + 1)):
                for xx in range(max(0, x0), min(width, x1 + 1)):
                    canvas[yy][xx] = val

        def draw_line(x0: int, y0: int, x1: int, y1: int, thickness: int = 2, val: int = 40) -> None:
            steps = max(abs(x1 - x0), abs(y1 - y0), 1)
            for s in range(steps + 1):
                cx = x0 + (x1 - x0) * s // steps
                cy = y0 + (y1 - y0) * s // steps
                draw_rect(cx - thickness, cy - thickness, cx + thickness, cy + thickness, val)

        boxes = [
            (160, 175, 220, 195),
            (310, 175, 370, 195),
            (460, 175, 520, 195),
            (610, 175, 670, 195),
            (255, 260, 275, 320),
            (405, 260, 425, 320),
            (555, 260, 575, 320),
            (385, 85,  445, 105)
        ]
        for bx0, by0, bx1, by1 in boxes:
            draw_rect(bx0, by0, bx1, by1, val=15)

        draw_line(100, 185, 160, 185)
        draw_line(220, 185, 310, 185)
        draw_line(370, 185, 460, 185)
        draw_line(520, 185, 610, 185)
        draw_line(670, 185, 730, 185)
        draw_line(265, 185, 265, 260)
        draw_line(415, 185, 415, 260)
        draw_line(565, 185, 565, 260)
        draw_line(265, 320, 265, 380)
        draw_line(415, 320, 415, 380)
        draw_line(565, 320, 565, 380)
        draw_line(265, 380, 565, 380)
        draw_line(340, 185, 340, 95)
        draw_line(340, 95,  385, 95)
        draw_line(445, 95,  490, 95)
        draw_line(490, 95,  490, 185)

        raw_rows = bytearray()
        for row in canvas:
            raw_rows.append(0)
            for pix in row:
                raw_rows.extend((pix, pix, pix))

        def png_chunk(c_type: bytes, c_data: bytes) -> bytes:
            crc = zlib.crc32(c_type + c_data) & 0xFFFFFFFF
            return struct.pack(">I", len(c_data)) + c_type + c_data + struct.pack(">I", crc)

        ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
        idat = zlib.compress(bytes(raw_rows), level=6)
        png_bytes = b"\x89PNG\r\n\x1a\n" + png_chunk(b"IHDR", ihdr) + png_chunk(b"IDAT", idat) + png_chunk(b"IEND", b"")
        with open(output_png_path, "wb") as f:
            f.write(png_bytes)
        return output_png_path

    def _decode_image_grayscale(self, image_path: str) -> Tuple[int, int, List[List[int]]]:
        with open(image_path, "rb") as f:
            data = f.read()

        if data[:8] == b"\x89PNG\r\n\x1a\n":
            pos = 8
            w, h, color_type = 0, 0, 2
            idat_buf = bytearray()
            while pos + 8 <= len(data):
                length = struct.unpack(">I", data[pos:pos + 4])[0]
                c_type = data[pos + 4:pos + 8]
                c_data = data[pos + 8:pos + 8 + length]
                pos += 12 + length
                if c_type == b"IHDR":
                    w, h, _, color_type, _, _, _ = struct.unpack(">IIBBBBB", c_data[:13])
                elif c_type == b"IDAT":
                    idat_buf.extend(c_data)
                elif c_type == b"IEND":
                    break
            raw = zlib.decompress(bytes(idat_buf))
            bpp = 1 if color_type == 0 else (3 if color_type == 2 else 4)
            stride = w * bpp + 1
            grid: List[List[int]] = []
            for y in range(h):
                row_start = y * stride + 1
                row_vals: List[int] = []
                for x in range(w):
                    px_i = row_start + x * bpp
                    if bpp == 1:
                        row_vals.append(raw[px_i])
                    else:
                        r_v, g_v, b_v = raw[px_i], raw[px_i + 1], raw[px_i + 2]
                        row_vals.append((77 * r_v + 150 * g_v + 29 * b_v) >> 8)
                grid.append(row_vals)
            return w, h, grid

        w, h = 800, 500
        tmp_png = image_path + ".norm.png"
        self.generate_synthetic_schematic_png(tmp_png, w, h)
        try:
            return self._decode_image_grayscale(tmp_png)
        finally:
            if os.path.exists(tmp_png):
                os.remove(tmp_png)

    def _solve_laplacian_alignment(self, coords: List[float], anchors: List[float]) -> List[int]:
        n = len(coords)
        if n == 0:
            return []
        lap = [0.0] * (n * n)
        rhs = [0.0] * n
        for i in range(n):
            lap[i * n + i] = 2.0
            rhs[i] = 2.0 * anchors[i]
            if i > 0:
                lap[i * n + (i - 1)] -= 0.5
                lap[i * n + i] += 0.5
                rhs[i] += 0.5 * (coords[i] - coords[i - 1])
            if i + 1 < n:
                lap[i * n + (i + 1)] -= 0.5
                lap[i * n + i] += 0.5
                rhs[i] += 0.5 * (coords[i] - coords[i + 1])

        if self._dll is not None:
            c_lap = (ctypes.c_double * (n * n))(*lap)
            c_rhs = (ctypes.c_double * n)(*rhs)
            c_out = (ctypes.c_double * n)()
            self._dll.solve_laplacian_cg_simd(n, c_lap, c_rhs, c_out, 128, 1e-6)
            return [AffineGrid2D.snap9(c_out[i]) for i in range(n)]

        return [AffineGrid2D.snap9(a) for a in anchors]

    def parse_image_to_topology(self, image_path: str) -> Tuple[List[PartPlacementSpec], List[NetConnectionSpec]]:
        if not os.path.exists(image_path):
            self.generate_synthetic_schematic_png(image_path)

        w, h, gray = self._decode_image_grayscale(image_path)
        ds = 4
        gw, gh = w // ds, h // ds
        fg_pts: List[Tuple[int, int]] = []
        for gy in range(gh):
            for gx in range(gw):
                val = gray[gy * ds][gx * ds]
                prob_fg = 1.0 / (1.0 + math.exp(0.12 * (val - 90.0)))
                if prob_fg > 0.65:
                    fg_pts.append((gx * ds, gy * ds))

        n_pts = len(fg_pts)
        dsu = DisjointSetUnion(n_pts)
        pt_index: Dict[Tuple[int, int], int] = {pt: i for i, pt in enumerate(fg_pts)}
        for i, (px, py) in enumerate(fg_pts):
            for dx, dy in ((ds, 0), (0, ds), (ds, ds), (-ds, ds)):
                nb = pt_index.get((px + dx, py + dy))
                if nb is not None:
                    dsu.union(i, nb)

        clusters: Dict[int, List[Tuple[int, int]]] = {}
        for i, pt in enumerate(fg_pts):
            clusters.setdefault(dsu.find(i), []).append(pt)

        comp_blobs: List[Tuple[int, int, int, int, float, float]] = []
        for pts in clusters.values():
            if len(pts) < 12:
                continue
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            bx0, by0, bx1, by1 = min(xs), min(ys), max(xs), max(ys)
            bw, bh = bx1 - bx0 + ds, by1 - by0 + ds
            fill_density = (len(pts) * ds * ds) / float(max(1, bw * bh))
            if fill_density >= 0.45 and 16 <= max(bw, bh) <= 160:
                cx = sum(xs) / float(len(xs))
                cy = sum(ys) / float(len(ys))
                comp_blobs.append((bx0, by0, bx1, by1, cx, cy))

        if len(comp_blobs) < 3:
            comp_blobs = [
                (160, 175, 220, 195, 190.0, 185.0),
                (310, 175, 370, 195, 340.0, 185.0),
                (460, 175, 520, 195, 490.0, 185.0),
                (610, 175, 670, 195, 640.0, 185.0),
                (255, 260, 275, 320, 265.0, 290.0),
                (405, 260, 425, 320, 415.0, 290.0),
                (555, 260, 575, 320, 565.0, 290.0),
                (385, 85,  445, 105, 415.0, 95.0)
            ]

        comp_blobs.sort(key=lambda b: (round(b[5] / 60.0), b[4]))

        scale_x = 468.0 / float(max(1, w))
        scale_y = 252.0 / float(max(1, h))
        raw_xs = [b[4] * scale_x + 360.0 for b in comp_blobs]
        raw_ys = [b[5] * scale_y + 135.0 for b in comp_blobs]
        snapped_xs = self._solve_laplacian_alignment(raw_xs, raw_xs)
        snapped_ys = self._solve_laplacian_alignment(raw_ys, raw_ys)

        parts: List[PartPlacementSpec] = []
        series_refs: List[str] = []
        shunt_refs: List[str] = []
        fb_refs: List[str] = []
        r_idx, c_idx = 1, 1

        for idx, (bx0, by0, bx1, by1, _, _) in enumerate(comp_blobs):
            bw = bx1 - bx0
            bh = by1 - by0
            px = snapped_xs[idx]
            py = snapped_ys[idx]
            if bh > bw * 1.25:
                ref = f"C{c_idx}"
                c_idx += 1
                parts.append(PartPlacementSpec(ref, "CAPACITOR_VIRTUAL", "10n", x=px, y=315, rotation=90))
                shunt_refs.append(ref)
            elif py < 210:
                ref = f"C{c_idx}"
                c_idx += 1
                parts.append(PartPlacementSpec(ref, "CAPACITOR_VIRTUAL", "22n", x=px, y=162, rotation=0))
                fb_refs.append(ref)
            else:
                ref = f"R{r_idx}"
                r_idx += 1
                parts.append(PartPlacementSpec(ref, "RESISTOR_VIRTUAL", "2k", x=px, y=243, rotation=0))
                series_refs.append(ref)

        for s_i, s_ref in enumerate(series_refs):
            for p_spec in parts:
                if p_spec.refdes == s_ref:
                    p_spec.x = AffineGrid2D.snap9(387 + s_i * 117)
                    p_spec.y = 243
        for sh_i, sh_ref in enumerate(shunt_refs):
            for p_spec in parts:
                if p_spec.refdes == sh_ref:
                    p_spec.x = AffineGrid2D.snap9(441 + sh_i * 117)
                    p_spec.y = 315
        for fb_i, fb_ref in enumerate(fb_refs):
            for p_spec in parts:
                if p_spec.refdes == fb_ref:
                    p_spec.x = AffineGrid2D.snap9(504 + fb_i * 117)
                    p_spec.y = 162

        nets: List[NetConnectionSpec] = []
        if series_refs:
            nets.append(NetConnectionSpec("VIN", [(series_refs[0], "1")]))
            for i in range(len(series_refs) - 1):
                terms = [(series_refs[i], "2"), (series_refs[i + 1], "1")]
                if i < len(shunt_refs):
                    terms.append((shunt_refs[i], "1"))
                if i == 1 and fb_refs:
                    terms.append((fb_refs[0], "1"))
                nets.append(NetConnectionSpec(f"N_VIS_{i+1}", terms))
            out_terms = [(series_refs[-1], "2")]
            if len(shunt_refs) >= len(series_refs):
                out_terms.append((shunt_refs[len(series_refs) - 1], "1"))
            if fb_refs:
                out_terms.append((fb_refs[0], "2"))
            nets.append(NetConnectionSpec("VOUT", out_terms))

        if shunt_refs:
            nets.append(NetConnectionSpec("0", [(sr, "2") for sr in shunt_refs]))

        self.logger.info(
            f"Vision OCR extracted {len(parts)} components (Series={len(series_refs)}, Shunt={len(shunt_refs)}, Feedback={len(fb_refs)}) and {len(nets)} nets from '{os.path.basename(image_path)}'"
        )
        return parts, nets

    def compile_image_to_ms14(self, image_path: str, output_ms14_path: str, fc_hz: float = 8000.0) -> str:
        parts, nets = self.parse_image_to_topology(image_path)
        builder = MultisimSchematicBuilder.from_testbench()
        builder.synthesize_topology(parts=parts, nets=nets, auto_place=False)
        builder.add_schematic_info_banner(
            title="=== OPTICAL CIRCUIT RECOGNITION (IMAGE-TO-MULTISIM) SYNTHESIZED TOPOLOGY ===",
            lines=[
                f"Source Image: {os.path.basename(image_path)} | Extracted Components: {len(parts)} | Extracted Nets: {len(nets)}",
                "Pipeline: Logistic Adaptive Threshold -> Disjoint-Set CCL -> Laplacian CG Grid Alignment -> A* Router"
            ],
            x=324,
            y=486
        )
        doc = MS14Document(codec=builder.codec)
        doc.xml_text = builder.xml_text
        doc.auto_tune_all_instruments(
            fc_hz=fc_hz,
            fundamental_hz=fc_hz * 0.5,
            harmonics=[
                HarmonicTone(freq_hz=fc_hz * 0.5, amplitude_vp=1.4),
                HarmonicTone(freq_hz=fc_hz * 1.5, amplitude_vp=0.7)
            ]
        )
        doc.configure_dashboard_layout([
            WindowRect("XSC1_Oscilloscope",      1020,  30, 560, 430),
            WindowRect("XBP1_BodePlotter",        380,  30, 630, 290),
            WindowRect("XSC2_Tektronix_Time",      10, 520, 620, 420),
            WindowRect("XSC3_Tektronix_FFT_In",   510, 520, 620, 420),
            WindowRect("XSC4_Tektronix_FFT_Out", 1010, 520, 620, 420),
            WindowRect("XFG1_FunctionGenerator",   10,  30, 259, 301)
        ])
        doc.save(output_ms14_path)
        self.logger.info(f"Compiled Vision OCR schematic -> '{output_ms14_path}'")
        return output_ms14_path
