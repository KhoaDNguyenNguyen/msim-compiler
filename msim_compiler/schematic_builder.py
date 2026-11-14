import os
import re
import uuid
import json
import subprocess
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Dict, List, Tuple, Optional, Set
from .logger import get_logger
from .codec import MS14Codec
from .eda_engine import (
    GRID_PITCH,
    AffineGrid2D,
    NativeEDAEngine,
    NetConnectionSpec,
    PartPlacementSpec
)

DEFAULT_DB_CANDIDATES = [
    os.environ.get("MULTISIM_DB_PATH", ""),
    r"C:\ProgramData\National Instruments\Circuit Design Suite\14.3\database\MSCOMP_S.PRD",
    r"C:\ProgramData\National Instruments\Circuit Design Suite\14.2\database\MSCOMP_S.PRD",
    r"C:\ProgramData\National Instruments\Circuit Design Suite\14.1\database\MSCOMP_S.PRD",
    r"C:\ProgramData\National Instruments\Circuit Design Suite\14.0\database\MSCOMP_S.PRD"
]

@dataclass
class DbPinSpec:
    index: int
    name: str
    number: str
    wcp_x: int
    wcp_y: int
    line_coords: Tuple[int, int, int, int]
    name_pos: Tuple[int, int]
    num_pos: Tuple[int, int]
    pin_group: str = "GRP:A"
    pin_type: str = "A"

@dataclass
class DbGraphicPrimitive:
    g_type: str
    points: List[Tuple[int, int]] = field(default_factory=list)

@dataclass
class DbPartSpec:
    comp_id: int
    family_id: int
    comp_name: str
    family_name: str
    ref_template: str
    pin_count: int
    pinout: List[str]
    symbol_id: int
    drdoc_xml: str
    model_id: str
    spice_template: str
    spice_data: str
    family_group: str = "Basic"
    pin_family: str = "NON"
    manufacturer: str = "Generic"
    function_desc: str = ""
    bbox: Tuple[int, int, int, int] = (45, 45, 72, 63)
    pins: List[DbPinSpec] = field(default_factory=list)
    primitives: List[DbGraphicPrimitive] = field(default_factory=list)
    graphic_xml_items: List[str] = field(default_factory=list)

@dataclass
class PlacedPinInstance:
    pin_name: str
    pin_number: str
    port_ci_id: int
    pin_sym_id: int
    pin_conn_id: int
    cod_pin_id: int
    local_x: int
    local_y: int
    world_x: int
    world_y: int
    lead_x: int
    lead_y: int
    wire_attached: bool = False

@dataclass
class PlacedJunctionInstance:
    junction_key: str
    junc_obj_id: int
    x: int
    y: int
    net_name: str

@dataclass
class PlacedComponentInstance:
    refdes: str
    part: DbPartSpec
    ci_id: int
    sym_id: int
    model_ci_id: Optional[int]
    guid: str
    x: int
    y: int
    rotation: int
    value_label: str
    world_bbox: Tuple[int, int, int, int] = (0, 0, 0, 0)
    pins: Dict[str, PlacedPinInstance] = field(default_factory=dict)

class MultisimDatabaseBridge:
    def __init__(self, db_path: Optional[str] = None):
        self.logger = get_logger("MSIM-DB-BRIDGE")
        self.db_path = self._resolve_db_path(db_path)
        self.pkg_dir = os.path.dirname(os.path.abspath(__file__))
        self.bin_dir = os.path.join(self.pkg_dir, "bin")
        self.exe_path = os.path.join(self.bin_dir, "msim_db_bridge.exe")
        self.password = self._crack_jet3_password()
        self._part_cache: Dict[str, DbPartSpec] = {}

    @staticmethod
    def _resolve_db_path(db_path: Optional[str]) -> str:
        if db_path and os.path.exists(db_path):
            return db_path
        for cand in DEFAULT_DB_CANDIDATES:
            if cand and os.path.exists(cand):
                return cand
        raise FileNotFoundError("Multisim Master Database (MSCOMP_S.PRD) not found.")

    def _crack_jet3_password(self) -> str:
        with open(self.db_path, "rb") as f:
            hdr = f.read(128)
        xor_key = [
            0x86, 0xfb, 0xec, 0x37, 0x5d, 0x44, 0x9c, 0xfa, 0xc6, 0x5e,
            0x28, 0xe6, 0x13, 0xb6, 0x8a, 0x60, 0x54, 0x94, 0x7b, 0x36
        ]
        chars = []
        for i in range(20):
            b = hdr[0x42 + i] ^ xor_key[i]
            if b == 0:
                break
            chars.append(chr(b))
        return "".join(chars)

    def fetch_part(self, part_query: str) -> DbPartSpec:
        if part_query in self._part_cache:
            return self._part_cache[part_query]

        tmp_json = os.path.join(self.bin_dir, f"part_{uuid.uuid4().hex[:8]}.json")
        try:
            subprocess.run([self.exe_path, self.db_path, self.password, part_query, tmp_json], check=True)
            with open(tmp_json, "r", encoding="utf-8-sig") as f:
                data = json.load(f)
        finally:
            if os.path.exists(tmp_json):
                os.remove(tmp_json)

        pin_names = [p.strip() for p in data["pinout"].splitlines() if p.strip()]
        pin_groups = [p.strip() for p in data.get("pin_group", "").splitlines() if p.strip()]
        pin_types = [p.strip() for p in data.get("pin_type", "").splitlines() if p.strip()]

        fam_name = data["family_name"]
        comp_name_up = data["comp_name"].upper()
        sp_tpl = data["spice_template"]
        if "RESISTOR_VIRTUAL" in comp_name_up or (not sp_tpl.strip() and "RESISTOR" in fam_name.upper()):
            sp_tpl = "r%p %t1 %t2 \x231"
        elif "CAPACITOR_VIRTUAL" in comp_name_up or (not sp_tpl.strip() and "CAPACITOR" in fam_name.upper()):
            sp_tpl = "c%p %t1 %t2 \x231"
        elif "INDUCTOR_VIRTUAL" in comp_name_up or (not sp_tpl.strip() and "INDUCTOR" in fam_name.upper()):
            sp_tpl = "l%p %t1 %t2 \x231"

        part = DbPartSpec(
            comp_id=data["comp_id"],
            family_id=data.get("family_id", 29),
            comp_name=data["comp_name"],
            family_name=fam_name,
            ref_template=data["ref_template"],
            pin_count=data["pin_count"],
            pinout=pin_names,
            symbol_id=data["symbol_id"],
            drdoc_xml=data["drdoc_xml"],
            model_id=data["model_id"],
            spice_template=sp_tpl,
            spice_data=data["spice_data"],
            family_group=data.get("family_group", "Basic"),
            pin_family=data.get("pin_family", "NON"),
            manufacturer=data.get("manufacturer", "Generic"),
            function_desc=data.get("function", "")
        )
        self._parse_drdoc_symbol(part, pin_groups, pin_types)
        self._part_cache[part_query] = part
        self.logger.info(
            f"Loaded DB Part '{part.comp_name}' (CompID={part.comp_id}, FamID={part.family_id}, Family='{part.family_name}', Pins={[p.name for p in part.pins]})"
        )
        return part

    def _parse_drdoc_symbol(self, part: DbPartSpec, pin_groups: List[str], pin_types: List[str]) -> None:
        if not part.drdoc_xml:
            return
        root = ET.fromstring(part.drdoc_xml)
        lrs = root.find("LrsArr")
        if lrs is None:
            return

        for layer in lrs:
            ln = layer.attrib.get("LN", "")
            ds = layer.find("DS")
            if ds is None:
                continue

            if ln == "Border":
                for obj in ds:
                    if obj.attrib.get("Type") == "BoundaryBox":
                        pta = obj.find("PtA")
                        if pta is not None and len(pta) >= 2:
                            x0 = int(float(pta[0].attrib.get("X", "45")))
                            y0 = int(float(pta[0].attrib.get("Y", "45")))
                            x1 = int(float(pta[1].attrib.get("X", "72")))
                            y1 = int(float(pta[1].attrib.get("Y", "63")))
                            part.bbox = (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))

            elif ln == "Pins":
                for idx, pin_el in enumerate(ds):
                    if pin_el.attrib.get("Type") != "Pin":
                        continue
                    wcp = pin_el.find("WCP")
                    wx = int(float(wcp.attrib.get("X", "0"))) if wcp is not None else 0
                    wy = int(float(wcp.attrib.get("Y", "0"))) if wcp is not None else 0

                    lx0, ly0, lx1, ly1 = wx, wy, wx, wy
                    pd = pin_el.find("PD")
                    if pd is not None:
                        pta = pd.find("PtA")
                        if pta is not None and len(pta) >= 2:
                            lx0 = int(float(pta[0].attrib.get("X", str(wx))))
                            ly0 = int(float(pta[0].attrib.get("Y", str(wy))))
                            lx1 = int(float(pta[1].attrib.get("X", str(wx))))
                            ly1 = int(float(pta[1].attrib.get("Y", str(wy))))

                    pnat = pin_el.find("PNaT")
                    nx, ny = wx, wy
                    if pnat is not None:
                        pta = pnat.find("PtA")
                        if pta is not None and len(pta) >= 1:
                            nx = int(float(pta[0].attrib.get("X", str(wx))))
                            ny = int(float(pta[0].attrib.get("Y", str(wy))))

                    pnut = pin_el.find("PNuT")
                    numx, numy = wx, wy
                    if pnut is not None:
                        pta = pnut.find("PtA")
                        if pta is not None and len(pta) >= 1:
                            numx = int(float(pta[0].attrib.get("X", str(wx))))
                            numy = int(float(pta[0].attrib.get("Y", str(wy))))

                    p_name = part.pinout[idx] if idx < len(part.pinout) else str(idx + 1)
                    p_grp = pin_groups[idx] if idx < len(pin_groups) else "GRP:A"
                    p_typ = pin_types[idx].split(":")[0] if idx < len(pin_types) and ":" in pin_types[idx] else "A"

                    part.pins.append(DbPinSpec(
                        index=idx + 1,
                        name=p_name,
                        number=str(idx + 1),
                        wcp_x=wx,
                        wcp_y=wy,
                        line_coords=(lx0, ly0, lx1, ly1),
                        name_pos=(nx, ny),
                        num_pos=(numx, numy),
                        pin_group=p_grp,
                        pin_type=p_typ
                    ))

            else:
                for g_obj in ds:
                    g_type = g_obj.attrib.get("Type", "")
                    pta = g_obj.find("PtA")
                    pts: List[Tuple[int, int]] = []
                    if pta is not None:
                        for pt in pta:
                            px = int(float(pt.attrib.get("X", "0")))
                            py = int(float(pt.attrib.get("Y", "0")))
                            pts.append((px, py))
                    if g_type and pts:
                        part.primitives.append(DbGraphicPrimitive(g_type=g_type, points=pts))

        part.graphic_xml_items = self.render_rotated_primitives(part, rotation=0)

    @staticmethod
    def _pivot_of(part: DbPartSpec) -> Tuple[int, int]:
        bx0, by0, bx1, by1 = part.bbox
        return (
            AffineGrid2D.snap9((bx0 + bx1) / 2.0),
            AffineGrid2D.snap9((by0 + by1) / 2.0)
        )

    @classmethod
    def render_rotated_primitives(cls, part: DbPartSpec, rotation: int = 0) -> List[str]:
        cx, cy = cls._pivot_of(part)
        items: List[str] = []
        for prim in part.primitives:
            pts = [AffineGrid2D.rotate_point(px, py, cx, cy, rotation) for px, py in prim.points]
            g_type = prim.g_type
            if g_type == "Line" and len(pts) >= 2:
                items.append(
                    f'<CODLineComp Class="CODLineComp" instID="0" Stretchable="1" Moveable="1" LabelSelectable="1" Selectable="1" LockedPenColor="0" LockedBrushColor="0" Layer="511" ArrowAtSource="0" ArrowAtTarget="0" pt0X="{pts[0][0]}" pt0Y="{pts[0][1]}" pt1X="{pts[1][0]}" pt1Y="{pts[1][1]}" PenColor="0" FgColor="0" BgColor="16777215" Pen-Style="0" Pen-Width="1" Pen-Color="16728576" Hidden="0"/>'
                )
            elif g_type in ("MultiLine", "Polygon") and len(pts) >= 2:
                tag_cls = "CODPolygonComp" if g_type == "Polygon" else "CODMultiLineComp"
                pts_xml = "\n".join(f'<Item X="{px}" Y="{py}"/>' for px, py in pts)
                items.append(
                    f'<{tag_cls} Class="{tag_cls}" instID="0" Stretchable="1" Moveable="1" LabelSelectable="1" Selectable="1" LockedPenColor="0" LockedBrushColor="0" Layer="511" ArrowAtSource="0" ArrowAtTarget="0" PenColor="0" FgColor="0" BgColor="16777215" Pen-Style="0" Pen-Width="1" Pen-Color="16728576" Transformer-M00="1" Transformer-M01="0" Transformer-M10="0" Transformer-M11="1" Transformer-M20="0" Transformer-M21="0" Hidden="0">\n<Points>\n{pts_xml}\n</Points>\n</{tag_cls}>'
                )
            elif g_type == "HalfEllipseArc" and len(pts) >= 2:
                x0, y0 = pts[0]
                x1, y1 = pts[1]
                dx, dy = x1 - x0, y1 - y0
                nx_off = -4 if dy != 0 else 0
                ny_off = -4 if dx != 0 else 0
                items.append(
                    f'<CODMultiLineComp Class="CODMultiLineComp" instID="0" Stretchable="1" Moveable="1" LabelSelectable="1" Selectable="1" LockedPenColor="0" LockedBrushColor="0" Layer="511" ArrowAtSource="0" ArrowAtTarget="0" PenColor="0" FgColor="0" BgColor="16777215" Pen-Style="0" Pen-Width="1" Pen-Color="16728576" Transformer-M00="1" Transformer-M01="0" Transformer-M10="0" Transformer-M11="1" Transformer-M20="0" Transformer-M21="0" Hidden="0">\n'
                    f'<Points>\n<Item X="{x0}" Y="{y0}"/>\n<Item X="{x0 + dx // 4 + nx_off}" Y="{y0 + dy // 4 + ny_off}"/>\n<Item X="{x0 + (3 * dx) // 4 + nx_off}" Y="{y0 + (3 * dy) // 4 + ny_off}"/>\n<Item X="{x1}" Y="{y1}"/>\n</Points>\n</CODMultiLineComp>'
                )
            elif g_type == "Circle" and len(pts) >= 2:
                items.append(
                    f'<CODEllipseComp Class="CODEllipseComp" instID="0" Stretchable="1" Moveable="1" LabelSelectable="1" Selectable="1" LockedPenColor="0" LockedBrushColor="0" Layer="511" pt0X="{pts[0][0]}" pt0Y="{pts[0][1]}" pt1X="{pts[1][0]}" pt1Y="{pts[1][1]}" PenColor="0" FgColor="0" BgColor="16777215" Brush-Style="1" Brush-Color="0" Brush-Hatch="0" Pen-Style="0" Pen-Width="1" Pen-Color="16728576" Transformer-M00="1" Transformer-M01="0" Transformer-M10="0" Transformer-M11="1" Transformer-M20="0" Transformer-M21="0" Hidden="0"/>'
                )
        return items

class MultisimSchematicBuilder:
    def __init__(
        self,
        template_ms14_path: Optional[str] = None,
        db_bridge: Optional[MultisimDatabaseBridge] = None,
        eda_engine: Optional[NativeEDAEngine] = None
    ):
        self.logger = get_logger("SCHEM-BUILDER")
        self.codec = MS14Codec()
        self.db = db_bridge or MultisimDatabaseBridge()
        self.eda = eda_engine or NativeEDAEngine()
        tpl_path = self._resolve_template_path(template_ms14_path)
        self.xml_text = self.codec.decode_to_string(tpl_path).replace("\r\n", "\n")
        self._next_id = self._scan_max_id() + 10000
        self.circuit_id = self._find_circuit_id()
        self.placed_components: Dict[str, PlacedComponentInstance] = {}
        self.placed_junctions: Dict[str, PlacedJunctionInstance] = {}
        self.net_nodes: Dict[str, Tuple[int, int]] = {}
        self._discover_existing_nets()

    @staticmethod
    def _resolve_template_path(template_ms14_path: Optional[str]) -> str:
        if template_ms14_path and os.path.exists(template_ms14_path):
            return template_ms14_path
        pkg_dir = os.path.dirname(os.path.abspath(__file__))
        testbench_tpl = os.path.join(pkg_dir, "templates", "instrument_testbench.ms14")
        if os.path.exists(testbench_tpl):
            return testbench_tpl
        return os.path.join(pkg_dir, "templates", "base_sallen_key.ms14")

    @classmethod
    def from_testbench(cls) -> "MultisimSchematicBuilder":
        pkg_dir = os.path.dirname(os.path.abspath(__file__))
        tb_path = os.path.join(pkg_dir, "templates", "instrument_testbench.ms14")
        if os.path.exists(tb_path):
            return cls(template_ms14_path=tb_path)
        builder = cls(template_ms14_path=os.path.join(pkg_dir, "templates", "base_sallen_key.ms14"))
        return builder.prune_to_instruments_only()

    @classmethod
    def from_empty_sheet(cls) -> "MultisimSchematicBuilder":
        pkg_dir = os.path.dirname(os.path.abspath(__file__))
        empty_path = os.path.join(pkg_dir, "templates", "empty_sheet.ms14")
        if os.path.exists(empty_path):
            return cls(template_ms14_path=empty_path)
        builder = cls(template_ms14_path=os.path.join(pkg_dir, "templates", "base_sallen_key.ms14"))
        return builder.clear_existing_circuit()

    def _scan_max_id(self) -> int:
        ids = [int(m.group(1)) for m in re.finditer(r'\b(?:CiID|ID)="(\d+)"', self.xml_text)]
        return max(ids) if ids else 500000000

    def _alloc_id(self) -> int:
        self._next_id += 8
        return self._next_id

    def _find_circuit_id(self) -> int:
        m = re.search(r'<Item\s+CiID="(\d+)"\s+Class="CiCircuit">', self.xml_text)
        return int(m.group(1)) if m else 483610824

    def _ensure_node_text_exists(self, nt_id: int) -> None:
        if f'<Item ID="{nt_id}" Class="CODNodeTextComp">' in self.xml_text:
            return
        nt_xml = (
            f'<Item ID="{nt_id}" Class="CODNodeTextComp">\n'
            f'<CODNodeTextComp Class="CODNodeTextComp" instID="0" Stretchable="0" Moveable="1" LabelSelectable="1" Selectable="1" LockedPenColor="0" LockedBrushColor="0" Layer="511" Output="" PenColor="255" FgColor="0" BgColor="16777215" Font-Height="-13" Font-Width="0" Font-Escapement="0" Font-Orientation="0" Font-Weight="700" Font-Italic="0" Font-Underline="0" Font-StrikeOut="0" Font-CharSet="0" Font-OutPrecision="3" Font-ClipPrecision="2" Font-Quality="1" Font-PitchAndFamily="34" Font-FaceName="&amp;ASCArial" Transformer-M00="1" Transformer-M01="0" Transformer-M10="0" Transformer-M11="1" Transformer-M20="200" Transformer-M21="200" Hidden="0" HorizontalAlign="0" VerticalAlign="0" KeepCurrentFont="0">\n'
            f'<Links>\n</Links>\n</CODNodeTextComp>\n</Item>'
        )
        self.xml_text = self.xml_text.replace(
            "</Objects>\n<ReferencedComponents>",
            nt_xml + "\n</Objects>\n<ReferencedComponents>\n" + f'<Item ID="{nt_id}" Class="CODNodeTextComp"/>',
            1
        )

    def _discover_existing_nets(self) -> None:
        for m in re.finditer(r'<Item\s+CiID="(\d+)"\s+Class="CiNode">\s*<CiNode\s+[^>]*LocalName="&amp;ASC([^"]+)"', self.xml_text):
            n_id = int(m.group(1))
            n_name = m.group(2)
            link_m = re.search(rf'<CIITLinkComp\s+[^>]*Node="{n_id}"\s+NodeText="(\d+)"', self.xml_text)
            if link_m and f'<Item ID="{link_m.group(1)}" Class="CODNodeTextComp">' in self.xml_text:
                nt_id = int(link_m.group(1))
            else:
                nt_id = self._alloc_id()
                self._ensure_node_text_exists(nt_id)
            self.net_nodes[n_name] = (n_id, nt_id)

    @staticmethod
    def _extract_top_level_items(container_text: str, id_attr: str = "ID") -> List[Tuple[str, str, str]]:
        results: List[Tuple[str, str, str]] = []
        pattern = re.compile(rf'<Item\s+{id_attr}="(\d+)"\s+Class="([^"]+)">')
        pos = 0
        while True:
            m = pattern.search(container_text, pos)
            if not m:
                break
            item_id, item_cls = m.group(1), m.group(2)
            close_marker = f"</{item_cls}>\n</Item>"
            end_idx = container_text.find(close_marker, m.end())
            if end_idx < 0:
                pos = m.end()
                continue
            end_idx += len(close_marker)
            results.append((item_id, item_cls, container_text[m.start():end_idx]))
            pos = end_idx
        return results

    @staticmethod
    def _shift_xml_coordinates(block_text: str, dx: int, dy: int, icls: str = "") -> str:
        def fmt_num(v: float) -> str:
            return str(int(round(v))) if abs(v - round(v)) < 1e-4 else f"{v:g}"

        if icls in ("CIITSymbolComp", "CVirtualConnector", "CODNodeTextComp"):
            def shift_header_tag(m):
                tag_str = m.group(0)
                tag_str = re.sub(
                    r'Transformer-M20="([^"]+)"',
                    lambda am: f'Transformer-M20="{fmt_num(float(am.group(1)) + dx)}"',
                    tag_str, count=1
                )
                tag_str = re.sub(
                    r'Transformer-M21="([^"]+)"',
                    lambda am: f'Transformer-M21="{fmt_num(float(am.group(1)) + dy)}"',
                    tag_str, count=1
                )
                return tag_str
            return re.sub(rf'<{icls}\b[^>]*>', shift_header_tag, block_text, count=1)

        if icls == "CODPinComp":
            if 'Transformer-M20="' in block_text:
                block_text = re.sub(
                    r'Transformer-M20="([^"]+)"',
                    lambda am: f'Transformer-M20="{fmt_num(float(am.group(1)) + dx)}"',
                    block_text, count=1
                )
                block_text = re.sub(
                    r'Transformer-M21="([^"]+)"',
                    lambda am: f'Transformer-M21="{fmt_num(float(am.group(1)) + dy)}"',
                    block_text, count=1
                )
            else:
                block_text = re.sub(
                    r'CenterX="([^"]+)"',
                    lambda am: f'CenterX="{fmt_num(float(am.group(1)) + dx)}"',
                    block_text, count=1
                )
                block_text = re.sub(
                    r'CenterY="([^"]+)"',
                    lambda am: f'CenterY="{fmt_num(float(am.group(1)) + dy)}"',
                    block_text, count=1
                )
            return block_text

        def shift_xy(m):
            attr_name, val_str = m.group(1), m.group(2)
            delta = dx if attr_name == "X" else dy
            return f'{attr_name}="{fmt_num(float(val_str) + delta)}"'

        return re.sub(r'\b(X|Y)="([^"]+)"', shift_xy, block_text)

    def prune_to_instruments_only(self) -> "MultisimSchematicBuilder":
        self.xml_text = self.xml_text.replace("\r\n", "\n")
        self.eda.reset_grid()

        if "495898448" not in self.xml_text and "495902816" not in self.xml_text and set(self.net_nodes.keys()) == {"0", "VIN", "VOUT"}:
            self.net_nodes.clear()
            self.placed_components.clear()
            self.placed_junctions.clear()
            self._discover_existing_nets()
            return self

        obj_m = re.search(
            r'(<CODComposite\s+Class="CODComposite"[^>]*>\s*<Objects>)(.*?)(</Objects>\s*<ReferencedComponents>)(.*?)(</ReferencedComponents>)',
            self.xml_text,
            re.DOTALL
        )
        assert obj_m is not None
        obj_prefix, obj_body, mid_tag, ref_body, ref_suffix = obj_m.groups()

        el_m = re.search(
            r'(</HiddenTitleBlockStorage>\s*<Elements>)(.*?)(</Elements>\s*</CIITDiagram>)',
            self.xml_text,
            re.DOTALL
        )
        assert el_m is not None
        el_prefix, el_body, el_suffix = el_m.groups()

        el_items = self._extract_top_level_items(el_body, id_attr="CiID")
        obj_items = self._extract_top_level_items(obj_body, id_attr="ID")

        comp_name_by_ciid: Dict[str, str] = {}
        sym_to_ciid: Dict[str, str] = {}
        for cid, ccls, cblk in el_items:
            if ccls == "CiComponent":
                nm = re.search(r'LocalName="&amp;ASC([^"]*)"', cblk)
                sm = re.search(r'SymCompID="(\d+)"', cblk)
                lname = nm.group(1) if nm else ""
                comp_name_by_ciid[cid] = lname
                if sm:
                    sym_to_ciid[sm.group(1)] = cid

        inst_sym_ids: Dict[str, str] = {}
        old_circuit_sym_ids: Set[str] = set()
        sym_pins_map: Dict[str, Set[str]] = {}
        vconn_pins_map: Dict[str, Set[str]] = {}
        junc_pins_map: Dict[str, Set[str]] = {}
        wire_endpoints: Dict[str, Tuple[str, str, str]] = {}

        for iid, icls, iblk in obj_items:
            pins_in_blk = set(re.findall(r'<Item\s+ID="(\d+)"\s+Class="CODPinComp"\s*/>', iblk))
            if icls == "CIITSymbolComp":
                sym_pins_map[iid] = pins_in_blk
                cm = re.search(r'CiComponent="(\d+)"', iblk)
                cid = cm.group(1) if cm else sym_to_ciid.get(iid, "")
                cname = comp_name_by_ciid.get(cid, "")
                if cname.upper().startswith(("XFG", "XBP", "XSC")):
                    inst_sym_ids[cname.upper()] = iid
                else:
                    old_circuit_sym_ids.add(iid)
            elif icls == "CVirtualConnector":
                vconn_pins_map[iid] = pins_in_blk
            elif icls == "CODPinComp":
                junc_pins_map[iid] = pins_in_blk
            elif icls == "CIITLinkComp":
                c1_m = re.search(r'Connect1="(\d+)"', iblk)
                c2_m = re.search(r'Connect2="(\d+)"', iblk)
                nt_m = re.search(r'NodeText="(\d+)"', iblk)
                if c1_m and c2_m and nt_m:
                    wire_endpoints[iid] = (c1_m.group(1), c2_m.group(1), nt_m.group(1))

        forbidden_pins: Set[str] = set()
        for osid in old_circuit_sym_ids:
            forbidden_pins.update(sym_pins_map.get(osid, set()))

        def trace_island(seed_sym_ids: Set[str]) -> Tuple[Set[str], Set[str], Set[str], Set[str], Set[str], Set[str]]:
            visited_syms = set(seed_sym_ids)
            visited_vconns: Set[str] = set()
            visited_juncs: Set[str] = set()
            visited_wires: Set[str] = set()
            visited_nt: Set[str] = set()
            visited_pins: Set[str] = set()
            for s in visited_syms:
                visited_pins.update(sym_pins_map.get(s, set()))

            changed = True
            while changed:
                changed = False
                for wid, (c1, c2, ntid) in wire_endpoints.items():
                    if wid == "477621648" or c1 in forbidden_pins or c2 in forbidden_pins:
                        continue
                    if wid not in visited_wires and (c1 in visited_pins or c2 in visited_pins):
                        visited_wires.add(wid)
                        visited_pins.add(c1)
                        visited_pins.add(c2)
                        visited_nt.add(ntid)
                        changed = True
                for sid, pset in sym_pins_map.items():
                    if sid in old_circuit_sym_ids:
                        continue
                    if sid not in visited_syms and (pset & visited_pins):
                        visited_syms.add(sid)
                        visited_pins.update(pset)
                        changed = True
                for vid, pset in vconn_pins_map.items():
                    if vid not in visited_vconns and (pset & visited_pins):
                        visited_vconns.add(vid)
                        visited_pins.update(pset)
                        changed = True
                for jid, pset in junc_pins_map.items():
                    if jid not in visited_juncs and (pset & visited_pins):
                        visited_juncs.add(jid)
                        visited_pins.update(pset)
                        changed = True
            return visited_syms, visited_vconns, visited_juncs, visited_wires, visited_nt, visited_pins

        kept_syms, kept_vconns, kept_juncs, kept_wires, kept_nts, kept_pins = trace_island(set(inst_sym_ids.values()))

        xbp1_island = trace_island({inst_sym_ids["XBP1"]}) if "XBP1" in inst_sym_ids else (set(), set(), set(), set(), set(), set())
        xfg1_island = trace_island({inst_sym_ids["XFG1"]}) if "XFG1" in inst_sym_ids else (set(), set(), set(), set(), set(), set())
        xsc2_island = trace_island({inst_sym_ids["XSC2"]}) if "XSC2" in inst_sym_ids else (set(), set(), set(), set(), set(), set())
        xsc3_island = trace_island({inst_sym_ids["XSC3"]}) if "XSC3" in inst_sym_ids else (set(), set(), set(), set(), set(), set())
        xsc4_island = trace_island({inst_sym_ids["XSC4"]}) if "XSC4" in inst_sym_ids else (set(), set(), set(), set(), set(), set())
        xsc1_island = trace_island({inst_sym_ids["XSC1"]}) if "XSC1" in inst_sym_ids else (set(), set(), set(), set(), set(), set())

        def in_island(iid: str, isl_tuple) -> bool:
            return any(iid in s for s in isl_tuple)

        def get_shift_for_item(iid: str) -> Tuple[int, int]:
            if in_island(iid, xbp1_island):
                return (-72, -117)
            if in_island(iid, xfg1_island):
                return (-72, -63)
            if in_island(iid, xsc2_island):
                return (-99, 0)
            if in_island(iid, xsc3_island):
                return (-45, -72)
            if in_island(iid, xsc4_island):
                return (-45, -135)
            if in_island(iid, xsc1_island):
                return (162, -369)
            return (0, 0)

        vin_group = {"490443664", "490441000", "490440208", "490444096", "490442944", "490441864"}
        vout_group = {"490440424", "490440784", "490441936", "490440136", "490442872", "490442080", "490439704"}
        gnd_group = {"490440496", "490441432", "490439776", "490442800", "490443304", "490441576", "490443160"}

        def remap_node_id(nid: str) -> Optional[str]:
            if nid in vin_group:
                return "490443664"
            if nid in vout_group:
                return "490440424"
            if nid in gnd_group:
                return "490440496"
            return None

        new_obj_blocks: List[str] = []
        for iid, icls, iblk in obj_items:
            keep = (
                (icls == "CIITSymbolComp" and iid in kept_syms) or
                (icls == "CVirtualConnector" and iid in kept_vconns) or
                (icls == "CODPinComp" and iid in kept_juncs) or
                (icls == "CIITLinkComp" and iid in kept_wires) or
                (icls == "CODNodeTextComp" and iid in kept_nts)
            )
            if keep:
                if icls == "CIITLinkComp":
                    iblk = re.sub(
                        r'Node="(\d+)"',
                        lambda m: f'Node="{remap_node_id(m.group(1)) or m.group(1)}"',
                        iblk, count=1
                    )
                dx, dy = get_shift_for_item(iid)
                if dx != 0 or dy != 0:
                    iblk = self._shift_xml_coordinates(iblk, dx, dy, icls)
                new_obj_blocks.append(iblk)

        new_ref_blocks: List[str] = []
        for rm in re.finditer(r'<Item\s+ID="(\d+)"\s+Class="CODPinComp">.*?</CODPinComp>\s*</Item>|<Item\s+ID="(\d+)"\s+Class="([^"]+)"\s*/>', ref_body, re.DOTALL):
            if rm.group(1):
                pid = rm.group(1)
                if pid in kept_pins:
                    rblk = rm.group(0)
                    dx, dy = get_shift_for_item(pid)
                    if dx != 0 or dy != 0:
                        rblk = self._shift_xml_coordinates(rblk, dx, dy, "CODPinComp")
                    new_ref_blocks.append(rblk)
            else:
                rid, rcls = rm.group(2), rm.group(3)
                if (rcls == "CIITLinkComp" and rid in kept_wires) or (rcls == "CODNodeTextComp" and rid in kept_nts):
                    new_ref_blocks.append(rm.group(0))

        self.xml_text = (
            self.xml_text[:obj_m.start()] +
            obj_prefix + "\n" + "\n".join(new_obj_blocks) + "\n" +
            mid_tag + "\n" + "\n".join(new_ref_blocks) + "\n" + ref_suffix +
            self.xml_text[obj_m.end():]
        )

        kept_ci_comp_ids: Set[str] = set()
        for iid, icls, iblk in obj_items:
            if icls == "CIITSymbolComp" and iid in kept_syms:
                cm = re.search(r'CiComponent="(\d+)"', iblk)
                if cm:
                    kept_ci_comp_ids.add(cm.group(1))
        for cid, cname in comp_name_by_ciid.items():
            if cname in ("VIN", "VOUT", "0"):
                kept_ci_comp_ids.add(cid)

        kept_port_ids: Set[str] = set()
        node_to_ports: Dict[str, List[str]] = {"490443664": [], "490440424": [], "490440496": []}

        for cid, ccls, cblk in el_items:
            if ccls == "CiPort":
                cm = re.search(r'Component="(\d+)"', cblk)
                if cm and cm.group(1) in kept_ci_comp_ids:
                    kept_port_ids.add(cid)
                    n_refs = re.findall(r'<Nodes>\s*(.*?)\s*</Nodes>', cblk, re.DOTALL)
                    if n_refs:
                        for orig_nid in re.findall(r'CiID="(\d+)"', n_refs[0]):
                            canon_nid = remap_node_id(orig_nid)
                            if canon_nid and cid not in node_to_ports[canon_nid]:
                                node_to_ports[canon_nid].append(cid)

        kept_node_ids = {"490443664", "490440424", "490440496"}

        new_el_blocks: List[str] = []
        for cid, ccls, cblk in el_items:
            if ccls == "CiComponent" and cid in kept_ci_comp_ids:
                new_el_blocks.append(cblk)
            elif ccls == "CiPort" and cid in kept_port_ids:
                def rewrite_port_nodes(m_nodes):
                    orig_nids = re.findall(r'CiID="(\d+)"', m_nodes.group(2))
                    canon_nids = []
                    for onid in orig_nids:
                        cnid = remap_node_id(onid)
                        if cnid and cnid not in canon_nids:
                            canon_nids.append(cnid)
                    if not canon_nids:
                        return "<Nodes/>"
                    items_str = "\n".join(f'<Item CiID="{cn}"/>' for cn in canon_nids)
                    return f"<Nodes>\n{items_str}\n</Nodes>"
                cblk = re.sub(r'(<Nodes>)(.*?)(</Nodes>)', rewrite_port_nodes, cblk, count=1, flags=re.DOTALL)
                new_el_blocks.append(cblk)
            elif ccls == "CiNode" and cid in kept_node_ids:
                if cid == "490443664":
                    cblk = re.sub(r'LocalName="&amp;ASC[^"]*"', 'LocalName="&amp;ASCVIN"', cblk, count=1)
                elif cid == "490440424":
                    cblk = re.sub(r'LocalName="&amp;ASC[^"]*"', 'LocalName="&amp;ASCVOUT"', cblk, count=1)
                elif cid == "490440496":
                    cblk = re.sub(r'LocalName="&amp;ASC[^"]*"', 'LocalName="&amp;ASC0"', cblk, count=1)
                p_items = "\n".join(f'<Item CiID="{pid}"/>' for pid in node_to_ports[cid])
                cblk = re.sub(
                    r'(<Ports>)(.*?)(</Ports>)',
                    lambda m: f"<Ports>\n{p_items}\n</Ports>",
                    cblk, count=1, flags=re.DOTALL
                )
                new_el_blocks.append(cblk)
            elif ccls == "CiCircuit":
                cblk = re.sub(
                    r'(<Ports>)(.*?)(</Ports>)',
                    lambda m: m.group(1) + "\n" + "\n".join(f'<Item CiID="{p}"/>' for p in sorted(kept_port_ids)) + "\n" + m.group(3),
                    cblk, flags=re.DOTALL
                )
                cblk = re.sub(
                    r'(<Nodes>)(.*?)(</Nodes>)',
                    lambda m: m.group(1) + "\n" + "\n".join(f'<Item CiID="{n}"/>' for n in sorted(kept_node_ids)) + "\n" + m.group(3),
                    cblk, flags=re.DOTALL
                )
                cblk = re.sub(
                    r'(<Components>)(.*?)(</Components>)',
                    lambda m: m.group(1) + "\n" + "\n".join(f'<Item CiID="{c}"/>' for c in sorted(kept_ci_comp_ids)) + "\n" + m.group(3),
                    cblk, flags=re.DOTALL
                )
                cblk = re.sub(
                    r'(<Models>)(.*?)(</Models>)',
                    '<Models>\n<Item CiID="192837136"/>\n<Item CiID="192836912"/>\n</Models>',
                    cblk, flags=re.DOTALL
                )
                new_el_blocks.append(cblk)
            elif ccls == "CiModel" and cid in ("192837136", "192836912"):
                new_el_blocks.append(cblk)

        el_m2 = re.search(r'(</HiddenTitleBlockStorage>\s*<Elements>)(.*?)(</Elements>\s*</CIITDiagram>)', self.xml_text, re.DOTALL)
        self.xml_text = (
            self.xml_text[:el_m2.start()] +
            el_prefix + "\n" + "\n".join(new_el_blocks) + "\n" + el_suffix +
            self.xml_text[el_m2.end():]
        )

        for old_key in ("R1", "R2", "C1", "C2", "U2"):
            self.xml_text = re.sub(
                rf'<CIRToInfoMapItem\s+CIRKey="&amp;ASC{old_key}">.*?</CIRToInfoMapItem>\s*',
                "",
                self.xml_text,
                flags=re.DOTALL
            )
        old_comp_guids = {
            "{024AF9A4-979D-448E-9890-E52313D6D881}",
            "{D743AEAB-A434-46FD-8A3D-FF7034F491D9}",
            "{B40BAD44-5D95-4A4E-ADC6-364FB38CABFF}",
            "{59F75A1B-E97C-451E-B89D-A19D4E4EE9B0}",
            "{8919D084-0C99-4B42-B52E-A50F8A91BABF}",
            "{C189DBBE-A812-47B1-9745-F161DD9D358E}",
            "{8FDFC12A-C5B6-4B15-95F3-65F8D21E9FB0}"
        }
        for oguid in old_comp_guids:
            self.xml_text = re.sub(
                rf'<CSCH_CAPVirtualComponentPin[^>]*GUID="{re.escape(oguid)}"[^>]*/>\s*',
                "",
                self.xml_text
            )
        self.xml_text = re.sub(r'<CSchDesignNetLabel[^>]*LastNetName="&amp;ASC(?:VCC|VEE|1|2)"[^>]*/>\s*', "", self.xml_text)
        self.xml_text = re.sub(r'<Element\s+Key="&amp;ASC(?:VCC|VEE|1|2)">.*?</Element>\s*</MapData>\s*</CDataElementMap>\s*</CDataElement>\s*</Element>\s*', "", self.xml_text, flags=re.DOTALL)

        self.net_nodes.clear()
        self.placed_components.clear()
        self.placed_junctions.clear()
        self._discover_existing_nets()
        for n_name, (n_id, _) in list(self.net_nodes.items()):
            nt_id = self._alloc_id()
            self._ensure_node_text_exists(nt_id)
            self.net_nodes[n_name] = (n_id, nt_id)

        self.logger.info(f"Initialized Clean Instrument Testbench (Nets={list(self.net_nodes.keys())})")
        return self

    def clear_existing_circuit(self) -> "MultisimSchematicBuilder":
        self.eda.reset_grid()
        self.xml_text = re.sub(
            r'(<CODComposite\s+Class="CODComposite"[^>]*>\s*<Objects>).*?(</Objects>\s*<ReferencedComponents>).*?(</ReferencedComponents>)',
            r'\1\n\2\n\3',
            self.xml_text,
            count=1,
            flags=re.DOTALL
        )
        self.xml_text = re.sub(
            r'(</HiddenTitleBlockStorage>\s*<Elements>).*?(</Elements>\s*</CIITDiagram>)',
            lambda m: (
                m.group(1) + "\n" +
                f'<Item CiID="{self.circuit_id}" Class="CiCircuit">\n'
                f'<CiCircuit Class="CiCircuit" LocalName="">\n'
                f'<Attributes/>\n<Models>\n</Models>\n<Nodes>\n</Nodes>\n<Probes/>\n<ProbeExts/>\n<CompAddRefs/>\n<Components>\n</Components>\n'
                f'</CiCircuit>\n</Item>\n' +
                m.group(2)
            ),
            self.xml_text,
            count=1,
            flags=re.DOTALL
        )
        self.xml_text = re.sub(
            r'<InstrumentsData>.*?</InstrumentsData>',
            '<InstrumentsData/>',
            self.xml_text,
            count=1,
            flags=re.DOTALL
        )
        self.net_nodes.clear()
        self.placed_components.clear()
        self.placed_junctions.clear()
        self.logger.info("Initialized Empty Sheet Canvas")
        return self

    def _register_component_in_refdes_mgr(self, inst: PlacedComponentInstance) -> None:
        m_rd = re.match(r'^([A-Za-z]+)(\d+.*)?$', inst.refdes)
        prefix = m_rd.group(1) if m_rd else "U"
        num_m = re.match(r'^(\d+)', m_rd.group(2) or "1") if m_rd else None
        ir_num = int(num_m.group(1)) if num_m else 1

        refdes_str = f"!0!0!0{inst.refdes}!0D:\\com_sys_lab\\TUAN_2\\multisim\\figure_10_fix.ms14!01!0figure_10_fix!0"
        cir_item_xml = (
            f'<CIRToInfoMapItem CIRKey="&amp;ASC{inst.refdes}">\n'
            f'<RefDesInfo Class="CIITHierRefDesInfo" IRPrefix="&amp;ASC{prefix}" IRNumber="{ir_num}" Locked="0" IRSection="" IRSectionID="0" SpiceTemplate="">\n'
            f'<RefDesInfoData Class="CIITHierRefDes" RefDesBufSize="260" RefDesStrSize="{len(refdes_str)}" RefDesCount="2" RefDes="&amp;ASC{refdes_str}" SectionBufSize="0" SectionStrSize="0" Section="&amp;ASC(null)" Prefix="&amp;ASC{prefix}" Number="{ir_num}">\n'
            f'<RefDesData/>\n'
            f'</RefDesInfoData>\n'
            f'<PinOrderCIR/>\n<PinOrderIR/>\n<PinNumbersCIR/>\n<PinNumbersIR/>\n<SharedPins/>\n'
            f'</RefDesInfo>\n'
            f'</CIRToInfoMapItem>'
        )
        self.xml_text = re.sub(
            r'(<CIRToInfoMap>)',
            lambda m: m.group(1) + "\n" + cir_item_xml,
            self.xml_text,
            count=1
        )

        pin_items = []
        for p_spec in inst.part.pins:
            pin_items.append(
                f'<CSCH_CAPVirtualComponentPin Class="CSCH_CAPVirtualComponentPin" GUID="{inst.guid}" PinName="&amp;ASC{p_spec.name}" lastnet=""/>'
            )
        if pin_items:
            pins_str = "\n".join(pin_items)
            self.xml_text = re.sub(
                r'(<ComponentPins>\s*<Item[^>]*>\s*<Items>)',
                lambda m: m.group(1) + "\n" + pins_str,
                self.xml_text,
                count=1
            )

    def _update_pin_lastnet_in_refdes_mgr(self, comp_guid: str, pin_name: str, net_name: str) -> None:
        self.xml_text = re.sub(
            rf'(<CSCH_CAPVirtualComponentPin\s+Class="CSCH_CAPVirtualComponentPin"\s+GUID="{re.escape(comp_guid)}"\s+PinName="&amp;ASC{re.escape(pin_name)}"\s+lastnet=")[^"]*("/>)',
            rf'\g<1>&amp;ASC{net_name}\g<2>',
            self.xml_text,
            count=1
        )

    def _register_wire_in_design_net_container(self, wire_guid: str, net_name: str) -> None:
        label_xml = (
            f'<CSchDesignNetLabel Class="CSchDesignNetLabel" Modifier="256" Value="" ID="{wire_guid}" Priority="0" LastOverruleName="" LastNetName="&amp;ASC{net_name}" LastLocalName=""/>'
        )
        self.xml_text = re.sub(
            r'(<CDesignNetContainer\s+Class="CDesignNetContainer"[^>]*>\s*<NetLabels>\s*<Item[^>]*>\s*<Items>)',
            lambda m: m.group(1) + "\n" + label_xml,
            self.xml_text,
            count=1
        )

    def place_part_from_db(
        self,
        part_query: str,
        refdes: str,
        x: int,
        y: int,
        value_or_label: str = "",
        rotation: int = 0
    ) -> PlacedComponentInstance:
        part = self.db.fetch_part(part_query)
        x = AffineGrid2D.snap9(x)
        y = AffineGrid2D.snap9(y)
        rotation = rotation % 360

        ci_id = self._alloc_id()
        sym_id = self._alloc_id()
        comp_guid = "{" + str(uuid.uuid4()).upper() + "}"
        has_model = bool(part.spice_data.strip()) or ("%m" in part.spice_template)
        model_ci_id = self._alloc_id() if has_model else None

        disp_val = value_or_label if value_or_label else part.comp_name
        pivot_x, pivot_y = self.db._pivot_of(part)
        rot_bbox = AffineGrid2D.rotate_bbox(part.bbox, pivot_x, pivot_y, rotation)
        bx0, by0, bx1, by1 = rot_bbox
        world_bbox = (x + bx0, y + by0, x + bx1, y + by1)

        inst = PlacedComponentInstance(
            refdes=refdes,
            part=part,
            ci_id=ci_id,
            sym_id=sym_id,
            model_ci_id=model_ci_id,
            guid=comp_guid,
            x=x,
            y=y,
            rotation=rotation,
            value_label=disp_val,
            world_bbox=world_bbox
        )

        name_txt_id = self._alloc_id()
        val_txt_id = self._alloc_id()
        loc_txt_id = self._alloc_id()
        bdr_id = self._alloc_id()

        sym_objects = [
            f'<Item ID="{name_txt_id}" Class="CIITSymTextCompName">\n<CIITSymTextCompName Class="CIITSymTextCompName" instID="0" Stretchable="1" Moveable="1" LabelSelectable="1" Selectable="1" LockedPenColor="0" LockedBrushColor="0" Layer="511" Output="&amp;ASC{refdes}" PenColor="0" FgColor="0" BgColor="16777215" Font-Height="-13" Font-Width="0" Font-Escapement="0" Font-Orientation="0" Font-Weight="700" Font-Italic="0" Font-Underline="0" Font-StrikeOut="0" Font-CharSet="0" Font-OutPrecision="3" Font-ClipPrecision="2" Font-Quality="1" Font-PitchAndFamily="34" Font-FaceName="&amp;ASCArial" Transformer-M00="1" Transformer-M01="0" Transformer-M10="0" Transformer-M11="1" Transformer-M20="{(bx0 + bx1) // 2}" Transformer-M21="{by0 - 15}" Hidden="0" HorizontalAlign="1" VerticalAlign="0" KeepCurrentFont="0"/>\n</Item>',
            f'<Item ID="{val_txt_id}" Class="CIITSymTextCompValue">\n<CIITSymTextCompValue Class="CIITSymTextCompValue" instID="0" Stretchable="1" Moveable="1" LabelSelectable="1" Selectable="1" LockedPenColor="0" LockedBrushColor="0" Layer="511" Output="&amp;ASC{disp_val}" PenColor="0" FgColor="0" BgColor="16777215" Font-Height="-13" Font-Width="0" Font-Escapement="0" Font-Orientation="0" Font-Weight="700" Font-Italic="0" Font-Underline="0" Font-StrikeOut="0" Font-CharSet="0" Font-OutPrecision="3" Font-ClipPrecision="2" Font-Quality="1" Font-PitchAndFamily="34" Font-FaceName="&amp;ASCArial" Transformer-M00="1" Transformer-M01="0" Transformer-M10="0" Transformer-M11="1" Transformer-M20="{(bx0 + bx1) // 2}" Transformer-M21="{by1 + 4}" Hidden="0" HorizontalAlign="1" VerticalAlign="0" KeepCurrentFont="0"/>\n</Item>',
            f'<Item ID="{loc_txt_id}" Class="CIITSymTextCompLocation">\n<CIITSymTextCompLocation Class="CIITSymTextCompLocation" instID="0" Stretchable="1" Moveable="1" LabelSelectable="1" Selectable="1" LockedPenColor="0" LockedBrushColor="0" Layer="511" Output="" PenColor="0" FgColor="0" BgColor="16777215" Font-Height="-9" Font-Width="0" Font-Escapement="0" Font-Orientation="0" Font-Weight="400" Font-Italic="0" Font-Underline="0" Font-StrikeOut="0" Font-CharSet="0" Font-OutPrecision="3" Font-ClipPrecision="2" Font-Quality="1" Font-PitchAndFamily="34" Font-FaceName="&amp;ASCCourier New" Transformer-M00="1" Transformer-M01="0" Transformer-M10="0" Transformer-M11="1" Transformer-M20="{(bx0 + bx1) // 2}" Transformer-M21="{by1 + 4}" Hidden="0" HorizontalAlign="1" VerticalAlign="0" KeepCurrentFont="0"/>\n</Item>',
            f'<Item ID="{bdr_id}" Class="CIITSymbolBorderRect">\n<CIITSymbolBorderRect Class="CIITSymbolBorderRect" instID="0" Stretchable="1" Moveable="1" LabelSelectable="1" Selectable="1" LockedPenColor="0" LockedBrushColor="0" Layer="511" pt0X="{bx0}" pt0Y="{by0}" pt1X="{bx1}" pt1Y="{by1}" Transformer-M00="1" Transformer-M01="0" Transformer-M10="0" Transformer-M11="1" Transformer-M20="0" Transformer-M21="0" Hidden="0"/>\n</Item>'
        ]

        rotated_graphics = self.db.render_rotated_primitives(part, rotation=rotation)
        for g_xml in rotated_graphics:
            g_id = self._alloc_id()
            g_cls_m = re.match(r'<([A-Za-z0-9_]+)', g_xml)
            g_cls = g_cls_m.group(1) if g_cls_m else "CODLineComp"
            sym_objects.append(f'<Item ID="{g_id}" Class="{g_cls}">\n{g_xml}\n</Item>')

        ci_ports_xml = []
        port_id_refs = []
        circuit_port_refs = []
        pin_obstacle_corridors: List[Tuple[int, int, int, int]] = []

        w_cx = x + (bx0 + bx1) / 2.0
        w_cy = y + (by0 + by1) / 2.0

        for p_spec in part.pins:
            port_ci_id = self._alloc_id()
            pin_sym_id = self._alloc_id()
            pin_conn_id = self._alloc_id()
            pname_id = self._alloc_id()
            pnum_id = self._alloc_id()
            pline_id = self._alloc_id()

            loc_wx, loc_wy = AffineGrid2D.rotate_point(p_spec.wcp_x, p_spec.wcp_y, pivot_x, pivot_y, rotation)
            lx0, ly0 = AffineGrid2D.rotate_point(p_spec.line_coords[0], p_spec.line_coords[1], pivot_x, pivot_y, rotation)
            lx1, ly1 = AffineGrid2D.rotate_point(p_spec.line_coords[2], p_spec.line_coords[3], pivot_x, pivot_y, rotation)
            nx, ny = AffineGrid2D.rotate_point(p_spec.name_pos[0], p_spec.name_pos[1], pivot_x, pivot_y, rotation)
            numx, numy = AffineGrid2D.rotate_point(p_spec.num_pos[0], p_spec.num_pos[1], pivot_x, pivot_y, rotation)

            wx = x + loc_wx
            wy = y + loc_wy

            dx_out = wx - w_cx
            dy_out = wy - w_cy
            if abs(dx_out) >= abs(dy_out):
                lead_x = wx + (GRID_PITCH if dx_out >= 0 else -GRID_PITCH)
                lead_y = wy
            else:
                lead_x = wx
                lead_y = wy + (GRID_PITCH if dy_out >= 0 else -GRID_PITCH)

            p_inst = PlacedPinInstance(
                pin_name=p_spec.name,
                pin_number=p_spec.number,
                port_ci_id=port_ci_id,
                pin_sym_id=pin_sym_id,
                pin_conn_id=pin_conn_id,
                cod_pin_id=0,
                local_x=loc_wx,
                local_y=loc_wy,
                world_x=wx,
                world_y=wy,
                lead_x=lead_x,
                lead_y=lead_y,
                wire_attached=False
            )
            inst.pins[p_spec.name] = p_inst
            if str(p_spec.index) not in inst.pins:
                inst.pins[str(p_spec.index)] = p_inst
            pin_obstacle_corridors.append((wx, wy, lead_x, lead_y))

            sym_objects.append(
                f'<Item ID="{pin_sym_id}" Class="CIITPinSymbolComp">\n'
                f'<CIITPinSymbolComp Class="CIITPinSymbolComp" instID="0" Stretchable="0" Moveable="1" LabelSelectable="1" Selectable="1" LockedPenColor="0" LockedBrushColor="0" Layer="511" Brush-Style="1" Brush-Hatch="0" PenColor="0" FgColor="0" BgColor="16777215" Font-Height="-13" Font-Width="0" Font-Escapement="0" Font-Orientation="0" Font-Weight="700" Font-Italic="0" Font-Underline="0" Font-StrikeOut="0" Font-CharSet="1" Font-OutPrecision="0" Font-ClipPrecision="0" Font-Quality="0" Font-PitchAndFamily="0" Font-FaceName="&amp;ASCCourier New" Pen-Style="0" Pen-Width="1" Pen-Color="16728576" Transformer-M00="1" Transformer-M01="0" Transformer-M10="0" Transformer-M11="1" Transformer-M20="0" Transformer-M21="0" Hidden="0" Container="0" Type="" ToolTip="" Prompt="" UserDefinedLabelPos="1" LabelPos="0" HorizLabelPos="0" PinName="&amp;ASC{p_spec.name}" PinNumber="&amp;ASC{p_spec.number}" PinShape="2" ShowName="0" ShowNameUser="2" ShowNumber="0" ShowNumberUser="2" RotationName="0" PositionName="0" RotationNumber="0" PositionNumber="0" OriginalPinNamePositionInSEX="{nx}" OriginalPinNamePositionInSEY="{ny}" PinNumberOrientationInSE="0" PinNameOrientationInSE="0" InternalPin="0" PinNumberLocationside="1" PinNameLocationside="1" PinType="&amp;ASC{p_spec.pin_group}" PortID="{port_ci_id}">\n'
                f'<Objects>\n'
                f'<Item ID="{pin_conn_id}" Class="CIITPinConnectorComp">\n'
                f'<CIITPinConnectorComp Class="CIITPinConnectorComp" instID="0" Stretchable="1" Moveable="1" LabelSelectable="1" Selectable="1" LockedPenColor="0" LockedBrushColor="0" Layer="511" Text="" Internal="0" ptCenterX="{loc_wx}" ptCenterY="{loc_wy}" Mobility="0" PenColor="0" FgColor="0" BgColor="16777215" Pen-Style="0" Pen-Width="1" Pen-Color="16728576" Transformer-M00="1" Transformer-M01="0" Transformer-M10="0" Transformer-M11="1" Transformer-M20="0" Transformer-M21="0" Hidden="0">\n'
                f'<ElectricalObject HiddenName="0">\n<ModifierInfo/>\n</ElectricalObject>\n'
                f'<ConnectList>\n</ConnectList>\n'
                f'</CIITPinConnectorComp>\n</Item>\n'
                f'<Item ID="{pname_id}" Class="CIITPinSymTextCompName">\n'
                f'<CIITPinSymTextCompName Class="CIITPinSymTextCompName" instID="0" Stretchable="1" Moveable="1" LabelSelectable="1" Selectable="1" LockedPenColor="0" LockedBrushColor="0" Layer="511" Output="&amp;ASC{p_spec.name}" Rotation="0" Position="0" Font-Height="-9" Font-Width="0" Font-Escapement="0" Font-Orientation="0" Font-Weight="400" Font-Italic="0" Font-Underline="0" Font-StrikeOut="0" Font-CharSet="0" Font-OutPrecision="3" Font-ClipPrecision="2" Font-Quality="1" Font-PitchAndFamily="34" Font-FaceName="&amp;ASCCourier New" FontOriginal-Height="-9" FontOriginal-Width="0" FontOriginal-Escapement="0" FontOriginal-Orientation="0" FontOriginal-Weight="400" FontOriginal-Italic="0" FontOriginal-Underline="0" FontOriginal-StrikeOut="0" FontOriginal-CharSet="0" FontOriginal-OutPrecision="3" FontOriginal-ClipPrecision="2" FontOriginal-Quality="1" FontOriginal-PitchAndFamily="49" FontOriginal-FaceName="&amp;ASCCourier New" Transformer-M00="1" Transformer-M01="0" Transformer-M10="0" Transformer-M11="1" Transformer-M20="{nx}" Transformer-M21="{ny}" Hidden="0" HorizontalAlign="2" VerticalAlign="1"/>\n'
                f'</Item>\n'
                f'<Item ID="{pnum_id}" Class="CIITPinSymTextCompNumber">\n'
                f'<CIITPinSymTextCompNumber Class="CIITPinSymTextCompNumber" instID="0" Stretchable="1" Moveable="1" LabelSelectable="1" Selectable="1" LockedPenColor="0" LockedBrushColor="0" Layer="511" Output="&amp;ASC{p_spec.number}" Rotation="0" Position="0" Font-Height="-9" Font-Width="0" Font-Escapement="0" Font-Orientation="0" Font-Weight="400" Font-Italic="0" Font-Underline="0" Font-StrikeOut="0" Font-CharSet="0" Font-OutPrecision="3" Font-ClipPrecision="2" Font-Quality="1" Font-PitchAndFamily="34" Font-FaceName="&amp;ASCCourier New" Transformer-M00="1" Transformer-M01="0" Transformer-M10="0" Transformer-M11="1" Transformer-M20="{numx}" Transformer-M21="{numy}" Hidden="0"/>\n'
                f'</Item>\n'
                f'<Item ID="{pline_id}" Class="CODLineComp">\n'
                f'<CODLineComp Class="CODLineComp" instID="0" Stretchable="1" Moveable="1" LabelSelectable="1" Selectable="1" LockedPenColor="0" LockedBrushColor="0" Layer="511" ArrowAtSource="0" ArrowAtTarget="0" pt0X="{lx0}" pt0Y="{ly0}" pt1X="{lx1}" pt1Y="{ly1}" PenColor="0" FgColor="0" BgColor="16777215" Pen-Style="0" Pen-Width="0" Pen-Color="0" Hidden="0"/>\n'
                f'</Item>\n'
                f'</Objects>\n<ElectricalObject HiddenName="0">\n<ModifierInfo/>\n</ElectricalObject>\n</CIITPinSymbolComp>\n</Item>'
            )

            port_id_refs.append(f'<Item CiID="{port_ci_id}"/>')
            circuit_port_refs.append(f'<Item CiID="{port_ci_id}"/>')
            ci_ports_xml.append(
                f'<Item CiID="{port_ci_id}" Class="CiPort">\n'
                f'<CiPort Class="CiPort" LocalName="&amp;ASC{p_spec.name}" Component="{ci_id}">\n'
                f'<Attributes>\n<Item/>\n<Item/>\n<Item/>\n<Item/>\n'
                f'<Item>\n<CiaCollString Class="CiaCollString">\n<strings>\n'
                f'<Item Value="&amp;ASC{part.family_name}"/>\n<Item Value="&amp;ASC{part.comp_name}"/>\n<Item Value="&amp;ASC{part.pin_family}"/>\n<Item Value=""/>\n<Item Value="&amp;ASC1"/>\n<Item Value=""/>\n'
                f'</strings>\n</CiaCollString>\n</Item>\n'
                f'<Item>\n<CiaCollString Class="CiaCollString">\n<strings>\n'
                f'<Item Value=""/>\n<Item Value=""/>\n<Item Value=""/>\n<Item Value=""/>\n<Item Value=""/>\n<Item Value=""/>\n<Item Value="&amp;ASC{p_spec.number}"/>\n<Item Value=""/>\n<Item Value="&amp;ASC{p_spec.pin_group}"/>\n<Item Value=""/>\n<Item Value="&amp;ASC{p_spec.pin_type}"/>\n<Item Value="&amp;ASC{p_spec.pin_type}"/>\n'
                f'</strings>\n</CiaCollString>\n</Item>\n'
                f'<Item/>\n<Item>\n<CiaNodeType Class="CiaNodeType" DWORD="256"/>\n</Item>\n'
                + ('<Item/>\n' * 26) +
                f'<Item>\n<CiaDOUBLE Class="CiaDOUBLE" Double="6"/>\n</Item>\n'
                + ('<Item/>\n' * 3) +
                f'<Item>\n<CiaDOUBLE Class="CiaDOUBLE" Double="1"/>\n</Item>\n'
                f'<Item/>\n'
                f'<Item>\n<CiaDOUBLE Class="CiaDOUBLE" Double="0"/>\n</Item>\n'
                f'</Attributes>\n<Nodes>\n</Nodes>\n</CiPort>\n</Item>'
            )

        self.eda.register_component_obstacle(world_bbox, pin_obstacle_corridors, refdes=refdes)

        sym_block = (
            f'<Item ID="{sym_id}" Class="CIITSymbolComp">\n'
            f'<CIITSymbolComp Class="CIITSymbolComp" instID="0" Stretchable="0" Moveable="1" LabelSelectable="0" Selectable="1" LockedPenColor="0" LockedBrushColor="0" Layer="511" PenColor="0" FgColor="0" Transformer-M00="1" Transformer-M01="0" Transformer-M10="0" Transformer-M11="1" Transformer-M20="{x}" Transformer-M21="{y}" Hidden="0" Container="0" Type="" ToolTip="" Prompt="" UserDefinedLabelPos="1" LabelPos="0" HorizLabelPos="0" DBComponentSectionName="" DBComponentType="0" OrientationFlip="0" OriginalColor="4294967295" FileDataPackageID="" InstanceRefDes="" IRSection="" IRSectionID="-1" ElvisFlags="0" NumSections="1" CiComponent="{ci_id}">\n'
            f'<Objects>\n' + "\n".join(sym_objects) + '\n</Objects>\n'
            f'<CComponentVariantData Class="CComponentVariantData" DefaultNestedVariant="" IncludeVariantByDefault="1">\n'
            f'<VariantNames>\n<Item Value="&amp;ASCDefault1"/>\n</VariantNames>\n'
            f'<VariantToNestedMap>\n<Element Key="&amp;ASCDefault1">\n<Item Value="&amp;ASCDefault1"/>\n</Element>\n</VariantToNestedMap>\n'
            f'</CComponentVariantData>\n'
            f'<HiddenPins/>\n</CIITSymbolComp>\n</Item>'
        )

        self.xml_text = re.sub(
            r'(<CODComposite\s+Class="CODComposite"[^\n]*\n<Objects>)',
            lambda m: m.group(1) + "\n" + sym_block,
            self.xml_text,
            count=1
        )

        model_attr = f' Model="{model_ci_id}"' if model_ci_id else ""
        spice_val = value_or_label if value_or_label else "1k"
        mod_id_str = f"&amp;ASC{part.model_id}" if part.model_id else ""
        mfr_str = f"&amp;ASC{part.manufacturer}" if part.manufacturer else "&amp;ASCGeneric"
        fgrp_str = f"&amp;ASC{part.family_group}" if part.family_group else "&amp;ASCBasic"
        fdesc_str = f"&amp;ASC{part.function_desc}" if part.function_desc else "&amp;ASC$ANALOG$"

        ci_comp_block = (
            f'<Item CiID="{ci_id}" Class="CiComponent">\n'
            f'<CiComponent Class="CiComponent" LocalName="&amp;ASC{refdes}"{model_attr} SymCompID="{sym_id}" InstanceID="{comp_guid}" Circuit="{self.circuit_id}">\n'
            f'<Attributes>\n'
            f'<Item/>\n'
            f'<Item>\n<CiaSpiceTmpltExprt Class="CiaSpiceTmpltExprt" String="&amp;ASC{part.spice_template}\n"/>\n</Item>\n'
            f'<Item>\n<CiaParamList Class="CiaParamList">\n<doubles/>\n<strings/>\n'
            f'<parameters>\n<Item Value="&amp;ASC0"/>\n<Item Value="&amp;ASC{spice_val}"/>\n<Item Value="&amp;ASC0"/>\n<Item Value="&amp;ASC0"/>\n<Item Value="&amp;ASC0"/>\n<Item Value="&amp;ASC27"/>\n<Item Value="&amp;ASC0"/>\n<Item Value="&amp;ASC0"/>\n<Item Value="&amp;ASC0"/>\n<Item Value="&amp;ASC0"/>\n<Item Value="&amp;ASC0"/>\n<Item Value="&amp;ASC27"/>\n</parameters>\n'
            f'<paramindicators>\n<Item Value="0"/>\n<Item Value="1"/>\n<Item Value="0"/>\n<Item Value="0"/>\n<Item Value="0"/>\n<Item Value="0"/>\n<Item Value="0"/>\n<Item Value="0"/>\n<Item Value="0"/>\n<Item Value="0"/>\n<Item Value="0"/>\n<Item Value="0"/>\n</paramindicators>\n'
            f'</CiaParamList>\n</Item>\n'
            f'<Item/>\n'
            f'<Item>\n<CiaCollString Class="CiaCollString">\n<strings>\n'
            f'<Item Value="&amp;ASC{part.family_name}"/>\n<Item Value="&amp;ASC{part.comp_name}"/>\n<Item Value="&amp;ASC{part.family_name}"/>\n<Item Value="{mod_id_str}"/>\n<Item Value="&amp;ASC1"/>\n<Item Value=""/>\n<Item Value=""/>\n<Item Value=""/>\n<Item Value="&amp;ASCIIT"/>\n<Item Value=""/>\n<Item Value=""/>\n<Item Value="&amp;ASCL0"/>\n<Item Value="&amp;ASCV7"/>\n<Item Value="&amp;ASCA0"/>\n<Item Value="{fgrp_str}"/>\n<Item Value="{fdesc_str}"/>\n<Item Value="{mfr_str}"/>\n<Item Value="&amp;ASCV6"/>\n<Item Value="&amp;ASCA0"/>\n<Item Value="&amp;ASCB"/>\n<Item Value="&amp;ASC{part.family_name}"/>\n<Item Value=""/>\n<Item Value=""/>\n<Item Value=""/>\n<Item Value=""/>\n<Item Value=""/>\n<Item Value=""/>\n<Item Value=""/>\n<Item Value=""/>\n<Item Value="&amp;ASC1"/>\n<Item Value=""/>\n<Item Value=""/>\n<Item Value=""/>\n<Item Value=""/>\n<Item Value=""/>\n<Item Value="&amp;ASC1"/>\n'
            f'</strings>\n</CiaCollString>\n</Item>\n'
            f'<Item>\n<CiaCollString Class="CiaCollString">\n<strings>\n'
            f'<Item Value="&amp;ASC{part.family_id}"/>\n<Item Value="&amp;ASCProduct"/>\n<Item Value="&amp;ASC{part.family_name}"/>\n<Item Value=""/>\n<Item Value="&amp;ASC{part.ref_template}"/>\n<Item Value="&amp;ASC{part.spice_template}"/>\n<Item Value=""/>\n<Item Value=""/>\n<Item Value=""/>\n<Item Value=""/>\n<Item Value=""/>\n<Item Value=""/>\n<Item Value=""/>\n<Item Value="&amp;ASCDMN"/>\n<Item Value=""/>\n<Item Value=""/>\n<Item Value=""/>\n<Item Value=""/>\n<Item Value="&amp;ASC17"/>\n<Item Value=""/>\n<Item Value=""/>\n<Item Value=""/>\n<Item Value=""/>\n<Item Value=""/>\n'
            f'</strings>\n</CiaCollString>\n</Item>\n'
            f'<Item/>\n<Item/>\n'
            f'<Item>\n<CiaFault Class="CiaFault" FaultType="3" LeakResistanceValue="100" LeakResistanceScale="&amp;ASC Ohm">\n<FaultPorts/>\n</CiaFault>\n</Item>\n'
            f'<Item/>\n<Item/>\n<Item/>\n<Item/>\n'
            f'<Item>\n<CiaProperDlg Class="CiaProperDlg" ActivePage="2" LastTimeInTableViewStatus="0">\n<doubles/>\n<strings/>\n<parameters/>\n<paramindicators/>\n</CiaProperDlg>\n</Item>\n'
            f'<Item>\n<CiaCollString Class="CiaCollString">\n<strings>\n<Item Value="&amp;ASCA"/>\n</strings>\n</CiaCollString>\n</Item>\n'
            f'<Item/>\n<Item/>\n'
            f'<Item>\n<CiaDOUBLE Class="CiaDOUBLE" Double="200"/>\n</Item>\n'
            f'<Item>\n<CiaCollString Class="CiaCollString">\n<strings/>\n</CiaCollString>\n</Item>\n'
            f'<Item/>\n'
            f'<Item>\n<CiaDOUBLE Class="CiaDOUBLE" Double="-1"/>\n</Item>\n'
            f'<Item>\n<CiaCString Class="CiaCString" String=""/>\n</Item>\n'
            + ('<Item/>\n' * 14) +
            f'<Item>\n<CiaDOUBLE Class="CiaDOUBLE" Double="1"/>\n</Item>\n'
            f'<Item>\n<CiaDOUBLE Class="CiaDOUBLE" Double="1"/>\n</Item>\n'
            f'<Item/>\n<Item/>\n<Item/>\n'
            f'<Item>\n<CiaCString Class="CiaCString" String="&amp;ASC{spice_val}"/>\n</Item>\n'
            f'<Item>\n<CiaCString Class="CiaCString" String="&amp;ASC{disp_val}"/>\n</Item>\n'
            f'<Item>\n<CiaCString Class="CiaCString" String=""/>\n</Item>\n'
            f'<Item>\n<CiaCString Class="CiaCString" String="&amp;ASC1"/>\n</Item>\n'
            f'<Item/>\n'
            f'<Item>\n<CiaDOUBLE Class="CiaDOUBLE" Double="0"/>\n</Item>\n'
            f'<Item>\n<CiaCollString Class="CiaCollString">\n<strings>\n<Item Value="&amp;ASCA"/>\n</strings>\n</CiaCollString>\n</Item>\n'
            f'<Item/>\n<Item/>\n<Item/>\n<Item/>\n'
            f'<Item>\n<CiaCString Class="CiaCString" String="&amp;ASC0"/>\n</Item>\n'
            f'</Attributes>\n<Ports>\n' + "\n".join(port_id_refs) + '\n</Ports>\n</CiComponent>\n</Item>\n' +
            "\n".join(ci_ports_xml)
        )

        self.xml_text = self.xml_text.replace(
            "</HiddenTitleBlockStorage>\n<Elements>",
            "</HiddenTitleBlockStorage>\n<Elements>\n" + ci_comp_block,
            1
        )

        self.xml_text = re.sub(
            r'(<Item\s+CiID="' + str(self.circuit_id) + r'"\s+Class="CiCircuit">.*?<Ports>)',
            lambda m: m.group(1) + "\n" + "\n".join(circuit_port_refs),
            self.xml_text,
            count=1,
            flags=re.DOTALL
        )
        self.xml_text = re.sub(
            r'(<Item\s+CiID="' + str(self.circuit_id) + r'"\s+Class="CiCircuit">.*?<Components>)',
            lambda m: m.group(1) + f'\n<Item CiID="{ci_id}"/>',
            self.xml_text,
            count=1,
            flags=re.DOTALL
        )

        if model_ci_id:
            clean_spice = part.spice_data.replace("&", "&amp;").replace("\"", "&quot;").replace("<", "&lt;").replace(">", "&gt;")
            ci_model_block = (
                f'<Item CiID="{model_ci_id}" Class="CiModel">\n'
                f'<CiModel Class="CiModel" LocalName="&amp;ASC{part.model_id}" ChangedByUser="0" Scope="{self.circuit_id}">\n'
                f'<Attributes>\n<Item/>\n'
                f'<Item>\n<CiaCString Class="CiaCString" String="&amp;ASC{clean_spice}"/>\n</Item>\n'
                f'<Item>\n<CiaParamList Class="CiaParamList">\n<doubles/>\n<strings/>\n<parameters/>\n<paramindicators/>\n</CiaParamList>\n</Item>\n'
                f'</Attributes>\n</CiModel>\n</Item>'
            )
            self.xml_text = re.sub(
                r'(<Item\s+CiID="' + str(self.circuit_id) + r'"\s+Class="CiCircuit">.*?<Models>)',
                lambda m: m.group(1) + f'\n<Item CiID="{model_ci_id}"/>',
                self.xml_text,
                count=1,
                flags=re.DOTALL
            )
            self.xml_text = self.xml_text.replace(
                "</CiCircuit>\n</Item>",
                "</CiCircuit>\n</Item>\n" + ci_model_block,
                1
            )

        self._register_component_in_refdes_mgr(inst)
        self.placed_components[refdes] = inst
        self.logger.info(
            f"Placed [{refdes}] ({part.comp_name}) at (X={x}, Y={y}, Rot={rotation}deg) -> CiID={ci_id}, SymID={sym_id}"
        )
        return inst

    def place_junction(self, junction_key: str, x: int, y: int, net_name: str) -> PlacedJunctionInstance:
        x = AffineGrid2D.snap9(x)
        y = AffineGrid2D.snap9(y)
        junc_obj_id = self._alloc_id()
        j_xml = (
            f'<Item ID="{junc_obj_id}" Class="CODPinComp">\n'
            f'<CODPinComp Class="CODPinComp" instID="0" Stretchable="1" Moveable="1" LabelSelectable="1" Selectable="1" LockedPenColor="0" LockedBrushColor="0" Layer="511" Text="" Internal="0" CenterX="{x}" CenterY="{y}" Mobility="0" PenColor="255" FgColor="255" Pen-Style="0" Pen-Width="1" Pen-Color="16728576" Transformer-M00="1" Transformer-M01="0" Transformer-M10="0" Transformer-M11="1" Transformer-M20="0" Transformer-M21="0" Hidden="0">\n'
            f'<ElectricalObject HiddenName="0">\n<ModifierInfo/>\n</ElectricalObject>\n'
            f'<ConnectList>\n</ConnectList>\n'
            f'</CODPinComp>\n</Item>'
        )
        self.xml_text = self.xml_text.replace(
            "</Objects>\n<ReferencedComponents>",
            j_xml + "\n</Objects>\n<ReferencedComponents>",
            1
        )
        j_inst = PlacedJunctionInstance(junction_key=junction_key, junc_obj_id=junc_obj_id, x=x, y=y, net_name=net_name)
        self.placed_junctions[junction_key] = j_inst
        return j_inst

    def _alloc_junction_wire_endpoint(self, junc: PlacedJunctionInstance) -> int:
        ep_cod_id = self._alloc_id()
        ep_xml = (
            f'<Item ID="{ep_cod_id}" Class="CODPinComp">\n'
            f'<CODPinComp Class="CODPinComp" instID="0" Stretchable="1" Moveable="1" LabelSelectable="1" Selectable="1" LockedPenColor="0" LockedBrushColor="0" Layer="511" Text="" Internal="0" CenterX="{junc.x}" CenterY="{junc.y}" Mobility="1" Pen-Style="5" Pen-Width="1" Pen-Color="0" Hidden="0">\n'
            f'<ElectricalObject HiddenName="0">\n<ModifierInfo/>\n</ElectricalObject>\n'
            f'<ConnectList>\n<Item ID="{junc.junc_obj_id}" Class="CODPinComp"/>\n</ConnectList>\n'
            f'</CODPinComp>\n</Item>'
        )
        self.xml_text = self.xml_text.replace(
            "<ReferencedComponents>",
            "<ReferencedComponents>\n" + ep_xml,
            1
        )
        self.xml_text = re.sub(
            r'(<Item\s+ID="' + str(junc.junc_obj_id) + r'"\s+Class="CODPinComp">.*?<ConnectList>)',
            lambda m: m.group(1) + f'\n<Item ID="{ep_cod_id}" Class="CODPinComp"/>',
            self.xml_text,
            count=1,
            flags=re.DOTALL
        )
        return ep_cod_id

    def _ensure_net_node(self, net_name: str) -> Tuple[int, int]:
        if net_name in self.net_nodes:
            n_id, nt_id = self.net_nodes[net_name]
            self._ensure_node_text_exists(nt_id)
            return n_id, nt_id

        node_ci_id = self._alloc_id()
        node_text_id = self._alloc_id()

        node_xml = (
            f'<Item CiID="{node_ci_id}" Class="CiNode">\n'
            f'<CiNode Class="CiNode" LocalName="&amp;ASC{net_name}" Circuit="{self.circuit_id}">\n'
            f'<Attributes>\n<Item/>\n<Item/>\n'
            f'<Item>\n<CiaParamList Class="CiaParamList">\n<doubles>\n<Item Value="0"/>\n<Item Value="0"/>\n<Item Value="0"/>\n<Item Value="0"/>\n<Item Value="-1"/>\n<Item Value="1"/>\n</doubles>\n<strings/>\n<parameters/>\n<paramindicators/>\n</CiaParamList>\n</Item>\n'
            + ('<Item/>\n' * 8) +
            f'<Item>\n<CiaDisplay Class="CiaDisplay" ShowLabels="1" ShowValues="1" ShowReferenceID="0" ShowCompIC="1" ShowCompTol="1"/>\n</Item>\n'
            + ('<Item/>\n' * 9) +
            f'<Item>\n<CiaCString Class="CiaCString" String=""/>\n</Item>\n'
            + ('<Item/>\n' * 3) +
            f'<Item>\n<CiaDOUBLE Class="CiaDOUBLE" Double="-1"/>\n</Item>\n'
            f'<Item>\n<CiaDOUBLE Class="CiaDOUBLE" Double="-1"/>\n</Item>\n'
            f'<Item>\n<CiaDOUBLE Class="CiaDOUBLE" Double="-1"/>\n</Item>\n'
            f'<Item>\n<CiaDOUBLE Class="CiaDOUBLE" Double="-1"/>\n</Item>\n'
            f'</Attributes>\n<Ports>\n</Ports>\n</CiNode>\n</Item>'
        )
        self.xml_text = self.xml_text.replace(
            f'<Item CiID="{self.circuit_id}" Class="CiCircuit">',
            node_xml + f'\n<Item CiID="{self.circuit_id}" Class="CiCircuit">',
            1
        )
        self.xml_text = re.sub(
            r'(<Item\s+CiID="' + str(self.circuit_id) + r'"\s+Class="CiCircuit">.*?<Nodes>)',
            lambda m: m.group(1) + f'\n<Item CiID="{node_ci_id}"/>',
            self.xml_text,
            count=1,
            flags=re.DOTALL
        )

        self._ensure_node_text_exists(node_text_id)
        self.net_nodes[net_name] = (node_ci_id, node_text_id)
        return node_ci_id, node_text_id

    def _alloc_pin_wire_endpoint(self, pin: PlacedPinInstance) -> int:
        ep_cod_id = self._alloc_id()
        pin.cod_pin_id = ep_cod_id
        pin.wire_attached = True
        ep_xml = (
            f'<Item ID="{ep_cod_id}" Class="CODPinComp">\n'
            f'<CODPinComp Class="CODPinComp" instID="0" Stretchable="1" Moveable="1" LabelSelectable="1" Selectable="1" LockedPenColor="0" LockedBrushColor="0" Layer="511" Text="" Internal="0" CenterX="{pin.world_x}" CenterY="{pin.world_y}" Mobility="1" Pen-Style="5" Pen-Width="1" Pen-Color="0" Hidden="0">\n'
            f'<ElectricalObject HiddenName="0">\n<ModifierInfo/>\n</ElectricalObject>\n'
            f'<ConnectList>\n<Item ID="{pin.pin_conn_id}" Class="CIITPinConnectorComp"/>\n</ConnectList>\n'
            f'</CODPinComp>\n</Item>'
        )
        self.xml_text = self.xml_text.replace(
            "<ReferencedComponents>",
            "<ReferencedComponents>\n" + ep_xml,
            1
        )
        self.xml_text = re.sub(
            r'(<Item\s+ID="' + str(pin.pin_conn_id) + r'"\s+Class="CIITPinConnectorComp">.*?<ConnectList>)',
            lambda m: m.group(1) + f'\n<Item ID="{ep_cod_id}" Class="CODPinComp"/>',
            self.xml_text,
            count=1,
            flags=re.DOTALL
        )
        return ep_cod_id

    def _bind_port_to_node(self, port_ci_id: int, node_ci_id: int) -> None:
        def add_node_to_port(m_port):
            body = m_port.group(2)
            tag = f'<Item CiID="{node_ci_id}"/>'
            if tag in body:
                return m_port.group(0)
            return f"{m_port.group(1)}\n{tag}{body}{m_port.group(3)}"

        self.xml_text = re.sub(
            r'(<Item\s+CiID="' + str(port_ci_id) + r'"\s+Class="CiPort">.*?<Nodes>)(.*?)(</Nodes>)',
            add_node_to_port,
            self.xml_text,
            count=1,
            flags=re.DOTALL
        )

        def add_port_to_node(m_node):
            body = m_node.group(2)
            tag = f'<Item CiID="{port_ci_id}"/>'
            if tag in body:
                return m_node.group(0)
            return f"{m_node.group(1)}\n{tag}{body}{m_node.group(3)}"

        self.xml_text = re.sub(
            r'(<Item\s+CiID="' + str(node_ci_id) + r'"\s+Class="CiNode">.*?<Ports>)(.*?)(</Ports>)',
            add_port_to_node,
            self.xml_text,
            count=1,
            flags=re.DOTALL
        )

    def _resolve_endpoint(self, ref_key: str, pin_key: str, net_name: str, node_ci_id: int) -> Tuple[int, int, int, int, int, str]:
        if ref_key in self.placed_junctions:
            j = self.placed_junctions[ref_key]
            ep_id = self._alloc_junction_wire_endpoint(j)
            return (ep_id, j.x, j.y, j.x, j.y, f"J({j.x},{j.y})")
        c = self.placed_components[ref_key]
        p = c.pins[pin_key]
        ep_id = self._alloc_pin_wire_endpoint(p)
        self._bind_port_to_node(p.port_ci_id, node_ci_id)
        self._update_pin_lastnet_in_refdes_mgr(c.guid, p.pin_name, net_name)
        return (ep_id, p.world_x, p.world_y, p.lead_x, p.lead_y, f"{ref_key}.{p.pin_name}")

    def connect_pins(
        self,
        comp1_ref: str,
        pin1_key: str,
        comp2_ref: str,
        pin2_key: str,
        net_name: str
    ) -> int:
        node_ci_id, node_text_id = self._ensure_net_node(net_name)

        ep1_cod_id, x1, y1, lx1, ly1, lbl1 = self._resolve_endpoint(comp1_ref, pin1_key, net_name, node_ci_id)
        ep2_cod_id, x2, y2, lx2, ly2, lbl2 = self._resolve_endpoint(comp2_ref, pin2_key, net_name, node_ci_id)

        wire_id = self._alloc_id()
        wire_guid = "{" + str(uuid.uuid4()).upper() + "}"

        route_pts = self.eda.route_pin_pair(
            net_name=net_name,
            x1=x1, y1=y1, lx1=lx1, ly1=ly1,
            x2=x2, y2=y2, lx2=lx2, ly2=ly2
        )

        pts_xml = "\n".join(f'<Item X="{px}" Y="{py}"/>' for px, py in route_pts)
        wire_xml = (
            f'<Item ID="{wire_id}" Class="CIITLinkComp">\n'
            f'<CIITLinkComp Class="CIITLinkComp" instID="0" Stretchable="1" Moveable="1" LabelSelectable="1" Selectable="1" LockedPenColor="0" LockedBrushColor="0" Layer="511" ArrowAtSource="0" ArrowAtTarget="0" LineHidden="0" PenColor="255" FgColor="0" BgColor="16777215" Pen-Style="0" Pen-Width="1" Pen-Color="16728576" Hidden="0" ShowWhenNetSet="0" Connect1="{ep1_cod_id}" Connect2="{ep2_cod_id}" IsElvisHiddenWireFlags="0" Node="{node_ci_id}" NodeText="{node_text_id}">\n'
            f'<Points>\n{pts_xml}\n</Points>\n'
            f'<ElectricalObject HiddenName="0">\n<ModifierInfo>\n<Element NetModifier="&amp;ASCNI_EWB_NET_AUTONAMED">\n<Item Value="" Guid="{wire_guid}"/>\n</Element>\n</ModifierInfo>\n</ElectricalObject>\n'
            f'</CIITLinkComp>\n</Item>'
        )

        nt_anchor = f'<Item ID="{node_text_id}" Class="CODNodeTextComp">'
        if nt_anchor in self.xml_text:
            self.xml_text = self.xml_text.replace(nt_anchor, wire_xml + "\n" + nt_anchor, 1)
        else:
            self.xml_text = self.xml_text.replace("</Objects>\n<ReferencedComponents>", wire_xml + "\n</Objects>\n<ReferencedComponents>", 1)

        self.xml_text = re.sub(
            r'(<Item\s+ID="' + str(node_text_id) + r'"\s+Class="CODNodeTextComp">.*?<Links>)',
            lambda m: m.group(1) + f'\n<Item ID="{wire_id}" Class="CIITLinkComp"/>',
            self.xml_text,
            count=1,
            flags=re.DOTALL
        )
        self.xml_text = self.xml_text.replace(
            "<ReferencedComponents>",
            f'<ReferencedComponents>\n<Item ID="{wire_id}" Class="CIITLinkComp"/>',
            1
        )
        self._register_wire_in_design_net_container(wire_guid, net_name)

        self.logger.info(
            f"Routed Wire [ID={wire_id}] Net='{net_name}' (Node={node_ci_id}): {lbl1}({x1},{y1}) -> {lbl2}({x2},{y2}) via {route_pts}"
        )
        return wire_id

    def connect_net(self, net_name: str, terminals: List[Tuple[str, str]]) -> List[int]:
        resolved: List[Tuple[str, str, int, int, int, int]] = []
        for cref, pkey in terminals:
            c = self.placed_components[cref]
            p = c.pins[pkey]
            resolved.append((cref, pkey, p.world_x, p.world_y, p.lead_x, p.lead_y))

        plan = self.eda.synthesize_steiner_junction_plan(net_name, resolved)
        for j_key, jx, jy in plan.junctions:
            self.place_junction(j_key, jx, jy, net_name=net_name)

        wire_ids: List[int] = []
        for (c1, p1), (c2, p2) in plan.point_to_point_edges:
            wid = self.connect_pins(c1, p1, c2, p2, net_name=net_name)
            wire_ids.append(wid)
        return wire_ids

    def synthesize_topology(
        self,
        parts: List[PartPlacementSpec],
        nets: List[NetConnectionSpec],
        auto_place: bool = False
    ) -> "MultisimSchematicBuilder":
        for p in parts:
            px = AffineGrid2D.snap9(p.x if p.x is not None else 279)
            py = AffineGrid2D.snap9(p.y if p.y is not None else 216)
            self.place_part_from_db(
                part_query=p.part_query,
                refdes=p.refdes,
                x=px,
                y=py,
                value_or_label=p.value_or_label,
                rotation=p.rotation
            )
        for net in nets:
            self.connect_net(net.net_name, net.terminals)
        return self

    def add_schematic_info_banner(self, title: str, lines: List[str], x: int = 333, y: int = 477) -> None:
        all_lines = [title] + lines
        for idx, line_txt in enumerate(all_lines):
            tid = self._alloc_id()
            ly = y + idx * 18
            clean_txt = line_txt.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "")
            f_weight = "700" if idx == 0 else "400"
            f_color = "128" if idx == 0 else "8388608"
            t_xml = (
                f'<Item ID="{tid}" Class="CIITSymTextCompLocation">\n'
                f'<CIITSymTextCompLocation Class="CIITSymTextCompLocation" instID="0" Stretchable="1" Moveable="1" LabelSelectable="1" Selectable="1" LockedPenColor="0" LockedBrushColor="0" Layer="511" Output="&amp;ASC{clean_txt}" PenColor="0" FgColor="{f_color}" BgColor="16777215" Font-Height="-12" Font-Width="0" Font-Escapement="0" Font-Orientation="0" Font-Weight="{f_weight}" Font-Italic="0" Font-Underline="0" Font-StrikeOut="0" Font-CharSet="0" Font-OutPrecision="3" Font-ClipPrecision="2" Font-Quality="1" Font-PitchAndFamily="49" Font-FaceName="&amp;ASCCourier New" Transformer-M00="1" Transformer-M01="0" Transformer-M10="0" Transformer-M11="1" Transformer-M20="{x}" Transformer-M21="{ly}" Hidden="0" HorizontalAlign="0" VerticalAlign="0" KeepCurrentFont="1"/>\n'
                f'</Item>'
            )
            self.xml_text = re.sub(
                r'(<CODComposite\s+Class="CODComposite"[^\n]*\n<Objects>)',
                lambda m: m.group(1) + "\n" + t_xml,
                self.xml_text,
                count=1
            )

    def validate_dom_integrity(self) -> bool:
        ET.fromstring(self.xml_text.encode("utf-8"))
        return True

    def save_ms14(self, output_ms14_path: str, save_debug_xml: Optional[str] = None) -> int:
        self.validate_dom_integrity()
        if save_debug_xml:
            os.makedirs(os.path.dirname(os.path.abspath(save_debug_xml)), exist_ok=True)
            with open(save_debug_xml, "wb") as f:
                f.write(self.xml_text.encode("utf-8"))
        sz = self.codec.encode_from_string(self.xml_text, output_ms14_path)
        self.logger.info(f"Validated XML DOM & Encoded Multisim binary -> '{output_ms14_path}' ({sz} bytes)")
        return sz
