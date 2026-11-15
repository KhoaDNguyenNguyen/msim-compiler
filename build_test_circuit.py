import os
import sys

# Thêm đường dẫn repo hiện tại vào sys.path để import msim_compiler
repo_root = os.path.dirname(os.path.abspath(__file__))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from msim_compiler.synthesizers import SynthesizerRegistry
from msim_compiler.schematic_builder import MultisimSchematicBuilder
from msim_compiler.document import MS14Document
from msim_compiler.models import HarmonicTone, WindowRect

def main():
    # 1. Gọi plugin tạo ma trận mạch cực kì phức tạp (RLC + Transistor + Diode)
    plugin = SynthesizerRegistry.get("rlc_bjt_matrix")
    plan = plugin.generate_topology(fc_hz=2500.0)

    # 2. Xây dựng sơ đồ bằng EDA Engine (không cần tính tọa độ tay, hệ thống sẽ auto_place một phần)
    builder = MultisimSchematicBuilder.from_testbench()
    builder.synthesize_topology(parts=plan.parts, nets=plan.nets, auto_place=False)

    # Gắn banner thông tin xuống dưới cùng của bản vẽ
    builder.add_schematic_info_banner(
        title="=== MẠCH TEST PHỨC TẠP: RLC LADDER + DUAL BJT CLAMP ===",
        lines=[
            "Yêu cầu: Kiểm tra Ngõ vào (VIN) & Ngõ ra (VOUT) trên miền Thời gian (Time) và Tần số (FFT)",
            "Cấu trúc: Mạng lọc RLC bậc cao kết hợp tầng khuếch đại BJT 2N2222A và ghim áp 1N4148."
        ],
        x=297, y=576
    )

    # 3. Can thiệp vào Document DOM để setup máy đo
    doc = MS14Document(codec=builder.codec)
    doc.xml_text = builder.xml_text

    # Tạo tín hiệu đa hài (Multi-tone) để phổ FFT đầu vào/đầu ra hiện rõ các đỉnh phổ
    harmonics = [
        HarmonicTone(freq_hz=1000.0, amplitude_vp=1.6),  # Tần số cơ bản
        HarmonicTone(freq_hz=5000.0, amplitude_vp=0.8),  # Hài bậc 5
        HarmonicTone(freq_hz=9000.0, amplitude_vp=0.5)   # Hài bậc 9
    ]

    # Hàm này tự động cấu hình bộ thông số quét cho máy phát và các Oscillosope
    doc.auto_tune_all_instruments(
        fc_hz=2500.0,
        fundamental_hz=1000.0,
        harmonics=harmonics,
        vim_vp=1.6
    )

    # Sắp xếp các cửa sổ gọn gàng để khi bật Multisim lên là thấy ngay
    doc.configure_dashboard_layout([
        WindowRect("XSC1_Oscilloscope",      1020,  30, 560, 430),
        WindowRect("XBP1_BodePlotter",        380,  30, 630, 290),
        WindowRect("XSC2_Tektronix_Time",      10, 520, 620, 420), # So sánh In/Out (Time)
        WindowRect("XSC3_Tektronix_FFT_In",   510, 520, 620, 420), # Phân tích FFT (VIN)
        WindowRect("XSC4_Tektronix_FFT_Out", 1010, 520, 620, 420), # Phân tích FFT (VOUT)
        WindowRect("XFG1_FunctionGenerator",   10,  30, 259, 301)
    ])

    # 4. Xuất file nhị phân
    os.makedirs("dist", exist_ok=True)
    out_ms14 = "dist/Complex_Test_Time_FFT.ms14"
    doc.save(out_ms14)
    print(f"✅ Đã tạo thành công file sơ đồ Multisim tại: {out_ms14}")

if __name__ == "__main__":
    main()
