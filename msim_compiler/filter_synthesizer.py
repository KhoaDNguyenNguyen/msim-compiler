import math
from typing import List
from .logger import get_logger
from .models import BiquadStageSpec, HighOrderFilterDesign

class HighOrderButterworthSynthesizer:
    def __init__(self, base_resistance_ohm: float = 2000.0):
        self.logger = get_logger("FILTER-SYNTH")
        self.base_r = base_resistance_ohm

    @staticmethod
    def compute_butterworth_q_factors(order: int) -> List[float]:
        q_list: List[float] = []
        pairs = order // 2
        for k in range(1, pairs + 1):
            angle = (2.0 * k + order - 1.0) * math.pi / (2.0 * order)
            damping = -2.0 * math.cos(angle)
            q_list.append(1.0 / max(1e-6, damping))
        return sorted(q_list)

    def synthesize(self, order: int, fc_hz: float) -> HighOrderFilterDesign:
        if order < 3:
            raise ValueError("Filter order must be >= 3 for multi-stage synthesis.")
        wc = 2.0 * math.pi * float(fc_hz)
        r_std = self.base_r
        stages: List[BiquadStageSpec] = []
        idx = 1

        if order % 2 == 1:
            c1 = 1.0 / (wc * r_std)
            stages.append(BiquadStageSpec(idx, 1, 0.5, r_std, 0.0, c1, 0.0))
            idx += 1

        for q_val in self.compute_butterworth_q_factors(order):
            c_shunt = 1.0 / (2.0 * q_val * wc * r_std)
            c_fb = (2.0 * q_val) / (wc * r_std)
            stages.append(BiquadStageSpec(idx, 2, q_val, r_std, r_std, c_shunt, c_fb))
            idx += 1

        pre_stages = stages[:-1]
        final_stage = stages[-1]

        spice_lines: List[str] = []
        cur_node = "%t1"
        for st in pre_stages:
            s_idx = st.stage_index
            out_node = f"n_st{s_idx}_out"
            if st.order == 1:
                rc_node = f"n_st{s_idx}_rc"
                spice_lines.append(f"r_st{s_idx} {cur_node} {rc_node} {st.r1_ohm:.4f}")
                spice_lines.append(f"c_st{s_idx} {rc_node} 0 {st.c1_farad:.6e}")
                spice_lines.append(f"e_st{s_idx} {out_node} 0 {rc_node} 0 1.0")
            else:
                mid_node = f"n_st{s_idx}_mid"
                in_plus = f"n_st{s_idx}_plus"
                spice_lines.append(f"r_st{s_idx}a {cur_node} {mid_node} {st.r1_ohm:.4f}")
                spice_lines.append(f"r_st{s_idx}b {mid_node} {in_plus} {st.r2_ohm:.4f}")
                spice_lines.append(f"c_st{s_idx}fb {mid_node} {out_node} {st.c2_farad:.6e}")
                spice_lines.append(f"c_st{s_idx}sh {in_plus} 0 {st.c1_farad:.6e}")
                spice_lines.append(f"e_st{s_idx} {out_node} 0 {in_plus} 0 1.0")
            cur_node = out_node

        spice_lines.append(f"r%p {cur_node} %t2 \x231")
        netlist_str = "\r\n".join(spice_lines)

        self.logger.info(
            f"Synthesized {order}th-Order Butterworth LPF (fc={fc_hz:.1f} Hz, {len(stages)} stages, Slope=-{20*order} dB/dec)"
        )
        return HighOrderFilterDesign(order, fc_hz, stages, netlist_str, final_stage)
