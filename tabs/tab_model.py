"""TAB 5 — MODEL AI: Quản lý LSTM + Cảnh báo Telegram."""
import json
import os
import subprocess
import sys
import time

import pandas as pd
import streamlit as st

from vn_invest.lstm import get_model_info, model_ready


def render(ctx: dict) -> None:
    _APP_DIR     = ctx["app_dir"]
    _TRAIN_LOG   = ctx["train_log"]
    _METRICS_FILE = ctx["metrics_file"]
    _V7_MODEL    = ctx["v7_model"]
    _V6_MODEL    = ctx["v6_model"]

    # ── Section 1: Quản lý model LSTM ────────────────────────────────────────
    st.header("🤖 Quản lý Model LSTM")

    def _is_training() -> bool:
        return _TRAIN_LOG.exists()

    def _training_lines() -> list:
        if not _TRAIN_LOG.exists():
            return []
        try:
            return _TRAIN_LOG.read_text(encoding="utf-8", errors="replace").splitlines()[-30:]
        except Exception:
            return []

    def _start_training(mode: str):
        _APP_DIR.joinpath("data").mkdir(parents=True, exist_ok=True)
        _TRAIN_LOG.write_text(f"[{time.strftime('%H:%M:%S')}] Bắt đầu training mode={mode}\n", encoding="utf-8")
        cmd = [sys.executable, "-m", "vn_invest.train_lstm", "--mode", mode]
        with open(_TRAIN_LOG, "a", encoding="utf-8") as log_f:
            subprocess.Popen(cmd, stdout=log_f, stderr=log_f,
                             cwd=str(_APP_DIR),
                             creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)

    def _stop_training():
        _TRAIN_LOG.unlink(missing_ok=True)

    info = get_model_info()
    col_info, col_action = st.columns([3, 2])

    with col_info:
        st.subheader("Trạng thái model")
        mi1, mi2, mi3 = st.columns(3)
        mi1.metric("Version đang dùng", info["version"].upper() if info["version"] != "none" else "Chưa có")
        mi2.metric("Số tickers Amibroker", info["tickers_available"])
        mi3.metric("Số features", info["n_features"])

        v7_exists = _V7_MODEL.exists()
        v6_exists = _V6_MODEL.exists()
        st.markdown(
            f"- **v7** ({_V7_MODEL.name}): {'✅ Có' if v7_exists else '❌ Chưa train'}"
            + (f" — `{time.strftime('%d/%m/%Y %H:%M', time.localtime(_V7_MODEL.stat().st_mtime))}`" if v7_exists else "")
        )
        st.markdown(
            f"- **v6** ({_V6_MODEL.name}): {'✅ Có' if v6_exists else '❌ Không có'}"
            + (f" — `{time.strftime('%d/%m/%Y %H:%M', time.localtime(_V6_MODEL.stat().st_mtime))}`" if v6_exists else "")
        )

        if _METRICS_FILE.exists():
            try:
                m = json.loads(_METRICS_FILE.read_text(encoding="utf-8"))
                st.divider()
                st.subheader("Kết quả lần train gần nhất")
                ev = m.get("evaluation", {})
                cal = ev.get("calibrated", {})
                mc1, mc2, mc3, mc4 = st.columns(4)
                mc1.metric("Mode", m.get("mode","—").upper())
                mc2.metric("Epochs chạy", m.get("epochs_run","—"))
                mc3.metric("Precision T+25", f"{ev.get('best_precision_pct',0):.1f}%")
                mc4.metric("Recall T+25",    f"{ev.get('best_recall_pct',0):.1f}%")

                st.markdown(f"""
**Ngưỡng tín hiệu (đã calibrate):**
| BUY-A | BUY-B | HOLD max | SELL-B |
|-------|-------|----------|--------|
| ≥ {cal.get('buy_a','—')} | ≥ {cal.get('buy_b','—')} | ≤ {cal.get('hold_max','—')} | ≤ {cal.get('sell_b','—')} |
""")
                trained_at = m.get("trained_at","")
                if trained_at:
                    st.caption(f"Train lúc: {trained_at[:16].replace('T',' ')}")

                if "loss" in m and "val_loss" in m:
                    st.subheader("Loss curve")
                    loss_df = pd.DataFrame({"Train loss": m["loss"], "Val loss": m["val_loss"]})
                    st.line_chart(loss_df, use_container_width=True)
            except Exception:
                pass

    with col_action:
        st.subheader("Huấn luyện lại")

        _outdated = False
        _outdated_reason = ""
        if v7_exists:
            model_age_days = (time.time() - _V7_MODEL.stat().st_mtime) / 86400
            from pathlib import Path as _Path
            newest_data = max(
                (f.stat().st_mtime for f in _Path(info["history_dir"]).glob("*.csv")),
                default=0
            ) if _Path(info["history_dir"]).exists() else 0
            data_newer = newest_data > _V7_MODEL.stat().st_mtime
            if model_age_days > 30:
                _outdated = True
                _outdated_reason = f"Model đã {int(model_age_days)} ngày chưa train lại"
            elif data_newer:
                _outdated = True
                _outdated_reason = "Có data Amibroker mới hơn model"

        if _outdated:
            st.warning(f"⚠️ {_outdated_reason}")
        elif v7_exists:
            st.success("✅ Model còn mới, chưa cần train lại")
        else:
            st.info("ℹ️ Chưa có model v7 — cần train lần đầu")

        st.divider()

        training_active = _is_training()

        if training_active:
            st.warning("⏳ **Đang training...**")
            log_lines = _training_lines()
            if log_lines:
                st.code("\n".join(log_lines), language=None)
            if st.button("🔄 Refresh trạng thái", use_container_width=True):
                st.rerun()
            if st.button("⛔ Hủy training", use_container_width=True, type="secondary"):
                _stop_training()
                st.rerun()
        else:
            st.markdown("**Chọn chế độ training:**")

            if st.button("⚡ Train nhanh (dùng cache)", use_container_width=True, type="primary",
                         help="Dùng dataset cache đã build. Nhanh hơn 5x nếu cache còn đó."):
                _start_training("train")
                st.success("Đã bắt đầu training! Refresh để xem tiến độ.")
                time.sleep(1)
                st.rerun()

            if st.button("🔁 Rebuild cache + Train lại", use_container_width=True,
                         help="Đọc lại toàn bộ 440 tickers Amibroker, build dataset mới, rồi train. Mất ~20-30 phút."):
                cache_f = _APP_DIR / "data" / "dataset_cache.npz"
                cache_f.unlink(missing_ok=True)
                _start_training("train")
                st.success("Đang rebuild dataset và train lại...")
                time.sleep(1)
                st.rerun()

            if st.button("📊 Phân tích backtest", use_container_width=True,
                         help="Phân tích backtest_results.csv, không train model."):
                _start_training("analyze")
                st.success("Đang phân tích...")
                time.sleep(1)
                st.rerun()

        st.divider()
        st.subheader("Auto-retrain")
        _AUTORETRAIN_CFG = _APP_DIR / "data" / "autoretrain.json"
        _auto_cfg = {}
        if _AUTORETRAIN_CFG.exists():
            try:
                _auto_cfg = json.loads(_AUTORETRAIN_CFG.read_text(encoding="utf-8"))
            except Exception:
                pass

        auto_enabled   = st.checkbox("Tự động train khi model lạc hậu", value=_auto_cfg.get("enabled", False))
        auto_threshold = st.slider("Train lại sau N ngày", 7, 90, _auto_cfg.get("days_threshold", 30))

        if st.button("💾 Lưu cài đặt Auto-retrain", use_container_width=True):
            _AUTORETRAIN_CFG.write_text(
                json.dumps({"enabled": auto_enabled, "days_threshold": auto_threshold}, indent=2),
                encoding="utf-8"
            )
            st.success("Đã lưu!")

        if auto_enabled and not training_active and v7_exists:
            model_age = (time.time() - _V7_MODEL.stat().st_mtime) / 86400
            if model_age > auto_threshold:
                st.warning(f"⚡ Auto-retrain: model {int(model_age)} ngày — tự động bắt đầu train!")
                _start_training("train")
                time.sleep(1)
                st.rerun()
        elif auto_enabled and not training_active and not v7_exists:
            st.info("Auto-retrain bật: sẽ train lần đầu khi bạn mở tab này.")
            _start_training("train")
            time.sleep(1)
            st.rerun()

    # ── Section 2: Cảnh báo Telegram ─────────────────────────────────────────
    st.divider()
    st.header("📢 Cảnh Báo Telegram")

    from vn_invest.alerter import (
        run_alert_scan, get_alert_history,
        _BUY_THRESHOLD, _SELL_THRESHOLD, _COOLDOWN_DAYS,
    )
    from vn_invest.screener import get_ami_scan_data as _get_ami_scan_data

    with st.expander("⚙️ Cấu hình cảnh báo", expanded=False):
        al_c1, al_c2, al_c3 = st.columns(3)
        al_buy_thr  = al_c1.number_input("Ngưỡng BUY (composite ≥)", 50, 100, int(_BUY_THRESHOLD), step=5,
                                          help="Composite score >= ngưỡng này mới gửi cảnh báo mua")
        al_sell_thr = al_c2.number_input("Ngưỡng SELL (composite ≤)", 0, 50, int(_SELL_THRESHOLD), step=5,
                                          help="Composite score <= ngưỡng này mới gửi cảnh báo bán")
        al_cooldown = al_c3.number_input("Cooldown (ngày)", 1, 30, _COOLDOWN_DAYS,
                                          help="Không re-alert cùng mã+tín hiệu trong N ngày")
        al_use_lstm = st.checkbox("Dùng LSTM trong tính điểm tổng hợp", value=model_ready(),
                                   help="Tắt nếu không có model hoặc muốn chạy nhanh hơn")
        al_dry_run  = st.checkbox("Dry run (không gửi thật, chỉ xem kết quả)", value=False)

        os.environ["ALERT_BUY_THRESHOLD"]  = str(al_buy_thr)
        os.environ["ALERT_SELL_THRESHOLD"] = str(al_sell_thr)
        os.environ["ALERT_COOLDOWN_DAYS"]  = str(al_cooldown)

    _tg_token   = os.getenv("TELEGRAM_TOKEN", "")
    _tg_chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    if _tg_token and _tg_chat_id:
        st.success(f"✅ Telegram đã cấu hình (chat_id: {_tg_chat_id})")
    else:
        st.warning("⚠️ Chưa cấu hình Telegram. Thêm TELEGRAM_TOKEN và TELEGRAM_CHAT_ID vào file .env")

    _ami_rows_count = len(_get_ami_scan_data())
    st.caption(f"Nguồn: scan_result.csv — {_ami_rows_count} mã từ Amibroker Explorer")

    btn_c1, btn_c2 = st.columns(2)
    with btn_c1:
        run_alert = st.button(
            "🚀 Quét & Gửi Cảnh Báo",
            use_container_width=True,
            type="primary",
            disabled=not _ami_rows_count,
            help="Quét toàn bộ, lọc tín hiệu chất lượng, gửi Telegram (có spam filter)"
        )
    with btn_c2:
        preview_alert = st.button(
            "👁️ Preview (không gửi)",
            use_container_width=True,
            disabled=not _ami_rows_count,
            help="Chạy dry-run để xem kết quả trước khi gửi thật"
        )

    if run_alert or preview_alert:
        _dry = al_dry_run or preview_alert
        _pb_al = st.progress(0)
        _st_al = st.empty()

        def _alert_progress(i, total, sym):
            _pb_al.progress(int((i + 1) / total * 100))
            _st_al.text(f"Đang xử lý {sym}... ({i+1}/{total})")

        with st.spinner("Đang quét và lọc tín hiệu..."):
            result = run_alert_scan(
                use_lstm=al_use_lstm,
                progress_callback=_alert_progress,
                dry_run=_dry,
            )

        _pb_al.empty(); _st_al.empty()

        r_c1, r_c2, r_c3, r_c4 = st.columns(4)
        r_c1.metric("Mã đã quét",          result["scanned"])
        r_c2.metric("Đạt ngưỡng chất lượng", result["qualified"])
        r_c3.metric("Đã gửi Telegram",     result["sent"] if not _dry else f"{result['sent']} (dry)")
        r_c4.metric("Bỏ qua (spam filter)", result["skipped_spam"])

        if not _dry and result["sent"] > 0:
            st.success(f"✅ Đã gửi {result['sent']} cảnh báo qua Telegram!")
        elif _dry:
            st.info("👁️ Dry run — không gửi thật. Bỏ tick 'Preview' để gửi.")

        if result["alerts"]:
            st.subheader(f"Tín hiệu đạt ngưỡng ({len(result['alerts'])} mã)")
            _SIG_ICON = {"BUY-A":"🟢","BUY-B":"🟩","HOLD":"🟡","SELL-B":"🟠","SELL-A":"🔴"}
            df_alerts = pd.DataFrame(result["alerts"])
            df_alerts["Tín hiệu"] = df_alerts["signal"].map(lambda s: f"{_SIG_ICON.get(s,'')} {s}")
            display_alert_cols = {
                "symbol": "Mã", "close": "Giá", "pct_change": "% ngày",
                "comp_score": "Điểm TH", "ami_score": "AMI", "lstm_score": "LSTM",
                "tech_score": "KT", "Tín hiệu": "Tín hiệu", "risk": "Rủi ro", "phase": "Giai đoạn",
            }
            df_disp = df_alerts[[c for c in display_alert_cols if c in df_alerts.columns]].rename(columns=display_alert_cols)
            st.dataframe(df_disp, use_container_width=True, hide_index=True,
                column_config={
                    "Giá":     st.column_config.NumberColumn(format="%,.0f"),
                    "% ngày":  st.column_config.NumberColumn(format="%.2f%%"),
                    "Điểm TH": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%.0f"),
                    "AMI":     st.column_config.NumberColumn(format="%.0f"),
                    "LSTM":    st.column_config.NumberColumn(format="%.1f"),
                    "KT":      st.column_config.NumberColumn(format="%.0f"),
                })
        else:
            st.info("Không có mã nào đạt ngưỡng chất lượng trong lần quét này.")

    st.divider()

    with st.expander("📋 Lịch sử cảnh báo đã gửi", expanded=False):
        hist_data = get_alert_history()
        if hist_data:
            df_hist = pd.DataFrame(hist_data)
            df_hist["sent_at"] = pd.to_datetime(df_hist["sent_at"]).dt.strftime("%d/%m/%Y %H:%M")
            st.dataframe(df_hist.rename(columns={
                "symbol": "Mã", "signal": "Tín hiệu", "score": "Điểm TH", "sent_at": "Thời gian gửi"
            }), use_container_width=True, hide_index=True)
        else:
            st.info("Chưa có lịch sử cảnh báo nào.")
