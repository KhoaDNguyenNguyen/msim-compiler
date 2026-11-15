from collections import Counter
import os
import re
import math
import heapq
import ctypes
from dataclasses import dataclass, field
from typing import Dict, List, Tuple, Optional, Set
from .logger import get_logger
from .config_registry import GLOBAL_CONFIG
from .dom_engine import UniversalNetGraph

GRID_PITCH = GLOBAL_CONFIG.routing.grid_pitch

@dataclass
class PartPlacementSpec:
    refdes: str
    part_query: str
    value_or_label: str = ""
    x: Optional[int] = None
    y: Optional[int] = None
    rotation: int = 0
    row_hint: int = 0
    col_hint: int = 0

@dataclass
class NetConnectionSpec:
    net_name: str
    terminals: List[Tuple[str, str]] = field(default_factory=list)

@dataclass
class SteinerJunctionPlan:
    junctions: List[Tuple[str, int, int]] = field(default_factory=list)
    point_to_point_edges: List[Tuple[Tuple[str, str], Tuple[str, str]]] = field(default_factory=list)

@dataclass
class ClosedLoopAuditReport:
    total_symbols: int
    total_junctions: int
    total_wires: int
    total_bends: int
    total_wirelength: int
    max_pin_degree: int
    bounding_box: Tuple[int, int, int, int]
    net_summary: Dict[str, List[str]]

class DisjointSetUnion:
    def __init__(self, n: int):
        self.parent = list(range(n))
        self.rank = [0] * n

    def find(self, i: int) -> int:
        while self.parent[i] != i:
            self.parent[i] = self.parent[self.parent[i]]
            i = self.parent[i]
        return i

    def union(self, i: int, j: int) -> bool:
        root_i = self.find(i)
        root_j = self.find(j)
        if root_i == root_j:
            return False
        if self.rank[root_i] < self.rank[root_j]:
            self.parent[root_i] = root_j
        elif self.rank[root_i] > self.rank[root_j]:
            self.parent[root_j] = root_i
        else:
            self.parent[root_j] = root_i
            self.rank[root_i] += 1
        return True

class AffineGrid2D:
    @staticmethod
    def snap9(value: float) -> int:
        return int(round(value / float(GRID_PITCH))) * GRID_PITCH

    @staticmethod
    def se2_matrix(cx: int, cy: int, angle_deg: int) -> Tuple[Tuple[int, int, int], Tuple[int, int, int], Tuple[int, int, int]]:
        deg = angle_deg % 360
        if deg == 90:
            r00, r01, r10, r11 = 0, -1, 1, 0
        elif deg == 180:
            r00, r01, r10, r11 = -1, 0, 0, -1
        elif deg == 270:
            r00, r01, r10, r11 = 0, 1, -1, 0
        else:
            r00, r01, r10, r11 = 1, 0, 0, 1
        tx = cx - r00 * cx - r01 * cy
        ty = cy - r10 * cx - r11 * cy
        return ((r00, r01, tx), (r10, r11, ty), (0, 0, 1))

    @classmethod
    def rotate_point(cls, x: int, y: int, cx: int, cy: int, angle_deg: int) -> Tuple[int, int]:
        m = cls.se2_matrix(cx, cy, angle_deg)
        nx = m[0][0] * x + m[0][1] * y + m[0][2]
        ny = m[1][0] * x + m[1][1] * y + m[1][2]
        return (nx, ny)

    @staticmethod
    def logistic_penalty(
        occupancy: float,
        capacity: float = GLOBAL_CONFIG.routing.logistic_capacity,
        steepness: float = GLOBAL_CONFIG.routing.logistic_steepness,
        max_penalty: float = GLOBAL_CONFIG.routing.logistic_max_penalty
    ) -> int:
        z = max(-20.0, min(20.0, steepness * (occupancy - capacity)))
        return int(round(max_penalty / (1.0 + math.exp(-z))))

    @classmethod
    def rotate_bbox(cls, bbox: Tuple[int, int, int, int], cx: int, cy: int, angle_deg: int) -> Tuple[int, int, int, int]:
        x0, y0, x1, y1 = bbox
        pts = [
            cls.rotate_point(x0, y0, cx, cy, angle_deg),
            cls.rotate_point(x1, y1, cx, cy, angle_deg),
            cls.rotate_point(x0, y1, cx, cy, angle_deg),
            cls.rotate_point(x1, y0, cx, cy, angle_deg)
        ]
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        return (min(xs), min(ys), max(xs), max(ys))

    @staticmethod
    def simplify_orthogonal_polyline(points: List[Tuple[int, int]]) -> List[Tuple[int, int]]:
        dedup: List[Tuple[int, int]] = []
        for pt in points:
            if not dedup or dedup[-1] != pt:
                dedup.append(pt)
        if len(dedup) < 3:
            return dedup
        simplified: List[Tuple[int, int]] = [dedup[0]]
        for i in range(1, len(dedup) - 1):
            prv = simplified[-1]
            cur = dedup[i]
            nxt = dedup[i + 1]
            if (prv[0] == cur[0] == nxt[0]) or (prv[1] == cur[1] == nxt[1]):
                continue
            simplified.append(cur)
        simplified.append(dedup[-1])
        return simplified

class NativeEDAEngine:
    def __init__(
        self,
        grid_width: int = GLOBAL_CONFIG.routing.grid_width,
        grid_height: int = GLOBAL_CONFIG.routing.grid_height
    ):
        self.logger = get_logger("EDA-DSA-ENGINE")
        self.grid_w = grid_width
        self.grid_h = grid_height
        self.obstacle_grid: List[int] = [0] * (grid_width * grid_height)
        self.congestion_grid: List[int] = [0] * (grid_width * grid_height)
        self.net_owner_grid: List[int] = [0] * (grid_width * grid_height)
        self.net_name_to_id: Dict[str, int] = {}
        self.routed_trees: Dict[str, Set[Tuple[int, int]]] = {}
        self.component_boxes: Dict[str, Tuple[int, int, int, int]] = {}
        self.pin_grid_cells: Set[Tuple[int, int]] = set()
        self._dll = self._load_native_dll()

    def _load_native_dll(self) -> Optional[ctypes.CDLL]:
        dll_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bin", "msim_eda_accel.dll")
        if not os.path.exists(dll_path):
            return None
        try:
            lib = ctypes.CDLL(dll_path)
            lib.astar_orthogonal_route.argtypes = [
                ctypes.c_int32, ctypes.c_int32,
                ctypes.POINTER(ctypes.c_int32),
                ctypes.POINTER(ctypes.c_int32),
                ctypes.POINTER(ctypes.c_int32),
                ctypes.c_int32,
                ctypes.c_int32, ctypes.c_int32,
                ctypes.c_int32, ctypes.c_int32,
                ctypes.c_int32, ctypes.c_int32,
                ctypes.c_int32, ctypes.c_int32,
                ctypes.c_int32, ctypes.c_int32, ctypes.c_int32,
                ctypes.POINTER(ctypes.c_int32),
                ctypes.c_int32
            ]
            lib.astar_orthogonal_route.restype = ctypes.c_int32
            if hasattr(lib, "solve_quadratic_placement_mt"):
                lib.solve_quadratic_placement_mt.argtypes = [
                    ctypes.c_int32,
                    ctypes.POINTER(ctypes.c_double),
                    ctypes.POINTER(ctypes.c_double),
                    ctypes.POINTER(ctypes.c_double),
                    ctypes.POINTER(ctypes.c_double),
                    ctypes.c_int32,
                    ctypes.POINTER(ctypes.c_int32),
                    ctypes.POINTER(ctypes.c_int32)
                ]
                lib.solve_quadratic_placement_mt.restype = ctypes.c_int32
            return lib
        except Exception:
            return None

    def solve_spectral_quadratic_placement(
        self,
        parts: List[PartPlacementSpec],
        nets: List[NetConnectionSpec]
    ) -> List[PartPlacementSpec]:
        n = len(parts)
        if n == 0:
            return parts

        graph = UniversalNetGraph(
            parts=[{"refdes": p.refdes, "x": p.x, "y": p.y} for p in parts],
            nets={net.net_name: net.terminals for net in nets}
        )
        _, adj, anchor_w, anchor_x, anchor_y = graph.build_laplacian_matrices()

        if self._dll is not None and hasattr(self._dll, "solve_quadratic_placement_mt"):
            c_adj = (ctypes.c_double * (n * n))(*adj)
            c_aw = (ctypes.c_double * n)(*anchor_w)
            c_ax = (ctypes.c_double * n)(*anchor_x)
            c_ay = (ctypes.c_double * n)(*anchor_y)
            out_x = (ctypes.c_int32 * n)()
            out_y = (ctypes.c_int32 * n)()
            self._dll.solve_quadratic_placement_mt(n, c_adj, c_aw, c_ax, c_ay, GRID_PITCH, out_x, out_y)
            for idx, p in enumerate(parts):
                if p.x is None:
                    p.x = int(out_x[idx])
                if p.y is None:
                    p.y = int(out_y[idx])
        else:
            for idx, p in enumerate(parts):
                if p.x is None:
                    p.x = AffineGrid2D.snap9(anchor_x[idx])
                if p.y is None:
                    p.y = AffineGrid2D.snap9(anchor_y[idx])
        return parts

    def reset_grid(self) -> None:
        n = self.grid_w * self.grid_h
        self.obstacle_grid = [0] * n
        self.congestion_grid = [0] * n
        self.net_owner_grid = [0] * n
        self.net_name_to_id.clear()
        self.routed_trees.clear()
        self.component_boxes.clear()
        self.pin_grid_cells.clear()

    def _get_net_id(self, net_name: str) -> int:
        if net_name not in self.net_name_to_id:
            self.net_name_to_id[net_name] = len(self.net_name_to_id) + 1
        return self.net_name_to_id[net_name]

    def register_component_obstacle(
        self,
        world_bbox: Tuple[int, int, int, int],
        pin_coords_and_leads: List[Tuple[int, int, int, int]],
        refdes: str = ""
    ) -> None:
        if refdes:
            self.component_boxes[refdes] = world_bbox
        gx0 = max(0, world_bbox[0] // GRID_PITCH)
        gy0 = max(0, world_bbox[1] // GRID_PITCH)
        gx1 = min(self.grid_w - 1, world_bbox[2] // GRID_PITCH)
        gy1 = min(self.grid_h - 1, world_bbox[3] // GRID_PITCH)

        for gy in range(gy0, gy1 + 1):
            for gx in range(gx0, gx1 + 1):
                self.obstacle_grid[gy * self.grid_w + gx] = 1

        for hy in range(max(0, gy0 - 1), min(self.grid_h - 1, gy1 + 1) + 1):
            for hx in range(max(0, gx0 - 1), min(self.grid_w - 1, gx1 + 1) + 1):
                idx = hy * self.grid_w + hx
                if self.obstacle_grid[idx] == 0:
                    self.congestion_grid[idx] += 4

        for px, py, lx, ly in pin_coords_and_leads:
            gpx, gpy = px // GRID_PITCH, py // GRID_PITCH
            glx, gly = lx // GRID_PITCH, ly // GRID_PITCH
            steps = max(abs(glx - gpx), abs(gly - gpy), 1)
            for s in range(steps + 1):
                cx = gpx + (glx - gpx) * s // steps
                cy = gpy + (gly - gpy) * s // steps
                if 0 <= cx < self.grid_w and 0 <= cy < self.grid_h:
                    self.obstacle_grid[cy * self.grid_w + cx] = 0
            self.pin_grid_cells.add((gpx, gpy))

    def _is_grid_point_free(self, gx: int, gy: int) -> bool:
        if not (0 <= gx < self.grid_w and 0 <= gy < self.grid_h):
            return False
        if (gx, gy) in self.pin_grid_cells:
            return False
        return self.obstacle_grid[gy * self.grid_w + gx] == 0

    def _find_nearest_free_junction(
        self,
        target_x: int,
        target_y: int,
        forbidden_coords: Optional[Set[Tuple[int, int]]] = None
    ) -> Tuple[int, int]:
        forb = forbidden_coords or set()
        gx0 = AffineGrid2D.snap9(target_x) // GRID_PITCH
        gy0 = AffineGrid2D.snap9(target_y) // GRID_PITCH
        pt0 = (gx0 * GRID_PITCH, gy0 * GRID_PITCH)
        if self._is_grid_point_free(gx0, gy0) and pt0 not in forb:
            return pt0
        for radius in range(1, 14):
            for dy in range(-radius, radius + 1):
                for dx in range(-radius, radius + 1):
                    if abs(dx) + abs(dy) == radius:
                        cand = ((gx0 + dx) * GRID_PITCH, (gy0 + dy) * GRID_PITCH)
                        if self._is_grid_point_free(gx0 + dx, gy0 + dy) and cand not in forb:
                            return cand
        return pt0

    def synthesize_steiner_junction_plan(
        self,
        net_name: str,
        terminals: List[Tuple[str, str, int, int, int, int]]
    ) -> SteinerJunctionPlan:
        plan = SteinerJunctionPlan()
        k = len(terminals)
        if k < 2:
            return plan
        if k == 2:
            t0, t1 = terminals[0], terminals[1]
            plan.point_to_point_edges.append(((t0[0], t0[1]), (t1[0], t1[1])))
            return plan

        pin_coords: Set[Tuple[int, int]] = {(t[2], t[3]) for t in terminals}

        if net_name == "0" and k >= 3:
            gnd_terms = [t for t in terminals if t[0].upper().startswith("GND") or t[0] == "0"]
            comp_terms = [t for t in terminals if t not in gnd_terms]
            max_comp_y = max((t[3] for t in comp_terms), default=max(t[3] for t in terminals))
            bus_y = AffineGrid2D.snap9(max_comp_y + 18)

            by_col: Dict[int, List[Tuple[str, str, int, int, int, int]]] = {}
            for t in terminals:
                by_col.setdefault(t[2], []).append(t)
            sorted_cols = sorted(by_col.keys())

            if len(sorted_cols) == 1:
                col_x = sorted_cols[0]
                jx, jy = self._find_nearest_free_junction(col_x, bus_y, pin_coords)
                pin_coords.add((jx, jy))
                j_id = f"__J_{net_name}_0__"
                plan.junctions.append((j_id, jx, jy))
                for t in terminals:
                    plan.point_to_point_edges.append(((t[0], t[1]), (j_id, "J")))
                return plan

            col_reps: List[Tuple[str, str]] = []
            for idx, col_x in enumerate(sorted_cols):
                col_t = by_col[col_x]
                is_internal = (0 < idx < len(sorted_cols) - 1)
                if is_internal or len(col_t) >= 2:
                    jx, jy = self._find_nearest_free_junction(col_x, bus_y, pin_coords)
                    pin_coords.add((jx, jy))
                    j_id = f"__J_{net_name}_{idx}__"
                    plan.junctions.append((j_id, jx, jy))
                    for t in col_t:
                        plan.point_to_point_edges.append(((t[0], t[1]), (j_id, "J")))
                    col_reps.append((j_id, "J"))
                else:
                    col_reps.append((col_t[0][0], col_t[0][1]))

            for i in range(len(col_reps) - 1):
                plan.point_to_point_edges.append((col_reps[i], col_reps[i + 1]))
            return plan

        horiz_leads = [t[3] for t in terminals if t[4] != t[2]]
        if horiz_leads:
            y_counts = Counter(horiz_leads)
            med_y = sorted(t[3] for t in terminals)[len(terminals) // 2]
            spine_y = max(y_counts.keys(), key=lambda y_val: (y_counts[y_val], -abs(y_val - med_y)))
        else:
            spine_y = AffineGrid2D.snap9(min(t[3] for t in terminals) - 18)

        by_col_sig: Dict[int, List[Tuple[str, str, int, int, int, int]]] = {}
        for t in terminals:
            eff_x = t[4] if (t[4] != t[2] and not t[0].startswith("__VCONN_")) else t[2]
            by_col_sig.setdefault(eff_x, []).append(t)
        sorted_sig_cols = sorted(by_col_sig.keys())

        if len(sorted_sig_cols) >= 1:
            sig_reps: List[Tuple[str, str]] = []
            for idx, col_x in enumerate(sorted_sig_cols):
                col_t = by_col_sig[col_x]
                is_internal = (0 < idx < len(sorted_sig_cols) - 1)
                horiz_in_col = [t for t in col_t if t[4] != t[2]]
                if len(horiz_in_col) >= 3 and len({t[3] for t in horiz_in_col}) >= 3:
                    col_t_sorted = sorted(col_t, key=lambda item: item[3])
                    j_ids: List[str] = []
                    for r_i in range(len(col_t_sorted) - 1):
                        t_cur = col_t_sorted[r_i]
                        jx, jy = self._find_nearest_free_junction(col_x, t_cur[3], pin_coords)
                        pin_coords.add((jx, jy))
                        j_id = f"__J_{net_name}_{idx}_{r_i}__"
                        plan.junctions.append((j_id, jx, jy))
                        plan.point_to_point_edges.append(((t_cur[0], t_cur[1]), (j_id, "J")))
                        if j_ids:
                            plan.point_to_point_edges.append(((j_ids[-1], "J"), (j_id, "J")))
                        j_ids.append(j_id)
                    last_t = col_t_sorted[-1]
                    plan.point_to_point_edges.append(((j_ids[-1], "J"), (last_t[0], last_t[1])))
                    sig_reps.append((j_ids[0], "J"))
                elif is_internal or len(col_t) >= 2:
                    jx, jy = self._find_nearest_free_junction(col_x, spine_y, pin_coords)
                    pin_coords.add((jx, jy))
                    j_id = f"__J_{net_name}_{idx}__"
                    plan.junctions.append((j_id, jx, jy))
                    for t in col_t:
                        plan.point_to_point_edges.append(((t[0], t[1]), (j_id, "J")))
                    sig_reps.append((j_id, "J"))
                else:
                    sig_reps.append((col_t[0][0], col_t[0][1]))
            for i in range(len(sig_reps) - 1):
                plan.point_to_point_edges.append((sig_reps[i], sig_reps[i + 1]))
            return plan

        xs_sorted = sorted(t[2] for t in terminals)
        med_x = xs_sorted[len(xs_sorted) // 2]
        jx, jy = self._find_nearest_free_junction(med_x, spine_y, pin_coords)
        pin_coords.add((jx, jy))
        j_id = f"__J_{net_name}_0__"
        plan.junctions.append((j_id, jx, jy))
        for t in terminals:
            plan.point_to_point_edges.append(((t[0], t[1]), (j_id, "J")))
        return plan

    def _is_grid_segment_clear(
        self,
        gx0: int,
        gy0: int,
        gx1: int,
        gy1: int,
        net_id: int,
        allow_endpoints: Set[Tuple[int, int]]
    ) -> bool:
        steps = max(abs(gx1 - gx0), abs(gy1 - gy0))
        if steps == 0:
            return True
        for s in range(steps + 1):
            cx = gx0 + (gx1 - gx0) * s // steps
            cy = gy0 + (gy1 - gy0) * s // steps
            if not (0 <= cx < self.grid_w and 0 <= cy < self.grid_h):
                return False
            cell = cy * self.grid_w + cx
            if (cx, cy) not in allow_endpoints:
                if self.obstacle_grid[cell] > 0 or (cx, cy) in self.pin_grid_cells:
                    return False
            owner = self.net_owner_grid[cell]
            if owner > 0 and owner != net_id:
                if s == 0 or s == steps:
                    return False
                if gx0 == gx1:
                    l_own = self.net_owner_grid[cy * self.grid_w + max(0, cx - 1)]
                    r_own = self.net_owner_grid[cy * self.grid_w + min(self.grid_w - 1, cx + 1)]
                    if l_own != owner or r_own != owner:
                        return False
                else:
                    u_own = self.net_owner_grid[max(0, cy - 1) * self.grid_w + cx]
                    d_own = self.net_owner_grid[min(self.grid_h - 1, cy + 1) * self.grid_w + cx]
                    if u_own != owner or d_own != owner:
                        return False
        return True

    def _try_pattern_route(
        self,
        net_id: int,
        sx: int, sy: int,
        slx: int, sly: int,
        tx: int, ty: int,
        tlx: int, tly: int
    ) -> Optional[List[Tuple[int, int]]]:
        allowed = {(sx, sy), (slx, sly), (tx, ty), (tlx, tly)}
        dx1, dy1 = slx - sx, sly - sy
        dx2, dy2 = tlx - tx, tly - ty

        if sy == ty and dy1 == 0 and dy2 == 0 and (dx1 == 0 or (tx - sx) * dx1 > 0) and (dx2 == 0 or (sx - tx) * dx2 > 0):
            if self._is_grid_segment_clear(sx, sy, tx, ty, net_id, allowed):
                return [(sx, sy), (tx, ty)]

        if sx == tx and dx1 == 0 and dx2 == 0 and (dy1 == 0 or (ty - sy) * dy1 > 0) and (dy2 == 0 or (sy - ty) * dy2 > 0):
            if self._is_grid_segment_clear(sx, sy, tx, ty, net_id, allowed):
                return [(sx, sy), (tx, ty)]

        candidates: List[List[Tuple[int, int]]] = []

        c1_x, c1_y = tx, sy
        s1_ok = (dx1 == 0 and dy1 == 0) or (dx1 != 0 and dy1 == 0 and (c1_x - sx) * dx1 > 0)
        t1_ok = (dx2 == 0 and dy2 == 0) or (dx2 == 0 and dy2 != 0 and (c1_y - ty) * dy2 > 0)
        if s1_ok and t1_ok:
            candidates.append([(sx, sy), (c1_x, c1_y), (tx, ty)])

        c2_x, c2_y = sx, ty
        s2_ok = (dx1 == 0 and dy1 == 0) or (dx1 == 0 and dy1 != 0 and (c2_y - sy) * dy1 > 0)
        t2_ok = (dx2 == 0 and dy2 == 0) or (dx2 != 0 and dy2 == 0 and (c2_x - tx) * dx2 > 0)
        if s2_ok and t2_ok:
            candidates.append([(sx, sy), (c2_x, c2_y), (tx, ty)])

        candidates.append([(sx, sy), (slx, sly), (tlx, sly), (tlx, tly), (tx, ty)])
        candidates.append([(sx, sy), (slx, sly), (slx, tly), (tlx, tly), (tx, ty)])

        mid_x = (slx + tlx) // 2
        mid_y = (sly + tly) // 2
        candidates.append([(sx, sy), (slx, sly), (mid_x, sly), (mid_x, tly), (tlx, tly), (tx, ty)])
        candidates.append([(sx, sy), (slx, sly), (slx, mid_y), (tlx, mid_y), (tlx, tly), (tx, ty)])

        for cand in candidates:
            clean = AffineGrid2D.simplify_orthogonal_polyline(cand)
            if len(clean) >= 2 and (dx1 != 0 or dy1 != 0):
                fx, fy = clean[1][0] - sx, clean[1][1] - sy
                if fx * dx1 + fy * dy1 <= 0:
                    continue
            if len(clean) >= 2 and (dx2 != 0 or dy2 != 0):
                lx_v, ly_v = clean[-2][0] - tx, clean[-2][1] - ty
                if lx_v * dx2 + ly_v * dy2 <= 0:
                    continue
            ok = True
            for i in range(len(clean) - 1):
                if not self._is_grid_segment_clear(clean[i][0], clean[i][1], clean[i + 1][0], clean[i + 1][1], net_id, allowed):
                    ok = False
                    break
            if ok:
                return clean
        return None

    def route_pin_pair(
        self,
        net_name: str,
        x1: int,
        y1: int,
        lx1: int,
        ly1: int,
        x2: int,
        y2: int,
        lx2: int,
        ly2: int
    ) -> List[Tuple[int, int]]:
        net_id = self._get_net_id(net_name)
        sx, sy = x1 // GRID_PITCH, y1 // GRID_PITCH
        slx, sly = lx1 // GRID_PITCH, ly1 // GRID_PITCH
        tx, ty = x2 // GRID_PITCH, y2 // GRID_PITCH
        tlx, tly = lx2 // GRID_PITCH, ly2 // GRID_PITCH

        if (sx, sy) == (tx, ty):
            return [(x1, y1), (x2, y2)]

        for gx, gy in ((sx, sy), (slx, sly), (tx, ty), (tlx, tly)):
            if 0 <= gx < self.grid_w and 0 <= gy < self.grid_h:
                self.obstacle_grid[gy * self.grid_w + gx] = 0

        pattern_pts = self._try_pattern_route(net_id, sx, sy, slx, sly, tx, ty, tlx, tly)
        if pattern_pts is not None:
            self._commit_path_to_grid(net_name, net_id, pattern_pts)
            return AffineGrid2D.simplify_orthogonal_polyline([(gx * GRID_PITCH, gy * GRID_PITCH) for gx, gy in pattern_pts])

        path_grid: List[Tuple[int, int]] = []
        if self._dll is not None:
            c_obs = (ctypes.c_int32 * len(self.obstacle_grid))(*self.obstacle_grid)
            c_cong = (ctypes.c_int32 * len(self.congestion_grid))(*self.congestion_grid)
            c_owner = (ctypes.c_int32 * len(self.net_owner_grid))(*self.net_owner_grid)
            max_pts = 512
            out_buf = (ctypes.c_int32 * (max_pts * 2))()
            cnt = self._dll.astar_orthogonal_route(
                self.grid_w, self.grid_h,
                c_obs, c_cong, c_owner,
                net_id,
                sx, sy, slx, sly,
                tx, ty, tlx, tly,
                GLOBAL_CONFIG.routing.step_cost,
                GLOBAL_CONFIG.routing.bend_penalty,
                GLOBAL_CONFIG.routing.cross_penalty,
                out_buf, max_pts
            )
            if cnt >= 2:
                for i in range(cnt):
                    path_grid.append((out_buf[i * 2], out_buf[i * 2 + 1]))

        if not path_grid:
            path_grid = self._python_astar_route(net_id, sx, sy, slx, sly, tx, ty, tlx, tly)

        self._commit_path_to_grid(net_name, net_id, path_grid)
        world_pts = [(gx * GRID_PITCH, gy * GRID_PITCH) for gx, gy in path_grid]
        return AffineGrid2D.simplify_orthogonal_polyline(world_pts)

    def _python_astar_route(
        self,
        net_id: int,
        sx: int, sy: int,
        slx: int, sly: int,
        tx: int, ty: int,
        tlx: int, tly: int
    ) -> List[Tuple[int, int]]:
        step_cost = GLOBAL_CONFIG.routing.step_cost
        bend_penalty = GLOBAL_CONFIG.routing.bend_penalty
        cross_penalty = GLOBAL_CONFIG.routing.cross_penalty

        init_dir = 4
        if slx > sx: init_dir = 0
        elif slx < sx: init_dir = 1
        elif sly > sy: init_dir = 2
        elif sly < sy: init_dir = 3

        start_state = (slx, sly, init_dir)
        dist: Dict[Tuple[int, int, int], int] = {start_state: step_cost}
        parent: Dict[Tuple[int, int, int], Tuple[int, int, int]] = {}

        def h_func(gx: int, gy: int) -> int:
            return (abs(gx - tlx) + abs(gy - tly)) * step_cost

        pq = [(step_cost + h_func(slx, sly), step_cost, slx, sly, init_dir)]
        dirs = ((1, 0, 0), (-1, 0, 1), (0, 1, 2), (0, -1, 3))
        goal_state: Optional[Tuple[int, int, int]] = None

        while pq:
            _, g_val, cx, cy, cdir = heapq.heappop(pq)
            if g_val > dist.get((cx, cy, cdir), 10**9):
                continue
            if cx == tlx and cy == tly:
                goal_state = (cx, cy, cdir)
                break

            for dx, dy, ndir in dirs:
                nx, ny = cx + dx, cy + dy
                if not (0 <= nx < self.grid_w and 0 <= ny < self.grid_h):
                    continue
                cell = ny * self.grid_w + nx
                if self.obstacle_grid[cell] > 0 and not (nx == tlx and ny == tly):
                    continue

                mc = step_cost
                if cdir != 4 and cdir != ndir:
                    mc += bend_penalty
                owner = self.net_owner_grid[cell]
                sig_cost = AffineGrid2D.logistic_penalty(float(self.congestion_grid[cell]))
                if owner > 0 and owner != net_id:
                    mc += cross_penalty + sig_cost
                elif owner == net_id:
                    mc = max(1, step_cost // 2)
                else:
                    mc += sig_cost // 4

                ng = g_val + mc
                nstate = (nx, ny, ndir)
                if ng < dist.get(nstate, 10**9):
                    dist[nstate] = ng
                    parent[nstate] = (cx, cy, cdir)
                    heapq.heappush(pq, (ng + h_func(nx, ny), ng, nx, ny, ndir))

        if goal_state is None:
            mid_x = (slx + tlx) // 2
            return [(sx, sy), (slx, sly), (mid_x, sly), (mid_x, tly), (tlx, tly), (tx, ty)]

        rev: List[Tuple[int, int]] = [(tx, ty)]
        cur: Optional[Tuple[int, int, int]] = goal_state
        while cur is not None:
            rev.append((cur[0], cur[1]))
            cur = parent.get(cur)
        rev.append((sx, sy))
        rev.reverse()
        return rev

    def _commit_path_to_grid(self, net_name: str, net_id: int, grid_pts: List[Tuple[int, int]]) -> None:
        tree_set = self.routed_trees.setdefault(net_name, set())
        for i in range(len(grid_pts) - 1):
            x0, y0 = grid_pts[i]
            x1, y1 = grid_pts[i + 1]
            steps = max(abs(x1 - x0), abs(y1 - y0), 1)
            for s in range(steps + 1):
                cx = x0 + (x1 - x0) * s // steps
                cy = y0 + (y1 - y0) * s // steps
                if 0 <= cx < self.grid_w and 0 <= cy < self.grid_h:
                    cell = cy * self.grid_w + cx
                    self.net_owner_grid[cell] = net_id
                    self.congestion_grid[cell] += 3
                    tree_set.add((cx, cy))

    def audit_schematic_dom(self, xml_text: str) -> ClosedLoopAuditReport:
        sym_count = len(re.findall(r'<Item\s+ID="\d+"\s+Class="CIITSymbolComp">', xml_text))
        obj_sec_m = re.search(r'<CODComposite\s+Class="CODComposite"[^>]*>\s*<Objects>(.*?)</Objects>\s*<ReferencedComponents>', xml_text, re.DOTALL)
        obj_sec = obj_sec_m.group(1) if obj_sec_m else ""
        junc_count = len(re.findall(r'<Item\s+ID="\d+"\s+Class="CODPinComp">\s*<CODPinComp', obj_sec))

        pin_conns = re.findall(r'<CIITPinConnectorComp\b[^>]*>.*?<ConnectList>(.*?)</ConnectList>\s*</CIITPinConnectorComp>', obj_sec, re.DOTALL)
        max_pin_deg = max((len(re.findall(r'<Item\s+ID="\d+"\s+Class="CODPinComp"\s*/>', pc)) for pc in pin_conns), default=0)

        wire_blocks = re.findall(r'<CIITLinkComp\s+[^>]*>\s*<Points>(.*?)</Points>', obj_sec, re.DOTALL)
        total_bends = 0
        total_wl = 0
        xs_all: List[int] = []
        ys_all: List[int] = []

        for wb in wire_blocks:
            pts = [(int(float(mx)), int(float(my))) for mx, my in re.findall(r'<Item\s+X="([^"]+)"\s+Y="([^"]+)"\s*/>', wb)]
            if len(pts) >= 2:
                total_bends += max(0, len(pts) - 2)
                for i in range(len(pts) - 1):
                    total_wl += abs(pts[i + 1][0] - pts[i][0]) + abs(pts[i + 1][1] - pts[i][1])
                for px, py in pts:
                    xs_all.append(px)
                    ys_all.append(py)

        comp_names: Dict[str, str] = {}
        for m in re.finditer(r'<Item\s+CiID="(\d+)"\s+Class="CiComponent">\s*<CiComponent\s+[^>]*LocalName="&amp;ASC([^"]+)"', xml_text):
            comp_names[m.group(1)] = m.group(2)

        port_map: Dict[str, str] = {}
        for m in re.finditer(r'<Item\s+CiID="(\d+)"\s+Class="CiPort">\s*<CiPort\s+Class="CiPort"\s+LocalName="&amp;ASC([^"]+)"\s+Component="(\d+)"', xml_text):
            pid, pname, cid = m.groups()
            port_map[pid] = f"{comp_names.get(cid, cid)}.{pname}"

        net_summary: Dict[str, List[str]] = {}
        for m in re.finditer(r'<Item\s+CiID="(\d+)"\s+Class="CiNode">\s*<CiNode\s+[^>]*LocalName="&amp;ASC([^"]+)"[^>]*>.*?<Ports>(.*?)</Ports>\s*</CiNode>', xml_text, re.DOTALL):
            nid, nname, pbody = m.groups()
            pids = re.findall(r'CiID="(\d+)"', pbody)
            net_summary[nname] = [port_map.get(p, p) for p in pids]

        bbox = (min(xs_all, default=0), min(ys_all, default=0), max(xs_all, default=0), max(ys_all, default=0))
        report = ClosedLoopAuditReport(
            total_symbols=sym_count,
            total_junctions=junc_count,
            total_wires=len(wire_blocks),
            total_bends=total_bends,
            total_wirelength=total_wl,
            max_pin_degree=max_pin_deg,
            bounding_box=bbox,
            net_summary=net_summary
        )
        self.logger.info(
            f"Closed-Loop Audit: Symbols={report.total_symbols}, Junctions={report.total_junctions}, Wires={report.total_wires}, MaxPinDeg={report.max_pin_degree}, Bends={report.total_bends}, WL={report.total_wirelength}u"
        )
        return report
