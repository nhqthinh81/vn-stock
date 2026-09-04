"""Kiểm chứng nhãn TÍN HIỆU MẠNH qua chính _get_rule_signal của sản xuất.

Cắt dữ liệu 1 phút VN30F1M thật tại ~400 thời điểm, gọi hàm y hệt app đang
chạy thật. Chuyển từ scratchpad/test_strong.py (phase 27) sang pytest chính
thức — SKIP nếu máy chạy không có feed Amibroker (không phải máy trực bot).
"""
import os

import pandas as pd
import pytest

_FEED = r"C:\AmibrokerData\vn30f1m_1min.csv"


@pytest.mark.skipif(not os.path.exists(_FEED),
                    reason=f"Can feed Amibroker that ({_FEED}) - chi chay duoc tren may truc bot")
def test_strong_label_consistent_with_production_logic():
    from vn_invest.phaisinh_tab import _get_rule_signal, _STRONG_DMH_ATR

    df = pd.read_csv(_FEED)
    df.index = pd.to_datetime(df.Date + " " + df.Time, dayfirst=True)
    for c in ["Open", "High", "Low", "Close", "Volume"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["Close"]).sort_index()

    fails = []

    def chk(c, m):
        if not c:
            fails.append(m)

    n_sig = n_strong = n_checked = 0
    step = max(1, (len(df) - 2000) // 400)
    for end in range(1500, len(df), step):
        sig, reason, det = _get_rule_signal(df.iloc[:end])
        if not det:
            continue
        n_checked += 1
        chk("dmh_atr" in det and "strong" in det, f"thieu key tai {end}")
        da, stg = det.get("dmh_atr"), det.get("strong")
        if da is not None:
            chk(stg == (da >= _STRONG_DMH_ATR), f"strong != nguong tai {end}: {da:.3f} vs {stg}")
        if sig in ("LONG", "SHORT"):
            n_sig += 1
            starts = reason.startswith("\u2b50 M\u1ea0NH \u00b7 ")
            chk(starts == bool(stg), f"reason/strong lech tai {end}: strong={stg}, reason={reason[:40]!r}")
            if stg:
                n_strong += 1
        else:
            chk(not reason.startswith("\u2b50"), f"WAIT ma co sao tai {end}")

    chk(n_sig > 50, "qua it tin hieu de ket luan")
    chk(0.05 <= n_strong / max(n_sig, 1) <= 0.30, "ty le manh lech xa backtest (~14%)")

    assert not fails, "\n" + "\n".join(fails)
