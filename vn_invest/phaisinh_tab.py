"""Tab Phái Sinh — VN30F1M Signal Bot v4 (VWAP trend-follow + quản lý vị thế).

v4 vs v3 — viết lại sau khi backtest 65.786 nến (11/07/2025–13/08/2026, 273 phiên):
- Bảng điểm v3 bị loại bỏ. Đo trên dữ liệu thật, engine v3 bắn 105 tín hiệu/ngày
  với edge ~0 và lỗ -0,44đ/lệnh sau phí. Phân rã từng thành phần cho thấy
  thành phần trọng số CAO nhất (RSI<40/>60, +2.0) lại là thành phần dự báo NGƯỢC
  (alpha -0,29đ), còn thành phần tốt nhất (giá vs VWAP) chỉ được trọng số 0.5.
  Lý do: VN30F1M khung 1 phút là momentum, không phải mean-reversion.
- Engine v4 = lọc xu hướng bằng VWAP phiên + MACD histogram quyết định chiều:
    LONG : Close > VWAP phiên AND MACD histogram tăng
    SHORT: Close < VWAP phiên AND MACD histogram giảm
- Thoát lệnh là một phần của chiến thuật, không phải phụ trợ: giữ tối đa
  _HOLD_BARS nến hoặc chạm SL _SL_ATR_MULT×ATR14, đóng bắt buộc cuối phiên.
  SL chặt kiểu v3 (pivot 10 nến) bị nhiễu quét trước khi edge kịp hiện → âm.
- Chỉ 1 vị thế tại 1 thời điểm. Đây là ràng buộc load-bearing, không phải
  chống spam: nới ra là quay lại 105 lệnh/ngày và mất toàn bộ edge.
- Kết quả v4 — replay toàn bộ lịch sử qua CHÍNH các hàm trong file này
  (phí 0,25đ/lệnh, 1 vị thế tại 1 thời điểm, vào tại giá đóng nến đã đóng):
    2.343 lệnh (8,6/ngày) | win 44,9% | +0,349đ/lệnh sau phí
    +818,2đ tổng = +81.825.000 VND trên 1 hợp đồng | DƯƠNG Ở CẢ 5/5 QUÝ
    LONG +0,265đ/lệnh (n=1.155) | SHORT +0,431đ/lệnh (n=1.188)
    Thoát: chạm SL n=770 · hết hạn giữ n=1.355 · cuối phiên n=218
    Hoà vốn ở mức phí 0,62đ/lệnh — biên an toàn ~2,5 lần so với phí thực tế.
  Ràng buộc 1 vị thế lọc bỏ 33.338 tín hiệu thừa (93,4% số lần có tín hiệu).
- Tín hiệu tính trên nến ĐÃ ĐÓNG (v3 dùng nến đang hình thành: RSI lệch trung
  bình 3,46 điểm, 15,6% số lần điều kiện đảo trạng thái giữa 2 nến).

v2 vs v1:
- Label 3 class (0=SHORT / 1=WAIT / 2=LONG): loại bỏ lỗi "label=0 = SHORT"
- Daily trend: tail(20000) + loại nến ngày chưa hoàn thành
- Features: 8 features (thêm VWAP_Dist, Session_Gap, ATR_Norm)
- Training: class_weight balanced, sparse_categorical_crossentropy
"""
import os
import hashlib
import threading
from datetime import datetime, time as dtime

import numpy as np
import pandas as pd
import streamlit as st

# Dùng chung helper escape với alerter.py (module đó không phụ thuộc streamlit
# nên chạy được cả headless qua alert_watcher.py) — tránh định nghĩa 2 bản.
from .alerter import tg_escape as _tg_escape, fmt_vn as _fvn

# ── Đường dẫn dữ liệu ────────────────────────────────────────────────────────
_POTENTIAL_PATHS = [
    r"D:\AmibrokerData",
    r"C:\AmibrokerData",
    os.path.join(os.getcwd(), "AmibrokerData"),
]
_BASE_DATA_DIR = next((p for p in _POTENTIAL_PATHS if os.path.exists(p)), None)

if _BASE_DATA_DIR:
    _DATA_FILE_1M = os.path.join(_BASE_DATA_DIR, "vn30f1m_1min.csv")
    _MODEL_PATH   = os.path.join(_BASE_DATA_DIR, "lstm_brain.keras")
    _SCALER_PATH  = os.path.join(_BASE_DATA_DIR, "lstm_scaler.pkl")
    _JOURNAL_FILE = os.path.join(_BASE_DATA_DIR, "vn30_ai_journal.csv")
    _CRED_FILE    = os.path.join(_BASE_DATA_DIR, "credentials.json")
else:
    _DATA_FILE_1M = _MODEL_PATH = _SCALER_PATH = _JOURNAL_FILE = _CRED_FILE = None

# ── Hằng số ──────────────────────────────────────────────────────────────────
_SEQ_LEN                 = 30
_DEFAULT_THRESHOLD_BUY   = 0.55   # prob_long >= 55% → LONG
_DEFAULT_THRESHOLD_SELL  = 0.55   # prob_short >= 55% → SHORT
_GSHEET_NAME             = "VN30_Trading_Journal"

# ── Tham số chiến thuật v4 (đã backtest, xem docstring đầu file) ─────────────
# Vùng tham số dương liên tục: hold 25–50 nến × SL 2,5–4,0×ATR. Chọn giữa vùng.
_HOLD_BARS    = 30    # giữ tối đa 30 nến 1 phút rồi thoát theo thị giá
_SL_ATR_MULT  = 3.0   # SL = 3 × ATR14. SL chặt hơn (pivot 10 nến kiểu v3) bị nhiễu quét
_MIN_SL_PTS   = 1.0   # sàn SL khi ATR quá nhỏ (đầu phiên)
_FEE_PTS      = 0.25  # phí + spread + slippage round-trip, quy ra điểm chỉ số
_MAX_STALE_MIN = 5    # dữ liệu cũ hơn ngần này phút → KHÔNG vào lệnh mới
# Cổng biến động: bỏ qua giai đoạn thị trường "chết" — khi ATR quá thấp so với
# nền chung, biên độ không đủ bù phí 0,25đ. Đo trên 65.786 nến, hiệu ứng ĐƠN ĐIỆU
# theo ngưỡng (không phải một ô may mắn), chọn 0,9 vì cải thiện win rate ở CẢ 5/5
# quý và OOS tăng mạnh nhất:
#   không lọc  win 44,8% · +0,356đ/lệnh · OOS +0,280
#   0,9×median win 46,0% · +0,462đ/lệnh · OOS +0,440   (ít hơn 24% số lệnh)
_MIN_ATR_RATIO = 0.9
# Nguong TIN HIEU MANH: |MACD hist - MACD hist truoc| >= nguong nay x ATR14.
# Do tren 273 phien (research_v5b): quet 0.06..0.14 don dieu tang toi dinh bang
# phang 0.09-0.12; tai 0.10 nhom manh (~1 lenh/ngay, ~14% so lenh) dat
# +1,454d/lenh (IS +1,660 / OOS +1,301, 5/5 quy duong, p hoan vi = 0.007),
# nhom thuong chi +0,285d/lenh. CHI GAN NHAN de nguoi theo lenh thu cong uu
# tien — KHONG loc bo lenh thuong (tong cua chung van +459d).
_STRONG_DMH_ATR = 0.10
_ATR_MED_BARS  = 500

# Chốt lời tuỳ chọn, tính theo bội số RỦI RO (R = khoảng cách tới SL).
# Mặc định BẬT theo yêu cầu của user (19/08/2026). Lưu ý số liệu: A/B cùng harness
# cho +9,8% tổng, nhưng +69,1 trong +71,3đ đến từ riêng quý 2025Q3 — bốn quý còn
# lại cộng lại +2,2đ ≈ 0, và kết quả không đơn điệu theo mức TP (2R/3R/4R lộn xộn).
# Tức lợi ích CHƯA được chứng minh là lặp lại được; tắt đi cũng không mất gì.
_TP_R_MULT = 3.0
_PT_VALUE_VND = 100_000   # 1 điểm VN30F1M = 100.000đ / hợp đồng

# Phân vị PnL của các lệnh THẮNG trong backtest v4 (điểm, đã trừ phí, n=1.065).
# Dùng làm MỐC THAM CHIẾU để biết lệnh đang chạy tốt/xấu — KHÔNG phải lệnh chốt.
#
# Engine v4 CỐ Ý không có take-profit. Đo trên chính cấu hình đã kiểm chứng
# (giữ ≤30 nến, SL 3×ATR), thêm TP ở mọi mức đều làm KÉM đi:
#     không TP +0,356đ/lệnh | 3R +0,347 | 2R +0,276 | 1,5R +0,218 | 1R +0,112
# Lý do: lệnh thắng chạy xa (trung vị +4,25đ, 10% vượt +12,91đ) — cắt đuôi lãi
# đó thì không còn gì bù cho 55% lệnh thua.
_WIN_PCTL = {"trung vị": 4.25, "top 30%": 7.25, "top 10%": 12.91}

# Features v2 (8 features) — KHÔNG thay đổi thứ tự (scaler phụ thuộc)
_FEATURES = ["RSI", "MACD", "Dist_EMA", "Log_Ret", "Vol_Change",
             "VWAP_Dist", "Session_Gap", "ATR_Norm"]

# ── Giờ giao dịch VN30F1M ────────────────────────────────────────────────────
_SESSION1_START        = dtime(9, 0)
_SESSION1_END          = dtime(11, 30)
_SESSION2_START        = dtime(13, 0)
_SESSION2_END          = dtime(14, 45)
_WARN_BEFORE_CLOSE_MIN = 5


def _in_trading_session(now: dtime | None = None) -> tuple[bool, str]:
    if now is None:
        now = datetime.now().time()
    if _SESSION1_START <= now <= _SESSION1_END:
        dt_now  = datetime.combine(datetime.today(), now)
        dt_end1 = datetime.combine(datetime.today(), _SESSION1_END)
        mins_left = (dt_end1 - dt_now).seconds // 60
        if mins_left <= _WARN_BEFORE_CLOSE_MIN:
            return True, f"⚠️ Phiên 1 đóng sau {mins_left} phút"
        return True, f"🟢 Phiên 1 (09:00–11:30)"
    if _SESSION2_START <= now <= _SESSION2_END:
        dt_now  = datetime.combine(datetime.today(), now)
        dt_end2 = datetime.combine(datetime.today(), _SESSION2_END)
        mins_left = (dt_end2 - dt_now).seconds // 60
        if mins_left <= _WARN_BEFORE_CLOSE_MIN:
            return True, f"⚠️ Phiên 2 đóng sau {mins_left} phút"
        return True, f"🟢 Phiên 2 (13:00–14:45)"
    if now < _SESSION1_START:
        return False, "⏳ Chờ mở phiên 1 (09:00)"
    if _SESSION1_END < now < _SESSION2_START:
        return False, "⏸ Nghỉ trưa (11:30–13:00)"
    return False, "🔴 Ngoài giờ giao dịch (>14:45)"


# ── Load AI model ─────────────────────────────────────────────────────────────
@st.cache_resource
def _load_ai_system():
    if not (_SCALER_PATH and _MODEL_PATH and
            os.path.exists(_SCALER_PATH) and os.path.exists(_MODEL_PATH)):
        return None, None
    try:
        import joblib
        from tensorflow.keras.models import load_model  # type: ignore
        scaler = joblib.load(_SCALER_PATH)
        model  = load_model(_MODEL_PATH)
        return scaler, model
    except Exception as e:
        return None, str(e)


# ── Feature helpers ───────────────────────────────────────────────────────────

def _clean_num(x):
    try:
        return float(str(x).replace(",", ""))
    except Exception:
        return np.nan


def _load_df_1m(path: str):
    if not path or not os.path.exists(path):
        return None
    try:
        df = pd.read_csv(path)
        for c in ["Open", "High", "Low", "Close", "Volume"]:
            if c in df.columns:
                df[c] = df[c].apply(_clean_num)
        if "Date" in df.columns and "Time" in df.columns:
            df.index = pd.to_datetime(df["Date"] + " " + df["Time"], dayfirst=True)
        df = df.dropna(subset=["Close"])
        df.sort_index(inplace=True)
        # Chỉ giữ 25000 nến gần nhất — đủ ~66 ngày, giảm memory + tốc độ downstream
        return df.tail(25000).copy()
    except Exception:
        return None


def _calc_vwap(df: pd.DataFrame) -> pd.Series:
    """VWAP theo từng ngày giao dịch (reset về 0 mỗi ngày)."""
    d = df.copy()
    d["_date"]    = d.index.date
    d["_typical"] = (d["High"] + d["Low"] + d["Close"]) / 3
    d["_tpv"]     = d["_typical"] * d["Volume"].clip(lower=0)
    cum_tpv = d.groupby("_date")["_tpv"].cumsum()
    cum_vol = d.groupby("_date")["Volume"].cumsum().clip(lower=1)
    return cum_tpv / cum_vol


def _calculate_features(df: pd.DataFrame) -> pd.DataFrame:
    """Tính 8 features v2. Drop warmup rows — KHÔNG fillna(0) trừ hàng đầu."""
    try:
        import pandas_ta as ta  # type: ignore
    except ImportError:
        raise ImportError("pip install pandas_ta")

    df = df.copy()

    # --- Core indicators ---
    df["RSI"]      = ta.rsi(df["Close"], 14)
    _macd          = ta.macd(df["Close"])
    df["MACD"]     = _macd.iloc[:, 0] if _macd is not None else np.nan
    df["EMA_34"]   = ta.ema(df["Close"], 34)
    df["Dist_EMA"] = (df["Close"] - df["EMA_34"]) / df["Close"].clip(lower=1)
    df["Log_Ret"]  = np.log(df["Close"] / df["Close"].shift(1))
    df["Vol_Change"] = df["Volume"].pct_change().clip(-5, 5)

    # --- ATR normalized ---
    _atr = ta.atr(df["High"], df["Low"], df["Close"], 14)
    df["ATR_Norm"] = (_atr / df["Close"].clip(lower=1)).fillna(0)

    # --- VWAP distance (per-session, reset daily) ---
    vwap = _calc_vwap(df)
    df["VWAP_Dist"] = (df["Close"] - vwap) / df["Close"].clip(lower=1)

    # --- Session gap: % thay đổi từ giá mở cửa phiên hôm nay ---
    df["_date"]        = df.index.date
    df["Session_Open"] = df.groupby("_date")["Open"].transform("first")
    df["Session_Gap"]  = (df["Close"] - df["Session_Open"]) / df["Session_Open"].clip(lower=1)

    # Drop warmup NaN (RSI cần 14 bars, MACD cần 26, EMA34 cần 34)
    df.dropna(subset=["RSI", "MACD", "EMA_34"], inplace=True)
    # Hàng đầu tiên sau warmup có thể còn NaN ở Log_Ret/Vol_Change
    df[["Log_Ret", "Vol_Change", "VWAP_Dist", "Session_Gap", "ATR_Norm"]] = (
        df[["Log_Ret", "Vol_Change", "VWAP_Dist", "Session_Gap", "ATR_Norm"]].fillna(0)
    )
    return df


def _get_ai_prediction(df_1m: pd.DataFrame, scaler, model) -> tuple[float, float, str | None]:
    """Trả (prob_long, prob_short, warning).

    Model v2 (3-class): output [p_short, p_wait, p_long]
    Model v1 (binary):  output [p_long] — SHORT bị block hoàn toàn.
    """
    if scaler is None or model is None or len(df_1m) < _SEQ_LEN + 60:
        return 0.5, 0.0, None
    try:
        df_f = _calculate_features(df_1m.tail(300))
        if len(df_f) < _SEQ_LEN:
            return 0.5, 0.0, "Không đủ hàng sau drop warmup"

        # Dùng features có trong df_f (hỗ trợ cả model v1 với 5 features)
        is_v2 = hasattr(scaler, "n_features_in_") and scaler.n_features_in_ == len(_FEATURES)
        feat_cols = _FEATURES if is_v2 else ["RSI", "MACD", "Dist_EMA", "Log_Ret", "Vol_Change"]
        available = [c for c in feat_cols if c in df_f.columns]
        if len(available) < len(feat_cols):
            return 0.5, 0.0, f"Thiếu features: {set(feat_cols) - set(available)}"

        seq = df_f[available].tail(_SEQ_LEN).values

        # Kiểm tra out-of-range
        warn = None
        if hasattr(scaler, "data_min_") and hasattr(scaler, "data_max_"):
            lo, hi = scaler.data_min_, scaler.data_max_
            # Nới biên theo ĐỘ RỘNG range, không nhân trực tiếp vào lo/hi:
            # với feature âm (Log_Ret) thì lo*0.7 > lo nên phép cũ báo nhầm
            # chính các giá trị vẫn nằm trong range.
            pad = (hi - lo) * 0.3
            if seq.shape[1] == len(lo) and (np.any(seq < lo - pad) or np.any(seq > hi + pad)):
                warn = "⚠️ Feature nằm ngoài range lúc train — model có thể không chính xác"

        scaled = scaler.transform(seq).reshape(1, _SEQ_LEN, len(available))
        pred   = model.predict(scaled, verbose=0)[0]

        n_out = model.output_shape[-1]
        if n_out == 3:
            # v2: [p_short, p_wait, p_long]
            prob_short = float(pred[0])
            prob_long  = float(pred[2])
        else:
            # v1 binary: p_long only. KHÔNG sinh SHORT từ model cũ (bug label).
            prob_long  = float(pred[0])
            prob_short = 0.0
            if warn is None:
                warn = "ℹ️ Model v1 (binary) — SHORT bị tắt. Retrain để dùng model v2."

        return prob_long, prob_short, warn

    except Exception as e:
        return 0.5, 0.0, f"Lỗi AI: {e}"


def _closed_bars(df_1m: pd.DataFrame, n: int = 300) -> pd.DataFrame | None:
    """Trả n nến ĐÃ ĐÓNG gần nhất — bỏ nến cuối đang hình thành.

    v3 quyết định trên nến đang chạy: đo trên dữ liệu thật, RSI của nến chưa đóng
    lệch trung bình 3,46 điểm so với nến đã đóng và 15,6% số lần điều kiện đảo
    trạng thái giữa 2 nến liên tiếp — tức tín hiệu bị chốt theo 1 tick đầu nến.
    """
    if df_1m is None or len(df_1m) < 2:
        return None
    closed = df_1m.iloc[:-1]
    return closed.tail(n).copy() if len(closed) else None


def _get_rule_signal(df_1m: pd.DataFrame) -> tuple[str, str, dict]:
    """Rule engine v4 — lọc xu hướng bằng VWAP phiên, MACD histogram quyết định chiều.

      LONG : Close > VWAP phiên  AND  MACD histogram tăng so với nến trước
      SHORT: Close < VWAP phiên  AND  MACD histogram giảm so với nến trước

    Hai điều kiện, không có bảng điểm. Bảng điểm v3 gộp 7 thành phần rồi lấy
    ngưỡng 2.5, nhưng đo alpha từng thành phần trên 65.786 nến cho thấy chỉ
    2 thành phần này có alpha dương bền vững ở cả 2 nửa dữ liệu; 5 thành phần
    còn lại trung tính hoặc dự báo ngược. Cộng chúng vào chỉ làm loãng tín hiệu.

    Quyết định trên nến ĐÃ ĐÓNG. Không dùng trend đa khung: đo được alpha của
    trend=±1 xấp xỉ 0 (IS +0,006 / OOS -0,061) — nó chỉ phản ánh drift thị trường.

    Trả (signal, reason, detail). `detail` là giá trị thô của 2 điều kiện để UI
    hiển thị được "đang thiếu điều kiện nào" thay vì chỉ báo WAIT trống không.
    """
    empty: dict = {}
    try:
        import pandas_ta as ta  # type: ignore
    except ImportError:
        return "WAIT", "Thiếu pandas_ta", empty

    if df_1m is None or len(df_1m) < 60:
        return "WAIT", "Chưa đủ dữ liệu", empty

    try:
        # tail(300) > số nến 1 phiên (~241) nên VWAP phiên vẫn tính đủ từ đầu phiên
        recent = _closed_bars(df_1m, 300)
        if recent is None or len(recent) < 40:
            return "WAIT", "Chưa đủ nến đã đóng", empty

        _macd = ta.macd(recent["Close"])
        if _macd is None or _macd.shape[1] < 3:
            return "WAIT", "MACD chưa tính được", empty
        recent["MACD_hist"] = _macd.iloc[:, 2]
        recent["VWAP"]      = _calc_vwap(recent)
        recent.dropna(subset=["MACD_hist", "VWAP"], inplace=True)
        if len(recent) < 2:
            return "WAIT", "Chưa đủ sau warmup", empty

        last, prev = recent.iloc[-1], recent.iloc[-2]
        close  = float(last["Close"])
        vwap   = float(last["VWAP"])
        mh     = float(last["MACD_hist"])
        mh_p   = float(prev["MACD_hist"])
        bar_at = last.name.strftime("%H:%M")

        above  = close > vwap
        rising = mh > mh_p
        _atr, _med = _get_atr_state(df_1m)
        _ratio = (_atr / _med) if (_atr and _med) else None
        _vol_ok = (_ratio is None) or (_ratio >= _MIN_ATR_RATIO)
        # Do manh cua cu tang toc MACD hist, chuan hoa theo ATR — xem chu thich
        # tai _STRONG_DMH_ATR. Chi la NHAN uu tien, khong tham gia dieu kien.
        _dmh_atr = (abs(mh - mh_p) / _atr) if (_atr and _atr > 0) else None
        _strong  = bool(_dmh_atr is not None and _dmh_atr >= _STRONG_DMH_ATR)
        detail = {"close": close, "vwap": vwap, "mh": mh, "mh_prev": mh_p,
                  "above": above, "rising": rising, "bar": bar_at,
                  "atr": _atr, "atr_med": _med, "atr_ratio": _ratio,
                  "vol_ok": _vol_ok, "dmh_atr": _dmh_atr, "strong": _strong}
        ctx = (f"nến đóng {bar_at} · giá {_fvn(close, 1)} {'trên' if above else 'dưới'} "
               f"VWAP {_fvn(vwap, 1)} · MACD hist {_fvn(mh, 2, signed=True)} "
               f"{'tăng' if rising else 'giảm'}")

        # Cổng biến động chặn TRƯỚC: hai điều kiện có đồng pha cũng không vào lệnh
        # khi thị trường quá lặng — biên độ không đủ bù phí.
        if not _vol_ok:
            return "WAIT", (f"Biến động quá thấp (ATR {_fvn(_atr, 2)} = "
                            f"{_ratio:.0%} nền, cần ≥{_MIN_ATR_RATIO:.0%}) — {ctx}"), detail
        _tag = "⭐ MẠNH · " if _strong else ""
        if above and rising:
            return "LONG",  f"{_tag}Trên VWAP + MACD hist tăng — {ctx}", detail
        if (not above) and (not rising):
            return "SHORT", f"{_tag}Dưới VWAP + MACD hist giảm — {ctx}", detail
        return "WAIT", f"Chưa đồng pha — {ctx}", detail

    except Exception as e:
        return "WAIT", f"Lỗi rule engine: {e}", empty


def _data_age_min(df_1m: pd.DataFrame) -> float | None:
    """Số phút kể từ nến cuối cùng trong dữ liệu tới bây giờ.

    Dùng để chặn vào lệnh khi nguồn dữ liệu đã dừng. Không thể tin vào mtime
    của file: AFL vẫn ghi đè file đều đặn ngay cả khi Amibroker không còn
    nhận dữ liệu mới, nên file "mới" mà nội dung vẫn là nến của hôm trước.
    """
    if df_1m is None or len(df_1m) == 0:
        return None
    try:
        return (pd.Timestamp.now() - df_1m.index[-1]).total_seconds() / 60
    except Exception:
        return None


def _get_atr_state(df_1m: pd.DataFrame) -> tuple[float | None, float | None]:
    """Trả (ATR14 hiện tại, trung vị ATR gần đây) trên nến ĐÃ ĐÓNG.

    Trung vị dùng làm nền so sánh cho cổng biến động — xem _MIN_ATR_RATIO.
    """
    try:
        import pandas_ta as ta  # type: ignore
        recent = _closed_bars(df_1m, _ATR_MED_BARS + 200)
        if recent is None or len(recent) < 40:
            return None, None
        s = ta.atr(recent["High"], recent["Low"], recent["Close"], length=14)
        if s is None:
            return None, None
        s = s.dropna()
        if s.empty:
            return None, None
        atr = float(s.iloc[-1])
        med = float(s.tail(_ATR_MED_BARS).median())
        if not (np.isfinite(atr) and atr > 0):
            return None, None
        return atr, (med if np.isfinite(med) and med > 0 else None)
    except Exception:
        return None, None


def _get_atr(df_1m: pd.DataFrame) -> float | None:
    """ATR14 trên nến đã đóng — dùng để đặt SL."""
    try:
        import pandas_ta as ta  # type: ignore
        recent = _closed_bars(df_1m, 500)
        if recent is None or len(recent) < 20:
            return None
        s = ta.atr(recent["High"], recent["Low"], recent["Close"], length=14)
        if s is None or s.dropna().empty:
            return None
        atr = float(s.dropna().iloc[-1])
        return atr if np.isfinite(atr) and atr > 0 else None
    except Exception:
        return None


def _get_trend_full(df_1m: pd.DataFrame) -> tuple[int, str, dict]:
    """Multi-TF trend + chi tiết từng khung — một lần resample duy nhất.

    Trả về: (trend: int, trend_text: str, tf_detail: dict)
    - trend: -1/0/1
    - tf_detail: {"1m": {...}, "15m": {...}, "1h": {...}}
    """
    empty_tf = {"trend": 0, "ema": None, "close": None, "label": "—"}
    tf_detail = {"1m": empty_tf.copy(), "15m": empty_tf.copy(), "1h": empty_tf.copy()}

    try:
        import pandas_ta as ta  # type: ignore
    except ImportError:
        return 0, "Thiếu pandas_ta", tf_detail

    if df_1m is None or len(df_1m) < 200:
        return 0, "Không đủ dữ liệu", tf_detail

    agg    = {"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}
    # df_1m đã bị giới hạn tail(25000) khi load → không cần tail() lại
    recent = df_1m

    # Resample một lần, dùng chung cho trend + tf_detail
    df_15 = recent.resample("15min").agg(agg).dropna()
    df_1h = recent.resample("60min").agg(agg).dropna()
    df_d  = recent.resample("1D").agg(agg).dropna()

    # Loại nến ngày chưa hoàn thành
    today = pd.Timestamp.now().normalize()
    df_d  = df_d[df_d.index < today]

    # ── tf_detail: 1m ─────────────────────────────────────────────────────────
    _1m_df = recent.tail(200).copy()
    _1m_df["EMA10"] = ta.ema(_1m_df["Close"], 10)
    _1m_df.dropna(subset=["EMA10"], inplace=True)
    if not _1m_df.empty:
        last = _1m_df.iloc[-1]
        t1m  = 1 if last["Close"] > last["EMA10"] else -1
        tf_detail["1m"] = {"trend": t1m, "ema": float(last["EMA10"]),
                            "close": float(last["Close"]),
                            "label": "↑ Tăng" if t1m == 1 else "↓ Giảm"}

    # ── tf_detail: 15m ────────────────────────────────────────────────────────
    if not df_15.empty:
        _15m = df_15.copy()
        _15m["EMA10"] = ta.ema(_15m["Close"], 10)
        _15m.dropna(subset=["EMA10"], inplace=True)
        if not _15m.empty:
            last = _15m.iloc[-1]
            t15  = 1 if last["Close"] > last["EMA10"] else -1
            tf_detail["15m"] = {"trend": t15, "ema": float(last["EMA10"]),
                                 "close": float(last["Close"]),
                                 "label": "↑ Tăng" if t15 == 1 else "↓ Giảm"}

    # ── tf_detail: 1h ─────────────────────────────────────────────────────────
    if not df_1h.empty:
        _1h = df_1h.copy()
        _1h["EMA20"] = ta.ema(_1h["Close"], 20)
        _1h.dropna(subset=["EMA20"], inplace=True)
        if not _1h.empty:
            last = _1h.iloc[-1]
            t1h  = 1 if last["Close"] > last["EMA20"] else -1
            tf_detail["1h"] = {"trend": t1h, "ema": float(last["EMA20"]),
                                "close": float(last["Close"]),
                                "label": "↑ Tăng" if t1h == 1 else "↓ Giảm"}

    # ── Multi-TF trend score ──────────────────────────────────────────────────
    if len(df_15) < 20 or len(df_1h) < 10 or len(df_d) < 25:
        missing = []
        if len(df_d)  < 25: missing.append(f"daily={len(df_d)}<25")
        if len(df_1h) < 10: missing.append(f"1h={len(df_1h)}<10")
        if len(df_15) < 20: missing.append(f"15m={len(df_15)}<20")
        return 0, f"Đang gom dữ liệu đa khung ({', '.join(missing)})", tf_detail

    _d  = df_d.copy();  _d["EMA20"]  = ta.ema(_d["Close"], 20);  _d.dropna(subset=["EMA20"],  inplace=True)
    _1h = df_1h.copy(); _1h["EMA20"] = ta.ema(_1h["Close"], 20); _1h.dropna(subset=["EMA20"], inplace=True)
    _15 = df_15.copy(); _15["EMA10"] = ta.ema(_15["Close"], 10); _15.dropna(subset=["EMA10"], inplace=True)

    if not (_d.shape[0] and _1h.shape[0] and _15.shape[0]):
        return 0, "EMA chưa đủ nến warmup", tf_detail

    t_d  = 1 if _d.iloc[-1]["Close"]  > _d.iloc[-1]["EMA20"]  else -1
    t_1h = 1 if _1h.iloc[-1]["Close"] > _1h.iloc[-1]["EMA20"] else -1
    t_15 = 1 if _15.iloc[-1]["Close"] > _15.iloc[-1]["EMA10"] else -1
    score = t_d + t_1h + t_15

    if score ==  3: return  1, "UPTREND mạnh (D/H/15p đồng pha tăng)", tf_detail
    if score ==  2: return  1, "UPTREND (D+H tăng, 15p nhiễu)",        tf_detail
    if score ==  1: return  0, "Trung tính thiên tăng",                 tf_detail
    if score == -1: return  0, "Trung tính thiên giảm",                 tf_detail
    if score == -2: return -1, "DOWNTREND (D+H giảm, 15p nhiễu)",      tf_detail
    if score == -3: return -1, "DOWNTREND mạnh (D/H/15p đồng pha giảm)", tf_detail
    return 0, "Xu hướng trung tính", tf_detail


def _get_stop_levels(df_1m: pd.DataFrame) -> dict:
    """Tính Buy Stop / Sell Stop từ pivot gần nhất (10 nến) + SL đề xuất.

    - Entry: vượt đỉnh/đáy 10 nến gần nhất + buffer nhỏ (0.2×ATR, tối thiểu 0.5đ)
    - SL Buy Stop  = đáy thấp nhất 10 nến - buffer  (rủi ro ≈ 0.4×ATR)
    - SL Sell Stop = đỉnh cao nhất 10 nến + buffer
    Thiết kế cho scalping 1m — vùng chặt, R/R thường 1:1.5+
    """
    empty = {"buy_stop_price": None, "buy_stop_sl": None,
             "sell_stop_price": None, "sell_stop_sl": None, "atr": None}
    if df_1m is None or len(df_1m) < 20:
        return empty
    try:
        import pandas_ta as ta  # type: ignore
        recent = df_1m.tail(500).copy()
        atr_s = ta.atr(recent["High"], recent["Low"], recent["Close"], length=14)
        if atr_s is None or atr_s.dropna().empty:
            return empty
        atr = float(atr_s.dropna().iloc[-1])
        buf = max(round(atr * 0.2, 1), 0.5)   # buffer vào lệnh: 0.2×ATR, tối thiểu 0.5đ
        sl_buf = max(round(atr * 0.2, 1), 0.5) # buffer SL: tương tự

        w = recent.tail(10)
        pivot_high = float(w["High"].max())
        pivot_low  = float(w["Low"].min())

        return {
            "buy_stop_price":  round(pivot_high + buf, 1),
            "buy_stop_sl":     round(pivot_low  - sl_buf, 1),
            "sell_stop_price": round(pivot_low  - buf, 1),
            "sell_stop_sl":    round(pivot_high + sl_buf, 1),
            "atr":             round(atr, 1),
        }
    except Exception:
        return empty


# ── Bền hoá trạng thái qua lần tải lại trang ─────────────────────────────────
# st.session_state chỉ sống trong 1 phiên trình duyệt. Tải lại trang (F5) là mất
# sạch, kéo theo 2 hậu quả nặng chứ không chỉ "mất nhật ký":
#   1. Vị thế đang mở bị bỏ rơi — journal có dòng MỞ nhưng không bao giờ có dòng
#      ĐÓNG, nên lệnh đó biến mất khỏi báo cáo lãi/lỗ.
#   2. ps_trade_seq reset về 0 → lệnh mới lại mang mã #1 trùng với lệnh cũ trong
#      ngày. load_trades() gộp theo trade_id nên ghép giá vào của lệnh này với
#      giá ra của lệnh kia ⇒ PnL sai hoàn toàn.
# Vì vậy vị thế + bộ đếm mã lệnh phải nằm trên đĩa, không nằm trong session.

_PS_STATE_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "ps_state.json"
)


# ── Ghi nhớ tuỳ chọn giao diện qua các lần tải lại ───────────────────────────
# Tách khỏi ps_state.json: đây là SỞ THÍCH người dùng, vòng đời khác hẳn trạng
# thái nghiệp vụ (vị thế, mã lệnh). Xem tasks/lessons.md mục 11.
_PS_PREF_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "ps_ui_pref.json"
)

# key session_state -> giá trị mặc định khi chưa có file
_PS_PREFS = {
    "ps_auto_toggle": False,
    "ps_signal_mode": "Rule-based",
    "ps_strict_trend": False,
    "ps_thr_buy":  int(_DEFAULT_THRESHOLD_BUY  * 100),
    "ps_thr_sell": int(_DEFAULT_THRESHOLD_SELL * 100),
    "ps_notify_counter": True,    # báo Telegram khi có tín hiệu ngược chiều lệnh
    "ps_use_tp": True,            # bật chốt lời tự động ở _TP_R_MULT × rủi ro
}


def _load_ui_pref() -> dict:
    import json
    pref = dict(_PS_PREFS)
    try:
        if os.path.exists(_PS_PREF_FILE):
            with open(_PS_PREF_FILE, encoding="utf-8") as f:
                saved = json.load(f)
            pref.update({k: v for k, v in saved.items() if k in _PS_PREFS})
    except Exception:
        pass
    return pref


def _save_ui_pref() -> None:
    """Ghi lại sở thích hiện tại. Gọi từ on_change nên chỉ chạy khi user đổi."""
    import json
    data = {k: st.session_state.get(k, d) for k, d in _PS_PREFS.items()}
    try:
        os.makedirs(os.path.dirname(_PS_PREF_FILE), exist_ok=True)
        with open(_PS_PREF_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception:
        pass


# ── Khoá một-phiên-ghi ───────────────────────────────────────────────────────
# session_state là RIÊNG từng tab trình duyệt. Hai tab cùng mở tab Phái Sinh sẽ
# chạy engine song song, mỗi bên có ps_position và ps_trade_seq riêng, cùng ghi
# vào một journal. Hậu quả đo được trên dữ liệu thật ngày 20/08: 6/56 dòng trùng,
# 4 mã lệnh bị dùng lại cho hai lệnh khác nhau, 3 cặp vị thế chồng nhau — khiến
# load_trades() ghép giá vào của lệnh này với giá ra của lệnh kia.
# Vì vậy chỉ MỘT phiên được quyền ghi; phiên còn lại chuyển sang chỉ xem.
_OWNER_TTL_SEC = 90        # chủ sở hữu im lặng quá ngần này giây thì mất quyền


def _session_token() -> str:
    """Mã định danh phiên trình duyệt hiện tại (sinh 1 lần, sống theo session)."""
    tok = st.session_state.get("ps_session_token")
    if not tok:
        import uuid
        tok = uuid.uuid4().hex[:12]
        st.session_state["ps_session_token"] = tok
    return tok


def _read_owner() -> tuple[str, float]:
    """Trả (token chủ sở hữu, số giây kể từ nhịp tim cuối). ("", inf) nếu trống."""
    import json
    try:
        with open(_PS_STATE_FILE, encoding="utf-8") as f:
            own = (json.load(f) or {}).get("owner") or {}
        hb = pd.Timestamp(own["heartbeat"])
        return str(own.get("token", "")), (pd.Timestamp.now() - hb).total_seconds()
    except Exception:
        return "", float("inf")


def _reload_from_disk() -> None:
    """Nạp lại vị thế + bộ đếm + nhật ký từ đĩa, đè lên bản trong RAM.

    BẮT BUỘC gọi khi một phiên dự phòng tiếp quản quyền điều khiển. Phiên đó chỉ
    đọc đĩa một lần lúc khởi động, nên `ps_position` trong RAM có thể đã cũ hàng
    giờ. Nếu tiếp quản mà không nạp lại: không thấy lệnh phiên kia đang mở ⇒ mở
    thêm lệnh thứ hai, còn lệnh cũ mãi kẹt với dòng MỞ không có ĐÓNG.
    """
    _pos, _seq = _load_ps_state()
    _log, _max = _restore_log_from_journal()
    if _log:
        st.session_state["ps_log_history"] = _log
    st.session_state["ps_trade_seq"] = max(
        _seq, _max, int(st.session_state.get("ps_trade_seq", 0) or 0))
    st.session_state["ps_position"] = _pos   # kể cả None — phiên kia có thể đã đóng


def _claim_ownership() -> tuple[bool, float]:
    """Giành/gia hạn quyền ghi. Trả (là_chủ_sở_hữu, tuổi nhịp tim của phiên kia).

    Gọi ở đầu mỗi lần render bảng điều khiển. Phiên đang giữ quyền tự gia hạn;
    phiên khác chỉ giành được khi chủ cũ đã im lặng quá _OWNER_TTL_SEC — hoặc khi
    người dùng bấm nút giành quyền ngay.
    """
    me = _session_token()
    tok, age = _read_owner()
    forced = bool(st.session_state.pop("ps_force_claim", False))
    if tok and tok != me and age < _OWNER_TTL_SEC and not forced:
        st.session_state["ps_is_owner"] = False
        return False, age

    # Chuyển từ "chỉ xem" sang "điều khiển" ⇒ bản trong RAM đã cũ, phải nạp lại.
    _took_over = not st.session_state.get("ps_is_owner", False)
    st.session_state["ps_is_owner"] = True
    if _took_over:
        _reload_from_disk()
        st.session_state["ps_took_over_at"] = pd.Timestamp.now()
        _save_ps_state()
        return True, age

    # Gia hạn nhịp tim có tiết chế: fragment chạy 1 lần/giây, ghi đĩa mỗi giây là
    # thừa. TTL 90s ≫ 10s nên không có nguy cơ bị coi là hết hạn oan.
    _last = st.session_state.get("ps_last_hb")
    if _last is None or (pd.Timestamp.now() - _last).total_seconds() >= 10:
        st.session_state["ps_last_hb"] = pd.Timestamp.now()
        _save_ps_state()
    return True, age


def _can_trade() -> bool:
    """Phiên này có được phép mở/đóng lệnh và ghi journal không."""
    return bool(st.session_state.get("ps_is_owner", False))


def _save_ps_state() -> None:
    """Ghi vị thế + bộ đếm mã lệnh ra đĩa. Gọi sau MỌI lần mở/đóng lệnh."""
    import json
    if not _can_trade():
        return                   # phiên chỉ xem không được giành đè trạng thái
    pos = st.session_state.get("ps_position")
    data = {"trade_seq": int(st.session_state.get("ps_trade_seq", 0)), "position": None,
            "owner": {"token": _session_token(),
                      "heartbeat": pd.Timestamp.now().isoformat()}}
    if pos:
        data["position"] = dict(pos,
                                entry_ts=pos["entry_ts"].isoformat(),
                                last_ts=pos["last_ts"].isoformat())
    try:
        os.makedirs(os.path.dirname(_PS_STATE_FILE), exist_ok=True)
        with open(_PS_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception:
        pass


def _load_ps_state() -> tuple[dict | None, int]:
    """Đọc lại vị thế + bộ đếm. Trả (position | None, trade_seq)."""
    import json
    if not os.path.exists(_PS_STATE_FILE):
        return None, 0
    try:
        with open(_PS_STATE_FILE, encoding="utf-8") as f:
            data = json.load(f)
        pos = data.get("position")
        if pos:
            pos = dict(pos,
                       entry_ts=pd.Timestamp(pos["entry_ts"]),
                       last_ts=pd.Timestamp(pos["last_ts"]))
        return pos, int(data.get("trade_seq", 0))
    except Exception:
        return None, 0


def _restore_log_from_journal() -> tuple[list, int]:
    """Dựng lại nhật ký phiên + mã lệnh lớn nhất từ journal của HÔM NAY.

    Nhờ vậy tải lại trang không làm trống bảng "Nhật Ký Lệnh (Session)", và mã
    lệnh tiếp tục tăng thay vì quay về #1 gây trùng.
    """
    if not (_JOURNAL_FILE and os.path.exists(_JOURNAL_FILE)):
        return [], 0
    try:
        df = pd.read_csv(_JOURNAL_FILE, on_bad_lines="skip", engine="python")
    except Exception:
        return [], 0
    if df.empty or "trade_id" not in df.columns:
        return [], 0

    df = df[pd.to_numeric(df["trade_id"], errors="coerce").notna()].copy()
    if df.empty:
        return [], 0
    df["_dt"] = pd.to_datetime(df["time"], errors="coerce")
    df = df[df["_dt"].dt.date == datetime.now().date()]
    if df.empty:
        return [], 0

    max_tid = int(pd.to_numeric(df["trade_id"]).max())
    # Giá vào của mỗi mã lệnh — dòng ĐÓNG cần nó để hiện "vào → ra", không thể
    # lấy giá của chính dòng ĐÓNG (sẽ ra "1.904,0 → 1.904,0").
    _entry_of = {}
    for _, r in df.iterrows():
        if str(r.get("action", "")).startswith("MỞ"):
            _entry_of[int(pd.to_numeric(r["trade_id"]))] = pd.to_numeric(
                r.get("price"), errors="coerce")

    log = []
    for _, r in df.sort_values("_dt").iterrows():          # mới nhất lên đầu
        log.insert(0, {
            "time":   str(r.get("time", "")),
            "ticker": str(r.get("ticker", "VN30F1M")),
            "act":    str(r.get("action", "")),
            "price":  pd.to_numeric(r.get("price"), errors="coerce"),
            "sl":     pd.to_numeric(r.get("sl"), errors="coerce"),
            "tp": None, "tp_method": "",
            "pnl":    str(r.get("pnl", "—")),
            "tid":    int(pd.to_numeric(r.get("trade_id"))),
            "result": str(r.get("result", "") or ""),
            "entry":  _entry_of.get(int(pd.to_numeric(r.get("trade_id"))),
                                    pd.to_numeric(r.get("price"), errors="coerce")),
            "reason": str(r.get("reason", "") or ""),
        })
    return log, max_tid


# ── Vòng đời vị thế ───────────────────────────────────────────────────────────
# Chỉ 1 vị thế tại 1 thời điểm. Đây là ràng buộc CHIẾN THUẬT chứ không phải
# chống spam UI: backtest cho phép chồng lệnh thì tần suất vọt lên 105 lệnh/ngày
# và toàn bộ edge bị phí ăn hết (-0,44đ/lệnh). Giữ nguyên ràng buộc này.

def _open_position(side: str, entry: float, entry_ts: pd.Timestamp, atr: float,
                   tid: int = 0, tp_r: float | None = None) -> dict:
    risk = max(_SL_ATR_MULT * atr, _MIN_SL_PTS)
    _tp = None
    if tp_r:
        _tp = round(entry + tp_r * risk if side == "LONG" else entry - tp_r * risk, 1)
    return {
        "tp":       _tp,     # None = không chốt lời tự động (mặc định)
        "tp_r":     tp_r,
        "tid":      tid,   # mã lệnh — dùng để ghép cặp dòng MỞ và ĐÓNG trong nhật ký
        "side":     side,
        "entry":    round(entry, 1),
        "entry_ts": entry_ts,
        # con trỏ nến đã xử lý — KHÔNG dùng entry_ts để lọc nến mới, vì hàm
        # kiểm tra thoát được gọi lại nhiều lần và sẽ đếm trùng "bars"
        "last_ts":  entry_ts,
        "sl":       round(entry - risk if side == "LONG" else entry + risk, 1),
        "risk":     round(risk, 1),
        "atr":      round(atr, 2),
        "bars":     0,
    }


def _check_position_exit(pos: dict, new_bars: pd.DataFrame,
                         in_session: bool) -> tuple[bool, str, float, object]:
    """Duyệt MỌI nến đã đóng kể từ lúc vào lệnh, theo đúng thứ tự thời gian.

    Phải duyệt hết chứ không chỉ nến cuối: Amibroker export theo chu kỳ (thực tế
    ~5 phút/lần), nên mỗi lần app thấy "nến mới" có thể đã trôi qua nhiều nến —
    chỉ soi nến cuối sẽ bỏ sót SL đã chạm ở giữa và ghi nhận lãi/lỗ sai.

    Thứ tự ưu tiên trong 1 nến: SL trước (giả định bi quan, vì trong nến 1 phút
    không biết giá chạm SL hay chạm mục tiêu trước) → hết hạn giữ → hết phiên.
    Trả (có thoát?, lý do, giá thoát, timestamp nến thoát).
    """
    entry_day = pos["entry_ts"].date()
    prev_close, prev_ts = pos["entry"], pos["entry_ts"]

    for ts, bar in new_bars.iterrows():
        # Không bao giờ giữ lệnh qua đêm: nếu nến mới đã sang ngày khác thì
        # phiên vào lệnh đã đóng cửa (AFL có thể ngừng export nên app không
        # thấy nến cuối phiên) → chốt tại nến cuối cùng còn thuộc phiên đó.
        if ts.date() != entry_day:
            return True, "Đóng cuối phiên", prev_close, prev_ts

        pos["bars"]   += 1
        pos["last_ts"] = ts
        high, low, close = float(bar["High"]), float(bar["Low"]), float(bar["Close"])
        # SL xét TRƯỚC TP: trong nến 1 phút không biết giá chạm bên nào trước,
        # giả định bi quan cho ra con số backtest không bị thổi phồng.
        if pos["side"] == "LONG" and low <= pos["sl"]:
            return True, "Chạm SL", pos["sl"], ts
        if pos["side"] == "SHORT" and high >= pos["sl"]:
            return True, "Chạm SL", pos["sl"], ts
        _tp = pos.get("tp")
        if _tp is not None:
            if pos["side"] == "LONG" and high >= _tp:
                return True, "Chạm mục tiêu (TP)", _tp, ts
            if pos["side"] == "SHORT" and low <= _tp:
                return True, "Chạm mục tiêu (TP)", _tp, ts
        if pos["bars"] >= _HOLD_BARS:
            return True, f"Hết {_HOLD_BARS} phút giữ lệnh", close, ts
        prev_close, prev_ts = close, ts

    if not in_session and len(new_bars):
        return True, "Đóng cuối phiên", prev_close, prev_ts
    return False, "", 0.0, None


def _position_pnl(pos: dict, exit_price: float) -> float:
    """PnL gộp (điểm chỉ số, chưa trừ phí)."""
    return (exit_price - pos["entry"]) if pos["side"] == "LONG" else (pos["entry"] - exit_price)


# ── Telegram / journal ────────────────────────────────────────────────────────

# Nhật ký gửi Telegram — module-level vì hàm gửi chạy trong thread riêng, không
# được chạm vào st.session_state. UI đọc lại danh sách này ở mỗi lần render.
_TG_LOG: list[str] = []
_TG_LOG_LOCK = threading.Lock()


def _tg_log(line: str) -> None:
    with _TG_LOG_LOCK:
        _TG_LOG.insert(0, f"[{datetime.now().strftime('%H:%M:%S')}] {line}")
        del _TG_LOG[30:]


# Chặn cuối cùng chống spam: tin nhắn y hệt nhau trong ngần này giây thì bỏ qua.
# Độc lập với mọi guard ở tầng trên — đã có hai lần lỗi tầng trên gây gửi trùng
# (hai cửa sổ cùng chạy engine; chốt chặn mỗi-nến-một-lần commit quá muộn).
_TG_DEDUP_SEC  = 90
_TG_LAST: dict[str, float] = {}
_TG_LAST_LOCK  = threading.Lock()


def _tg_is_duplicate(msg: str) -> bool:
    """Tin nhắn này vừa được gửi chưa? Đồng thời dọn các mục đã hết hạn."""
    import time as _t
    now = _t.time()
    key = hashlib.sha256(msg.encode("utf-8")).hexdigest()
    with _TG_LAST_LOCK:
        for k in [k for k, v in _TG_LAST.items() if now - v > _TG_DEDUP_SEC]:
            del _TG_LAST[k]
        if key in _TG_LAST:
            return True
        _TG_LAST[key] = now
    return False


def _send_telegram(msg: str) -> bool:
    if _tg_is_duplicate(msg):
        _tg_log(f"⏭️ Bỏ qua tin trùng (trong {_TG_DEDUP_SEC}s): "
                f"{msg.splitlines()[0][:50]}")
        return True
    token   = os.getenv("TELEGRAM_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    if not (token and chat_id):
        _tg_log("❌ Chưa cấu hình TELEGRAM_TOKEN / TELEGRAM_CHAT_ID trong .env")
        return False
    try:
        import requests  # type: ignore
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": msg, "parse_mode": "HTML"},
            timeout=10,
        )
        if r.ok:
            _tg_log(f"✅ Đã gửi: {msg.splitlines()[0][:60]}")
            return True
        # Telegram trả mô tả lỗi rất rõ ràng — PHẢI log ra. Bản cũ chỉ trả về
        # False rồi bị vứt bỏ, nên lỗi 400 hoàn toàn vô hình suốt nhiều tháng.
        try:
            desc = r.json().get("description", "")
        except Exception:
            desc = r.text[:200]
        _tg_log(f"❌ HTTP {r.status_code}: {desc}")
        return False
    except Exception as e:
        _tg_log(f"❌ {type(e).__name__}: {e}")
        return False


def _send_telegram_async(msg: str):
    threading.Thread(target=_send_telegram, args=(msg,), daemon=True).start()


# Schema journal v4. Đổi danh sách này BẮT BUỘC phải kèm cơ chế xoay vòng file
# bên dưới, nếu không dòng mới sẽ lệch cột so với header cũ.
_JOURNAL_COLUMNS = ["date", "time", "ticker", "action", "price", "sl", "tp",
                    "tp_method", "pnl", "trade_id", "result", "reason"]


def _rotate_journal_if_stale(path: str) -> str | None:
    """Nếu header file không khớp schema hiện tại → đổi tên file cũ, trả tên đó.

    Lý do cần thiết: `to_csv(mode="a", header=False)` ghi theo thứ tự cột của
    DataFrame mà KHÔNG kiểm tra header sẵn có. File journal thực tế đã trộn 3 thế
    hệ (7 / 10 / 12 trường) nên `read_csv(on_bad_lines="skip")` âm thầm bỏ qua
    360/483 dòng — gồm cả dòng v4 mới ghi. Đổi tên (không xoá) để dữ liệu cũ vẫn
    còn tra cứu được, đồng thời file mới bắt đầu sạch với đúng schema.
    """
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            header = f.readline().strip()
    except Exception:
        return None

    expected = ",".join(f'"{c}"' for c in _JOURNAL_COLUMNS)   # quoting=QUOTE_ALL
    if header in (expected, ",".join(_JOURNAL_COLUMNS)):
        return None

    stem, ext = os.path.splitext(path)
    backup = f"{stem}_legacy_{datetime.now().strftime('%Y%m%d_%H%M%S')}{ext}"
    try:
        os.rename(path, backup)
        return backup
    except Exception:
        return None


def _append_journal(entry: dict):
    if not _JOURNAL_FILE or not _can_trade():
        return
    row = pd.DataFrame([{
        "date":   datetime.now().strftime("%Y-%m-%d"),
        "time":   entry["time"],
        "ticker": entry["ticker"],
        "action": entry["act"],
        "price":  entry["price"],
        "sl":     entry.get("sl", ""),
        "tp":     entry.get("tp", ""),
        "tp_method": entry.get("tp_method", ""),
        "pnl":    entry.get("pnl", ""),
        "trade_id": entry.get("tid", ""),      # ghép cặp dòng MỞ ↔ ĐÓNG
        "result":   entry.get("result", ""),   # THẮNG / THUA (chỉ dòng ĐÓNG)
        "reason": entry["reason"],
    }], columns=_JOURNAL_COLUMNS)
    try:
        moved  = _rotate_journal_if_stale(_JOURNAL_FILE)
        if moved:
            print(f"[journal] Schema cũ — đã lưu trữ sang: {moved}")
        exists = os.path.exists(_JOURNAL_FILE)
        if exists and _is_duplicate_of_last(row):
            return                                   # phiên khác vừa ghi đúng dòng này
        row.to_csv(_JOURNAL_FILE, mode="a" if exists else "w",
                   header=not exists, index=False, quoting=1)  # QUOTE_ALL
    except Exception:
        pass


def _is_duplicate_of_last(row: "pd.DataFrame") -> bool:
    """Dòng sắp ghi có trùng hệt dòng cuối file không?

    Hai phiên Streamlit chạy song song cùng thấy một nến mới và cùng ghi một
    bản ghi. Đã quan sát được 6/56 dòng trùng trong journal thực tế.
    """
    try:
        with open(_JOURNAL_FILE, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - 4096))
            tail = f.read().decode("utf-8", "replace").strip().splitlines()
        if not tail:
            return False
        last = tail[-1]
        new  = row.to_csv(header=False, index=False, quoting=1).strip()
        # Bỏ cột "date" đầu tiên khi so — hai phiên có thể lệch nhau vài giây
        return last.split(",", 1)[-1] == new.split(",", 1)[-1]
    except Exception:
        return False


def _next_trade_id() -> int:
    """Mã lệnh DUY NHẤT TOÀN CỤC, cấp từ đĩa chứ không từ bộ đếm trong RAM.

    Hai lý do bắt buộc phải toàn cục (không reset theo ngày):
      1. `load_trades()` ghép cặp bằng `groupby("trade_id")`. Báo cáo nhiều ngày
         sẽ ghép lệnh #1 của ngày A với lệnh #1 của ngày B ⇒ PnL sai.
      2. Bộ đếm trong session_state là riêng từng phiên trình duyệt. Hai tab
         cùng mở sẽ cấp trùng mã — đã xảy ra thật: ngày 20/08 mã #1, #2, #3
         mỗi mã được dùng cho HAI lệnh khác nhau.
    """
    mx = int(st.session_state.get("ps_trade_seq", 0) or 0)
    if _JOURNAL_FILE and os.path.exists(_JOURNAL_FILE):
        try:
            _j = pd.read_csv(_JOURNAL_FILE, on_bad_lines="skip", engine="python",
                             usecols=["trade_id"])
            _v = pd.to_numeric(_j["trade_id"], errors="coerce").dropna()
            if len(_v):
                mx = max(mx, int(_v.max()))
        except Exception:
            pass
    return mx + 1


def _sync_gsheet_async(entry: dict):
    if not (_CRED_FILE and os.path.exists(_CRED_FILE)):
        return
    def _run():
        try:
            import gspread  # type: ignore
            from oauth2client.service_account import ServiceAccountCredentials  # type: ignore
            scope  = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
            creds  = ServiceAccountCredentials.from_json_keyfile_name(_CRED_FILE, scope)
            client = gspread.authorize(creds)
            sheet  = client.open(_GSHEET_NAME).sheet1
            sheet.append_row([
                datetime.now().strftime("%Y-%m-%d"),
                entry["time"], entry["ticker"],
                entry["act"],  entry["price"],
                entry.get("pnl", ""), entry["reason"],
            ])
        except Exception:
            pass
    threading.Thread(target=_run, daemon=True).start()


# ── Thống kê session ──────────────────────────────────────────────────────────

def _calc_session_stats(log: list[dict]) -> dict:
    """Thống kê từ các record ĐÓNG. Trả cả PnL gộp và PnL sau phí.

    v3 lọc theo act.startswith("ĐÓNG") nhưng không chỗ nào sinh record đó
    → mọi chỉ số luôn bằng 0, nên không ai phát hiện chiến thuật đang lỗ.
    """
    empty = {"total": 0, "wins": 0, "losses": 0, "total_pnl": 0.0,
             "net_pnl": 0.0, "win_rate": 0.0}
    closed = [x for x in log if str(x.get("act", "")).startswith("ĐÓNG")]
    if not closed:
        return empty
    total_pnl = 0.0
    wins = losses = 0
    for x in closed:
        try:
            pnl = float(str(x["pnl"]).replace("—", "0"))
        except (TypeError, ValueError):
            continue
        total_pnl += pnl
        if pnl > 0:
            wins += 1
        else:
            losses += 1
    n = wins + losses
    if not n:
        return empty
    return {
        "total":     n,
        "wins":      wins,
        "losses":    losses,
        "total_pnl": total_pnl,
        "net_pnl":   total_pnl - n * _FEE_PTS,
        "win_rate":  wins / n * 100,
    }


# ══════════════════════════════════════════════════════════════════════════════
# LSTM Training v2 — 3-class label + class_weight balanced
# ══════════════════════════════════════════════════════════════════════════════

def _run_lstm_training(data_file, model_file, scaler_file,
                       future_bars=5, profit_target=1.0, epochs=30):
    """Train LSTM 3-class trong UI. Label: 0=SHORT / 1=WAIT / 2=LONG."""
    SEQ_LEN    = 30
    BATCH_SIZE = 64
    TEST_RATIO = 0.15

    try:
        import pandas_ta as ta
        import joblib
        from sklearn.preprocessing import MinMaxScaler
        from sklearn.metrics import classification_report
        from sklearn.utils.class_weight import compute_class_weight
        import tensorflow as tf
        from tensorflow.keras.models import Sequential
        from tensorflow.keras.layers import LSTM, Dense, Dropout, BatchNormalization
        from tensorflow.keras.callbacks import EarlyStopping, ReduceLROnPlateau
    except ImportError as e:
        st.error(f"❌ Thiếu thư viện: {e}")
        return

    with st.status("🧠 Đang train model LSTM v2...", expanded=True) as status:

        # 1. Load & parse
        st.write("📂 Đọc dữ liệu...")
        df = pd.read_csv(data_file)
        df.index = pd.to_datetime(df["Date"] + " " + df["Time"], dayfirst=True)
        for c in ["Open", "High", "Low", "Close", "Volume"]:
            df[c] = pd.to_numeric(df[c], errors="coerce")
        df.dropna(subset=["Close"], inplace=True)
        df.sort_index(inplace=True)
        st.write(f"✅ {len(df):,} nến | {df.index[0].date()} → {df.index[-1].date()}")

        # 2. Features v2 (8 features)
        st.write("⚙️ Tính 8 features (RSI, MACD, EMA34, LogRet, Vol, VWAP, Gap, ATR)...")
        df["RSI"]        = ta.rsi(df["Close"], 14)
        _macd            = ta.macd(df["Close"])
        df["MACD"]       = _macd.iloc[:, 0] if _macd is not None else np.nan
        df["EMA_34"]     = ta.ema(df["Close"], 34)
        df["Dist_EMA"]   = (df["Close"] - df["EMA_34"]) / df["Close"].clip(lower=1)
        df["Log_Ret"]    = np.log(df["Close"] / df["Close"].shift(1))
        df["Vol_Change"] = df["Volume"].pct_change().clip(-5, 5)
        _atr             = ta.atr(df["High"], df["Low"], df["Close"], 14)
        df["ATR_Norm"]   = (_atr / df["Close"].clip(lower=1))
        vwap             = _calc_vwap(df)
        df["VWAP_Dist"]  = (df["Close"] - vwap) / df["Close"].clip(lower=1)
        df["_date"]      = df.index.date
        df["Session_Open"] = df.groupby("_date")["Open"].transform("first")
        df["Session_Gap"]  = (df["Close"] - df["Session_Open"]) / df["Session_Open"].clip(lower=1)

        # 3. Label 3-class (FIX CHÍNH)
        st.write(f"🏷️ Tạo nhãn 3 class (target={profit_target} điểm, nhìn trước {future_bars} nến)...")
        df["future_close"] = df["Close"].shift(-future_bars)
        df.dropna(subset=["RSI", "MACD", "EMA_34", "future_close"], inplace=True)
        df[["Log_Ret", "Vol_Change", "VWAP_Dist", "Session_Gap", "ATR_Norm"]] = (
            df[["Log_Ret", "Vol_Change", "VWAP_Dist", "Session_Gap", "ATR_Norm"]].fillna(0)
        )

        diff = df["future_close"] - df["Close"]
        df["label"] = 1  # WAIT (default)
        df.loc[diff >=  profit_target, "label"] = 2   # LONG
        df.loc[diff <= -profit_target, "label"] = 0   # SHORT

        label_counts = df["label"].value_counts().sort_index()
        st.write(
            f"✅ {len(df):,} nến sau clean | "
            f"SHORT={label_counts.get(0,0):,} ({label_counts.get(0,0)/len(df)*100:.1f}%) | "
            f"WAIT={label_counts.get(1,0):,} ({label_counts.get(1,0)/len(df)*100:.1f}%) | "
            f"LONG={label_counts.get(2,0):,} ({label_counts.get(2,0)/len(df)*100:.1f}%)"
        )

        # 4. Sequences
        st.write("🔢 Tạo sequences...")
        X_data = df[_FEATURES].values
        y_data = df["label"].values
        X_seqs, y_seqs = [], []
        for i in range(SEQ_LEN, len(X_data)):
            X_seqs.append(X_data[i - SEQ_LEN:i])
            y_seqs.append(y_data[i])
        X_seqs = np.array(X_seqs, dtype=np.float32)
        y_seqs = np.array(y_seqs, dtype=np.int32)

        # 5. Scale + split
        st.write("📐 Scale + train/test split...")
        split   = int(len(X_seqs) * (1 - TEST_RATIO))
        X_train, X_test = X_seqs[:split], X_seqs[split:]
        y_train, y_test = y_seqs[:split], y_seqs[split:]

        scaler = MinMaxScaler()
        X_flat_train = X_train.reshape(-1, len(_FEATURES))
        X_flat_test  = X_test.reshape(-1, len(_FEATURES))
        X_train = scaler.fit_transform(X_flat_train).reshape(-1, SEQ_LEN, len(_FEATURES))
        X_test  = scaler.transform(X_flat_test).reshape(-1, SEQ_LEN, len(_FEATURES))
        joblib.dump(scaler, scaler_file)
        st.write(f"✅ Train: {len(X_train):,} | Test: {len(X_test):,}")

        # 6. Class weights balanced (FIX: chống bias về WAIT)
        st.write("⚖️ Tính class weights (balanced)...")
        cw_arr = compute_class_weight("balanced", classes=np.array([0, 1, 2]), y=y_train)
        class_weight = {0: float(cw_arr[0]), 1: float(cw_arr[1]), 2: float(cw_arr[2])}
        st.write(f"   SHORT={class_weight[0]:.2f} | WAIT={class_weight[1]:.2f} | LONG={class_weight[2]:.2f}")

        # 7. Build model v2 (softmax 3 outputs)
        st.write("🏗️ Build model LSTM v2 (3-class softmax)...")
        model = Sequential([
            LSTM(64, input_shape=(SEQ_LEN, len(_FEATURES)), return_sequences=True),
            Dropout(0.2),
            BatchNormalization(),
            LSTM(32, return_sequences=False),
            Dropout(0.2),
            Dense(16, activation="relu"),
            Dense(3, activation="softmax"),   # 3 outputs: SHORT / WAIT / LONG
        ])
        model.compile(
            optimizer="adam",
            loss="sparse_categorical_crossentropy",
            metrics=["accuracy"],
        )

        # 8. Train
        st.write(f"🚀 Train ({epochs} epochs max, early stopping patience=5)...")
        progress_bar = st.progress(0)

        class _StreamlitCallback(tf.keras.callbacks.Callback):
            def on_epoch_end(self, epoch, logs=None):
                pct = int((epoch + 1) / epochs * 100)
                progress_bar.progress(
                    pct,
                    text=f"Epoch {epoch+1}/{epochs} | loss={logs.get('loss',0):.4f} | "
                         f"val_acc={logs.get('val_accuracy',0):.3f}"
                )

        model.fit(
            X_train, y_train,
            epochs=epochs,
            batch_size=BATCH_SIZE,
            validation_data=(X_test, y_test),
            class_weight=class_weight,
            callbacks=[
                EarlyStopping(patience=5, restore_best_weights=True, monitor="val_loss"),
                ReduceLROnPlateau(patience=3, factor=0.5, monitor="val_loss"),
                _StreamlitCallback(),
            ],
            verbose=0,
        )

        # 9. Evaluate
        st.write("📊 Đánh giá...")
        _, acc = model.evaluate(X_test, y_test, verbose=0)
        y_pred = np.argmax(model.predict(X_test, verbose=0), axis=1)
        report = classification_report(y_test, y_pred,
                                       target_names=["SHORT", "WAIT", "LONG"],
                                       output_dict=True)

        model.save(model_file)
        status.update(label=f"✅ Train xong! Accuracy: {acc*100:.1f}%", state="complete")

    r1, r2, r3, r4, r5 = st.columns(5)
    r1.metric("Test Accuracy",    f"{acc*100:.1f}%")
    r2.metric("LONG Precision",   f"{report['LONG']['precision']*100:.1f}%")
    r3.metric("LONG Recall",      f"{report['LONG']['recall']*100:.1f}%")
    r4.metric("SHORT Precision",  f"{report['SHORT']['precision']*100:.1f}%")
    r5.metric("SHORT Recall",     f"{report['SHORT']['recall']*100:.1f}%")
    st.success(f"✅ Model v2 lưu: `{model_file}` | Scaler: `{scaler_file}`\nRestart app để load model mới.")
    st.cache_resource.clear()


# ── 2 fragment: run_every phải cố định lúc khai báo nên cần 2 hàm riêng ──────

@st.fragment(run_every=1)
def _live_panel_auto():
    _live_panel_body()


@st.fragment
def _live_panel_manual():
    _live_panel_body()


# ══════════════════════════════════════════════════════════════════════════════
# Main render
# ══════════════════════════════════════════════════════════════════════════════

def render_phaisinh_tab():
    st.markdown("""
    <style>
        .ps-box    { background:#1E2129;padding:16px;border-radius:10px;
                     text-align:center;border:1px solid #333;margin-bottom:4px; }
        .ps-active { border:2px solid #00E676 !important;background:rgba(0,230,118,.08) !important; }
        .ps-warn   { border:2px solid #FF5252 !important;background:rgba(255,82,82,.08) !important; }
        .ps-log    { width:100%;border-collapse:collapse;margin-top:8px; }
        .ps-log th { background:#222;color:#FFF;padding:10px;text-align:left;border-bottom:2px solid #555; }
        .ps-log td { border-bottom:1px solid #2a2a2a;padding:9px;text-align:center;font-size:.93rem; }
        .c-long  { color:#00E676;font-weight:bold }
        .c-short { color:#FF5252;font-weight:bold }
        .c-wait  { color:#9CA3AF;font-weight:bold }
        .c-pos   { color:#00B0FF;font-weight:bold }
        .c-neg   { color:#FFD600;font-weight:bold }
        .prob-bar { height:10px;border-radius:5px;margin:3px 0; }
    </style>
    """, unsafe_allow_html=True)

    # ── Session state defaults ────────────────────────────────────────────────
    _defaults = {
        "ps_log_history":     [],
        "ps_last_time":       "",
        "ps_last_mtime":      0,
        "ps_df_1m":           None,
        "ps_last_prob_long":  0.33,
        "ps_last_prob_short": 0.33,
        "ps_last_trend":      (0, "—"),
        "ps_tf_detail":       {"1m": {}, "15m": {}, "1h": {}},
        "ps_trend_mtime":     -1,
        "ps_signal_audit":    [],
        "ps_errors":          [],
        "ps_ai_warn":         None,
        "ps_rule_signal":     "WAIT",
        "ps_rule_reason":     "—",
        "ps_rule_detail":     {},   # giá trị thô 2 điều kiện engine v4
        "ps_rule_mtime":      -1,
        "ps_stop_levels":     {},   # Buy Stop / Sell Stop gợi ý
        "ps_position":        None, # vị thế ảo đang mở (chỉ 1 tại 1 thời điểm)
        "ps_trade_seq":       0,    # bộ đếm mã lệnh trong phiên
        "ps_last_closed":     None, # lệnh vừa đóng — để hiện banner nhận biết
        "ps_is_owner":        False,# có giữ quyền ghi không (xem _claim_ownership)
        "ps_took_over_at":    None, # thời điểm tiếp quản từ phiên khác
    }
    # Nạp sở thích giao diện TRƯỚC khi tạo widget. Widget có `key` sẽ lấy giá trị
    # từ session_state nếu key đã tồn tại — nên không truyền `value=` nữa, kẻo
    # Streamlit cảnh báo "created with a default value but also set via Session State".
    for _k, _v in _load_ui_pref().items():
        st.session_state.setdefault(_k, _v)

    _fresh = "ps_position" not in st.session_state      # phiên trình duyệt mới
    for k, v in _defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

    # Tải lại trang → session_state trắng. Khôi phục từ đĩa để không bỏ rơi vị
    # thế đang mở và không đánh trùng mã lệnh. Xem chú thích ở _save_ps_state().
    if _fresh:
        _pos_disk, _seq_disk = _load_ps_state()
        _log_disk, _max_tid  = _restore_log_from_journal()
        if _log_disk:
            st.session_state["ps_log_history"] = _log_disk
        st.session_state["ps_trade_seq"] = max(_seq_disk, _max_tid)
        if _pos_disk:
            st.session_state["ps_position"] = _pos_disk

    st.header("⚡ VN30F1M — Signal Bot v4 (VWAP + MACD histogram)")

    if not _BASE_DATA_DIR:
        st.error(
            "❌ Không tìm thấy thư mục AmibrokerData tại D:\\ hoặc C:\\. "
            "Đảm bảo Amibroker đang xuất vn30f1m_1min.csv vào đúng đường dẫn."
        )
        return

    # ── Controls ──────────────────────────────────────────────────────────────
    with st.expander("⚙️ Cấu hình chiến thuật", expanded=False):
        st.caption(
            f"**Engine v4 (Rule-based):** LONG khi giá trên VWAP phiên và MACD histogram tăng; "
            f"SHORT khi ngược lại. Thoát sau tối đa **{_HOLD_BARS} phút** hoặc chạm "
            f"**SL {_SL_ATR_MULT:g}×ATR14**. Chỉ giữ 1 vị thế tại 1 thời điểm. "
            f"Backtest 273 phiên: +0,385đ/lệnh, dương ở cả 5/5 quý."
        )
        cfg_c1, cfg_c2 = st.columns(2)
        thr_long  = cfg_c1.slider("Ngưỡng LONG LSTM (%)",  50, 90,
                                  key="ps_thr_buy",  on_change=_save_ui_pref) / 100
        thr_short = cfg_c2.slider("Ngưỡng SHORT LSTM (%)", 50, 90,
                                  key="ps_thr_sell", on_change=_save_ui_pref) / 100

        mode_col, strict_col = st.columns([2, 1])
        signal_mode = mode_col.radio(
            "Chế độ tín hiệu",
            options=["Rule-based", "LSTM", "Ensemble (cả 2 đồng thuận)"],
            horizontal=True, key="ps_signal_mode", on_change=_save_ui_pref,
            help="Rule-based: VWAP + MACD histogram (đã backtest). "
                 "LSTM: model AI. Ensemble: cần cả 2 cùng chiều.",
        )
        strict_trend = strict_col.checkbox(
            "Strict trend (3 khung)",
            key="ps_strict_trend", on_change=_save_ui_pref,
            help="Chỉ áp dụng cho chế độ LSTM/Ensemble. Engine v4 không dùng trend đa khung: "
                 "đo trên dữ liệu thật, alpha của trend=±1 xấp xỉ 0 nên lọc thêm chỉ giảm số lệnh.",
        )
        st.checkbox(
            f"Bật chốt lời tự động ở {_TP_R_MULT:g}R",
            key="ps_use_tp", on_change=_save_ui_pref,
            help=f"R = khoảng cách tới cắt lỗ, nên TP nằm cách giá vào {_TP_R_MULT:g} lần "
                 f"rủi ro. Lưu ý từ backtest: bật cho +9,8% tổng nhưng gần như toàn bộ "
                 f"lợi ích đến từ riêng quý 2025Q3 (+69,1/+71,3đ) — bốn quý còn lại "
                 f"cộng lại chỉ +2,2đ. Tắt đi thì bot giữ lệnh đủ 30 phút.",
        )
        st.checkbox(
            "Báo Telegram khi có tín hiệu ngược chiều lệnh đang giữ",
            key="ps_notify_counter", on_change=_save_ui_pref,
            help="Chỉ để quan sát — bot vẫn giữ nguyên lệnh. Số liệu cho thấy KHÔNG nên "
                 "đảo lệnh: thoát theo tín hiệu ngược làm lợi nhuận rơi 76%, đảo lệnh rơi "
                 "87%. Gửi tối đa 1 tin cho mỗi vị thế nên không gây spam.",
        )

    top_c1, top_c2, top_c3 = st.columns([1, 1, 2])
    top_c1.toggle("🔄 Auto Refresh (1s)", key="ps_auto_toggle",
                  on_change=_save_ui_pref,
                  help="Được ghi nhớ — bật một lần là giữ nguyên qua các lần tải lại trang.")
    if top_c2.button("🔄 Xóa log lỗi", use_container_width=True):
        st.session_state["ps_errors"] = []
        st.rerun()

    _, session_msg = _in_trading_session()
    top_c3.caption(session_msg)

    st.divider()

    # ── Chọn fragment đúng theo trạng thái toggle ─────────────────────────────
    # run_every phải cố định lúc khai báo decorator nên cần 2 hàm riêng.
    # Fragment chỉ rerun vùng bên trong — app.py và các tab khác không bị block.
    if st.session_state.get("ps_auto_toggle", False):
        _live_panel_auto()
    else:
        _live_panel_manual()


# ── Trạng thái Telegram ───────────────────────────────────────────────────────

def _render_telegram_panel():
    """Hiển thị cấu hình + nhật ký gửi Telegram, kèm nút gửi thử.

    Có panel này vì lỗi gửi trước đây hoàn toàn vô hình: `_send_telegram_async`
    bắn thread rồi bỏ qua kết quả, nên tin nhắn bị Telegram từ chối (HTTP 400)
    mà không có dấu hiệu nào trên giao diện.
    """
    token   = os.getenv("TELEGRAM_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    with _TG_LOG_LOCK:
        log = list(_TG_LOG)
    n_fail = sum(1 for x in log if "❌" in x)
    title  = "📨 Telegram" + (f" — {n_fail} lỗi gần đây" if n_fail else
                              (" — OK" if log else ""))

    st.divider()
    with st.expander(title, expanded=bool(n_fail)):
        if not (token and chat_id):
            st.error(
                "❌ Chưa cấu hình. Thêm `TELEGRAM_TOKEN` và `TELEGRAM_CHAT_ID` "
                "vào `.env` rồi khởi động lại app."
            )
        else:
            st.caption(f"Bot token `…{token[-6:]}` · chat_id `{chat_id}`")

        c1, c2 = st.columns([1, 3])
        if c1.button("Gửi thử", disabled=not (token and chat_id),
                     use_container_width=True, key="ps_tg_test"):
            ok = _send_telegram(
                "🔧 <b>#VN30F1M Kiểm tra kết nối</b>\n"
                f"Gửi lúc {datetime.now().strftime('%H:%M:%S %d/%m/%Y')}\n"
                "Nếu bạn thấy tin nhắn này thì cảnh báo đang hoạt động."
            )
            (st.success if ok else st.error)(
                "Đã gửi — kiểm tra Telegram." if ok else
                "Gửi thất bại, xem nhật ký bên dưới."
            )
            st.rerun()

        c2.caption(
            "Tin nhắn gửi ở chế độ HTML. Mọi nội dung động đều đi qua "
            "`_tg_escape()` — nếu bỏ qua bước này, một ký tự `<` trong dữ liệu "
            "sẽ khiến Telegram từ chối cả tin nhắn (lỗi 400)."
        )

        if log:
            st.markdown("**Nhật ký gửi gần đây**")
            for line in log[:12]:
                st.code(line, language=None)
        else:
            st.caption("Chưa có lần gửi nào trong phiên chạy này.")


# ── Báo cáo lãi/lỗ cuối ngày ──────────────────────────────────────────────────

def _render_daily_report(in_session: bool):
    """Báo cáo lệnh phái sinh: xem, tải về, tổng hợp theo ngày/tuần/tháng, gửi mail.

    Import daily_report ở TRONG hàm: module đó import ngược phaisinh_tab để lấy
    hằng số phí/giá trị điểm, đặt ở module-level sẽ thành vòng lặp import.
    """
    from datetime import date as _date, timedelta as _td
    from .daily_report import (build_range_report, build_report, email_ready,
                               send_report_email, fmt_vn)

    st.divider()
    with st.expander("📊 Báo cáo lệnh phái sinh", expanded=False):
        _today = _date.today()
        c1, c2 = st.columns([1, 1])
        _mode = c1.radio(
            "Khoảng thời gian", ["Hôm nay", "7 ngày", "Tháng này", "Tuỳ chọn"],
            index=0, horizontal=True, key="ps_rep_mode",
        )
        if _mode == "Hôm nay":
            _d0 = _d1 = _today
        elif _mode == "7 ngày":
            _d0, _d1 = _today - _td(days=6), _today
        elif _mode == "Tháng này":
            _d0, _d1 = _today.replace(day=1), _today
        else:
            _rng = c1.date_input(
                "Từ ngày – đến ngày", value=(_today - _td(days=30), _today),
                key="ps_rep_range", format="DD/MM/YYYY",
            )
            if isinstance(_rng, (list, tuple)) and len(_rng) == 2:
                _d0, _d1 = _rng
            else:
                st.info("Chọn đủ ngày bắt đầu và ngày kết thúc.")
                return

        _freq_lbl = c2.radio(
            "Gom nhóm theo", ["Ngày", "Tuần", "Tháng"], index=1,
            horizontal=True, key="ps_rep_freq",
        )
        _freq = {"Ngày": "D", "Tuần": "W", "Tháng": "M"}[_freq_lbl]

        ok_mail, mail_msg = email_ready()
        c2.caption(("✅ Email: " if ok_mail else "⚠️ Email chưa cấu hình — ") + mail_msg)
        if not ok_mail:
            c2.caption(
                "Thêm vào `.env`: `SMTP_USER`, `SMTP_PASSWORD`, `REPORT_EMAIL_TO` "
                "(Gmail cần **App Password**). `SMTP_HOST`/`SMTP_PORT` mặc định "
                "smtp.gmail.com:587."
            )

        try:
            trades, s, by_period, html = build_range_report(_d0, _d1, _freq)
        except Exception as e:
            st.error(f"Không dựng được báo cáo: {type(e).__name__}: {e}")
            return

        _label = (f"{_d0:%d/%m/%Y}" if _d0 == _d1
                  else f"{_d0:%d/%m/%Y} – {_d1:%d/%m/%Y}")

        if not s.get("n"):
            st.info(
                f"Không có lệnh nào đóng trong khoảng **{_label}**. Báo cáo đọc từ "
                "journal (bản ghi v4 có `trade_id`) — dữ liệu của bot phiên bản cũ "
                "không được tính."
            )
            return

        st.caption(f"Khoảng **{_label}** · gom theo **{_freq_lbl.lower()}**")
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Số lệnh", fmt_vn(s["n"]))
        m2.metric("Thắng / Thua", f"{s['wins']} / {s['losses']}",
                  delta=f"{fmt_vn(s['win_rate'], 1)}%")
        m3.metric("PnL ròng", f"{fmt_vn(s['pnl_net'], 1, signed=True)}đ",
                  delta=f"{fmt_vn(s['vnd_net'], 0, signed=True)} VND/HĐ")
        m4.metric("TB mỗi lệnh", f"{fmt_vn(s['avg_net'], 2, signed=True)}đ",
                  delta=f"PF {fmt_vn(s['profit_factor'], 2)}")

        # ── Bảng tổng hợp theo kỳ ────────────────────────────────────────────
        st.markdown(f"**Tổng hợp theo {_freq_lbl.lower()}**")
        st.dataframe(
            pd.DataFrame({
                "Kỳ":          by_period["ky"],
                "Số lệnh":     by_period["n"],
                "Thắng/Thua":  by_period["thang"].astype(str) + " / "
                               + by_period["thua"].astype(str),
                "Tỷ lệ thắng": by_period["win_rate"].map(lambda v: fmt_vn(v, 1) + "%"),
                "PnL ròng":    by_period["pnl_net"].map(lambda v: fmt_vn(v, 1, signed=True) + "đ"),
                "VND/HĐ":      by_period["vnd"].map(lambda v: fmt_vn(v, 0, signed=True)),
                "TB/lệnh":     by_period["avg"].map(lambda v: fmt_vn(v, 2, signed=True) + "đ"),
            }),
            use_container_width=True, hide_index=True,
        )

        # ── Biểu đồ luỹ kế ───────────────────────────────────────────────────
        _eq = trades[["exit_time", "net"]].copy()
        _eq["Luỹ kế (điểm)"] = _eq["net"].cumsum()
        st.line_chart(_eq.set_index("exit_time")["Luỹ kế (điểm)"], height=180)

        # ── Chi tiết từng lệnh ───────────────────────────────────────────────
        st.markdown("**Chi tiết từng lệnh**")
        _detail = pd.DataFrame({
            "Mã":       trades["tid"],
            "Chiều":    trades["side"],
            "Ngày":     trades["exit_time"].dt.strftime("%d/%m/%Y"),
            "Vào":      trades["entry_time"].dt.strftime("%H:%M") + " · "
                        + trades["entry"].map(lambda v: fmt_vn(v, 1)),
            "Ra":       trades["exit_time"].dt.strftime("%H:%M") + " · "
                        + trades["exit"].map(lambda v: fmt_vn(v, 1)),
            "Giữ":      trades["hold_min"].map(lambda v: f"{v} phút" if v != "" else "—"),
            "PnL ròng": trades["net"].map(lambda v: fmt_vn(v, 2, signed=True) + "đ"),
            "VND/HĐ":   (trades["net"] * _PT_VALUE_VND).map(
                            lambda v: fmt_vn(v, 0, signed=True)),
            "Kết quả":  trades["won"].map({True: "✅ THẮNG", False: "❌ THUA"}),
            "Lý do":    trades["reason"],
        })
        st.dataframe(_detail, use_container_width=True, hide_index=True)

        # ── Tải về / gửi mail ────────────────────────────────────────────────
        _stamp = (f"{_d0:%Y%m%d}" if _d0 == _d1 else f"{_d0:%Y%m%d}_{_d1:%Y%m%d}")
        d1c, d2c, d3c, d4c = st.columns(4)
        d1c.download_button("⬇️ HTML", data=html.encode("utf-8"),
                            file_name=f"bao_cao_vn30f1m_{_stamp}.html",
                            mime="text/html", use_container_width=True)
        d2c.download_button("⬇️ CSV chi tiết",
                            data=_detail.to_csv(index=False).encode("utf-8-sig"),
                            file_name=f"chi_tiet_lenh_{_stamp}.csv",
                            mime="text/csv", use_container_width=True)
        d3c.download_button(f"⬇️ CSV theo {_freq_lbl.lower()}",
                            data=by_period.drop(columns=["_sort"]).to_csv(index=False)
                                          .encode("utf-8-sig"),
                            file_name=f"tong_hop_{_freq_lbl.lower()}_{_stamp}.csv",
                            mime="text/csv", use_container_width=True)
        if d4c.button("📨 Gửi email", disabled=not ok_mail,
                      use_container_width=True, type="primary"):
            sent, msg = send_report_email(
                html, f"[VN30F1M] Báo cáo {_label} — "
                      f"{fmt_vn(s['pnl_net'], 1, signed=True)}đ")
            (st.success if sent else st.error)(msg)

        st.checkbox(
            "Tự động gửi email báo cáo NGÀY sau khi kết phiên (1 lần/ngày)",
            value=False, key="ps_rep_auto", disabled=not ok_mail,
            help="Chỉ gửi báo cáo trong ngày, không phụ thuộc khoảng đang chọn ở trên.",
        )

        with st.expander("👁️ Xem trước bản HTML sẽ tải/gửi", expanded=False):
            st.components.v1.html(html, height=620, scrolling=True)

    # Tự động gửi báo cáo NGÀY — chỉ khi user bật, đã ngoài phiên, chưa gửi hôm nay
    if (st.session_state.get("ps_rep_auto") and not in_session
            and st.session_state.get("ps_report_sent_date") != _date.today().isoformat()):
        try:
            _tr, _s, _html = build_report(_date.today())
            if _s.get("n"):
                sent, msg = send_report_email(
                    _html, f"[VN30F1M] Báo cáo lãi/lỗ {_date.today():%d/%m/%Y} — "
                           f"{fmt_vn(_s['pnl_net'], 1, signed=True)}đ")
                st.session_state["ps_report_sent_date"] = _date.today().isoformat()
                (st.success if sent else st.error)(f"Tự động gửi báo cáo: {msg}")
        except Exception as e:
            st.session_state["ps_errors"].insert(
                0, f"[{datetime.now().strftime('%H:%M:%S')}] Auto-report: {e}")


# ── Nội dung live panel (dùng chung cho cả 2 fragment) ───────────────────────
def _live_panel_body():
    """Logic signal + hiển thị UI — được gọi từ cả 2 fragment (auto/manual)."""
    _owner, _other_age = _claim_ownership()
    if not _owner:
        _left = max(0, _OWNER_TTL_SEC - _other_age)
        st.info(
            f"🛡️ **Cửa sổ DỰ PHÒNG** — một cửa sổ khác đang điều khiển bot "
            f"(nhịp tim cách đây {_other_age:.0f}s).\n\n"
            f"Cửa sổ này theo dõi bình thường nhưng **không mở/đóng lệnh** — hai bên "
            f"cùng giao dịch sẽ cấp trùng mã lệnh và làm hỏng báo cáo lãi/lỗ.\n\n"
            f"Nếu cửa sổ kia đóng đột ngột, cửa sổ này **tự động tiếp quản sau "
            f"~{_left:.0f}s** và nạp lại vị thế đang mở từ đĩa — không mất lệnh nào. "
            f"Để tiếp quản tự động, giữ **Tự làm tươi** ở trạng thái bật."
        )
        if st.button("⚡ Giành quyền điều khiển ngay", key="ps_btn_claim",
                     help="Chỉ bấm khi chắc cửa sổ kia đã đóng. "
                          "Vị thế đang mở sẽ được nạp lại từ đĩa."):
            st.session_state["ps_force_claim"] = True
            st.rerun()
    elif st.session_state.get("ps_took_over_at") is not None:
        _t = st.session_state["ps_took_over_at"]
        if (pd.Timestamp.now() - _t).total_seconds() < 300:
            _p = st.session_state.get("ps_position")
            st.success(
                f"✅ **Đã tiếp quản quyền điều khiển** lúc {_t.strftime('%H:%M:%S')} "
                f"(cửa sổ trước ngừng phản hồi). Đã nạp lại trạng thái từ đĩa: "
                + (f"đang giữ **{_p['side']} #{_p['tid']}** vào tại {_fvn(_p['entry'], 1)}."
                   if _p else "không có vị thế nào đang mở.")
            )
        else:
            st.session_state["ps_took_over_at"] = None
    # Đọc config từ session_state (widgets đã khai báo key= trong render_phaisinh_tab)
    thr_long    = st.session_state.get("ps_thr_buy",  _DEFAULT_THRESHOLD_BUY  * 100) / 100
    thr_short   = st.session_state.get("ps_thr_sell", _DEFAULT_THRESHOLD_SELL * 100) / 100
    signal_mode = st.session_state.get("ps_signal_mode",  "Rule-based")
    strict_trend = st.session_state.get("ps_strict_trend", False)
    in_session, _ = _in_trading_session()

    # ── Load AI ───────────────────────────────────────────────────────────────
    ai_result = _load_ai_system()
    if isinstance(ai_result[1], str) and ai_result[0] is None:
        st.error(f"❌ Lỗi load model: {ai_result[1]}")
        ai_scaler, ai_model = None, None
    else:
        ai_scaler, ai_model = ai_result

    if ai_model is None:
        st.warning(f"⚠️ Chưa có model (`{_MODEL_PATH}`). Train model mới bên dưới.")
    else:
        n_out = ai_model.output_shape[-1]
        ver   = "v2 (3-class)" if n_out == 3 else "v1 (binary — SHORT bị tắt, cần retrain)"
        col_mv, _ = st.columns([3, 1])
        col_mv.caption(f"🧠 Model: **{ver}** | Features: {ai_scaler.n_features_in_ if hasattr(ai_scaler,'n_features_in_') else '?'}")

    # ── Đọc CSV khi mtime thay đổi ───────────────────────────────────────────
    current_price = 0.0
    last_time     = st.session_state["ps_last_time"]
    ai_signal     = "WAIT"
    rule_sig      = st.session_state.get("ps_rule_signal", "WAIT")
    prob_long     = st.session_state["ps_last_prob_long"]
    prob_short    = st.session_state["ps_last_prob_short"]

    try:
        cur_mtime = os.path.getmtime(_DATA_FILE_1M)
        if cur_mtime != st.session_state["ps_last_mtime"]:
            st.session_state["ps_df_1m"]      = _load_df_1m(_DATA_FILE_1M)
            st.session_state["ps_last_mtime"] = cur_mtime
    except Exception:
        pass

    df_1m = st.session_state["ps_df_1m"]

    # ── Tuổi dữ liệu — chặn giao dịch khi nguồn đã dừng ──────────────────────
    data_age   = _data_age_min(df_1m)
    data_stale = data_age is not None and data_age > _MAX_STALE_MIN

    # Vị thế còn treo từ phiên trước: nguồn dữ liệu dừng nên không có nến mới
    # nào để kích hoạt điều kiện thoát, lệnh sẽ kẹt vô hạn. Chốt tại giá đóng
    # cuối cùng biết được — đúng như thể đã đóng cuối phiên hôm đó.
    _stuck = st.session_state.get("ps_position")
    if _stuck and df_1m is not None and len(df_1m) and _can_trade():
        if _stuck["entry_ts"].date() != datetime.now().date():
            _px  = float(df_1m.iloc[-1]["Close"])
            _pnl = _position_pnl(_stuck, _px)
            _net = _pnl - _FEE_PTS
            _entry = {
                "time":   df_1m.index[-1].strftime("%Y-%m-%d %H:%M"),
                "ticker": "VN30F1M", "act": f"ĐÓNG {_stuck['side']}",
                "price":  _px, "sl": _stuck["sl"], "tp": None, "tp_method": "",
                "pnl":    f"{_pnl:+.1f}", "tid": _stuck["tid"],
                "result": "THẮNG" if _net > 0 else "THUA", "entry": _stuck["entry"],
                "reason": f"Đóng cuối phiên (dữ liệu dừng) · vào {_fvn(_stuck['entry'], 1)}",
            }
            st.session_state["ps_log_history"].insert(0, _entry)
            _append_journal(_entry)
            st.session_state["ps_last_closed"] = {
                "tid": _stuck["tid"], "side": _stuck["side"], "entry": _stuck["entry"],
                "exit": _px, "pnl": _pnl, "net": _net, "won": _net > 0,
                "reason": "Đóng cuối phiên (dữ liệu dừng)", "bars": _stuck["bars"],
                "time": _entry["time"],
            }
            st.session_state["ps_position"] = None
            _save_ps_state()

    # ── Cache trend (tính lại khi mtime đổi) ─────────────────────────────────
    cur_mtime_val = st.session_state["ps_last_mtime"]
    if cur_mtime_val != st.session_state["ps_trend_mtime"] and df_1m is not None:
        trend, trend_text, tf_detail = _get_trend_full(df_1m)
        st.session_state["ps_last_trend"]  = (trend, trend_text)
        st.session_state["ps_tf_detail"]   = tf_detail
        st.session_state["ps_trend_mtime"] = cur_mtime_val
    else:
        trend, trend_text = st.session_state["ps_last_trend"]

    # ── Logic trading ─────────────────────────────────────────────────────────
    # Phiên chỉ xem vẫn vẽ đủ giao diện bên dưới, chỉ không chạy engine
    # ⇒ không sinh mã lệnh, không ghi journal, không bắn Telegram trùng.
    if df_1m is not None and len(df_1m) > 0 and _can_trade():
        try:
            current_price = float(df_1m.iloc[-1]["Close"])
            last_time     = df_1m.index[-1].strftime("%Y-%m-%d %H:%M")

            # ── Xử lý mỗi khi có nến mới (tức nến trước đó vừa đóng) ────────
            if last_time != st.session_state["ps_last_time"]:
                # Ghi nhận ĐÃ NHẬN nến này NGAY, trước mọi tác động phụ.
                # Trước đây dòng này nằm cuối khối try, sau tất cả các lần gửi
                # Telegram và ghi journal. Chỉ cần một exception ở giữa là nến
                # cũ vẫn bị coi là "mới" ở lần render kế tiếp — mà fragment chạy
                # 1 lần/giây, nên tin nhắn bị gửi lại tới ~60 lần mỗi phút cho
                # tới khi có nến mới. Bỏ lỡ 1 nến còn hơn spam 60 tin.
                st.session_state["ps_last_time"] = last_time
                closed_df   = _closed_bars(df_1m, 400)
                has_closed  = closed_df is not None and len(closed_df) >= 2
                closed_ts   = closed_df.index[-1] if has_closed else None
                closed_px   = float(closed_df.iloc[-1]["Close"]) if has_closed else current_price
                closed_time = closed_ts.strftime("%Y-%m-%d %H:%M") if has_closed else last_time

                # Buy Stop / Sell Stop (thông tin tham khảo cho lệnh chờ)
                st.session_state["ps_stop_levels"] = _get_stop_levels(df_1m)

                rule_sig, rule_reason, rule_detail = _get_rule_signal(df_1m)
                st.session_state["ps_rule_signal"] = rule_sig
                st.session_state["ps_rule_reason"] = rule_reason
                st.session_state["ps_rule_detail"] = rule_detail
                st.session_state["ps_rule_mtime"]  = cur_mtime_val

                pos         = st.session_state.get("ps_position")
                closed_now  = False   # vừa đóng lệnh ở nhịp này → không mở lệnh mới cùng nhịp

                # ── 1) Đang có vị thế: chỉ xét thoát, KHÔNG nhận tín hiệu mới ──
                if pos and has_closed:
                    new_bars = closed_df[closed_df.index > pos["last_ts"]]
                    do_exit, exit_reason, exit_px, exit_ts = _check_position_exit(
                        pos, new_bars, in_session
                    )
                    if do_exit:
                        pnl     = _position_pnl(pos, exit_px)
                        net     = pnl - _FEE_PTS
                        won     = net > 0
                        icon    = "✅" if won else "❌"
                        ex_time = (exit_ts.strftime("%Y-%m-%d %H:%M")
                                   if exit_ts is not None else closed_time)
                        _send_telegram_async(
                            f"{icon} <b>#VN30F1M ĐÓNG {_tg_escape(pos['side'])} "
                            f"(lệnh #{pos['tid']})</b>\n"
                            f"{'🟢 THẮNG' if won else '🔴 THUA'}\n"
                            f"📥 Vào: {_fvn(pos['entry'], 1)} → 📤 Ra: {_fvn(exit_px, 1)}\n"
                            f"💵 PnL: {_fvn(pnl, 1, signed=True)}đ (sau phí {_fvn(net, 2, signed=True)}đ "
                            f"= {_fvn(net * _PT_VALUE_VND, 0, signed=True)} VND/HĐ)\n"
                            f"⏱️ Giữ {pos['bars']} phút · {_tg_escape(exit_reason)}"
                        )
                        log_entry = {
                            "time":   ex_time, "ticker": "VN30F1M",
                            "act":    f"ĐÓNG {pos['side']}", "price": exit_px,
                            "sl":     pos["sl"], "tp": None, "tp_method": "",
                            "pnl":    f"{pnl:+.1f}",
                            "tid":    pos["tid"],
                            "result": "THẮNG" if won else "THUA",
                            "entry":  pos["entry"],
                            "reason": f"{exit_reason} · vào {_fvn(pos['entry'], 1)} · giữ {pos['bars']} phút",
                        }
                        st.session_state["ps_log_history"].insert(0, log_entry)
                        _append_journal(log_entry)
                        # Lưu lệnh vừa đóng để hiện banner nhận biết ngay trên đầu tab
                        st.session_state["ps_last_closed"] = {
                            "tid": pos["tid"], "side": pos["side"], "entry": pos["entry"],
                            "exit": exit_px, "pnl": pnl, "net": net, "won": won,
                            "reason": exit_reason, "bars": pos["bars"], "time": ex_time,
                        }
                        st.session_state["ps_position"] = None
                        _save_ps_state()
                        pos, closed_now = None, True
                    else:
                        # Tín hiệu ngược chiều lệnh đang giữ — chỉ báo MỘT lần cho
                        # mỗi vị thế, và chỉ khi user bật. Xem chú thích ở checkbox.
                        if (st.session_state.get("ps_notify_counter")
                                and rule_sig in ("LONG", "SHORT")
                                and rule_sig != pos["side"]
                                and not pos.get("counter_notified")):
                            pos["counter_notified"] = True
                            _send_telegram_async(
                                f"👀 <b>#VN30F1M Tín hiệu {_tg_escape(rule_sig)} "
                                f"ngược chiều lệnh {_tg_escape(pos['side'])} #{pos['tid']}</b>\n"
                                f"📊 CHỈ ĐỂ QUAN SÁT — số liệu cho thấy KHÔNG nên đảo lệnh:\n"
                                f"   giữ đến hết +0,356đ/lệnh · thoát sớm +0,086đ (−76%) · "
                                f"đảo lệnh +0,047đ (−87%)\n"
                                f"🤖 Bot vẫn giữ {_tg_escape(pos['side'])} #{pos['tid']} "
                                f"đến khi hết {_HOLD_BARS} phút hoặc chạm SL "
                                f"{_fvn(pos['sl'], 1)}\n"
                                f"⚡ {_tg_escape(rule_reason)}"
                            )
                        st.session_state["ps_position"] = pos
                        _save_ps_state()

                # ── 2) LSTM — vẫn tính ở mọi chế độ để panel "Dự báo AI" không
                #        hiển thị số cũ; chi phí ~30ms và chỉ chạy mỗi nến mới.
                lstm_sig = "WAIT"
                if ai_model is not None:
                    prob_long_new, prob_short_new, ai_warn = _get_ai_prediction(df_1m, ai_scaler, ai_model)
                    prob_long, prob_short = prob_long_new, prob_short_new
                    st.session_state["ps_last_prob_long"]  = prob_long
                    st.session_state["ps_last_prob_short"] = prob_short
                    st.session_state["ps_ai_warn"]         = ai_warn
                    if prob_long >= thr_long:     lstm_sig = "LONG"
                    elif prob_short >= thr_short: lstm_sig = "SHORT"
                    if strict_trend:
                        if lstm_sig == "LONG"  and trend != 1:  lstm_sig = "WAIT"
                        if lstm_sig == "SHORT" and trend != -1: lstm_sig = "WAIT"

                # ── 3) Tổng hợp tín hiệu ──────────────────────────────────────
                if signal_mode == "Rule-based":
                    ai_signal, signal_desc = rule_sig, rule_reason
                elif signal_mode == "LSTM":
                    ai_signal   = lstm_sig
                    signal_desc = f"LSTM L={prob_long*100:.0f}% S={prob_short*100:.0f}%"
                elif rule_sig != "WAIT" and rule_sig == lstm_sig:
                    ai_signal, signal_desc = rule_sig, f"Ensemble✓ {rule_reason}"
                else:
                    ai_signal   = "WAIT"
                    signal_desc = f"Ensemble: Rule={rule_sig} LSTM={lstm_sig}"

                if not in_session:
                    ai_signal = "WAIT"
                # Dữ liệu đã dừng → tuyệt đối không vào lệnh mới. Nếu không có
                # chặn này, lần đầu mở tab trong ngày sẽ thấy "nến mới" (thực ra
                # là nến của phiên trước) và bot vào lệnh theo giá đã cũ.
                if data_stale:
                    ai_signal = "WAIT"

                # ── 4) Nhật ký chẩn đoán (ghi mọi nến, kể cả WAIT) ────────────
                _atr_now  = _get_atr(df_1m)
                _blocked  = ("đang giữ lệnh" if st.session_state.get("ps_position")
                             else ("vừa đóng lệnh" if closed_now else ""))
                _audit_sl = None
                if ai_signal != "WAIT" and _atr_now:
                    _r = max(_SL_ATR_MULT * _atr_now, _MIN_SL_PTS)
                    _audit_sl = round(closed_px - _r if ai_signal == "LONG" else closed_px + _r, 1)
                audit = st.session_state.setdefault("ps_signal_audit", [])
                audit.insert(0, {
                    "time":    closed_time, "price":  closed_px,
                    "signal":  ai_signal,   "reason": (f"[{_blocked}] " if _blocked else "") + signal_desc,
                    "trend":   trend,       "sl":     _audit_sl,
                })
                st.session_state["ps_signal_audit"] = audit[:50]

                # ── 5) Chưa có vị thế → mở lệnh mới ───────────────────────────
                if (ai_signal != "WAIT" and not st.session_state.get("ps_position")
                        and not closed_now and has_closed and _atr_now):
                    _tid = _next_trade_id()
                    st.session_state["ps_trade_seq"] = _tid
                    _tp_r = _TP_R_MULT if st.session_state.get("ps_use_tp") else None
                    pos = _open_position(ai_signal, closed_px, closed_ts, _atr_now,
                                         tid=_tid, tp_r=_tp_r)
                    st.session_state["ps_position"] = pos
                    _save_ps_state()
                    # Mốc lãi tham chiếu — KHÔNG phải lệnh chốt. Engine cố ý không có
                    # TP (thêm TP làm kém đi, xem _WIN_PCTL); nhưng tin nhắn chỉ có SL
                    # khiến người đọc tưởng thiếu thông tin, nên đưa mốc vào cho đủ.
                    _sgn    = 1 if ai_signal == "LONG" else -1
                    _tp_ref = " / ".join(
                        _fvn(pos["entry"] + _sgn * _p, 1) for _p in _WIN_PCTL.values()
                    )
                    icon = "🚀" if ai_signal == "LONG" else "🔻"
                    _rd = st.session_state.get("ps_rule_detail") or {}
                    _strong_line = (
                        "⭐ <b>TÍN HIỆU MẠNH</b> — cú tăng tốc MACD thuộc nhóm ~14% "
                        "mạnh nhất (lịch sử: +1,45đ/lệnh so với +0,29đ nhóm thường)\n"
                        if _rd.get("strong") else ""
                    )
                    _send_telegram_async(
                        f"{icon} <b>#VN30F1M MỞ {_tg_escape(ai_signal)} (lệnh #{_tid})</b>\n"
                        + _strong_line +
                        f"📥 Vào: {_fvn(pos['entry'], 1)}  (nến đóng {closed_ts.strftime('%H:%M')})\n"
                        f"🛡️ SL: {_fvn(pos['sl'], 1)}  (rủi ro {_fvn(pos['risk'], 1)}đ = "
                        f"{_fvn(pos['risk'] * _PT_VALUE_VND)} VND/HĐ)\n"
                        f"📈 Vùng lãi tham chiếu: {_tp_ref}\n"
                        f"   (trung vị / top 30% / top 10% của lệnh thắng — bot KHÔNG tự chốt ở đây)\n"
                        + (f"🎯 TP: {_fvn(pos['tp'], 1)} ({_TP_R_MULT:g}R)\n"
                           if pos.get("tp") else "")
                        + f"⏱️ Thoát sau tối đa {_HOLD_BARS} phút hoặc khi chạm SL\n"
                        f"⚡ {_tg_escape(signal_desc)}\n"
                        f"⚠️ Chỉ tham khảo — tự quyết định vào lệnh"
                    )
                    log_entry = {
                        "time":   closed_time, "ticker": "VN30F1M",
                        "act":    f"MỞ {ai_signal}", "price": pos["entry"],
                        "sl":     pos["sl"], "tp": None, "tp_method": "",
                        "pnl":    "—",       "tid": _tid, "result": "",
                        "entry":  pos["entry"], "reason": signal_desc,
                    }
                    st.session_state["ps_log_history"].insert(0, log_entry)
                    _append_journal(log_entry)

        except Exception as e:
            err_msg = f"[{datetime.now().strftime('%H:%M:%S')}] {type(e).__name__}: {e}"
            errs = st.session_state["ps_errors"]
            errs.insert(0, err_msg)
            st.session_state["ps_errors"] = errs[:10]

    elif _DATA_FILE_1M and not os.path.exists(_DATA_FILE_1M):
        st.info(f"📂 Đang chờ file `{_DATA_FILE_1M}` từ Amibroker...")

    # ── Cảnh báo dữ liệu cũ — phải chỉ đúng thủ phạm ─────────────────────────
    # Có HAI nguyên nhân khác hẳn nhau, chữa cũng khác nhau:
    #   (a) AFL không chạy       → mtime file cũ
    #   (b) AFL chạy nhưng nguồn Amibroker đã dừng → mtime mới, nến bên trong cũ
    # Bản cũ luôn đổ lỗi cho (a) nên dẫn người dùng đi tìm sai chỗ.
    if df_1m is not None and in_session and data_age is not None and data_age > 3:
        try:
            last_dt = df_1m.index[-1]
            try:
                file_age = (datetime.now().timestamp()
                            - os.path.getmtime(_DATA_FILE_1M)) / 60
            except Exception:
                file_age = None

            if file_age is not None and file_age <= 10:
                st.error(
                    f"🛑 **Nguồn dữ liệu Amibroker đã dừng.** Nến mới nhất là "
                    f"`{last_dt.strftime('%d/%m %H:%M')}` (cũ {data_age:.0f} phút), "
                    f"trong khi AFL vẫn ghi file bình thường "
                    f"({file_age:.0f} phút trước) — **AFL không phải nguyên nhân**. "
                    f"Hãy kiểm tra kết nối / đăng nhập nguồn dữ liệu intraday "
                    f"trong Amibroker. Bot đã ngừng vào lệnh mới "
                    f"(ngưỡng {_MAX_STALE_MIN} phút)."
                )
            else:
                _fa = f"{file_age:.0f} phút trước" if file_age is not None else "không rõ"
                st.warning(
                    f"⚠️ **Dữ liệu cũ {data_age:.0f} phút.** AFL có thể chưa chạy "
                    f"Explorer — file ghi lần cuối {_fa}, nến gần nhất "
                    f"`{last_dt.strftime('%d/%m %H:%M')}`. Bot đã ngừng vào lệnh mới "
                    f"(ngưỡng {_MAX_STALE_MIN} phút)."
                )
        except Exception:
            pass

    if st.session_state.get("ps_ai_warn"):
        st.warning(st.session_state["ps_ai_warn"])

    if st.session_state["ps_errors"]:
        with st.expander(f"🐛 Lỗi hệ thống ({len(st.session_state['ps_errors'])})", expanded=True):
            for err in st.session_state["ps_errors"]:
                st.code(err, language=None)
            if st.button("Xóa log lỗi", key="ps_clear_err"):
                st.session_state["ps_errors"] = []
                st.rerun()

    # ── Panel chẩn đoán tín hiệu — 20 candle gần nhất ───────────────────────
    audit_log = st.session_state.get("ps_signal_audit", [])
    if audit_log:
        with st.expander(f"🔍 Chẩn đoán tín hiệu — {len(audit_log)} candle gần nhất", expanded=False):
            _sig_colors = {"LONG": "#00E676", "SHORT": "#FF5252", "WAIT": "#555"}
            rows_html = ""
            for a in audit_log[:20]:
                clr   = _sig_colors.get(a["signal"], "#555")
                tdot  = "↑" if a["trend"] == 1 else "↓" if a["trend"] == -1 else "→"
                sl_v  = a.get("sl")
                sl_td = (f"<td style='color:#FF5252;font-size:11px;padding:2px 6px'>{sl_v:.1f}</td>"
                         if sl_v else "<td style='color:#555;font-size:11px;padding:2px 6px'>—</td>")
                rows_html += (
                    f"<tr>"
                    f"<td style='color:#888;font-size:11px;padding:2px 6px'>{a['time'][-5:]}</td>"
                    f"<td style='color:#ccc;font-size:11px;padding:2px 6px'>{_fvn(a['price'], 1)}</td>"
                    f"<td style='color:{clr};font-weight:700;font-size:11px;padding:2px 6px'>{a['signal']}</td>"
                    f"{sl_td}"
                    f"<td style='color:#888;font-size:10px;padding:2px 6px'>{tdot} {a['reason'][:90]}</td>"
                    f"</tr>"
                )
            st.markdown(
                f"<table style='width:100%;border-collapse:collapse'>"
                f"<thead><tr>"
                f"<th style='color:#666;font-size:10px;padding:2px 6px'>Giờ</th>"
                f"<th style='color:#666;font-size:10px;padding:2px 6px'>Giá</th>"
                f"<th style='color:#666;font-size:10px;padding:2px 6px'>Signal</th>"
                f"<th style='color:#FF5252;font-size:10px;padding:2px 6px'>SL</th>"
                f"<th style='color:#666;font-size:10px;padding:2px 6px'>Lý do</th>"
                f"</tr></thead><tbody>{rows_html}</tbody></table>",
                unsafe_allow_html=True,
            )

    # ══ TRẠNG THÁI BOT — nguồn sự thật duy nhất ═══════════════════════════════
    # Trước đây panel vị thế, card "TÍN HIỆU" và 4 card hành động đều tự suy ra
    # trạng thái riêng nên mâu thuẫn nhau (đang giữ lệnh mà card báo "QUAN SÁT").
    # Nay mọi thứ đọc từ đúng một biến trạng thái tính ở đây.
    _pos    = st.session_state.get("ps_position")
    _detail = st.session_state.get("ps_rule_detail", {})

    if _pos:
        _st_clr   = "#00E676" if _pos["side"] == "LONG" else "#FF5252"
        _st_label = (f"{'🚀' if _pos['side'] == 'LONG' else '🔻'} "
                     f"ĐANG GIỮ {_pos['side']} #{_pos['tid']}")
        _st_sub   = (f"vào {_fvn(_pos['entry'], 1)} lúc {_pos['entry_ts'].strftime('%H:%M')} · "
                     f"còn tối đa {max(0, _HOLD_BARS - _pos['bars'])} phút")
    elif not in_session:
        _st_clr, _st_label = "#9CA3AF", "🌙 NGOÀI PHIÊN"
        _st_sub = "bot không vào lệnh mới ngoài giờ giao dịch"
    elif data_stale:
        _st_clr, _st_label = "#FFB300", "🛑 DỮ LIỆU DỪNG"
        _st_sub = (f"nến mới nhất đã cũ {data_age:.0f} phút — "
                   f"bot ngừng vào lệnh để tránh giao dịch theo giá quá hạn")
    elif rule_sig in ("LONG", "SHORT"):
        _st_clr   = "#00E676" if rule_sig == "LONG" else "#FF5252"
        _st_label = f"{'🚀' if rule_sig == 'LONG' else '🔻'} CHUẨN BỊ VÀO {rule_sig}"
        _st_sub   = "đủ điều kiện — sẽ mở lệnh ở nến tiếp theo"
    else:
        _st_clr, _st_label = "#9CA3AF", "⏳ CHỜ TÍN HIỆU"
        _st_sub = "chưa đủ 2 điều kiện vào lệnh (xem bên dưới)"

    st.subheader("🎯 Trạng Thái Bot")

    # ── Banner lệnh vừa đóng — dấu hiệu nhận biết rõ ràng ────────────────────
    # Chỉ hiện khi CHƯA có lệnh mới: nếu bot đã vào lệnh khác thì banner "vừa
    # đóng" sẽ mâu thuẫn với khối trạng thái ngay bên dưới. Lịch sử đầy đủ nằm
    # ở bảng nhật ký.
    _lc = st.session_state.get("ps_last_closed") if not _pos else None
    if _lc:
        _lc_clr = "#00E676" if _lc["won"] else "#FF5252"
        _lc_bg  = "#0d2b1a" if _lc["won"] else "#2b0d0d"
        st.markdown(
            f"<div style='background:{_lc_bg};color:#f0f0f0;border:2px solid {_lc_clr};"
            f"border-radius:10px;padding:11px 16px;margin-bottom:10px'>"
            f"<span style='font-size:15px;font-weight:700;color:{_lc_clr}'>"
            f"{'✅' if _lc['won'] else '❌'} ĐÃ ĐÓNG lệnh #{_lc['tid']} {_lc['side']} — "
            f"{'THẮNG' if _lc['won'] else 'THUA'} {_fvn(_lc['net'], 2, signed=True)}đ</span>"
            f"<span style='font-size:12px;color:#cccccc'> "
            f"({_fvn(_lc['net'] * _PT_VALUE_VND, 0, signed=True)} VND/HĐ, đã trừ phí)</span><br>"
            f"<span style='font-size:12.5px;color:#cccccc'>"
            f"vào {_fvn(_lc['entry'], 1)} → ra {_fvn(_lc['exit'], 1)} · giữ {_lc['bars']} phút · "
            f"{_lc['reason']} · lúc {_lc['time'][-5:]}</span></div>",
            unsafe_allow_html=True,
        )

    col1, col2, col3 = st.columns([1, 1.6, 1])

    col1.markdown(
        f"<div class='ps-box' style='color:#f0f0f0'>"
        f"<span style='font-size:12px;color:#9CA3AF'>Giá thị trường</span><br>"
        f"<b style='font-size:28px;color:#f0f0f0'>{_fvn(current_price, 1)}</b><br>"
        f"<span style='font-size:11px;color:#9CA3AF'>{last_time or '—'}</span></div>",
        unsafe_allow_html=True,
    )
    col2.markdown(
        f"<div class='ps-box' style='color:#f0f0f0;border-color:{_st_clr}'>"
        f"<b style='font-size:21px;color:{_st_clr}'>{_st_label}</b><br>"
        f"<span style='font-size:12px;color:#cccccc'>{_st_sub}</span></div>",
        unsafe_allow_html=True,
    )

    if _pos:
        _live_pnl = _position_pnl(_pos, current_price) if current_price else 0.0
        _pnl_clr  = "#00E676" if _live_pnl > 0 else "#FF5252"
        col3.markdown(
            f"<div class='ps-box' style='color:#f0f0f0'>"
            f"<span style='font-size:12px;color:#9CA3AF'>PnL lệnh đang giữ</span><br>"
            f"<b style='font-size:24px;color:{_pnl_clr}'>{_fvn(_live_pnl, 1, signed=True)}đ</b><br>"
            f"<span style='font-size:11px;color:#cccccc'>"
            f"{_fvn(_live_pnl * _PT_VALUE_VND, 0, signed=True)} VND/HĐ · chưa trừ phí</span></div>",
            unsafe_allow_html=True,
        )
    else:
        _s = _calc_session_stats(st.session_state["ps_log_history"])
        _n_clr = "#00E676" if _s["net_pnl"] > 0 else ("#FF5252" if _s["net_pnl"] < 0 else "#9CA3AF")
        col3.markdown(
            f"<div class='ps-box' style='color:#f0f0f0'>"
            f"<span style='font-size:12px;color:#9CA3AF'>PnL phiên (sau phí)</span><br>"
            f"<b style='font-size:24px;color:{_n_clr}'>{_fvn(_s['net_pnl'], 2, signed=True)}đ</b><br>"
            f"<span style='font-size:11px;color:#cccccc'>{_s['total']} lệnh đã đóng</span></div>",
            unsafe_allow_html=True,
        )

    # ── Chi tiết vị thế đang giữ ─────────────────────────────────────────────
    if _pos:
        _done = min(_pos["bars"], _HOLD_BARS)
        _pct  = int(_done / _HOLD_BARS * 100)
        st.markdown(
            f"<div style='background:#12161f;border:1px solid {_st_clr};border-radius:8px;"
            f"padding:9px 14px;margin:6px 0;color:#f0f0f0;font-size:13px'>"
            f"Giá vào <b style='color:#f0f0f0'>{_fvn(_pos['entry'], 1)}</b>"
            + (f" &nbsp;·&nbsp; Chốt lời <b style='color:#00E676'>"
               f"{_fvn(_pos['tp'], 1)}</b>" if _pos.get("tp") else "")
            + f" &nbsp;·&nbsp; Cắt lỗ <b style='color:#FF6B6B'>{_fvn(_pos['sl'], 1)}</b>"
            f" <span style='color:#cccccc'>(rủi ro {_fvn(_pos['risk'], 1)}đ = "
            f"{_fvn(_pos['risk'] * _PT_VALUE_VND)} VND/HĐ)</span>"
            f" &nbsp;·&nbsp; Đã giữ <b style='color:#f0f0f0'>{_done}/{_HOLD_BARS}</b> phút"
            f"<div style='background:#2a2a2a;border-radius:3px;height:6px;margin-top:7px'>"
            f"<div style='background:{_st_clr};width:{_pct}%;height:6px;border-radius:3px'></div>"
            f"</div>"
            + (f"<span style='font-size:11px;color:#cccccc'>Bot sẽ tự đóng khi chạm "
               f"chốt lời, cắt lỗ, hết {_HOLD_BARS} phút, hoặc kết phiên.</span></div>"
               if _pos.get("tp") else
               f"<span style='font-size:11px;color:#cccccc'>Bot sẽ tự đóng khi chạm cắt lỗ, "
               f"hết {_HOLD_BARS} phút, hoặc kết phiên — "
               f"<b style='color:#cccccc'>không có lệnh chốt lời cố định</b>.</span></div>"),
            unsafe_allow_html=True,
        )

    # ── Vùng lãi tham chiếu ──────────────────────────────────────────────────
    # Engine không có TP cố định (thêm TP làm kém đi — xem _WIN_PCTL). Nhưng
    # user cần biết "lệnh này đang chạy tốt hay xấu", nên quy các phân vị của
    # lệnh THẮNG lịch sử ra mức giá cụ thể. Ghi rõ đây KHÔNG phải lệnh chốt.
    if _pos:
        _sign = 1 if _pos["side"] == "LONG" else -1
        _now  = _position_pnl(_pos, current_price) if current_price else 0.0
        _cells = ""
        for _lbl, _pts in _WIN_PCTL.items():
            _px      = _pos["entry"] + _sign * _pts
            _reached = _now >= _pts
            _clr     = "#00E676" if _reached else "#9CA3AF"
            _cells += (
                f"<td style='padding:6px 10px;text-align:center;color:#f0f0f0'>"
                f"<div style='font-size:10px;color:#9CA3AF'>{_lbl}</div>"
                f"<div style='font-size:15px;font-weight:700;color:{_clr}'>"
                f"{'✓ ' if _reached else ''}{_fvn(_px, 1)}</div>"
                f"<div style='font-size:10px;color:#cccccc'>+{_fvn(_pts, 1)}đ · "
                f"{_fvn(_pts * _PT_VALUE_VND)}đ</div></td>"
            )
        st.markdown(
            f"<div style='background:#111;color:#f0f0f0;border:1px solid #2a2a2a;"
            f"border-radius:8px;padding:8px 12px;margin-bottom:8px'>"
            f"<div style='font-size:11px;color:#9CA3AF;margin-bottom:4px'>"
            f"📈 Vùng lãi tham chiếu — mức giá mà lệnh <b>thắng</b> trong quá khứ "
            f"thường đạt tới (n=1.065). Bot <b>không</b> tự chốt ở đây.</div>"
            f"<table style='width:100%;border-collapse:collapse'><tr>{_cells}</tr></table>"
            f"</div>",
            unsafe_allow_html=True,
        )
        st.caption(
            f"Engine v4 cố ý **không đặt chốt lời**: đo trên chính cấu hình này, "
            f"thêm TP ở mọi mức đều làm kém đi (không TP +0,356đ/lệnh · 3R +0,347 · "
            f"2R +0,276 · 1R +0,112) vì cắt mất đuôi lãi của các lệnh chạy xa. "
            f"Nếu bạn muốn tự chốt tay, các mốc trên là tham chiếu."
        )

    # ── Hai điều kiện của engine — giải thích vì sao đang chờ ────────────────
    if _detail:
        _above, _rising = _detail["above"], _detail["rising"]
        _c1_dir = "LONG" if _above  else "SHORT"
        _c2_dir = "LONG" if _rising else "SHORT"
        _agree  = _c1_dir == _c2_dir

        def _cond_card(title: str, value_html: str, direction: str) -> str:
            clr = "#00E676" if direction == "LONG" else "#FF5252"
            return (
                f"<div style='background:#111;color:#f0f0f0;border:1px solid #2a2a2a;"
                f"border-left:3px solid {clr};border-radius:6px;padding:8px 12px'>"
                f"<div style='font-size:11px;color:#9CA3AF;margin-bottom:3px'>{title}</div>"
                f"<div style='font-size:13px;color:#f0f0f0'>{value_html}</div>"
                f"<div style='font-size:11px;color:{clr};font-weight:700;margin-top:2px'>"
                f"→ nghiêng {direction}</div></div>"
            )

        cc1, cc2, cc3 = st.columns(3)
        cc1.markdown(_cond_card(
            "① Giá so với VWAP phiên",
            f"<b>{_fvn(_detail['close'], 1)}</b> {'&gt;' if _above else '&lt;'} "
            f"VWAP <b>{_fvn(_detail['vwap'], 1)}</b>",
            _c1_dir,
        ), unsafe_allow_html=True)
        cc2.markdown(_cond_card(
            "② MACD histogram",
            f"{_fvn(_detail['mh_prev'], 2, signed=True)} → "
            f"<b>{_fvn(_detail['mh'], 2, signed=True)}</b> "
            f"({'đang tăng' if _rising else 'đang giảm'})",
            _c2_dir,
        ), unsafe_allow_html=True)

        # ③ Cổng biến động — chặn TRƯỚC cả 2 điều kiện trên khi thị trường quá lặng
        _vr  = _detail.get("atr_ratio")
        _vok = _detail.get("vol_ok", True)
        _vclr = "#00E676" if _vok else "#FFB300"
        cc3.markdown(
            f"<div style='background:#111;color:#f0f0f0;border:1px solid #2a2a2a;"
            f"border-left:3px solid {_vclr};border-radius:6px;padding:8px 12px'>"
            f"<div style='font-size:11px;color:#9CA3AF;margin-bottom:3px'>"
            f"③ Biến động (cổng chặn)</div>"
            f"<div style='font-size:13px;color:#f0f0f0'>"
            + (f"ATR <b>{_fvn(_detail.get('atr'), 2)}</b> = <b>{_vr:.0%}</b> nền "
               f"(cần ≥{_MIN_ATR_RATIO:.0%})" if _vr is not None else "chưa đủ dữ liệu")
            + "</div>"
            f"<div style='font-size:11px;color:{_vclr};font-weight:700;margin-top:2px'>"
            f"{'→ đủ biến động' if _vok else '→ QUÁ LẶNG, chặn vào lệnh'}</div></div>",
            unsafe_allow_html=True,
        )

        # Kết luận PHẢI biết đang có vị thế hay không. Trước đây nó chỉ đọc 2 điều
        # kiện nên báo "engine ra tín hiệu LONG" (nền xanh, trông như lệnh nên vào)
        # ngay cả khi bot đang giữ SHORT — mâu thuẫn với khối trạng thái bên trên.
        if _agree and not _detail.get("vol_ok", True):
            _msg_clr, _msg_bg = "#FFB300", "#2b2410"
            _msg = (f"⚠️ Hai điều kiện nghiêng {_c1_dir} nhưng <b>biến động quá thấp</b> "
                    f"→ không vào lệnh. Thị trường lặng thì biên độ không bù nổi phí.")
        elif _agree and _pos and _c1_dir != _pos["side"]:
            _msg_clr, _msg_bg = "#FFB300", "#2b2410"
            _msg = (f"⚠️ Hai điều kiện nghiêng {_c1_dir}, <b>ngược chiều</b> lệnh "
                    f"{_pos['side']} #{_pos['tid']} đang giữ — bot <b>cố ý bỏ qua</b>, "
                    f"không đảo lệnh")
        elif _agree and _pos:
            _msg_clr, _msg_bg = ("#00E676", "#0d2b1a") if _c1_dir == "LONG" else ("#FF5252", "#2b0d0d")
            _msg = (f"✓ Hai điều kiện vẫn nghiêng {_c1_dir} — <b>thuận chiều</b> lệnh "
                    f"{_pos['side']} #{_pos['tid']} đang giữ")
        elif _agree:
            _msg_clr, _msg_bg = ("#00E676", "#0d2b1a") if _c1_dir == "LONG" else ("#FF5252", "#2b0d0d")
            _msg = f"✓ Hai điều kiện cùng nghiêng {_c1_dir} → engine ra tín hiệu <b>{_c1_dir}</b>"
        else:
            _msg_clr, _msg_bg = "#9CA3AF", "#1a1a1a"
            _msg = ("✗ Hai điều kiện ngược nhau → <b>chờ</b>. "
                    "Engine chỉ vào lệnh khi cả hai cùng chiều.")
        st.markdown(
            f"<div style='background:{_msg_bg};color:#f0f0f0;border-radius:6px;"
            f"padding:7px 12px;margin-top:6px;font-size:12.5px;border:1px solid {_msg_clr}'>"
            f"<span style='color:{_msg_clr}'>{_msg}</span>"
            f"<span style='color:#cccccc'> · tính trên nến đã đóng lúc {_detail['bar']}</span>"
            f"</div>",
            unsafe_allow_html=True,
        )
        if _pos:
            st.caption(
                "ℹ️ Đang giữ lệnh nên bot **bỏ qua mọi tín hiệu mới** cho tới khi đóng vị "
                "thế. Đây là ràng buộc đã kiểm chứng, không phải lỗi: **44,2%** số lệnh gặp "
                "tín hiệu ngược trước khi hết 30 phút — đó là nhiễu khung 1 phút, không phải "
                "đảo chiều thật. Thoát theo nó làm lợi nhuận rơi từ **+0,356 xuống +0,086đ/lệnh** "
                "(−76%, chỉ còn dương 3/5 quý); đảo lệnh luôn thì còn **+0,047đ** (−87%)."
            )

    # ── Thông tin tham khảo — KHÔNG tham gia quyết định tín hiệu ─────────────
    # Trend đa khung, lệnh chờ Buy/Sell Stop và dự báo LSTM từng nằm ngang hàng
    # với tín hiệu nên gây hiểu nhầm là chúng điều khiển bot. Đưa vào expander
    # và ghi rõ vai trò tham khảo.
    with st.expander("📊 Thông tin tham khảo (không dùng để ra tín hiệu)", expanded=True):
        st.caption(
            "Engine v4 chỉ dùng 2 điều kiện ở trên. Các chỉ báo dưới đây để bạn "
            "tự đối chiếu — bot **không** dựa vào chúng."
        )

        st.markdown("**Xu hướng đa khung**")
        tf_detail = st.session_state.get("ps_tf_detail", {})
        if tf_detail:
            tf_c1, tf_c2, tf_c3 = st.columns(3)
            for col_ui, tf_key, tf_label in [
                (tf_c1, "1m",  "🕐 1 Phút"),
                (tf_c2, "15m", "🕒 15 Phút"),
                (tf_c3, "1h",  "🕐 1 Giờ"),
            ]:
                info = tf_detail.get(tf_key, {})
                t    = info.get("trend", 0)
                lbl  = info.get("label", "—")
                ema  = info.get("ema")
                cls  = info.get("close")
                clr  = "#00E676" if t == 1 else ("#FF5252" if t == -1 else "#9CA3AF")
                ema_str = (f"<br><span style='font-size:10px;color:#cccccc'>"
                           f"EMA={_fvn(ema, 1)} | C={_fvn(cls, 1)}</span>") if ema else ""
                col_ui.markdown(
                    f"<div style='background:#111;color:#f0f0f0;border:1px solid #2a2a2a;"
                    f"border-left:3px solid {clr};border-radius:6px;padding:7px 10px;"
                    f"text-align:center'>"
                    f"<div style='font-size:10px;color:#9CA3AF;margin-bottom:2px'>{tf_label}</div>"
                    f"<div style='font-size:14px;font-weight:700;color:{clr}'>{lbl}</div>"
                    f"{ema_str}</div>",
                    unsafe_allow_html=True,
                )
            st.caption(f"Tổng hợp: {trend_text}")

        st.markdown("**Mức lệnh chờ (Buy Stop / Sell Stop)**")
        st.caption(
            "Đây là kiểu vào lệnh khác — đặt lệnh chờ vượt đỉnh/thủng đáy 10 nến. "
            "Bot v4 vào lệnh tại giá thị trường, **không** dùng các mức này."
        )
        stop_lvl = st.session_state.get("ps_stop_levels", {})
        atr_val  = stop_lvl.get("atr")

        def _stop_card(icon, label, entry_px, sl_px, atr, border):
            if entry_px is None:
                body = "<span style='color:#cccccc'>Đang tính...</span>"
            else:
                rr_pts = abs(entry_px - sl_px) if sl_px else 0
                body = (
                    f"<span style='font-size:12px;color:#cccccc'>Kích hoạt tại</span><br>"
                    f"<b style='font-size:19px;color:{border}'>{entry_px:.1f}</b><br>"
                    f"<span style='font-size:12px;color:#FF6B6B'>SL {sl_px:.1f}</span>"
                    f"<span style='font-size:11px;color:#cccccc'> ({_fvn(rr_pts, 1)}đ)</span>"
                )
            return (
                f"<div style='background:#111;color:#f0f0f0;border:1px solid {border};"
                f"border-radius:8px;padding:9px 12px;text-align:center'>"
                f"<div style='font-size:13px;font-weight:bold;color:{border}'>{icon} {label}</div>"
                f"<div style='margin-top:4px'>{body}</div></div>"
            )

        sc1, sc2 = st.columns(2)
        sc1.markdown(_stop_card("⬆️", "BUY STOP", stop_lvl.get("buy_stop_price"),
                                stop_lvl.get("buy_stop_sl"), atr_val, "#29B6F6"),
                     unsafe_allow_html=True)
        sc2.markdown(_stop_card("⬇️", "SELL STOP", stop_lvl.get("sell_stop_price"),
                                stop_lvl.get("sell_stop_sl"), atr_val, "#CE93D8"),
                     unsafe_allow_html=True)
        if atr_val:
            st.caption(f"ATR14 hiện tại: {atr_val}đ → cắt lỗ của bot = "
                       f"{_fvn(max(_SL_ATR_MULT * atr_val, _MIN_SL_PTS), 1)}đ")

        if ai_model is not None:
            st.markdown("**Dự báo LSTM**")
            prob_wait = max(0.0, 1.0 - prob_long - prob_short)
            st.markdown(
                f"<div style='display:flex;height:10px;border-radius:5px;overflow:hidden;"
                f"margin:4px 0'>"
                f"<div style='width:{prob_short*100:.1f}%;background:#FF5252'></div>"
                f"<div style='width:{prob_wait*100:.1f}%;background:#444'></div>"
                f"<div style='width:{prob_long*100:.1f}%;background:#00E676'></div></div>"
                f"<div style='font-size:11px;color:#f0f0f0'>"
                f"<span style='color:#FF5252'>SHORT {prob_short*100:.1f}%</span> · "
                f"<span style='color:#cccccc'>CHỜ {prob_wait*100:.1f}%</span> · "
                f"<span style='color:#00E676'>LONG {prob_long*100:.1f}%</span></div>",
                unsafe_allow_html=True,
            )
            st.caption(
                f"Chỉ có tác dụng khi chọn chế độ **LSTM** hoặc **Ensemble** "
                f"(hiện đang: {signal_mode})."
            )

    st.divider()

    # ── Thống kê session ──────────────────────────────────────────────────────
    stats = _calc_session_stats(st.session_state["ps_log_history"])
    s1, s2, s3, s4, s5 = st.columns(5)
    s1.metric("Số lệnh đóng",  stats["total"])
    s2.metric("Thắng / Thua",  f"{stats['wins']} / {stats['losses']}")
    s3.metric("Win Rate",      f"{stats['win_rate']:.0f}%")
    s4.metric("PnL gộp",       f"{_fvn(stats['total_pnl'], 1, signed=True)}đ")
    s5.metric(f"PnL sau phí ({_FEE_PTS}đ/lệnh)",
              f"{_fvn(stats['net_pnl'], 2, signed=True)}đ",
              delta=f"{_fvn(stats['net_pnl'] * _PT_VALUE_VND, 0, signed=True)} VND/HĐ")
    if stats["total"] == 0:
        st.caption(
            "Chưa có lệnh nào đóng trong phiên này. Thống kê tính từ các record "
            "**ĐÓNG** do bot tự sinh khi vị thế chạm SL / hết hạn giữ / hết phiên."
        )

    st.divider()

    # ── Nhật ký ──────────────────────────────────────────────────────────────
    st.subheader("📜 Nhật Ký Lệnh (Session)")
    st.caption(
        "Mỗi lệnh có 2 dòng cùng mã **#số**: dòng **MỞ** khi vào lệnh và dòng "
        "**ĐÓNG** khi thoát. Dòng ĐÓNG có nền đậm hơn, viền trái và dấu ✅/❌ "
        "theo kết quả sau phí."
    )
    log = st.session_state["ps_log_history"]
    if log:
        # Mã lệnh đã có dòng ĐÓNG → dòng MỞ của nó KHÔNG được ghi "đang chạy…"
        _closed_tids = {x.get("tid") for x in log
                        if str(x.get("act", "")).startswith("ĐÓNG") and x.get("tid")}
        rows = ""
        for item in log[:25]:
            act      = item["act"]
            is_close = act.startswith("ĐÓNG")
            side     = "LONG" if "LONG" in act else ("SHORT" if "SHORT" in act else "")
            tid      = item.get("tid", "")
            tid_str  = (f"<span style='color:#9CA3AF;font-size:.78rem'>#{tid}</span> "
                        if tid else "")
            pnl      = str(item["pnl"])
            p_str    = (_fvn(item['price'], 1) if isinstance(item["price"], (int, float))
                        else str(item["price"]))
            sl_v     = item.get("sl")
            sl_str   = f"<span style='color:#FF5252'>{_fvn(sl_v, 1)}</span>" if sl_v else "—"

            if is_close:
                # Màu theo KẾT QUẢ (thắng/thua), không theo chiều lệnh — trước đây
                # "LONG" in act nên lệnh LONG thua vẫn hiện xanh, không phân biệt được.
                try:
                    _net = float(pnl) - _FEE_PTS
                    won  = _net > 0
                except ValueError:
                    _net, won = 0.0, False
                res_clr  = "#00E676" if won else "#FF5252"
                act_html = (
                    f"<span style='color:{res_clr};font-weight:700'>"
                    f"{'✅' if won else '❌'} ĐÓNG {side}</span>"
                    f"<br><span style='font-size:.75rem;color:{res_clr}'>"
                    f"{'THẮNG' if won else 'THUA'}</span>"
                )
                # 2 chữ số thập phân: PnL gộp bước 0,1 nhưng phí 0,25 nên net luôn
                # lẻ 0,05 — làm tròn 1 chữ số sẽ lệch với con số VND ngay bên cạnh
                # (-1,1đ nhưng -105.000đ). Số theo chuẩn VN cho khớp báo cáo.
                pnl_str = (f"<span style='color:{res_clr};font-weight:700'>"
                           f"{_fvn(float(pnl), 1, signed=True)}đ</span>"
                           f"<br><span style='font-size:.72rem;color:#cccccc'>"
                           f"sau phí {_fvn(_net, 2, signed=True)}đ = "
                           f"{_fvn(_net * _PT_VALUE_VND, 0, signed=True)}đ</span>")
                row_style = (f"background:{'rgba(0,230,118,.10)' if won else 'rgba(255,82,82,.10)'};"
                             f"border-left:4px solid {res_clr}")
                ent = item.get("entry")
                p_str = (f"{_fvn(ent, 1)} → <b>{p_str}</b>" if isinstance(ent, (int, float))
                         else f"<b>{p_str}</b>")
            else:
                side_clr = "#00E676" if side == "LONG" else "#FF5252"
                act_html = (f"<span style='color:{side_clr};font-weight:700'>"
                            f"{'🚀' if side == 'LONG' else '🔻'} MỞ {side}</span>")
                if tid and tid in _closed_tids:
                    pnl_str = ("<span style='color:#9CA3AF'>đã đóng</span>"
                               "<br><span style='font-size:.72rem;color:#9CA3AF'>"
                               "xem dòng ĐÓNG cùng mã</span>")
                else:
                    pnl_str = "<span style='color:#9CA3AF'>đang chạy…</span>"
                row_style = "border-left:4px solid #333"
                p_str     = f"<b>{p_str}</b>"

            rows += (
                f"<tr style='{row_style}'>"
                f"<td>{tid_str}{item['time'][-5:]}</td>"
                f"<td>{act_html}</td>"
                f"<td>{p_str}</td>"
                f"<td>{sl_str}</td>"
                f"<td>{pnl_str}</td>"
                f"<td style='font-size:.82rem;color:#cccccc'>{item['reason']}</td></tr>"
            )
        st.markdown(
            f"<table class='ps-log'>"
            f"<tr><th>#/Giờ</th><th>Hành động</th><th>Giá vào → ra</th>"
            f"<th style='color:#FF5252'>Cắt lỗ</th>"
            f"<th>PnL (điểm)</th><th>Lý do</th></tr>"
            f"{rows}</table>",
            unsafe_allow_html=True,
        )
    else:
        st.info(
            "Chưa có lệnh nào trong phiên. Bot sẽ tự vào lệnh khi cả 2 điều kiện "
            "cùng chiều, và tự đóng khi chạm cắt lỗ / hết "
            f"{_HOLD_BARS} phút / kết phiên."
        )

    # ── Journal CSV ───────────────────────────────────────────────────────────
    if _JOURNAL_FILE and os.path.exists(_JOURNAL_FILE):
        with st.expander("📂 Lịch sử Journal (file CSV)", expanded=False):
            try:
                df_j = pd.read_csv(_JOURNAL_FILE, on_bad_lines="skip", engine="python")
                _n_raw = len(df_j)
                # Lọc phòng vệ: chỉ giữ bản ghi v4. Dấu hiệu nhận biết là cột
                # `trade_id` — KHÔNG dùng riêng từ vựng action, vì bot cũ cũng ghi
                # "ĐÓNG SHORT" nhưng cột pnl mang ý nghĩa khác (có dòng -1.976,9đ),
                # lọc theo action sẽ để lọt đúng những dòng rác đó.
                if "trade_id" in df_j.columns:
                    df_j = df_j[pd.to_numeric(df_j["trade_id"], errors="coerce").notna()]
                else:
                    df_j = df_j.iloc[0:0]      # file toàn schema cũ
                if "action" in df_j.columns and len(df_j):
                    df_j = df_j[df_j["action"].astype(str).str.startswith(("MỞ", "ĐÓNG"))]
                _n_drop = _n_raw - len(df_j)
                df_j = df_j.tail(50).iloc[::-1].reset_index(drop=True)
                if _n_drop:
                    st.caption(
                        f"Đã lọc bỏ {_n_drop} bản ghi từ bot phiên bản cũ "
                        f"(BÁO / TÍN HIỆU) — định dạng PnL không tương thích."
                    )
                # Dòng ĐÓNG tô theo KẾT QUẢ (thắng/thua) chứ không theo chiều lệnh
                rows_j = ""
                for _, r in df_j.iterrows():
                    act      = str(r.get("action", ""))
                    is_close = act.startswith("ĐÓNG")
                    side     = "LONG" if "LONG" in act else ("SHORT" if "SHORT" in act else "")
                    pnl_v    = str(r.get("pnl", "—"))
                    tid      = str(r.get("trade_id", "") or "")
                    tid_str  = (f"<span style='color:#9CA3AF;font-size:11px'>#{tid.split('.')[0]}</span> "
                                if tid and tid != "nan" else "")
                    try:    sl_str = f"<span style='color:#FF5252'>{float(r.get('sl','')):.1f}</span>"
                    except (TypeError, ValueError): sl_str = "—"

                    if is_close:
                        try:
                            _net = float(pnl_v) - _FEE_PTS
                            won  = _net > 0
                        except ValueError:
                            _net, won = 0.0, False
                        clr      = "#00E676" if won else "#FF5252"
                        act_html = (f"<span style='color:{clr};font-weight:700'>"
                                    f"{'✅' if won else '❌'} ĐÓNG {side} · "
                                    f"{'THẮNG' if won else 'THUA'}</span>")
                        pnl_html = (f"<span style='color:{clr};font-weight:700'>{pnl_v}đ</span>"
                                    f"<br><span style='font-size:10px;color:#cccccc'>"
                                    f"sau phí {_fvn(_net, 2, signed=True)}đ</span>")
                        row_st   = (f"background:{'rgba(0,230,118,.10)' if won else 'rgba(255,82,82,.10)'};"
                                    f"border-left:4px solid {clr}")
                    else:
                        clr      = "#00E676" if side == "LONG" else "#FF5252"
                        act_html = (f"<span style='color:{clr};font-weight:700'>"
                                    f"{'🚀' if side == 'LONG' else '🔻'} {act}</span>")
                        pnl_html = "<span style='color:#9CA3AF'>—</span>"
                        row_st   = "border-left:4px solid #333"

                    rows_j += (
                        f"<tr style='{row_st}'>"
                        f"<td style='color:#cccccc;white-space:nowrap'>{tid_str}{r.get('time','')}</td>"
                        f"<td>{act_html}</td>"
                        f"<td style='color:#f0f0f0;font-weight:700'>{r.get('price','')}</td>"
                        f"<td>{sl_str}</td>"
                        f"<td>{pnl_html}</td>"
                        f"<td style='color:#cccccc;font-size:11px'>{str(r.get('reason',''))[:80]}</td>"
                        f"</tr>"
                    )
                if rows_j:
                    st.markdown(
                        "<style>"
                        ".jn-tbl{width:100%;border-collapse:collapse;font-size:13px}"
                        ".jn-tbl th{background:#1a1d24;color:#cccccc;padding:8px 12px;text-align:left;border-bottom:2px solid #333;white-space:nowrap}"
                        ".jn-tbl td{padding:8px 12px;border-bottom:1px solid #222;vertical-align:middle;color:#f0f0f0}"
                        "</style>"
                        f"<table class='jn-tbl'>"
                        f"<tr><th>#/Thời gian</th><th>Hành động</th><th>Giá</th>"
                        f"<th style='color:#FF5252'>⛔ Cắt lỗ</th>"
                        f"<th>PnL</th><th>Lý do</th></tr>"
                        f"{rows_j}</table>",
                        unsafe_allow_html=True,
                    )
                else:
                    st.info(
                        "Chưa có bản ghi nào theo định dạng v4. File journal sẽ được "
                        "ghi lại từ lệnh tiếp theo."
                    )
            except Exception as e:
                st.warning(f"Không đọc được file journal: {e}")

    # ── Trạng thái Telegram ──────────────────────────────────────────────────
    _render_telegram_panel()

    # ── Báo cáo lãi/lỗ cuối ngày ─────────────────────────────────────────────
    _render_daily_report(in_session)

    # ── Train LSTM v2 ─────────────────────────────────────────────────────────
    st.divider()
    with st.expander("🧠 Train / Retrain Model LSTM v2 (3-class)", expanded=False):
        st.caption(
            "**v2**: Label 3 class (SHORT/WAIT/LONG), class_weight balanced, 8 features. "
            "Model cũ (v1 binary) vẫn dùng được nhưng bị block SHORT — nên retrain."
        )
        st.info(
            "💡 **Khuyến nghị:** `Nhìn trước=10, Target=0.5` → nhiều label LONG/SHORT hơn "
            "(v1 default: 5/1.0 → chỉ 15% label không phải WAIT → LSTM bias về WAIT). "
            "Nếu kết quả LONG Recall < 30% → tăng `Nhìn trước` hoặc giảm `Target`."
        )
        t_c1, t_c2, t_c3 = st.columns(3)
        future_bars   = t_c1.number_input("Nhìn trước (nến 1min)", 3, 30, 10, key="ps_train_future")
        profit_target = t_c2.number_input("Target (điểm)", 0.3, 5.0, 0.5, 0.1, key="ps_train_target")
        epochs        = t_c3.number_input("Epochs tối đa", 10, 100, 30, 5, key="ps_train_epochs")

        data_ok = _DATA_FILE_1M and os.path.exists(_DATA_FILE_1M)
        if not data_ok:
            st.warning(f"⚠️ Chưa có file `vn30f1m_1min.csv`. Export từ Amibroker trước.")
        else:
            try:
                n_rows = sum(1 for _ in open(_DATA_FILE_1M)) - 1
                model_info = "❌ Chưa train"
                if _MODEL_PATH and os.path.exists(_MODEL_PATH):
                    _sc, _mdl = ai_scaler, ai_model
                    if _mdl is not None:
                        n_out = _mdl.output_shape[-1]
                        model_info = f"✅ v2 (3-class)" if n_out == 3 else "⚠️ v1 binary (SHORT bị block)"
                st.info(f"📊 Data: **{n_rows:,} nến** | Model: {model_info}")
            except Exception:
                pass

        if st.button("🚀 Bắt Đầu Train v2", disabled=not data_ok, key="ps_train_btn", type="primary"):
            _run_lstm_training(
                data_file=_DATA_FILE_1M,
                model_file=_MODEL_PATH,
                scaler_file=_SCALER_PATH,
                future_bars=int(future_bars),
                profit_target=float(profit_target),
                epochs=int(epochs),
            )

    # Auto-refresh được xử lý bởi @st.fragment(run_every=1) — không cần sleep/rerun ở đây
