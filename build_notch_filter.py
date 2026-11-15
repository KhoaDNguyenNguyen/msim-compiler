import os
import sys

repo_root = os.path.dirname(os.path.abspath(__file__))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from msim_compiler.schematic_builder import MultisimSchematicBuilder
from msim_compiler.document import MS14Document
from msim_compiler.models import HarmonicTone, WindowRect
from msim_compiler.eda_engine import PartPlacementSpec, NetConnectionSpec

def main():
    # Định nghĩa Mạch Lọc Dải Chắn Twin-T (Notch Filter) tại đúng 5000 Hz
    # Công thức: f_notch = 1 / (2*pi*R*C)
    # Chọn C = 10nF -> R = 3.183 kOhm. 
    # Cấu trúc gồm 2 nhánh chữ T mắc song song: Low-pass (R-R-C) và High-pass (C-C-R)
    parts = [
        # Nhánh T trên: R - R - C (C nối GND, giá trị 2C)
        PartPlacementSpec("R1", "RESISTOR_VIRTUAL", "3.183k", x=396, y=243, rotation=0),
        PartPlacementSpec("R2", "RESISTOR_VIRTUAL", "3.183k", x=504, y=243, rotation=0),
        PartPlacementSpec("C1", "CAPACITOR_VIRTUAL", "20n",   x=450, y=315, rotation=90),

        # Nhánh T dưới: C - C - R (R nối GND, giá trị R/2)
        PartPlacementSpec("C2", "CAPACITOR_VIRTUAL", "10n",   x=396, y=387, rotation=0),
        PartPlacementSpec("C3", "CAPACITOR_VIRTUAL", "10n",   x=504, y=387, rotation=0),
        PartPlacementSpec("R3", "RESISTOR_VIRTUAL", "1.591k", x=450, y=459, rotation=90),

        # Tải ngõ ra
        PartPlacementSpec("R4", "RESISTOR_VIRTUAL", "100k",   x=594, y=315, rotation=90),
    ]

    # Khai báo các node (nút) để router A* tự động vẽ dây vuông góc
    nets = [
        NetConnectionSpec("VIN",     [("R1", "1"), ("C2", "1")]),
        NetConnectionSpec("N_MID_R", [("R1", "2"), ("R2", "1"), ("C1", "1")]),
        NetConnectionSpec("N_MID_C", [("C2", "2"), ("C3", "1"), ("R3", "1")]),
        NetConnectionSpec("VOUT",    [("R2", "2"), ("C3", "2"), ("R4", "1")]),
        NetConnectionSpec("0",       [("C1", "2"), ("R3", "2"), ("R4", "2")])
    ]

    # 1. Khởi tạo bản vẽ và đi dây
    builder = MultisimSchematicBuilder.from_testbench()
    builder.synthesize_topology(parts=parts, nets=nets, auto_place=False)

    # Thêm text chú thích thẳng vào bản vẽ
    builder.add_schematic_info_banner(
        title="=== BỘ LỌC DẢI CHẮN TWIN-T (NOTCH FILTER) - CẮT CHÍNH XÁC 5kHz ===",
        lines=[
            "Cấu trúc: Twin-T thụ động | R = 3.183 kOhm, C = 10 nF",
            "Đầu vào (VIN): Máy phát hàm trộn 3 tần số (1kHz, 5kHz, 9kHz) với biên độ bằng nhau.",
            "Đầu ra (VOUT): Tần số 5kHz bị lọc sạch hoàn toàn, FFT Out chỉ còn lại 2 đỉnh 1kHz và 9kHz."
        ],
        x=297, y=576
    )

    # 2. Cấu hình các máy đo
    doc = MS14Document(codec=builder.codec)
    doc.xml_text = builder.xml_text

    # Thiết lập 3 sóng sin đồng thời
    harmonics = [
        HarmonicTone(freq_hz=1000.0, amplitude_vp=1.5),  # Đi qua an toàn
        HarmonicTone(freq_hz=5000.0, amplitude_vp=1.5),  # BỊ TRIỆT TIÊU
        HarmonicTone(freq_hz=9000.0, amplitude_vp=1.5)   # Đi qua an toàn
    ]

    # auto_tune sẽ tự động scale trục X/Y của Tektronix và Bode Plot theo tần số Notch (5kHz)
    doc.auto_tune_all_instruments(
        fc_hz=5000.0, 
        fundamental_hz=1000.0,
        harmonics=harmonics,
        vim_vp=1.5
    )

    # Layout các máy đo chia 6 màn hình
    doc.configure_dashboard_layout([
        WindowRect("XSC1_Oscilloscope",      1020,  30, 560, 430),
        WindowRect("XBP1_BodePlotter",        380,  30, 630, 290),
        WindowRect("XSC2_Tektronix_Time",      10, 520, 620, 420),
        WindowRect("XSC3_Tektronix_FFT_In",   510, 520, 620, 420),
        WindowRect("XSC4_Tektronix_FFT_Out", 1010, 520, 620, 420),
        WindowRect("XFG1_FunctionGenerator",   10,  30, 259, 301)
    ])

    # 3. Biên dịch ra file nhị phân
    os.makedirs("dist", exist_ok=True)
    out_ms14 = "dist/Twin_T_Notch_FFT_Demo.ms14"
    doc.save(out_ms14)
    print(f"✅ Đã tạo thành công file sơ đồ Multisim tại: {out_ms14}")

if __name__ == "__main__":
    main()
