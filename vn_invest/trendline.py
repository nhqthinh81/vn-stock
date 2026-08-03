# -*- coding: utf-8 -*-
"""
Trendline tự động từ pivot swing — causal (chỉ dùng dữ liệu quá khứ tại mỗi bar).

Khái niệm:
  - Pivot low tại j: low[j] là min trong cửa sổ [j-wnd, j+wnd].
    Pivot chỉ được XÁC NHẬN tại bar j+wnd (tránh look-ahead bias).
  - Uptrend line  : nối 2 pivot low gần nhất có đáy sau CAO hơn đáy trước.
  - Downtrend line: nối 2 pivot high gần nhất có đỉnh sau THẤP hơn đỉnh trước.

State per-bar (ưu tiên từ trên xuống):
  "break_sup" : close thủng uptrend line > 2%  → cảnh báo xấu
  "brk_res"   : close vượt lên downtrend line (bar trước còn dưới) → đảo chiều sớm
  "test_sup"  : close nằm trong +0..3% phía trên uptrend line → điểm mua đẹp
  "none"      : không có gì đặc biệt

Dùng:
    from vn_invest.trendline import compute_trendline_states
    states = compute_trendline_states(df["high"].values, df["low"].values,
                                      df["close"].values)
"""
from __future__ import annotations

import numpy as np

PIVOT_WND      = 5      # pivot = cực trị trong ±5 bar; xác nhận trễ 5 bar
MAX_PIVOT_AGE  = 120    # chỉ dùng pivot trong 120 bar gần nhất
TEST_SUP_PCT   = 0.03   # test support: close cao hơn line 0..3%
BREAK_SUP_PCT  = 0.02   # break support: close thấp hơn line > 2%
BRK_RES_PCT    = 0.005  # breakout resistance: vượt line ≥ 0.5%


def compute_trendline_states(
    high: np.ndarray, low: np.ndarray, close: np.ndarray,
    wnd: int = PIVOT_WND,
) -> list[str]:
    """Trả list state per-bar, cùng độ dài với close. Causal 100%."""
    n = len(close)
    states = ["none"] * n
    if n < wnd * 2 + 10:
        return states

    piv_lows:  list[tuple[int, float]] = []   # (index, giá)
    piv_highs: list[tuple[int, float]] = []

    for i in range(wnd * 2, n):
        # Xác nhận pivot tại j = i - wnd (đủ wnd bar tương lai — đã qua)
        j = i - wnd
        lo_win = low[j - wnd: j + wnd + 1]
        hi_win = high[j - wnd: j + wnd + 1]
        if low[j] == lo_win.min() and not (piv_lows and piv_lows[-1][0] == j):
            piv_lows.append((j, float(low[j])))
        if high[j] == hi_win.max() and not (piv_highs and piv_highs[-1][0] == j):
            piv_highs.append((j, float(high[j])))

        # Bỏ pivot quá cũ
        piv_lows  = [p for p in piv_lows  if i - p[0] <= MAX_PIVOT_AGE]
        piv_highs = [p for p in piv_highs if i - p[0] <= MAX_PIVOT_AGE]

        c = float(close[i])
        state = "none"

        # ── Uptrend line: 2 pivot low gần nhất, đáy sau cao hơn ──
        if len(piv_lows) >= 2:
            (x1, y1), (x2, y2) = piv_lows[-2], piv_lows[-1]
            if y2 > y1 and x2 > x1:
                slope = (y2 - y1) / (x2 - x1)
                line_val = y2 + slope * (i - x2)
                if line_val > 0:
                    diff = (c - line_val) / line_val
                    if diff < -BREAK_SUP_PCT:
                        state = "break_sup"
                    elif 0 <= diff <= TEST_SUP_PCT:
                        state = "test_sup"

        # ── Downtrend line: 2 pivot high gần nhất, đỉnh sau thấp hơn ──
        # brk_res ưu tiên hơn test_sup (sự kiện hiếm + mạnh hơn), thua break_sup
        if state != "break_sup" and len(piv_highs) >= 2:
            (x1, y1), (x2, y2) = piv_highs[-2], piv_highs[-1]
            if y2 < y1 and x2 > x1:
                slope = (y2 - y1) / (x2 - x1)
                line_val = y2 + slope * (i - x2)
                if line_val > 0:
                    prev_c   = float(close[i - 1])
                    prev_val = y2 + slope * (i - 1 - x2)
                    # prev chưa vượt hẳn buffer (tránh lọt khe khi bar cắt
                    # thật vượt line nhưng chưa đủ buffer, bar sau prev đã trên line)
                    crossed  = (prev_c <= prev_val * (1 + BRK_RES_PCT)
                                and c >= line_val * (1 + BRK_RES_PCT))
                    if crossed:
                        state = "brk_res"

        states[i] = state

    return states
