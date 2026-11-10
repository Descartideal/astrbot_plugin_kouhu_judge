import math


def atcoder_to_cf(difficulty, config):
    if difficulty is None or not math.isfinite(float(difficulty)):
        return None
    # AtCoder Problems 低于 400 的显示修正；之后为维护者可调的线性近似。
    d = float(difficulty)
    d = 400 * math.exp((d-400)/400) if d < 400 else d
    return round(max(0, d*config.at_scale+config.at_offset))
