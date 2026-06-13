"""
LSTM Training Pipeline — vn_invest
===================================
Đọc data từ Amibroker history_by_ticker (live), train hoặc fine-tune model.

Chạy:
    python -m vn_invest.train_lstm                    # full train v7 từ đầu
    python -m vn_invest.train_lstm --mode finetune    # fine-tune v6 → v7
    python -m vn_invest.train_lstm --mode analyze     # phân tích backtest, không train
    python -m vn_invest.train_lstm --mode cache       # chỉ build dataset cache
"""
import argparse
import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("train_lstm")

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_AMI_DIR   = Path(os.getenv("AMIBROKER_HIST_DIR", r"C:\AmibrokerData\history_by_ticker"))
_V6_MODEL  = Path(r"C:\AmibrokerData\stock_lstm_v6_multi.keras")
_V6_SCALER = Path(r"C:\AmibrokerData\stock_scaler_v6.pkl")
_V7_MODEL  = Path(r"C:\AmibrokerData\stock_lstm_v7.keras")
_V7_SCALER = Path(r"C:\AmibrokerData\stock_scaler_v7.pkl")

_HERE        = Path(__file__).parent.parent  # vn-invest-app/
CACHE_FILE   = _HERE / "data" / "dataset_cache.npz"
METRICS_FILE = _HERE / "data" / "model_metrics.json"
CONFIG_FILE  = _HERE / "data" / "train_config.json"

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
DEFAULT_CONFIG = {
    "seq_len":         30,
    "features":        ["RSI", "MACD_hist", "Dist_EMA", "Log_Ret", "Vol_Change",
                        "ATR_norm", "RSI_slope5", "Vol_ratio20", "EMA_trend", "BB_pos"],
    "targets":         {"t5": 2.0, "t10": 2.0, "t25": 3.0},
    "train_ratio":     0.70,
    "val_ratio":       0.15,
    "lstm_units":      [64, 32],
    "dropout":         0.3,
    "batch_size":      256,
    "max_epochs":      80,
    "patience":        12,
    "lr_initial":      0.001,
    "lr_factor":       0.5,
    "lr_patience":     5,
    "min_rows":        80,
    "finetune_epochs": 20,
    "finetune_lr":     0.0002,
}


def load_config() -> dict:
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    if CONFIG_FILE.exists():
        try:
            return {**DEFAULT_CONFIG, **json.loads(CONFIG_FILE.read_text(encoding="utf-8"))}
        except Exception:
            pass
    CONFIG_FILE.write_text(json.dumps(DEFAULT_CONFIG, indent=2, ensure_ascii=False), encoding="utf-8")
    return DEFAULT_CONFIG.copy()


# ---------------------------------------------------------------------------
# Feature engineering (10 features)
# ---------------------------------------------------------------------------
def _parse_ami_date(d) -> str:
    s = str(int(d)).zfill(8)
    return f"20{s[2:4]}-{s[4:6]}-{s[6:8]}"


def load_ticker(path: Path) -> pd.DataFrame | None:
    try:
        df = pd.read_csv(path, header=0)
        df.columns = [c.strip() for c in df.columns]
        df["Date"] = pd.to_datetime(df["Date"].apply(_parse_ami_date), errors="coerce")
        df = df.dropna(subset=["Date"]).sort_values("Date").reset_index(drop=True)
        for col in ["Open", "High", "Low", "Close", "Volume"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        return df.dropna(subset=["Close"])
    except Exception:
        return None


def compute_features(df: pd.DataFrame) -> pd.DataFrame | None:
    if len(df) < 60:
        return None
    import pandas_ta as ta

    c, h, l, v = df["Close"], df["High"], df["Low"], df["Volume"]

    rsi    = ta.rsi(c, 14)
    mdf    = ta.macd(c, fast=12, slow=26, signal=9)
    macd_h = mdf["MACDh_12_26_9"] if mdf is not None else pd.Series(0, index=df.index)
    ema34  = ta.ema(c, 34)
    dist   = ((c - ema34) / c).clip(-0.5, 0.5)
    log_r  = np.log(c / c.shift(1)).clip(-0.15, 0.15)
    vol_c  = v.pct_change().clip(-2, 5)
    atr    = ta.atr(h, l, c, 14)
    atr_n  = (atr / c).fillna(0)
    rsi_s  = rsi - rsi.shift(5)
    vm20   = v.rolling(20).mean().replace(0, np.nan)
    vol_r  = (v / vm20).clip(0, 10)
    e9     = ta.ema(c, 9); e21 = ta.ema(c, 21)
    etr    = ((e9 - e21) / c).fillna(0)
    bb     = ta.bbands(c, length=20)
    if bb is not None and "BBU_20_2.0" in bb.columns:
        bw  = bb["BBU_20_2.0"] - bb["BBL_20_2.0"]
        bbp = ((c - bb["BBL_20_2.0"]) / bw.replace(0, np.nan)).clip(-0.5, 2.0)
    else:
        bbp = pd.Series(0.5, index=df.index)

    feat = pd.DataFrame({
        "RSI": rsi, "MACD_hist": macd_h, "Dist_EMA": dist,
        "Log_Ret": log_r, "Vol_Change": vol_c, "ATR_norm": atr_n,
        "RSI_slope5": rsi_s, "Vol_ratio20": vol_r, "EMA_trend": etr, "BB_pos": bbp,
    })
    return feat.ffill().fillna(0)


def compute_targets(df: pd.DataFrame, thresholds: dict) -> pd.DataFrame:
    c = df["Close"]
    return pd.DataFrame({
        f"y_{name}": ((c.shift(-int(name[1:])) - c) / c * 100 > thr).astype(float)
        for name, thr in thresholds.items()
    }, index=df.index)


def build_sequences(feat, tgt, seq_len, feat_cols):
    X_list, y_list = [], []
    F = feat[feat_cols].values.astype(np.float32)
    # Thay inf/nan bằng 0 trước khi tạo sequences
    F = np.where(np.isfinite(F), F, 0.0)
    Y = tgt[["y_t5", "y_t10", "y_t25"]].values
    for i in range(seq_len, len(feat) - 25):
        seq = F[i - seq_len:i]
        lbl = Y[i]
        if np.isnan(lbl).any():
            continue
        X_list.append(seq)
        y_list.append(lbl)
    if not X_list:
        return np.array([]), np.array([])
    return np.array(X_list, dtype=np.float32), np.array(y_list, dtype=np.float32)


def build_dataset(cfg: dict, progress_cb=None):
    files = sorted(_AMI_DIR.glob("*.csv"))
    log.info("Tìm thấy %d ticker files", len(files))
    feat_cols = cfg["features"]
    all_X, all_y = [], []
    ok = skipped = 0

    for i, fpath in enumerate(files):
        if progress_cb:
            progress_cb(i, len(files), fpath.stem)
        df = load_ticker(fpath)
        if df is None or len(df) < cfg["min_rows"]:
            skipped += 1; continue
        feat = compute_features(df)
        if feat is None:
            skipped += 1; continue
        tgt  = compute_targets(df, cfg["targets"])
        X, y = build_sequences(feat, tgt, cfg["seq_len"], feat_cols)
        if len(X) == 0:
            skipped += 1; continue
        all_X.append(X); all_y.append(y); ok += 1

    log.info("Build done: %d OK, %d skipped", ok, skipped)
    X = np.concatenate(all_X); y = np.concatenate(all_y)
    log.info("Dataset: X=%s y=%s", X.shape, y.shape)

    n = len(X)
    n_tr  = int(n * cfg["train_ratio"])
    n_val = int(n * cfg["val_ratio"])

    # Đảm bảo không còn inf/nan trước khi fit scaler
    X = np.where(np.isfinite(X), X, 0.0)
    X = np.clip(X, -1e6, 1e6)

    from sklearn.preprocessing import MinMaxScaler
    scaler = MinMaxScaler()
    scaler.fit(X[:n_tr].reshape(-1, X.shape[-1]))

    def sc(arr):
        s = arr.shape
        return scaler.transform(arr.reshape(-1, s[-1])).reshape(s)

    return sc(X[:n_tr]), y[:n_tr], sc(X[n_tr:n_tr+n_val]), y[n_tr:n_tr+n_val], \
           sc(X[n_tr+n_val:]), y[n_tr+n_val:], scaler


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------
def build_model(seq_len, n_feat, cfg):
    import tensorflow as tf
    from tensorflow.keras import layers, Model

    inp = layers.Input(shape=(seq_len, n_feat))
    x   = layers.LSTM(cfg["lstm_units"][0], return_sequences=True, recurrent_dropout=0.1)(inp)
    x   = layers.Dropout(cfg["dropout"])(x)
    x   = layers.LSTM(cfg["lstm_units"][1], recurrent_dropout=0.1)(x)
    x   = layers.Dropout(cfg["dropout"] / 2)(x)
    x   = layers.BatchNormalization()(x)

    out_t5  = layers.Dense(1, activation="sigmoid", name="t5")(x)
    out_t10 = layers.Dense(1, activation="sigmoid", name="t10")(x)
    out_t25 = layers.Dense(1, activation="sigmoid", name="t25")(x)

    model = Model(inp, [out_t5, out_t10, out_t25])
    model.compile(
        optimizer=tf.keras.optimizers.Adam(cfg["lr_initial"]),
        loss={"t5": "binary_crossentropy", "t10": "binary_crossentropy", "t25": "binary_crossentropy"},
        loss_weights={"t5": 0.25, "t10": 0.35, "t25": 0.40},
        metrics={"t5": "accuracy", "t10": "accuracy", "t25": "accuracy"},
    )
    return model


def _callbacks(cfg, save_path):
    import tensorflow as tf
    return [
        tf.keras.callbacks.EarlyStopping(
            monitor="val_t25_accuracy", patience=cfg["patience"],
            restore_best_weights=True, mode="max", verbose=1),
        tf.keras.callbacks.ReduceLROnPlateau(
            monitor="val_loss", factor=cfg["lr_factor"],
            patience=cfg["lr_patience"], min_lr=1e-5, verbose=1),
        tf.keras.callbacks.ModelCheckpoint(
            str(save_path), monitor="val_t25_accuracy",
            save_best_only=True, mode="max", verbose=0),
    ]


def train_model(X_tr, y_tr, X_val, y_val, cfg, save_path):
    model = build_model(X_tr.shape[1], X_tr.shape[2], cfg)
    model.summary(print_fn=log.info)
    history = model.fit(
        X_tr, {"t5": y_tr[:,0], "t10": y_tr[:,1], "t25": y_tr[:,2]},
        validation_data=(X_val, {"t5": y_val[:,0], "t10": y_val[:,1], "t25": y_val[:,2]}),
        epochs=cfg["max_epochs"], batch_size=cfg["batch_size"],
        callbacks=_callbacks(cfg, save_path), verbose=1,
    )
    return model, history


def finetune_model(X_tr, y_tr, X_val, y_val, cfg, old_path, save_path):
    import tensorflow as tf
    from tensorflow.keras.models import load_model as _lm

    log.info("Fine-tuning %s → %s", old_path.name, save_path.name)
    model = _lm(str(old_path))

    # Đóng băng LSTM layers
    for layer in model.layers:
        layer.trainable = "lstm" not in layer.name.lower()

    model.compile(
        optimizer=tf.keras.optimizers.Adam(cfg["finetune_lr"]),
        loss="binary_crossentropy", metrics=["accuracy"],
    )
    y_out = [y_tr[:,0], y_tr[:,1], y_tr[:,2]] if isinstance(model.output, list) else y_tr[:,2]
    yv_out= [y_val[:,0],y_val[:,1],y_val[:,2]] if isinstance(model.output, list) else y_val[:,2]

    history = model.fit(
        X_tr, y_out,
        validation_data=(X_val, yv_out),
        epochs=cfg["finetune_epochs"], batch_size=cfg["batch_size"],
        callbacks=[
            tf.keras.callbacks.EarlyStopping(monitor="val_loss", patience=8, restore_best_weights=True),
            tf.keras.callbacks.ModelCheckpoint(str(save_path), monitor="val_loss", save_best_only=True),
        ], verbose=1,
    )
    return model, history


# ---------------------------------------------------------------------------
# Evaluation & threshold calibration
# ---------------------------------------------------------------------------
def evaluate(model, X_te, y_te) -> dict:
    preds = model.predict(X_te, batch_size=512, verbose=0)
    p25 = preds[2].flatten() if isinstance(preds, list) else preds.flatten()
    y25 = y_te[:, 2]

    best_thr = best_prec = best_rec = 0.0
    thr_table = {}
    for thr in np.arange(0.30, 0.85, 0.02):
        mask = p25 >= thr
        n = mask.sum()
        if n < 20: break
        prec = float(y25[mask].mean())
        rec  = float((mask & (y25 == 1)).sum() / max((y25 == 1).sum(), 1))
        thr_table[round(float(thr), 2)] = {"n": int(n), "precision": round(prec, 3), "recall": round(rec, 3)}
        if prec >= 0.52 and prec > best_prec:
            best_prec, best_rec, best_thr = prec, rec, float(thr)

    log.info("Best threshold: %.2f → precision=%.1f%% recall=%.1f%%", best_thr, best_prec*100, best_rec*100)
    calibrated = {
        "buy_a":    round(best_thr * 100, 1),
        "buy_b":    round((best_thr - 0.08) * 100, 1),
        "hold_max": round((best_thr - 0.15) * 100, 1),
        "sell_b":   round((best_thr - 0.22) * 100, 1),
    }
    log.info("Calibrated thresholds: %s", calibrated)
    return {
        "best_threshold":     best_thr,
        "best_precision_pct": round(best_prec * 100, 2),
        "best_recall_pct":    round(best_rec * 100, 2),
        "baseline_pct":       round(float(y25.mean()) * 100, 2),
        "calibrated":         calibrated,
        "threshold_table":    thr_table,
        "test_samples":       int(len(X_te)),
    }


def save_metrics(metrics, history, mode):
    m = {
        "mode": mode, "trained_at": datetime.now().isoformat(),
        "epochs_run": len(history.history.get("loss", [])),
        "final_val_loss": float(history.history["val_loss"][-1]) if "val_loss" in history.history else None,
        "evaluation": metrics,
    }
    for k in ["loss", "val_loss", "t25_accuracy", "val_t25_accuracy"]:
        if k in history.history:
            m[k] = [round(v, 4) for v in history.history[k][-80:]]
    METRICS_FILE.write_text(json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("Metrics → %s", METRICS_FILE)
    return m


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="LSTM Training Pipeline — vn_invest")
    parser.add_argument("--mode", choices=["train", "finetune", "analyze", "cache"], default="train")
    parser.add_argument("--no-cache", action="store_true")
    args = parser.parse_args()

    cfg = load_config()
    log.info("Mode=%s | Seq=%d | Features=%d", args.mode, cfg["seq_len"], len(cfg["features"]))

    if args.mode == "analyze":
        _analyze_backtest()
        return

    if args.mode == "cache":
        log.info("Building dataset cache từ %d files...", len(list(_AMI_DIR.glob("*.csv"))))
        t0 = time.time()
        Xtr, ytr, Xv, yv, Xte, yte, scaler = build_dataset(cfg,
            progress_cb=lambda i,n,s: log.info("  [%d/%d] %s", i+1, n, s) if i % 50 == 0 else None)
        np.savez_compressed(CACHE_FILE, X_train=Xtr, y_train=ytr, X_val=Xv, y_val=yv, X_test=Xte, y_test=yte)
        joblib.dump(scaler, _V7_SCALER)
        log.info("Cache built in %.1fs → %s", time.time() - t0, CACHE_FILE)
        return

    # Load hoặc build dataset
    cached = None
    if not args.no_cache and CACHE_FILE.exists():
        try:
            d = np.load(CACHE_FILE)
            Xtr, ytr, Xv, yv, Xte, yte = d["X_train"], d["y_train"], d["X_val"], d["y_val"], d["X_test"], d["y_test"]
            scaler = joblib.load(_V7_SCALER) if _V7_SCALER.exists() else None
            cached = scaler is not None
            log.info("Cache loaded: X_train=%s", Xtr.shape)
        except Exception as e:
            log.warning("Cache load failed: %s", e)

    if not cached:
        log.info("Building dataset...")
        t0 = time.time()
        Xtr, ytr, Xv, yv, Xte, yte, scaler = build_dataset(cfg,
            progress_cb=lambda i,n,s: log.info("  [%d/%d] %s", i+1, n, s) if i % 50 == 0 else None)
        np.savez_compressed(CACHE_FILE, X_train=Xtr, y_train=ytr, X_val=Xv, y_val=yv, X_test=Xte, y_test=yte)
        joblib.dump(scaler, _V7_SCALER)
        log.info("Build in %.1fs", time.time() - t0)

    log.info("Train=%d | Val=%d | Test=%d", len(Xtr), len(Xv), len(Xte))

    t0 = time.time()
    # Kiểm tra input shape trước khi finetune
    can_finetune = False
    if args.mode == "finetune" and _V6_MODEL.exists():
        try:
            from tensorflow.keras.models import load_model as _lm
            _tmp = _lm(str(_V6_MODEL))
            v6_n_feat = _tmp.input_shape[-1]
            v7_n_feat = Xtr.shape[-1]
            if v6_n_feat == v7_n_feat:
                can_finetune = True
            else:
                log.warning("Shape mismatch: v6 expects %d features, data có %d features → train từ đầu", v6_n_feat, v7_n_feat)
            del _tmp
        except Exception as e:
            log.warning("Không kiểm tra được v6 shape: %s → train từ đầu", e)

    if can_finetune:
        model, history = finetune_model(Xtr, ytr, Xv, yv, cfg, _V6_MODEL, _V7_MODEL)
    else:
        if args.mode == "finetune":
            log.info("Train v7 từ đầu với 10 features (cache đã có → nhanh hơn)")
        model, history = train_model(Xtr, ytr, Xv, yv, cfg, _V7_MODEL)
    log.info("Done in %.1fs", time.time() - t0)

    metrics = evaluate(model, Xte, yte)
    save_metrics(metrics, history, args.mode)
    log.info("Model → %s", _V7_MODEL)
    log.info("BUY-A >= %.1f | BUY-B >= %.1f",
             metrics["calibrated"]["buy_a"], metrics["calibrated"]["buy_b"])


def _analyze_backtest():
    bt = Path(r"C:\AmibrokerData\backtest_results.csv")
    if not bt.exists():
        log.error("Không tìm thấy backtest_results.csv"); return
    df = pd.read_csv(bt, on_bad_lines="skip")
    if "AI_Score" not in df.columns:
        log.error("Không có cột AI_Score"); return
    log.info("Total: %d trades", len(df))
    log.info("AI_Score: min=%.1f max=%.1f mean=%.1f", df["AI_Score"].min(), df["AI_Score"].max(), df["AI_Score"].mean())
    if "Actual_Profit" in df.columns and "Signal" in df.columns:
        buy = df[df["Signal"] > 0]
        for thr in [20, 25, 30, 35, 40, 45, 50]:
            sub = buy[buy["AI_Score"] >= thr]
            if len(sub) < 10: break
            wr  = (sub["Actual_Profit"] > 0).mean() * 100
            avg = sub["Actual_Profit"].mean()
            log.info("  Score>=%d: n=%d | win=%.1f%% | avg=%+.2f%%", thr, len(sub), wr, avg)


if __name__ == "__main__":
    main()
