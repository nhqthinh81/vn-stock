# Tab Phái Sinh — Viết lại engine tín hiệu (v3 → v4)

**Ngày:** 14/08/2026
**Bối cảnh:** User bật Auto Refresh và giao dịch theo tín hiệu app sinh ra, hiệu quả rất kém.
**File:** `vn_invest/phaisinh_tab.py`

---

## Chẩn đoán (đo trên 65.786 nến thật, 11/07/2025 → 13/08/2026, 273 phiên)

| Chỉ số engine v3 | Giá trị |
|---|---|
| Tần suất tín hiệu | 43,7% số nến — 105 tín hiệu/ngày |
| Mô phỏng đầy đủ TP/SL | 8.524 lệnh, win 43,9% |
| PnL gross | +65,4đ (+0,008/lệnh — bằng 0) |
| PnL sau phí 0,25đ | **−2.065,6đ ≈ −206.560.000 VND** |

Thử 8 biến thể + 5 khung thời gian: **không cấu hình nào dương sau phí**.

### Nguyên nhân gốc — phân rã alpha từng thành phần bảng điểm

| Thành phần | Trọng số v3 | Alpha (T+10) |
|---|---:|---:|
| RSI<40 / RSI>60 | **2.0** (cao nhất) | **−0,287** ⛔ |
| EMA5 cắt EMA10 | 1.0 | −0,084 ⛔ |
| RSI<48 / RSI>52 | 1.0 | −0,032 |
| MACD hist tăng/giảm | 1.0 | +0,067 ✅ |
| MACD zero-cross | 1.5 | +0,142 ✅ |
| Giá vs VWAP | **0.5** (thấp nhất) | **+0,191** ✅ |

Bảng điểm trao trọng số cao nhất cho biến hại nhất. VN30F1M khung 1 phút là
**momentum** (tương quan quá khứ/tương lai +0,025), nhưng thành phần chi phối
lại là mean-reversion. Đường tín hiệu thống trị: `RSI<40 (+2.0) + MACD hist
nhích lên (+1.0) = 3.0 ≥ 2.5` → bắt dao rơi có hệ thống.

> ⚠️ **Bài học phương pháp:** IS/OOS có drift ngược nhau (+0,467 / −0,166).
> Phải đo **alpha đã trừ drift**, không đo gross — nếu không sẽ chỉ đang đo beta.

---

## Đã làm

### Phase 1 — Lỗi cơ học
- [x] Tín hiệu tính trên **nến đã đóng** (`_closed_bars()`), v3 dùng nến đang hình thành
      (RSI lệch TB 3,46 điểm; 15,6% số lần điều kiện đảo trạng thái)
- [x] Bỏ bug `trend == 0` cộng +0.5 cho **cả hai** chiều (v4 không dùng trend nữa)
- [x] Thêm slider ngưỡng SHORT (`ps_thr_sell`) — trước hardcode
- [x] Sửa `thr_long` đọc số nguyên 50–90 rồi so `prob >= 55` → **chế độ LSTM
      không bao giờ ra tín hiệu LONG**. Nay chia 100 đúng.
- [x] Sửa kiểm tra out-of-range sai dấu với feature âm (`lo*0.7` → `lo - pad`)
- [x] **Theo dõi vị thế ảo** — dedup 1 lệnh/thời điểm + sinh record `ĐÓNG`
- [x] `_calc_session_stats` nay hoạt động (v3 lọc `startswith("ĐÓNG")` nhưng không
      chỗ nào sinh record đó → Win Rate/PnL **luôn = 0**, nên không ai phát hiện lỗ)
- [x] Duyệt **mọi** nến kể từ lúc vào lệnh khi kiểm tra SL (AFL export ~5 phút/lần
      nên chỉ soi nến cuối sẽ bỏ sót SL đã chạm)
- [x] Con trỏ `last_ts` chống đếm trùng `bars`
- [x] Guard không giữ lệnh qua đêm

### Phase 2 — Engine v4
- [x] `_get_rule_signal()`: LONG = giá > VWAP phiên **và** MACD hist tăng;
      SHORT = ngược lại. Bỏ toàn bộ bảng điểm 7 thành phần.
- [x] Thoát lệnh là **một phần chiến thuật**: giữ ≤30 nến hoặc SL 3×ATR14.
      SL chặt kiểu v3 (pivot 10 nến) bị nhiễu quét trước khi edge kịp hiện.

---

## Kết quả — kiểm chứng bằng chính hàm trong code

Replay toàn bộ lịch sử qua `_get_rule_signal` / `_open_position` /
`_check_position_exit` / `_position_pnl` (không dùng bản backtest riêng):

```
2.343 lệnh (8,6/ngày) | win 44,9% | +0,349đ/lệnh sau phí | +818,2đ = +81.825.000 VND/HĐ
Theo quý: Q3/25 +0,296 | Q4/25 +0,601 | Q1/26 +0,201 | Q2/26 +0,200 | Q3/26 +0,469
  -> DƯƠNG Ở CẢ 5/5 QUÝ
Theo chiều: LONG +0,265 (n=1.155) | SHORT +0,431 (n=1.188)
Lý do thoát: chạm SL n=770 (−5,52đ) | hết hạn giữ n=1.355 (+3,09đ) | cuối phiên n=218 (+4,02đ)
Số phút giữ lệnh trung bình: 23,4 / tối đa 30
Ràng buộc 1 vị thế lọc bỏ 33.338 tín hiệu thừa (93,4%)
```

### Kiểm tra tự động (đều PASS)
- `_calc_session_stats` khớp replay (total=2.343, net=+818,3đ) — panel thống kê đã sống
- Không lệnh nào vượt 30 phút → `bars` không bị đếm trùng
- Không lệnh nào giữ qua đêm
- Mở 2.344 / đóng 2.343 → luôn đúng 1 vị thế tại 1 thời điểm
- 8/8 unit test cho `_open_position` / `_check_position_exit` / `_position_pnl` / `_closed_bars`

Robustness: vùng tham số dương liên tục (hold 25–50 nến × SL 2,5–4,0×ATR),
hoà vốn ở mức phí 0,62đ/lệnh (biên an toàn ~2,5× so với phí thực tế).

---

---

## Phase 3 — Sắp xếp lại giao diện (khó hiểu, mâu thuẫn trạng thái)

**Vấn đề:** Có **ba chỗ cùng nói về trạng thái** nhưng tự suy ra độc lập → mâu thuẫn.
Điển hình: đang giữ lệnh nhưng card "TÍN HIỆU" vẫn báo "⏸ QUAN SÁT" (vì nó chỉ đọc
`rule_sig` của nến hiện tại, không biết có vị thế).

- [x] **Một nguồn sự thật duy nhất** cho trạng thái bot, 4 giá trị:
      `ĐANG GIỮ LONG/SHORT` · `CHUẨN BỊ VÀO` · `CHỜ TÍN HIỆU` · `NGOÀI PHIÊN`
- [x] Gộp panel vị thế + card TÍN HIỆU thành 1 hàng: Giá | Trạng thái | PnL
      (PnL đổi ngữ cảnh: đang giữ lệnh → PnL lệnh; không giữ → PnL phiên sau phí)
- [x] Thêm thanh tiến trình thời gian giữ lệnh (`đã giữ x/30 phút`)
- [x] **Giải thích vì sao đang chờ** — 2 thẻ điều kiện engine với giá trị thật:
      ① giá vs VWAP phiên, ② MACD histogram, kèm dòng kết luận đồng pha/ngược pha
- [x] Khi đang giữ lệnh: ghi rõ bot **cố ý** bỏ qua tín hiệu mới (ràng buộc chiến
      thuật, không phải lỗi)
- [x] Đưa trend đa khung + Buy/Sell Stop + dự báo LSTM vào expander
      **"Thông tin tham khảo (không dùng để ra tín hiệu)"** — trước đây chúng nằm
      ngang hàng tín hiệu nên gây hiểu nhầm là đang điều khiển bot
- [x] Bỏ cột TP khỏi nhật ký (v4 không dùng TP); PnL hiển thị kèm quy đổi VND
- [x] Tuân thủ UI/CSS Safety Rules: mọi div có background đều khai báo `color`
      tường minh (`#f0f0f0` / `#cccccc`), không dùng `#aaa`/`#666` trên nền tối

**Kiểm chứng:** `streamlit.testing.v1.AppTest` render tab thật — **0 exception**.
Chụp trạng thái lúc chạy live 14/08 10:44:
```
🚀 ĐANG GIỮ LONG · vào 1906.7 lúc 10:40 · còn tối đa 30 phút
PnL lệnh đang giữ: -0.7đ (-70.000 VND/HĐ)
① Giá 1906.7 > VWAP 1904.3          → nghiêng LONG
② MACD hist +0.22 → +0.24 (tăng)    → nghiêng LONG
✓ Hai điều kiện cùng nghiêng LONG → tín hiệu LONG · nến đã đóng 10:40
Nhật ký: MỞ LONG 1.906,7 · cắt lỗ 1901.0
```
Tất cả khối đều nhất quán — không còn cảnh "QUAN SÁT" khi đang có lệnh.

---

## Phase 4 — Dấu hiệu nhận biết lệnh đã đóng

**Vấn đề:** `a_cls = "c-long" if "LONG" in act` → **MỞ LONG và ĐÓNG LONG cùng màu xanh**,
không phân biệt được dòng nào là vào lệnh, dòng nào là thoát. Tệ hơn: lệnh LONG **thua**
vẫn hiện màu xanh vì tô theo *chiều lệnh* chứ không theo *kết quả*.

- [x] **Mã lệnh `#số`** (`ps_trade_seq` → `pos["tid"]`) ghép cặp dòng MỞ ↔ ĐÓNG,
      hiện cả ở trạng thái (`ĐANG GIỮ LONG #3`) và Telegram
- [x] Dòng ĐÓNG tô theo **kết quả sau phí**, không theo chiều lệnh:
      ✅ xanh khi THẮNG / ❌ đỏ khi THUA
- [x] Dòng ĐÓNG có **nền màu** (xanh/đỏ nhạt) + **viền trái 4px** → nhìn lướt là thấy
- [x] Cột giá hiện **vào → ra** (`1.900,0 → 1.903,5`) thay vì chỉ 1 giá
- [x] Dòng MỞ hiện `đang chạy…` ở cột PnL thay vì `—` (phân biệt "chưa có kết quả"
      với "kết quả bằng 0")
- [x] **Banner lệnh vừa đóng** ở đầu tab: `✅ ĐÃ ĐÓNG lệnh #1 LONG — THẮNG +3,2đ
      (+325.000 VND/HĐ)` kèm giá vào/ra, thời gian giữ, lý do thoát
- [x] Banner **tự ẩn khi đã có lệnh mới** — nếu không sẽ lại mâu thuẫn với khối
      trạng thái ngay bên dưới (đúng loại lỗi Phase 3 vừa sửa)
- [x] Journal CSV: thêm cột `trade_id` + `result` (THẮNG/THUA); bảng lịch sử
      áp cùng quy tắc tô màu theo kết quả
- [x] Telegram thông báo đóng lệnh có thêm dòng 🟢 THẮNG / 🔴 THUA và mã lệnh

**Kiểm chứng** (AppTest, 0 exception, 2 kịch bản):

| Kịch bản | Banner | Trạng thái |
|---|---|---|
| Không có lệnh mở | ✅ ĐÃ ĐÓNG lệnh #1 LONG — THẮNG +3,2đ | ⏳ CHỜ TÍN HIỆU |
| Đang có lệnh mở | (tự ẩn) | 🔻 ĐANG GIỮ SHORT #2 |

Bảng nhật ký render thật:
```
[không nền] #4 11:10  🔻 MỞ SHORT       1.896,5              đang chạy…
[không nền] #3 10:40  🚀 MỞ LONG        1.906,7              đang chạy…
[NỀN ĐỎ  ]  #2 10:20  ❌ ĐÓNG SHORT THUA  1.895,0 → 1.899,0   -4,0đ · sau phí -4,2đ = -425.000đ
[NỀN XANH]  #1 09:50  ✅ ĐÓNG LONG THẮNG  1.900,0 → 1.903,5   +3,5đ · sau phí +3,2đ = +325.000đ
```

---

## Phase 5 — Lọc bỏ dữ liệu journal cũ (+ lỗi mất dữ liệu âm thầm)

**Phát hiện khi kiểm tra:** `vn30_ai_journal.csv` trộn **3 thế hệ schema** —
header 7 cột nhưng thân file có dòng 7 / 10 / 12 trường. Nguyên nhân:
`to_csv(mode="a", header=False)` ghi theo thứ tự cột của DataFrame mà **không**
đối chiếu header sẵn có, nên mỗi lần đổi schema là file lệch thêm một tầng.

Hệ quả: `read_csv(on_bad_lines="skip")` **âm thầm vứt 360/483 dòng** — gồm cả
những dòng v4 mới ghi. Đây là lỗi mất dữ liệu im lặng, không báo gì cho user.

- [x] Khai báo `_JOURNAL_COLUMNS` là schema chuẩn duy nhất
- [x] `_rotate_journal_if_stale()` — đối chiếu header trước mỗi lần ghi; nếu lệch
      thì **đổi tên** (KHÔNG xoá) file cũ sang `vn30_ai_journal_legacy_<ts>.csv`
      và bắt đầu file mới đúng schema. Xoay vòng đúng 1 lần, có test.
- [x] `_append_journal` truyền `columns=_JOURNAL_COLUMNS` để cố định thứ tự cột
- [x] Lọc phòng vệ tầng đọc: chỉ giữ dòng có **`trade_id`** hợp lệ.
      ⚠️ KHÔNG lọc theo `action`: bot cũ cũng ghi `"ĐÓNG SHORT"` nên 42 dòng rác
      (pnl từ −1.976,9 đến +64,4) sẽ lọt. Lọc theo action bỏ được 81/123 dòng,
      lọc theo `trade_id` bỏ đủ 123/123.
- [x] Có caption báo số bản ghi đã lọc, và thông báo riêng khi chưa có dòng v4 nào

**Kiểm chứng** (trên bản sao, không đụng file thật):
```
TRƯỚC: số trường mỗi dòng = [7, 10, 12] | 485 dòng | pandas đọc được 123
SAU  : đã lưu trữ → journal_legacy_20260814_111955.csv
       số trường mỗi dòng = [12] | đọc được 2/2 dòng — không còn dòng bị vứt bỏ
       ghi tiếp lần 3 → KHÔNG xoay vòng lại (đúng 1 lần)
Lọc trên file gốc: 123 → 0 dòng (toàn bộ là dữ liệu bot cũ)
```

> **Lưu ý cho user:** file journal thật vẫn nguyên vẹn. Lần bot ghi lệnh tiếp theo,
> file cũ sẽ tự được đổi tên thành `vn30_ai_journal_legacy_<ngày_giờ>.csv` trong
> `C:\AmibrokerData\` và journal mới bắt đầu sạch. Không có gì bị xoá.

---

## Phase 6 — Báo cáo lãi/lỗ cuối ngày + gửi email

Dự án chưa có hạ tầng email nào (không SMTP, không `smtplib`) → dựng mới.

**Module mới: `vn_invest/daily_report.py`**
- `load_trades()` — đọc journal, **ghép cặp MỞ↔ĐÓNG theo `trade_id`** → 1 dòng/lệnh.
  Chỉ nhận bản ghi v4; bản ghi bot cũ bị loại (xem Phase 5).
- `summarize()` — số lệnh, thắng/thua, tỷ lệ thắng, PnL gộp/phí/ròng, quy ra VND,
  TB mỗi lệnh, **profit factor**, **sụt giảm tối đa trong ngày** (từ equity curve),
  thời gian giữ TB, lệnh lãi/lỗ nhất, phân tách theo **chiều** và theo **lý do thoát**.
- `build_email_html()` — HTML inline-style, nền sáng (mail client không đọc `<style>`
  ngoài đáng tin và không có dark mode nhất quán).
- `send_report_email()` — SMTP STARTTLS, multipart (text + HTML), hỗ trợ nhiều
  người nhận; trả `(ok, msg)` chứ không ném lỗi.
- `email_ready()` — kiểm tra cấu hình trước, báo rõ thiếu biến nào.

**Định dạng số:** `fmt_vn()` theo chuẩn VN (chấm = nghìn, phẩy = thập phân) —
bắt buộc theo CLAUDE.md vì HTML email hiển thị đúng y chuỗi text, không tự áp locale.

**UI trong tab:** expander "📧 Báo cáo lãi/lỗ cuối ngày" — chọn ngày, 4 metric tổng
quan, bảng chi tiết từng lệnh, **Tải HTML** / **Tải CSV** / **Gửi email ngay**,
xem trước email, và tuỳ chọn **tự động gửi sau khi kết phiên** (mặc định TẮT,
chỉ gửi 1 lần/ngày, chỉ khi trong ngày có lệnh đã đóng).

**Cấu hình:** tạo mới `.env.example` với `SMTP_HOST/PORT/USER/PASSWORD`,
`REPORT_EMAIL_TO`, `REPORT_EMAIL_FROM`. Gmail bắt buộc **App Password**.

**Kiểm chứng** (6 lệnh giả lập: 4 thắng / 2 thua):
```
Ghép cặp 12 bản ghi → 6 lệnh
PnL gộp +6,8đ · phí -1,50đ · PnL ròng +5,3đ = +530.000 VND/HĐ
Tỷ lệ thắng 66,7% · TB +0,88đ/lệnh · profit factor 1,56 · sụt giảm max -5,2đ
Theo chiều : LONG (3, +3,95) · SHORT (3, +1,35)
Theo lý do : Chạm SL (2, -9,5) · Hết 30 phút (3, +13,45) · Cuối phiên (1, +1,35)
```
- Đường gửi email test bằng SMTP giả (KHÔNG gửi thật): STARTTLS ✓, login ✓,
  multipart `[text/plain, text/html]` ✓, nhiều người nhận ✓
- Assert nội dung email không còn số kiểu Mỹ (`1,234.56`) — toàn bộ chuẩn VN
- AppTest render tab: 0 exception; metric hiện `66,7%` / `+5,3đ` / `+530.000 VND/HĐ`

---

## Phase 7 — Cảnh báo Telegram không tới nơi

**Triệu chứng:** User không nhận được cảnh báo nào.

**Chẩn đoán (feedback loop, không đoán):**
1. `getMe` / `getChat` → HTTP 200, bot `Ami_broker_bot` và chat hợp lệ ⇒ không phải credentials
2. `load_dotenv()` có trong `app.py` ⇒ không phải biến môi trường
3. Gửi thử tin nhắn định dạng v3 → **tái hiện 100%**:
```
HTTP 400 ok=False  Bad Request: can't parse entities:
                   Unsupported start tag "40(+2)" at byte offset 80
```

**Nguyên nhân:** Gửi với `parse_mode: "HTML"`, mà lý do tín hiệu v3 chứa
`RSI=36.3<40(+2)`. Telegram đọc `<40(+2)` là thẻ HTML mở → từ chối cả tin nhắn.
Mọi tín hiệu v3 đều có `RSI=…<40` hoặc `>60` ⇒ **không tin nào tới nơi**.

**Vì sao im lặng:** `_send_telegram` trả `False`, `_send_telegram_async` vứt bỏ
giá trị đó. Không log, không hiển thị.

- [x] `_tg_escape()` (`html.escape`) — áp cho mọi nội dung động trong tin nhắn
- [x] `_send_telegram` log `description` mà Telegram trả về khi thất bại
- [x] `_TG_LOG` + lock ở module-level (thread không được chạm `st.session_state`)
- [x] Panel **📨 Telegram** trong tab: trạng thái cấu hình, nút **Gửi thử**,
      nhật ký 12 lần gửi gần nhất; **tự mở sẵn khi có lỗi**

**Kiểm chứng (gửi thật vào Telegram của user):**

| Trường hợp | Kết quả |
|---|---|
| Chuỗi có `<` đã escape | ✅ gửi được |
| Chuỗi có `<` **không** escape (cố ý) | ❌ 400 — và **được ghi vào nhật ký** |
| Tin nhắn MỞ lệnh (v4) | ✅ |
| Tin nhắn ĐÓNG lệnh (v4) | ✅ |

> ⚠️ `alerter.py` (cảnh báo cổ phiếu) cũng dùng `parse_mode: "HTML"` và cũng chỉ
> trả `status_code == 200` mà không log `description`. Hiện các trường nội suy
> đều là số/từ vựng cố định nên chưa lỗi, nhưng **cùng loại rủi ro** — nên áp
> `_tg_escape` + log description tương tự. Chưa sửa vì ngoài phạm vi yêu cầu.

---

## Phase 8 — Dọn dẹp theo yêu cầu (14/08/2026)

- [x] **`tg_escape` + log description cho `alerter.py`** (cảnh báo cổ phiếu).
      Helper đặt trong `alerter.py` vì module này KHÔNG phụ thuộc streamlit
      (chạy headless qua `alert_watcher.py`); `phaisinh_tab` import dùng chung
      → không nhân đôi code. Escape `symbol`, `signal`, `phase`, `risk`,
      `ami_score`, `ami_rec`. `send_telegram()` log
      `response.json()["description"]` thay vì chỉ so `status_code == 200`.
- [x] **Xoá `_calc_tp()`** — 67 dòng, không nơi nào gọi kể từ engine v4.
- [x] **Cập nhật `CLAUDE.md` mục Tab Phái Sinh** — thay toàn bộ mô tả v2/v3
      (trailing stop, `_get_trend_from_1m`, `ps_active_trade`, `sleep+rerun`)
      bằng engine v4; bổ sung cây thư mục (`daily_report.py`, `tasks/`), bảng
      tabs, mục `.env` (SMTP), và các bẫy đã gặp: schema journal, lọc theo
      `trade_id`, escape Telegram, chia 100 cho ngưỡng LSTM, `last_ts` chống
      đếm trùng, `st.fragment` thay `sleep+rerun`.

**Kiểm chứng:** 11/11 assert PASS (gồm `_calc_tp` đã biến mất, helper dùng
chung `is` cùng object, vòng đời vị thế, báo cáo, escape); gửi Telegram thật
với dữ liệu chứa `<script>` → escape thành `&lt;script&gt;` và gửi thành công;
tin nhắn hỏng cố ý → log `HTTP 400 ... Unclosed start tag`. AppTest 0 exception.

> Còn sót: cột `tp` / `tp_method` vẫn nằm trong `_JOURNAL_COLUMNS` (luôn rỗng).
> Bỏ chúng sẽ đổi schema → kích hoạt xoay vòng journal thêm lần nữa, nên để lại.

---

## Phase 9 — "Dữ liệu cũ" + bot vào lệnh trên dữ liệu hết hạn (17-18/08/2026)

**User hỏi:** tại sao tab Phái Sinh báo "Dữ liệu cũ 1458 phút".

**Đo thực tế (không đoán):**
| Chỉ số | Giá trị |
|---|---|
| mtime file `vn30f1m_1min.csv` | 2026-08-18 10:00:02 — 2 phút trước |
| Nến cuối **bên trong** file | 17/08/2026 09:41 — hôm qua |
| Bây giờ | 2026-08-18 10:02 |

⇒ **AFL vẫn chạy tốt**, nhưng nguồn dữ liệu intraday của Amibroker đã dừng từ
09:41 hôm trước. Thông báo cũ đổ lỗi "AFL có thể chưa chạy Explorer" → sai hướng.

**Lỗ hổng nghiêm trọng hơn (từ ảnh chụp của user):** bot đã **mở lệnh LONG #1
@1883.3 lúc 09:40** — giá của hôm qua. Lần đầu mở tab trong ngày, `ps_last_time`
rỗng nên `last_time != ps_last_time` → coi là "nến mới" → vào lệnh theo giá đã
hết hạn 24 giờ. Lệnh đó còn kẹt vĩnh viễn vì không có nến mới để `_check_position_exit`
kích hoạt.

- [x] `_MAX_STALE_MIN = 5` + `_data_age_min()` — đo tuổi bằng **timestamp của nến
      cuối trong dữ liệu**, KHÔNG dùng `os.path.getmtime()`
- [x] **Chặn vào lệnh** khi `data_stale` (đặt cạnh gate `not in_session`)
- [x] Trạng thái mới **🛑 DỮ LIỆU DỪNG** trong panel Trạng Thái Bot
- [x] Cảnh báo phân biệt 2 nguyên nhân bằng mtime + timestamp dữ liệu:
      mtime mới + nến cũ → `st.error` "nguồn Amibroker đã dừng, AFL không phải
      nguyên nhân"; mtime cũ → `st.warning` "AFL có thể chưa chạy Explorer"
- [x] Guard dọn **vị thế treo từ phiên trước** (chạy mỗi render, không phụ thuộc
      nến mới): chốt tại giá đóng cuối cùng biết được, lý do "Đóng cuối phiên
      (dữ liệu dừng)"
- [x] Sửa tiêu đề tab còn sót: "Signal Bot v3" → **v4 (VWAP + MACD histogram)**

**Kiểm chứng (AppTest, đúng tình huống thật của user):**

| Kịch bản | Kết quả |
|---|---|
| Chưa có vị thế | `ps_position = None`, 0 bản ghi — **bot KHÔNG vào lệnh** |
| Có vị thế treo từ hôm qua | tự đóng @1881.7, banner `❌ ĐÃ ĐÓNG lệnh #1 LONG — THUA −1,8đ` |
| Thông báo | 🛑 "Nguồn dữ liệu Amibroker đã dừng… AFL không phải nguyên nhân" |

14/14 assert PASS (thêm 3 test cho `_data_age_min` / `_MAX_STALE_MIN`).
Bài học ghi ở `tasks/lessons.md` mục 7.

---

## Phase 10 — Mâu thuẫn trong Nhật Ký Lệnh (18/08/2026)

**User hỏi:** nhật ký lệnh như vầy có mâu thuẫn không. Có — 3 cái.

| # | Mâu thuẫn | Nguyên nhân |
|---|---|---|
| 1 | Dòng **MỞ** của lệnh **đã đóng** vẫn ghi `đang chạy…` | nhãn gán vô điều kiện cho mọi dòng MỞ |
| 2 | `-1,1đ` nhưng ngay cạnh ghi `-105.000đ` (=1,05đ) | PnL gộp bước 0,1 + phí 0,25 ⇒ net luôn lẻ 0,05; làm tròn 1 chữ số |
| 3 | `-105,000đ` kiểu Mỹ trong khi báo cáo email dùng `-105.000đ` chuẩn VN | hai định nghĩa format khác nhau |

Kiểm lại phần **không** mâu thuẫn (để chắc): 09:40→10:10 = 30 phút, khớp
"giữ 30 phút" ✓ · 1882,5 − 1883,3 = −0,8đ ✓ · `MACD hist -0,13 tăng` đúng luật
v4 (chỉ cần đang tăng, không cần dương) ✓

- [x] Dòng MỞ của lệnh đã có dòng ĐÓNG → hiện **"đã đóng · xem dòng ĐÓNG cùng
      mã"**; lệnh thực sự đang mở mới giữ `đang chạy…`
- [x] PnL sau phí hiển thị **2 chữ số thập phân** (`-1,05đ`) để khớp số VND
- [x] **Gộp `fmt_vn` về `alerter.py`** (module không phụ thuộc streamlit, đã là
      nơi ở của `tg_escape`); `daily_report` và `phaisinh_tab` cùng import từ đó
      → một định nghĩa duy nhất, không vòng lặp import
- [x] Áp chuẩn VN cho **toàn bộ** tab: giá, cắt lỗ, PnL, tiền VND, chuỗi lý do,
      và cả tin nhắn Telegram (32 chỗ). `grep ",\.1f}|,\.0f}"` → **0 kết quả**

**Kiểm chứng (AppTest, dữ liệu y hệt ảnh chụp):**
```
#1 10:10  ❌ ĐÓNG LONG THUA  1.883,3 → 1.882,5  1.877,0  -0,8đ · sau phí -1,05đ = -105.000đ
#1 09:40  🚀 MỞ LONG          1.883,3           1.877,0  đã đóng · xem dòng ĐÓNG cùng mã
#2 10:20  🔻 MỞ SHORT         1.880,0           1.886,0  đang chạy…      ← lệnh mở thật
```
14/14 assert PASS (thêm test lý do tín hiệu theo chuẩn VN), 0 exception.

---

## Phase 11 — Cảnh báo cổ phiếu cơ sở không đẩy thông báo (18/08/2026)

**User báo:** phần chứng khoán cơ sở cũng không đẩy thông báo.

**Chẩn đoán — KHÔNG phải lỗi code:**

| Bằng chứng | Giá trị |
|---|---|
| `data/alert_watcher.log` ghi lần cuối | **21/07/2026 09:57** — 28 ngày trước |
| `data/alert_last_run.json` | 24/07/2026 |
| `scan_result.csv` | 18/08/2026 15:37 (mới) |
| `scan_result_is_new()` | `True` — sẵn sàng chạy |
| Task Scheduler / startup | **không có** đăng ký nào |

Dry-run `run_alert_scan(dry_run=True)` chạy ngay lúc này:
```
scanned=399  qualified=285  sent=10  skipped_spam=125  capped=150
Top: HHP BUY-A 93,7 · MST 90,5 · ABW 86,9 · STB 86,8 · ASP 85,1 …
```
⇒ Logic chấm điểm, lọc spam, chọn top hoàn toàn khoẻ. Đường gửi Telegram cũng đã
kiểm chứng ở Phase 8. **Nguyên nhân: tiến trình nền `alert_watcher.py` đã chết
từ 21/07 và không có gì khởi động lại nó.**

`alert_watcher.py` chạy TÁCH KHỎI Streamlit app — mở app không bật nó. File
`start_alert_watcher.bat` có sẵn nhưng chưa được đăng ký tự khởi động.

- [x] Thêm **panel tình trạng Watcher** đầu mục "📢 Cảnh Báo Telegram"
      (`tabs/tab_model.py`): đọc mtime `alert_watcher.log` →
      >24h → `st.error` "đã dừng N ngày" kèm cách bật lại ·
      >2h → `st.warning` · còn lại → `st.success`
- [x] Ghi rõ nút "Quét & gửi cảnh báo" chỉ chạy **1 lần thủ công**, khác với
      cảnh báo tự động

**Kiểm chứng:** panel với dữ liệu thật cho ra
`ERROR: Cảnh báo tự động đã dừng 28 ngày — lần cuối 21/07/2026 09:57`;
thử 3 mốc tuổi (30 phút / 6 giờ / 29 ngày) đều ra đúng mức success/warning/error.

> ⚠️ **Việc của user (ngoài code):** chạy `start_alert_watcher.bat` để bật lại.
> Muốn tự chạy cùng Windows: đặt shortcut file đó vào `shell:startup`, hoặc tạo
> Task Scheduler trigger "At log on".

---

## Phase 12 — Xếp hạng cảnh báo chọn ngược tín hiệu bán (19/08/2026)

**Bối cảnh:** sau khi bật lại watcher (Phase 11), user nhận được tin. Nhưng 7/10
tin là SELL-B điểm **32,5–34,7** — sát ngưỡng 35, tức các lệnh bán YẾU nhất.

**Lỗi thật trong code** (`alerter.py`, chọn top `max_alerts`):
```python
_pending.sort(key=lambda x: -x[0])   # sort composite GIAM DAN
_pending = _pending[:max_alerts]
```
Với BUY thì đúng (điểm cao = mua mạnh). Với **SELL thì điểm THẤP mới là bán
mạnh**, nên sort giảm dần chọn đúng lệnh bán yếu nhất và vứt bỏ lệnh mạnh nhất.

Đo thật 19/08: 295 tín hiệu đủ điều kiện (31 BUY / 264 SELL).
| | Mã |
|---|---|
| SELL mạnh nhất — **bị vứt** | PVX −4,5 · DVG −3,3 · VNH −2,7 · DCS −2,4 |
| SELL yếu nhất — **được gửi** | HSA 33,1 · TD6 33,4 · NLG 34,1 · VTP 34,1 |

**Sửa lần 1** — xếp theo *độ mạnh* (khoảng cách vượt ra ngoài vùng trung tính):
`BUY: comp − 65` · `SELL: 35 − comp`. Nhưng lộ ra vấn đề thứ hai: hôm nay
264 SELL vs 31 BUY và SELL đạt độ mạnh cao hơn (composite xuống âm được)
→ top 10 **toàn SELL-A**, mất sạch BUY-A 92,9 / 87,7 / 86,8.

**Sửa lần 2 (bản cuối)** — chia suất cho cả hai chiều: mỗi chiều lấy tối đa
`max_alerts // 2` theo độ mạnh, chiều nào thiếu thì chiều kia bù, rồi trộn lại
xếp theo độ mạnh.

- [x] `_strength()` thay cho sort composite thô
- [x] Chia suất BUY/SELL + bù chỗ khi một chiều không đủ
- [x] `stats["capped"]` tính lại theo số thực sự chọn

**Kiểm chứng** (chặn `send_telegram`, không gửi thật):
```
CŨ  : 3 BUY-A + 7 SELL-B (32,5–34,7)   ← toàn lệnh bán yếu
MỚI : 5 BUY-A + 5 SELL-A               ← mạnh nhất của cả hai chiều
      ITA · SJF · VPG · HDC · OGC | HHP · STB · VVS · PET · TC6
```

---

## Phase 13 — Tự chạy cảnh báo cùng Windows (19/08/2026)

Đăng ký Task Scheduler thay vì shortcut `shell:startup` (bền hơn: tự chạy lại
khi crash, không cần cửa sổ console).

```powershell
Task: VNInvest_AlertWatcher
  Execute  : pythonw.exe -X utf8 "<app>lert_watcher.py"
  Trigger  : AtLogOn (user Admin)
  Restart  : 999 lần, mỗi 5 phút
  ExecLimit: PT0S (không giới hạn)
```

**Hai lỗi phát hiện khi kiểm chứng — không đoán mà chạy thử thật:**

1. **`pythonw.exe` làm script chết ngay** với mã lỗi 1, log không có dòng nào.
   Nguyên nhân: chạy nền không có console nên `sys.stdout is None`, mà dòng đầu
   file gọi `sys.stdout.reconfigure(...)` → crash TRƯỚC khi logging kịp khởi
   tạo, nên không để lại dấu vết gì.
   → Guard `if sys.stdout is not None` cho cả `reconfigure` lẫn `StreamHandler`.

2. **Nguy cơ chạy trùng**: từ nay có task tự động, user vẫn có thể bấm
   `start_alert_watcher.bat` → 2 tiến trình cùng poll, cùng ghi
   `alert_history` / `alert_last_run`.
   → Thêm **mutex Windows** `VNInvest_AlertWatcher_Mutex`; tiến trình thứ 2 tự
   thoát. Đã test: tiến trình A giữ mutex, tiến trình B thoát với cảnh báo.

- [x] Đăng ký task, đã chạy và xác nhận `State=Running`, `LastTaskResult=267009`
- [x] Sửa crash khi không có console
- [x] Khoá chống chạy trùng
- [x] Viết lại `start_alert_watcher.bat` — nay là đường chạy tay dự phòng, có
      ghi lệnh kiểm tra/gỡ task

**Kiểm chứng end-to-end (không can thiệp gì):**
```
11:23:03  Watcher khoi dong (do task khoi chay)
11:23:38  LSTM v7 loaded
11:25:42  scan_result.csv MOI -> scanned=399 qualified=241 sent=10 ...
```
Tiến trình sống liên tục 161s+ khi kiểm tra, PID ổn định.

**Lệnh quản lý:**
```
schtasks /query  /tn VNInvest_AlertWatcher      # xem trạng thái
schtasks /end    /tn VNInvest_AlertWatcher      # dừng tạm
schtasks /run    /tn VNInvest_AlertWatcher      # chạy lại
schtasks /delete /tn VNInvest_AlertWatcher /f   # gỡ hẳn
```

---

## Phase 14 — Sửa NameError: Path (19/08/2026)

`tabs/tab_model.py` dòng 234 dùng `Path(__file__)` nhưng file **không có import
module-level** `pathlib` → app vỡ ngay khi mở tab Model AI.

Nguyên nhân kép: (1) `py_compile` chỉ kiểm cú pháp nên báo OK; (2) script vá của
tôi kiểm `'from pathlib import Path' not in s`, khớp nhầm với
`from pathlib import Path as _Path` ở dòng 105 (trong hàm) nên bỏ qua bước thêm import.

- [x] Dùng `_APP_DIR` (Path có sẵn trong `render()`, file vốn đã dùng
      `_APP_DIR / "data" / ...` khắp nơi) thay vì tự dựng `Path(__file__)`
- [x] Render thật `tab_model.render(ctx)` bằng AppTest → **0 exception**,
      panel hiện `✅ Watcher đang hoạt động — lần chạy cuối 19/08/2026 14:09`
- [x] Quét `pyflakes` toàn bộ 5 file đã sửa trong phiên → **không còn undefined name**

Bài học ghi ở `tasks/lessons.md` mục 10: không dừng ở `py_compile`.

---

## Phase 15 — Gợi ý chốt lời phái sinh (19/08/2026)

**User hỏi:** thông tin hiện ra không gợi ý giá chốt lời.

Đúng — engine v4 thoát theo thời gian (30 phút) hoặc SL, không có TP. Trước khi
thêm TP theo yêu cầu, **đo xem TP có tốt không** trên chính cấu hình đã kiểm chứng:

| TP | đ/lệnh | Tỷ lệ chạm TP | Dương mấy quý |
|---|---:|---:|---|
| **Không TP** | **+0,356** | — | 5/5 |
| 4,0R | +0,354 | 2,1% | 5/5 |
| 3,0R | +0,347 | 5,8% | 5/5 |
| 2,0R | +0,276 | 14,3% | 5/5 |
| 1,5R | +0,218 | 23,5% | 5/5 |
| 1,0R | +0,112 | 38,5% | 4/5 |

⇒ **Mọi mức TP đều làm kém đi, càng chặt càng tệ.** Nguyên nhân: lệnh thắng chạy
xa — trung vị +4,25đ, top 30% +7,25đ, top 10% +12,91đ (n=1.065). Cắt đuôi lãi đó
thì không đủ bù 55% lệnh thua.

**Quyết định: KHÔNG thêm TP.** Thay vào đó đáp ứng nhu cầu thật của user
("lệnh này đang chạy tốt hay xấu, chốt tay ở đâu") bằng **vùng lãi tham chiếu**:

- [x] `_WIN_PCTL` — phân vị PnL của lệnh THẮNG trong backtest, kèm bình luận
      giải thích vì sao không có TP
- [x] Khối "📈 Vùng lãi tham chiếu" trong panel vị thế: quy 3 phân vị ra **mức
      giá cụ thể** cho lệnh đang giữ, đánh dấu ✓ xanh khi đã đạt
- [x] Ghi rõ trên UI: *"Bot không tự chốt ở đây"* + số liệu chứng minh vì sao
      không đặt TP, để user tự quyết định chốt tay
- [x] Bổ sung *"không có lệnh chốt lời cố định"* vào dòng mô tả cách bot đóng lệnh
- [x] Sửa nốt 3 chỗ số còn định dạng Mỹ (`rủi ro 6.0đ` → `6,0đ`)

**Kiểm chứng (render thật, KHÔNG dừng ở py_compile — bài học mục 10):**
```
🚀 ĐANG GIỮ LONG #1 · vào 1.873,9 · còn tối đa 23 phút
Cắt lỗ 1.867,9 (rủi ro 6,0đ = 600.000 VND/HĐ) · Đã giữ 7/30 phút
   — không có lệnh chốt lời cố định
📈 Vùng lãi tham chiếu:
   trung vị ✓ 1.878,2 (+4,2đ · 425.000đ)
   top 30%    1.881,2 (+7,2đ · 725.000đ)
   top 10%    1.886,8 (+12,9đ · 1.291.000đ)
```
0 exception · pyflakes sạch.

---

## Phase 16 — Mốc lãi tham chiếu vào tin Telegram (19/08/2026)

**User hỏi lại:** tại sao lệnh phát sinh chỉ có stop loss mà không có mốc chốt lời.

Phase 15 đã thêm "vùng lãi tham chiếu" nhưng **chỉ hiện trên UI khi đang có vị
thế** (`if _pos:`). User đọc **tin Telegram** — mà tin đó chỉ có SL. Đó là lý do
câu hỏi lặp lại: phần giải thích không đến được chỗ user nhìn.

- [x] Thêm dòng `📈 Vùng lãi tham chiếu: a / b / c` vào tin Telegram MỞ lệnh,
      kèm chú thích "(trung vị / top 30% / top 10% của lệnh thắng — bot KHÔNG
      tự chốt ở đây)"
- [x] Gửi thử tin thật → nhận được, định dạng đúng chuẩn VN

**Kinh tế của chiến thuật — vì sao có SL mà không có TP** (n=2.381):

| | |
|---|---:|
| Tỷ lệ thắng | **44,8%** (thua nhiều hơn thắng) |
| Lệnh thắng TB | +5,94đ |
| Lệnh thua TB | −4,17đ |
| Tỷ lệ độ lớn thắng/thua | **1,42 lần** |
| Kỳ vọng | 0,448×(+5,94) + 0,552×(−4,17) = **+0,360đ/lệnh** |

Chiến thuật **lỗ về số lần**, chỉ lãi nhờ lệnh thắng lớn hơn lệnh thua. Nên:
- **SL bắt buộc** — nó là thứ giữ lệnh thua ở mức −4,17đ. Bỏ SL thì một lệnh
  xấu xoá sạch nhiều lệnh tốt.
- **TP phản tác dụng** — phân bổ lợi nhuận rất lệch:

| Nhóm lệnh thắng | Số lệnh | Đóng góp |
|---|---:|---:|
| **top 10%** | 107 | **32,9%** tổng lãi |
| 30–90% | 435 | 51,4% |
| dưới trung vị | 525 | 15,7% |

Đặt TP là cắt đúng nhóm 107 lệnh đang gánh 1/3 lợi nhuận. Đo trực tiếp: TP 1R
làm mất 68% lợi nhuận (+0,356 → +0,112đ/lệnh).

⇒ Bất đối xứng là **chủ ý**: cắt lỗ có mốc cứng, để lãi tự chạy đến hết 30 phút.

---

## Phase 17 — Tải lại trang làm mất lệnh trong phiên (19/08/2026)

**User hỏi:** ứng dụng tự động xoá lệnh đã xuất trong phiên khi làm tươi là như thế nào.

**Nguyên nhân:** toàn bộ trạng thái bot nằm trong `st.session_state`, chỉ sống
trong 1 phiên trình duyệt. F5 là Streamlit dựng lại session trắng → `_defaults`
áp lại từ đầu. Không có gì lưu ra đĩa ngoài journal CSV.

**Hậu quả nặng hơn "mất nhật ký" — 2 điểm hỏng dữ liệu:**

1. **Vị thế đang mở bị bỏ rơi.** `ps_position = None` → bot quên mất đang có
   lệnh. Journal đã có dòng `MỞ` nhưng **không bao giờ** có dòng `ĐÓNG`.
   `load_trades()` gặp `if cl.empty: continue` → lệnh đó **biến mất khỏi báo
   cáo lãi/lỗ**, không được tính vào PnL.

2. **Mã lệnh trùng nhau.** `ps_trade_seq` về 0 → lệnh sau F5 lại mang `#1`.
   Journal hôm đó có HAI `#1`. `load_trades()` gộp theo `trade_id`:
   `op = g[MỞ].iloc[0]` + `c = g[ĐÓNG].iloc[-1]` ⇒ ghép **giá vào của lệnh A với
   giá ra của lệnh B** ⇒ PnL sai hoàn toàn.

- [x] `_save_ps_state()` / `_load_ps_state()` — lưu vị thế + bộ đếm mã lệnh ra
      `data/ps_state.json`, gọi sau MỌI lần mở/đóng/cập nhật vị thế
- [x] `_restore_log_from_journal()` — dựng lại nhật ký phiên từ journal HÔM NAY,
      đồng thời lấy `max(trade_id)` làm sàn cho bộ đếm
- [x] Khôi phục 1 lần khi phát hiện phiên trình duyệt mới (`_fresh`)
- [x] Dòng ĐÓNG lấy giá vào từ dòng MỞ cùng mã (nếu không sẽ hiện
      `1.904,0 → 1.904,0`)

**Kiểm chứng (mô phỏng đúng hành vi F5):**

| | Trước | Sau |
|---|---|---|
| Vị thế đang mở | `None` — **bỏ rơi** | `SHORT #2 vào 1902,0` |
| Nhật ký phiên | 0 dòng | 3 dòng |
| Mã lệnh tiếp theo | `#1` — **trùng** | `#3` |

Render thật qua AppTest: 0 exception, bảng hiện đủ 3 dòng, dòng ĐÓNG hiển thị
`1.900,0 → 1.904,0` đúng cặp.

---

## Phase 18 — Tải lại trang mất luôn Auto Refresh (19/08/2026)

**User báo:** app reload là mất Auto Refresh của tab Phái Sinh, phải bật tay lại.

Cùng gốc với Phase 17: toggle chỉ sống trong `st.session_state`, lại còn khai báo
`value=False` nên mỗi lần tải lại là về TẮT. Các widget cấu hình khác
(`ps_signal_mode`, `ps_strict_trend`, `ps_thr_buy/sell`) cũng vậy.

- [x] `_load_ui_pref()` / `_save_ui_pref()` → `data/ps_ui_pref.json`.
      **Tách khỏi `ps_state.json`**: sở thích người dùng có vòng đời khác hẳn
      trạng thái nghiệp vụ (vị thế, mã lệnh) — xem lessons mục 11.
- [x] Nạp sở thích vào `session_state` **TRƯỚC** khi tạo widget, và **bỏ `value=`
      / `index=`**. Widget có `key` sẽ lấy giá trị từ session_state; để cả hai
      thì Streamlit cảnh báo *"created with a default value but also had its
      value set via Session State API"* và bỏ qua một trong hai.
- [x] Ghi đĩa qua `on_change=_save_ui_pref` — chỉ chạy khi user thực sự đổi,
      không ghi mỗi giây theo nhịp auto-refresh
- [x] Áp cho cả 5 tuỳ chọn, không riêng auto refresh

**Kiểm chứng (AppTest, mô phỏng đúng chu trình bật → tải lại):**

| | auto | mode | strict | thrB | thrS |
|---|---|---|---|---|---|
| Mặc định | False | Rule-based | False | 55 | 55 |
| Sau khi user đổi | True | Ensemble | True | 72 | 55 |
| **Sau khi tải lại** | **True** | **Ensemble** | **True** | **72** | **55** |

Không có cảnh báo Session State API, 0 exception. Xác nhận thêm: sau reload
`ps_auto_toggle=True` được nạp *trước* khi widget tạo, nên nhánh
`_live_panel_auto()` (fragment `run_every=1`) được chọn ngay từ lần render đầu —
auto refresh chạy lại tức thì, không cần thao tác gì.

---

## Phase 19 — Tín hiệu ngược chiều lệnh đang giữ (19/08/2026)

**User báo:** khối điều kiện ghi "✓ ... engine ra tín hiệu LONG" trong khi trạng
thái là "ĐANG GIỮ SHORT #4", và không có thông báo Telegram để phản ứng kịp.

**Đo trước khi sửa — đảo chiều theo tín hiệu ngược có tốt hơn không?**

| Cách xử lý | đ/lệnh | Quý dương |
|---|---:|---|
| **A. Giữ đến hết 30 phút (bản gốc)** | **+0,356** | **5/5** |
| B. Thoát ngay khi có tín hiệu ngược | +0,086 (−76%) | 3/5 |
| C. Thoát và đảo chiều | +0,047 (−87%) | 3/5 |

**44,2%** số lệnh gặp tín hiệu ngược trước khi hết 30 phút — nhiễu khung 1 phút,
không phải đảo chiều thật. Nhóm lệnh thoát theo nó lỗ TB **−2,32đ**: cắt đúng lúc
đang lỗ tạm rồi bỏ lỡ phần hồi.

⇒ Hành vi bot **đúng**. Lỗi nằm ở cách trình bày.

- [x] Dòng kết luận nay **nhận biết vị thế** — 3 nhánh thay vì 1:
  - ngược chiều lệnh đang giữ → ⚠️ nền hổ phách "bot **cố ý bỏ qua**, không đảo lệnh"
  - thuận chiều → ✓ "vẫn nghiêng X — **thuận chiều** lệnh #N đang giữ"
  - không có vị thế → ✓ "engine ra tín hiệu X" (như cũ, mới là tín hiệu hành động)
- [x] Caption giải thích kèm **số liệu** thay vì nói chung chung "ràng buộc chiến thuật"

**Kiểm chứng (AppTest, 3 kịch bản, 0 exception):**
```
A. Không vị thế      → ✓ engine ra tín hiệu X                    (nền xanh/đỏ)
B. Giữ SHORT #4, tín hiệu LONG → ⚠️ ngược chiều — cố ý bỏ qua    (nền hổ phách)
C. Giữ LONG #4, tín hiệu LONG  → ✓ thuận chiều lệnh #4            (nền xanh)
```

**Về Telegram:** KHÔNG thêm cảnh báo tín hiệu ngược. Gửi tin "tín hiệu LONG" khi
user đang giữ SHORT sẽ đẩy họ tới hành động mà số liệu chứng minh là lỗ (−76%).
Thông báo đúng lúc chỉ có giá trị nếu hành động theo nó có lợi. Nếu vẫn muốn,
làm dạng tuỳ chọn mặc định TẮT và ghi rõ "chỉ để quan sát, không nên hành động".

---

## Phase 20 — Thông báo tín hiệu ngược (tuỳ chọn) + Cổng biến động (19/08/2026)

### A. Thông báo tín hiệu ngược — theo yêu cầu, mặc định TẮT

- [x] Pref `ps_notify_counter` + checkbox trong "Cấu hình chiến thuật"
- [x] Gửi **một lần cho mỗi vị thế** (cờ `pos["counter_notified"]`), không spam
- [x] Nội dung ghi thẳng số liệu để không bị đọc thành lệnh hành động:
```
👀 #VN30F1M Tín hiệu LONG ngược chiều lệnh SHORT #4
📊 CHỈ ĐỂ QUAN SÁT — số liệu cho thấy KHÔNG nên đảo lệnh:
   giữ đến hết +0,356đ/lệnh · thoát sớm +0,086đ (−76%) · đảo lệnh +0,047đ (−87%)
🤖 Bot vẫn giữ SHORT #4 đến khi hết 30 phút hoặc chạm SL 1.904,0
```

### B. Tỷ lệ thắng — user thấy 44,8% quá thấp

**Trước hết: win rate KHÔNG phải mục tiêu.** Nâng nó rất dễ nhưng phải trả giá —
đã đo ở Phase 15: TP 1R cho win **50,4%** nhưng lợi nhuận rơi từ +0,356 xuống
**+0,112đ/lệnh (−68%)**. Cấu trúc hiện tại: thắng TB +5,94đ / thua TB −4,17đ
(tỷ lệ 1,42×). Muốn win 55% với cùng kỳ vọng thì tỷ lệ độ lớn chỉ cần 0,82× —
tức phải cắt lãi sớm, đúng thứ vừa chứng minh là làm giảm lợi nhuận.

**Đã thử 9 bộ lọc vào lệnh.** Không cái nào nâng win rate đáng kể (tốt nhất
+0,4 điểm phần trăm), phần lớn làm giảm tổng lợi nhuận:

| Bộ lọc | win% | đ/lệnh | tổng |
|---|---:|---:|---:|
| Bản gốc | 44,8 | +0,356 | +850 |
| MACD hist cùng dấu | 44,2 | +0,237 | +506 |
| Thuận trend 15m | 42,9 | +0,190 | +374 |
| Bỏ 30 phút đầu phiên | 45,2 | +0,381 | +808 |
| **ATR > 0,9× nền** | **46,0** | **+0,462** | **+833** |

**Chỉ cổng biến động ATR cải thiện được cả hai.** Hiệu ứng **đơn điệu** theo
ngưỡng (0,5→1,0 đều tăng dần) chứ không phải một ô may mắn:

| Ngưỡng | n | win% | đ/lệnh | IS | OOS |
|---|---:|---:|---:|---:|---:|
| không lọc | 2.385 | 44,8 | +0,356 | +0,431 | +0,280 |
| 0,7× | 2.244 | 45,1 | +0,400 | +0,508 | +0,293 |
| **0,9×** | **1.802** | **46,0** | **+0,462** | **+0,484** | **+0,440** |
| 1,0× | 1.514 | 46,2 | +0,468 | +0,509 | +0,425 |

Chọn **0,9×**: cải thiện win rate ở **cả 5/5 quý**, OOS tăng **+57%**
(+0,280 → +0,440), ít hơn 24% số lệnh nên đỡ phí và đỡ phải ngồi canh.

- [x] `_MIN_ATR_RATIO = 0.9`, `_get_atr_state()` trả (ATR, trung vị 500 nến)
- [x] Cổng chặn TRƯỚC 2 điều kiện trong `_get_rule_signal` → trả WAIT kèm lý do
- [x] Thẻ ③ "Biến động (cổng chặn)" trên UI + dòng kết luận riêng khi bị chặn

15/15 assert PASS. Lúc kiểm tra, thị trường thật đang lặng: ATR 1,05 / nền 1,51
= **70%** → cổng chặn, signal WAIT — đúng thiết kế.

### C. Kiểm chứng cuối bằng A/B CÓ KIỂM SOÁT (bắt buộc)

Bản backtest độc lập và replay qua code cho số khác nhau. Chỉ A/B trong **cùng
một harness** mới kết luận được:

| | n | win% | đ/lệnh | tổng | quý yếu nhất |
|---|---:|---:|---:|---:|---:|
| Không cổng | 2.447 | 43,8 | +0,275 | +672,5đ | +0,004 |
| **Cổng 0,9×** | 1.837 | **45,1** | **+0,395** | +725,1đ | +0,005 |
| Cổng 0,7× | 2.297 | 44,2 | +0,324 | **+743,9đ** | +0,009 |

⇒ **Giữ cổng ở 0,9×**: win +1,3 điểm, đ/lệnh +44%, tổng +7,8%. Chọn 0,9 thay vì
0,7 vì win rate cao hơn (45,1 vs 44,2) và biên trước phí tốt hơn; 0,7 chỉ hơn
2,6% tổng nhưng phải vào thêm 460 lệnh.

> ⚠️ **Sai lầm đã mắc trong quá trình này:** so 725,1đ (có cổng, harness mới) với
> 818,2đ (không cổng, harness cũ ở Phase 2) rồi kết luận cổng làm giảm 11% lợi
> nhuận — SAI, vì hai harness khác vòng lặp/cửa sổ/điểm bắt đầu. Cùng harness thì
> "không cổng" chỉ cho +672,5đ. Cũng đã đổ lỗi sai cho cổng về việc Q2/2026 gần
> như hoà: cả 3 biến thể đều có quý yếu nhất quanh +0,004–0,009.

---

## Phase 21 — Tuỳ chọn chốt lời 3R (19/08/2026)

User yêu cầu thêm tuỳ chọn TP 3R. **Mặc định TẮT.**

- [x] `_TP_R_MULT = 3.0`, pref `ps_use_tp`, checkbox trong Cấu hình chiến thuật
- [x] `_open_position(..., tp_r=None)` → thêm khoá `tp` (None = không chốt tự động)
- [x] `_check_position_exit` xét TP, lý do thoát `Chạm mục tiêu (TP)`
- [x] UI panel vị thế hiện `Chốt lời <giá>`; Telegram thêm dòng `🎯 TP: … (3R)`
- [x] **SL ưu tiên hơn TP** khi một nến chạm cả hai — giả định bi quan, tránh
      thổi phồng backtest (nến 1 phút không cho biết giá chạm bên nào trước)

**A/B trong CÙNG harness, CÙNG code, giữ nguyên cổng biến động 0,9:**

| Cấu hình | n | win% | đ/lệnh | tổng | quý dương | chạm TP |
|---|---:|---:|---:|---:|---|---:|
| **Tắt TP (mặc định)** | 1.837 | 45,1 | +0,395 | +725,1đ | **5/5** | — |
| TP 3R | 1.890 | 45,5 | +0,421 | **+796,3đ** | **4/5** ⚠️ | 5,1% |
| TP 2R | 2.007 | 45,6 | +0,367 | +736,1đ | 5/5 | 13,2% |
| TP 4R | 1.854 | 45,3 | +0,379 | +702,7đ | 5/5 | 1,7% |

**Chi tiết theo quý (quyết định dựa trên bảng này):**

| Quý | Tắt TP | TP 3R | Chênh |
|---|---:|---:|---:|
| 2025Q3 | +88,2đ | **+157,3đ** | **+69,1** |
| 2025Q4 | +324,1đ | +316,8đ | −7,3 |
| 2026Q1 | +167,1đ | +172,3đ | +5,2 |
| 2026Q2 | +2,0đ | −2,4đ | −4,4 |
| 2026Q3 | +138,9đ | +147,6đ | +8,7 |
| **Tổng** | **+720,3đ** | **+791,6đ** | **+71,3** |

> ⚠️ **Không khuyến nghị bật, dù TP 3R hơn 9,8% tổng.** Hai lý do:
> 1. **Toàn bộ lợi ích đến từ MỘT quý** — 2025Q3 đóng góp +69,1đ trong tổng
>    +71,3đ. Bốn quý còn lại cộng lại chỉ +2,2đ, tức bằng không. Cải tiến mà 97%
>    lợi ích dồn vào một giai đoạn là may mắn của giai đoạn đó, không phải hiệu
>    ứng lặp lại được.
> 2. Kết quả **không đơn điệu** theo mức TP (2R +736 · 3R +796 · 4R +703 · tắt
>    +725) — dấu hiệu khớp nhiễu.
>
> ✏️ **Đính chính diễn đạt trước đó:** từng viết "TP 3R rớt xuống 4/5 quý" và coi
> đó là cờ đỏ chính. Gây hiểu sai — 2026Q2 gần như hoà vốn ở CẢ HAI cấu hình
> (+2,0 vs −2,4 trên hơn 400 lệnh), chênh 4,4đ suốt một quý là nhiễu. Lý do thật
> để không bật là (1), không phải (2).
>
> Đối chiếu Phase 15 (chưa có cổng biến động) thì TP luôn làm kém đi. Hiệu ứng
> đảo chiều khi thêm một thành phần khác vào hệ thống ⇒ càng mong manh.

### ✅ User quyết định BẬT (19/08/2026)

Đã trình bày đầy đủ số liệu và khuyến nghị tắt; user chọn bật. Thực hiện:
- `_PS_PREFS["ps_use_tp"] = True` (mặc định mới cho cài đặt mới)
- Thêm `"ps_use_tp": true` vào `data/ps_ui_pref.json` đang dùng
- Bỏ chữ "(mặc định tắt)" khỏi nhãn checkbox; help text nêu rõ cảnh báo về việc
  lợi ích dồn vào một quý
- Comment hằng số ghi lại đây là lựa chọn của user, kèm số liệu

**Xác nhận đầu-cuối:** `_load_ui_pref()` → `ps_use_tp=True` → `tp_r=3.0` →
lệnh mẫu LONG 1900 (rủi ro 6,0đ) cho `SL 1894,0 | TP 1918,0`; nến chạm 1919 →
thoát `Chạm mục tiêu (TP) @ 1918,0`, PnL +18đ = +1.800.000 VND/HĐ.
Render thật: `ps_use_tp = True`, 0 exception.

**Cách tắt lại:** bỏ tick trong Cấu hình chiến thuật (ghi nhớ ngay qua reload).

### ✅ User cũng bật báo tín hiệu ngược (19/08/2026)

- `_PS_PREFS["ps_notify_counter"] = True` + thêm vào `data/ps_ui_pref.json`
- Help text bỏ chữ "mặc định tắt", nêu rõ **1 tin/vị thế** nên không spam

**Kiểm chứng logic gửi:**

| Tình huống | Kết quả |
|---|---|
| Giữ SHORT #4, tín hiệu LONG (lần 1) | **GỬI** |
| Cùng vị thế, tín hiệu LONG (lần 2) | không gửi — cờ `counter_notified` |
| Tín hiệu SHORT (thuận chiều) | không gửi |
| Không có tín hiệu (WAIT) | không gửi |
| Vị thế MỚI #5 (LONG), tín hiệu SHORT | **GỬI** — cờ reset theo vị thế |
| Tuỳ chọn TẮT | không gửi |

---

## Cấu hình chốt lại sau phiên 19/08/2026

```
Engine            : VWAP phiên + MACD histogram (nến đã đóng)
Thoát             : ≤30 phút · SL 3×ATR14 · TP 3R
Cổng biến động    : ATR ≥ 90% nền
Chặn dữ liệu cũ   : > 5 phút
Auto refresh      : BẬT
Chốt lời 3R       : BẬT   (user quyết, số liệu chưa ủng hộ — theo dõi tiếp)
Báo tín hiệu ngược: BẬT   (chỉ quan sát, bot không đảo lệnh)
Chế độ tín hiệu   : Rule-based
```

**Việc nên làm sau vài tuần chạy thật:** mở báo cáo cuối ngày, xem nhóm lý do
thoát `Chạm mục tiêu (TP)` — tỷ lệ và PnL của nhóm đó so với `Hết 30 phút giữ
lệnh`. Đó là dữ liệu thực tế của chính user để đánh giá lại quyết định bật TP,
thay vì dựa vào backtest.

**Kiểm chứng:** 8/8 unit test TP (gồm nến chạm cả hai → ưu tiên SL; tắt TP thì
giá vọt lên 1.950 vẫn không thoát) · 15/15 assert hồi quy xác nhận mặc định tắt
không đổi hành vi cũ · UI render 0 exception ở cả hai trạng thái.

---

## Phase 22 — Báo cáo lệnh theo khoảng thời gian, tổng hợp tuần/tháng (19/08/2026)

User cần: xem + tải về mọi lệnh đã sinh, lãi/lỗ từng lệnh, tổng hợp theo tuần và tháng.

### `vn_invest/daily_report.py`
- [x] `load_trades(..., day_from=, day_to=)` — lọc theo **ngày ĐÓNG lệnh**, và lọc
      **SAU** khi ghép cặp. Lọc trước sẽ cắt mất dòng MỞ của lệnh mở từ hôm trước.
- [x] `summarize_by_period(trades, freq)` — gom theo `"D"` / `"W"` / `"M"`, nhãn
      tiếng Việt: `Tuần 30/2026 (20/07–26/07)`, `Tháng 07/2026`
- [x] `build_report_html(trades, s, label, by_period, freq)` — tổng quát hoá;
      `build_email_html(trades, s, day)` giữ nguyên chữ ký cũ, gọi vào hàm mới
- [x] `build_range_report(day_from, day_to, freq)` → `(trades, summary, by_period, html)`

### UI — expander "📊 Báo cáo lệnh phái sinh"
- [x] Chọn khoảng: **Hôm nay / 7 ngày / Tháng này / Tuỳ chọn** (date range picker)
- [x] Gom nhóm: **Ngày / Tuần / Tháng**
- [x] 4 metric tổng quan + **bảng tổng hợp theo kỳ** + **biểu đồ luỹ kế** +
      **bảng chi tiết từng lệnh** (mã, chiều, ngày, vào, ra, giữ, PnL đ + VND, kết quả, lý do)
- [x] 3 nút tải: **HTML** · **CSV chi tiết** · **CSV theo kỳ** (UTF-8 BOM để Excel
      đọc đúng tiếng Việt) + nút gửi email
- [x] Tự động gửi email vẫn chỉ gửi báo cáo NGÀY, không phụ thuộc khoảng đang chọn

**Kiểm chứng** (94 lệnh giả lập trải 6 tuần / 2 tháng, 06/07–14/08/2026):

```
Tổng: 94 lệnh · thắng 52/thua 42 · win 55,3% · +38,6đ = +3.860.000 VND

Theo tuần (6 kỳ)                          Theo tháng (2 kỳ)
  Tuần 28 (06/07–12/07) n=14  -10,1đ        Tháng 07/2026  n=68  +33,5đ
  Tuần 29 (13/07–19/07) n=18   -9,3đ        Tháng 08/2026  n=26   +5,1đ
  Tuần 30 (20/07–26/07) n=18  +36,8đ
  Tuần 31 (27/07–02/08) n=18  +16,1đ
  Tuần 32 (03/08–09/08) n=13   +8,2đ
  Tuần 33 (10/08–16/08) n=13   -3,1đ
```

Assert: tổng theo tuần == tổng theo tháng == tổng chung (khớp tuyệt đối cả số
lệnh lẫn PnL). Render thật cả 2 chế độ: 0 exception, bảng theo kỳ 6/2 dòng,
bảng chi tiết 94 dòng × 10 cột, 3 nút tải đúng nhãn. HTML xuất 83.959 ký tự,
có đủ 6 khối nội dung, toàn bộ số theo chuẩn VN.

---

## Phase 23 — Nghiên cứu Price Action / Supply-Demand (24/08/2026)

**User hỏi:** ứng dụng supply/demand hay price action được không, tỷ lệ thắng đang quá thấp.

Dựng harness so 8 khái niệm PA/SD với engine v4 trên 67.432 nến / 280 phiên.
**Dùng chung một bộ luật thoát** (hold 30 · SL 3×ATR · phí 0,25 · cổng biến động
0,9) để chỉ so chất lượng ĐIỂM VÀO. Pivot chỉ dùng khi đã xác nhận (K=3 nến sau)
nên không lookahead.

| Biến thể | n | win% | đ/lệnh | tổng | IS | OOS | quý+ |
|---|---:|---:|---:|---:|---:|---:|---|
| **v4 hiện tại** | 1.809 | 45,9 | **+0,464** | **+840** | +0,516 | +0,413 | **5/5** |
| Break of Structure | 1.428 | 45,9 | +0,438 | +626 | +0,603 | +0,277 | 5/5 |
| Pin bar + VWAP | 1.441 | 46,3 | +0,253 | +365 | +0,435 | +0,075 | 4/5 |
| Inside bar breakout | 1.361 | 45,1 | +0,241 | +328 | +0,836 | −0,333 | 3/5 |
| Sweep + VWAP | 683 | 45,2 | +0,036 | +24 | — | — | 2/5 |
| Pin bar | 1.835 | 44,4 | −0,070 | −129 | — | — | 3/5 |
| Engulfing | 1.678 | 45,9 | −0,111 | −187 | — | — | 1/5 |
| S/D Zone retest | 1.114 | 45,0 | −0,157 | −175 | — | — | 2/5 |
| Liquidity sweep | 1.283 | 43,9 | −0,321 | −412 | — | — | **0/5** |

### Ba kết luận

**1. Win rate KHÔNG phải thuộc tính của cách vào lệnh.** Toàn bộ 9 phương pháp
nằm trong dải **43,9–46,3%** — chênh cao nhất so với v4 chỉ 0,4 điểm. Đổi lý
thuyết vào lệnh không nâng được win rate.

**2. Mọi khái niệm ĐẢO CHIỀU đều âm** (pin bar, liquidity sweep, engulfing,
S/D retest); chỉ khái niệm TIẾP DIỄN dương (v4, BOS). Khớp với phát hiện gốc:
VN30F1M khung 1 phút là momentum, không phải mean-reversion. Liquidity sweep
tệ nhất — 0/5 quý.

**3. Win rate nâng được bằng LUẬT THOÁT, và luôn phải trả giá:**

| Luật thoát | win% | đ/lệnh | tổng |
|---|---:|---:|---:|
| hold 30 · SL 3×ATR *(hiện tại)* | 45,9 | +0,464 | +840 |
| hold 30 · SL 5×ATR | 47,6 | +0,356 | +591 (−30%) |
| hold 30 · SL 8×ATR | **48,2** | +0,298 | +480 (−43%) |
| hold 15 · SL 3×ATR | 47,2 | +0,071 | +190 (−77%) |
| hold 60 · SL 3×ATR | **43,1** | **+0,681** | **+885** |

Nới SL nâng win rate nhưng cắt lợi nhuận; giữ lâu hơn cho lợi nhuận cao nhất
nhưng win rate thấp nhất. Hai chỉ số đi ngược nhau.

### Ứng viên bị bác bỏ

`v4 AND BOS` thoạt nhìn hấp dẫn: OOS +0,492 > IS +0,394 (dấu hiệu không khớp
nhiễu). Nhưng kiểm tra theo quý (bài học mục 13) cho thấy **thua ở 4/5 quý**,
tổng −122đ; lợi thế OOS chỉ do Q1/26 (+73,6) bù cho Q4/25 (−111,9).

### Bối cảnh chuỗi thua (giải thích cảm giác "win rate thấp")

| | SL 3×ATR | SL 5×ATR | SL 8×ATR |
|---|---:|---:|---:|
| win rate | 45,9% | 47,6% | 48,2% |
| Chuỗi thua dài nhất | 9 lệnh | 12 lệnh | 11 lệnh |
| Số lần thua ≥5 liên tiếp | 38 | 23 | 26 |
| Sụt giảm vốn tối đa | −12,7 tr | −17,4 tr | −17,6 tr |
| Tỷ lệ NGÀY có lãi | **52,2%** | 50,4% | 51,4% |

⚠️ Nới SL để win rate đẹp hơn lại làm **chuỗi thua dài hơn** (9→12) và **sụt
giảm vốn sâu hơn** (−12,7→−17,4 tr). Tức nó không hề dễ chịu hơn về tâm lý.

**Kết luận: giữ nguyên v4.** Không khái niệm PA/SD nào cải thiện được.

---

## Phase 24 — Nghiên cứu phương pháp Trend-following (24/08/2026)

**User hỏi:** phương pháp trend thì sao?

Lưu ý nền: **v4 vốn đã là phương pháp trend** (lọc xu hướng bằng VWAP phiên +
xác nhận momentum bằng MACD histogram). Nghiên cứu này thử các biến thể trend
khác và — quan trọng hơn — các **kiểu thoát lệnh trend-following**.

### Vòng A — tín hiệu trend, giữ nguyên luật thoát

| Tín hiệu | n | win% | đ/lệnh | tổng | quý+ |
|---|---:|---:|---:|---:|---|
| **v4 hiện tại** | 1.810 | 45,9 | +0,464 | **+839** | 5/5 |
| Donchian 20 breakout | 1.434 | 45,5 | **+0,481** | +689 | 5/5 |
| v4 + ADX>25 | 1.219 | 45,5 | +0,479 | +583 | 4/5 |
| Donchian + VWAP | 1.272 | 44,8 | +0,437 | +556 | 5/5 |
| EMA 9/21 cross | 927 | 46,3 | +0,414 | +384 | 4/5 |
| Supertrend(10,3) flip | 796 | 44,2 | +0,347 | +276 | 3/5 |
| EMA stack (C>20>50) | 1.918 | 44,8 | +0,249 | +479 | 4/5 |

Không tín hiệu nào vượt v4 về tổng.

### Vòng B — đổi kiểu THOÁT sang trend-following

Toàn mẫu cho quan hệ **đơn điệu hoàn hảo**: giữ càng lâu → đ/lệnh và tổng càng
cao, win rate càng thấp.

| Kiểu thoát | win% | đ/lệnh | TỔNG | IS | **OOS** |
|---|---:|---:|---:|---:|---:|
| **hold 30 (hiện tại)** | 45,9 | +0,464 | +836 | +468 | **+368** |
| hold 60 | 43,1 | +0,681 | +881 | +559 | +322 (−12,5%) |
| hold 120 | 40,4 | +0,998 | +961 | +600 | +361 (−2,0%) |
| cuối phiên | 37,0 | +1,536 | +965 | +663 | +302 (−17,9%) |
| trailing 4×ATR cuối phiên | 41,3 | +0,734 | +882 | +494 | +388 (+5,6%) |

### ⚠️ Bẫy suýt mắc

Cột TỔNG và cột IS tăng đơn điệu (468→559→600→663) trông rất thuyết phục. Nhưng
cột **OOS thì không có quy luật** (368→322→361→302) — toàn bộ ưu thế nằm ở nửa
dữ liệu đầu. Tỷ lệ OOS/IS suy giảm theo thời gian giữ: 0,79 → 0,58 → 0,61 → 0,43.

Nếu chỉ nhìn tổng +965 của "giữ đến cuối phiên" mà đổi, sẽ đổi lấy một cấu hình
chỉ đẹp trong quá khứ.

### Rủi ro & trải nghiệm

| Cấu hình | lệnh/ngày | win% | chuỗi thua | sụt giảm | **ngày có lãi** |
|---|---:|---:|---:|---:|---:|
| hold 30 (hiện tại) | 6,5 | **45,9** | 10 | −127đ | 52,2% |
| hold 60 | 4,6 | 43,1 | 11 | **−112đ** | 54,3% |
| hold 120 | 3,4 | 40,4 | 11 | −126đ | 50,7% |
| cuối phiên | 2,2 | 37,0 | 11 | −150đ | **55,8%** |
| trailing 4×ATR | 4,3 | 41,3 | 13 | −123đ | 55,0% |

**Kết luận: giữ nguyên v4 hold 30.** Sau 3 vòng nghiên cứu (price action,
supply/demand, trend) không phương pháp nào vượt được trên dữ liệu OOS.

Ứng viên duy nhất còn cơ sở: **trailing 4×ATR giữ đến cuối phiên** — OOS +5,6%,
tỷ lệ OOS/IS ổn định nhất (0,87), 5/5 quý, ngày có lãi 55,0%. Đổi lại win rate
xuống 41,3%. Chưa triển khai, chờ user quyết.

---

## Còn tồn / cần quyết định

- [ ] `use_container_width=True` đã deprecated (Streamlit khuyến nghị
      `width="stretch"`). Còn dùng ở nhiều nơi trong dự án — nên migrate đồng loạt,
      không sửa lẻ tẻ.
- [ ] Chưa cấu hình SMTP nên chưa gửi thử email thật lần nào — mới test bằng
      SMTP giả. Cần điền `.env` rồi bấm "Gửi email ngay" một lần để xác nhận.
- [ ] Chưa chạy thử app trên phiên giao dịch thật — cần theo dõi 1 phiên để đối chiếu
      log `ĐÓNG` thực tế với kỳ vọng backtest
- [ ] Backtest chỉ trên 1 mã (VN30F1M) và ~13 tháng; đã thử nhiều cấu hình nên vẫn
      còn rủi ro multiple-comparison dù đã walk-forward theo quý

---

## Cảnh báo khi đọc số liệu

Backtest giả định vào lệnh tại **giá đóng của nến đã đóng**. Thực tế app phát tín
hiệu ở đầu nến kế tiếp → có trượt giá. Phí 0,25đ/lệnh đã tính gộp spread +
slippage; nếu trượt nhiều hơn, xem lại bảng độ nhạy chi phí trong docstring file.

## Phase 25 — Đối chiếu dữ liệu THẬT, phát hiện journal hỏng (24/08/2026)

**Yêu cầu:** "tôi cần đối chiếu với data thực tế nha" → "bạn thực hiện đi nhé".

### Phát hiện: journal bị hỏng do hai phiên Streamlit chạy song song
Trên 56 bản ghi v4 (17/08–24/08):
| Vấn đề | Số lượng |
|---|---|
| Dòng trùng lặp hoàn toàn | 6 dòng (11%) |
| Mã lệnh bị dùng lại trong cùng ngày | 4 mã (#1, #2, #3 ngày 20/08 và #1 ngày 19/08) |
| Cặp vị thế chồng nhau | 3 cặp |

Nguyên nhân: `st.session_state` riêng từng tab ⇒ hai engine song song, hai bộ
đếm `ps_trade_seq` độc lập, cùng ghi một journal. Chi tiết: `tasks/lessons.md` mục 16.

### Đã sửa
1. **`_next_trade_id()`** — mã lệnh duy nhất TOÀN CỤC, đọc max `trade_id` từ đĩa
   thay vì bộ đếm RAM. Cũng vá luôn lỗi tiềm ẩn: mã reset theo ngày sẽ khiến báo
   cáo nhiều ngày (Phase 22) gộp lệnh #1 ngày A với #1 ngày B.
2. **Khoá một-phiên-ghi** — `_session_token()` / `_read_owner()` /
   `_claim_ownership()` / `_can_trade()`, nhịp tim trong `ps_state.json`,
   TTL 90s. Phiên không giữ quyền → banner đỏ + chỉ xem.
3. **`_is_duplicate_of_last()`** — bỏ qua dòng trùng hệt dòng cuối file.
4. **`_pair_by_time()`** trong `daily_report.py` — ghép MỞ↔ĐÓNG theo thứ tự thời
   gian, chịu được mã lệnh trùng có sẵn trong dữ liệu cũ (không phải sửa file).
5. **`_trading_minutes()`** — trừ nghỉ trưa 11:30–13:00 để cột "Giữ" khớp số nến.

Feedback loop: `scratchpad/test_lock.py` — 17 mục, mô phỏng 2 phiên bằng 2 dict
`session_state` riêng. Tất cả đạt. `test_render.py` (AppTest) render sạch.

### Đối chiếu sau khi sửa (26 lệnh · 5 phiên)
```
                          thuc te     backtest
Win rate                    34,6%        45,6%
PnL rong / lenh           -0,365d      +0,466d
So lenh vi pham luat 30 nen   0/26   <- truoc khi sua: "co lenh giu 118 nen"
Cham SL                     34,6%        33,0%
Cham TP                      3,8%         5,1%
```
Sau khi ghép cặp đúng, **0/26 lệnh vi phạm luật** — bot chạy đúng thiết kế.

### Kết luận: KHÔNG sửa thuật toán
Bootstrap 200.000 lần rút 26 lệnh từ 1.862 lệnh lịch sử (SD 7,16đ/lệnh):
- Khoảng 90% thường gặp: **[−1,767đ ; +2,859đ]** — kết quả thật −0,365đ nằm giữa
- Xác suất tệ bằng/hơn: **28,4%**
- Xác suất vẫn đang lỗ sau 26 lệnh dù edge đúng: **38,1%**
- Ngày 24/08 lỗ −41,65đ: lịch sử 278 phiên có 2 phiên tệ bằng/hơn (tệ nhất −54,9đ)

Đã thử và BÁC BỎ hai bộ lọc theo giờ (chi tiết `lessons.md` mục 18–19):
- "Bỏ khung 09h" — dữ liệu thật gợi ý, nhưng lịch sử cho 09h là khung TỐT
  (+0,483đ, 4/5 quý dương, IS +0,471 / OOS +0,495)
- "Không mở lệnh sát giờ đóng phiên" — quét K=0..40 phút: không đơn điệu,
  OOS/IS giảm từ 0,68 xuống 0,48 ⇒ khớp nhiễu

**Cần ~200 lệnh (≈29 phiên) mới bắt đầu kết luận được; ~3.350 lệnh để đo edge
0,2đ ở mức tin cậy 95%.**

## Phase 26 — Dự phòng nóng + sự cố mất file & khôi phục (25/08/2026)

### Bối cảnh
User cho biết **cố ý mở 2 cửa sổ trình duyệt** trên cùng 1 server Streamlit, vì
một trình duyệt hay bị đóng khi lấy cookie đăng nhập. Khoá ở Phase 25 coi cửa sổ
thứ hai là lỗi ⇒ thiết kế sai với ý định thật.

### Bug thật được phát hiện
Trạng thái chỉ nạp từ đĩa lúc khởi động phiên (`_fresh`). Cửa sổ dự phòng chờ
lâu rồi tiếp quản sẽ dùng `ps_position` cũ trong RAM ⇒ **mở lệnh thứ hai** trong
khi lệnh cũ vẫn treo. Chính là lỗi Phase 25 quay lại qua đường tiếp quản.

### Đã làm
1. **`_reload_from_disk()`** — nạp lại vị thế + bộ đếm + nhật ký ngay khi chuyển
   từ "chỉ xem" sang "điều khiển".
2. **`_claim_ownership()` viết lại** — thêm cờ `ps_force_claim`, phát hiện
   chuyển trạng thái, và **tiết chế nhịp tim 10s** (trước đó fragment 1s/lần sẽ
   ghi đĩa mỗi giây).
3. **UI đổi hẳn giọng** — từ ⛔ "đóng bớt cửa sổ" sang 🛡️ "Cửa sổ DỰ PHÒNG",
   nói rõ còn bao nhiêu giây thì tự tiếp quản, kèm nút "⚡ Giành quyền ngay" và
   banner ✅ báo đã tiếp quản (kèm vị thế nạp lại được).
4. **`test_takeover.py`** — mô phỏng đúng kịch bản: A mở lệnh → A chết → B tiếp
   quản → B phải THẤY lệnh của A, không cấp trùng mã, journal ghép cặp đúng.
   11/11 mục đạt.

### Sự cố: mất trắng `phaisinh_tab.py` và khôi phục
Script vá dùng `open(P, "w")` — mode `"w"` truncate NGAY, rồi mới encode và gặp
`UnicodeEncodeError` (emoji viết bằng cặp surrogate `\ud83d\udee1`). File
136.641 byte về 0.

Khôi phục bằng `.pyc` (oracle) + transcript (phát lại):
```
git HEAD (1.421 dong)  --57 Edit-->  1.998  --24 bash + 3 script-->  2.575
   --bu 5 cho thu cong-->  KHOP 100% bytecode, 136.641 byte, trung SHA-256
```
Xác nhận độc lập: pyflakes cảnh báo ở **đúng 10 dòng cũ**. Chi tiết cách làm:
`tasks/lessons.md` mục 20–21.

**Mọi script sửa file từ nay ghi nguyên tử**: `compile()` → `encode()` →
file `.tmp` → `os.replace()`.

## Phase 27 — Review sinh lời vòng 4: nhãn TÍN HIỆU MẠNH (26/08/2026)

**Yêu cầu:** "review toàn bộ code sinh tín hiệu phái sinh và đề xuất cải tiến,
nhấn mạnh về khả năng sinh lời."

### Review đúng-sai code sinh tín hiệu: KHÔNG có lỗi
- `_get_rule_signal`: tính trên nến ĐÃ ĐÓNG, VWAP phiên đủ nến (tail 300 > 241),
  cổng biến động chặn trước — đúng thiết kế.
- `_check_position_exit`: SL xét TRƯỚC TP trong cùng nến (bi quan) — khớp harness
  backtest, số backtest không bị thổi phồng.
- Khác biệt nhỏ chấp nhận được: production tính MACD trên 300 nến cuối, backtest
  trên toàn chuỗi — sai số warmup EMA sau 274 nến là không đáng kể.

### 7 hướng sinh lời CHƯA TỪNG thử (research_v5.py) — 6 bác bỏ, 1 nhận
Chuẩn đo: cùng harness sản xuất (hold 30 · SL 3×ATR · TP 3R · gate 0.9 · phí
0,25) · tổng lợi nhuận làm thước đo · IS/OOS · từng quý · đơn điệu tham số.
| Hướng | Kết quả (tổng so với chuẩn +844đ) |
|---|---|
| A. Bỏ 1 chiều (chỉ LONG / chỉ SHORT) | +366 / +392 — hai chiều bù nhau theo quý, giữ cả hai |
| B. Khoảng cách tới VWAP (min/max) | mọi ngưỡng đều giảm tổng, không đơn điệu |
| C. Độ lớn gia tốc MACD hist | **NHẬN — làm nhãn, không lọc** (dưới) |
| D. Dấu mh đồng pha với chiều | +677 — bác |
| E. Bền vững 2 nến | +620 — bác |
| F. Nghỉ sau khi chạm SL | tốt nhất +821 < 844 — bác |
| G. Cầu dao ngày (thua K lệnh nghỉ) | K=2 sập còn +247 — "ngày xấu tiếp tục xấu" là SAI |

### Nhãn ⭐ TÍN HIỆU MẠNH (`_STRONG_DMH_ATR = 0.10`)
`|MACD hist − MACD hist trước| ≥ 0.10 × ATR14` tại nến tín hiệu. Trên cùng dòng
lệnh chuẩn (research_v5b, gắn nhãn không lọc):
```
Quét 0.06→0.14: đơn điệu tăng tới đỉnh BẰNG PHẲNG 0.09–0.12 (+1,1..+1,45đ)
Mạnh : n=265 (~1 lệnh/ngày, 14%)  +1,454đ/lệnh · IS +1,660 / OOS +1,301 · 5/5 quý
       LONG +1,188 (n=137) · SHORT +1,740 (n=128) — cả hai chiều dương
Thường: n=1.608  +0,285đ/lệnh (quý gần nhất đã âm)
Hoán vị 100.000 lần: p = 0,007
Chỉ theo lệnh mạnh: sụt giảm vốn tối đa −46đ so với −118đ khi theo tất cả
```
**Vì sao gắn nhãn thay vì lọc:** nhóm thường vẫn cộng +459đ tổng — lọc bỏ là vứt
tiền (bài học 13). Bot giữ nguyên; nhãn giúp người theo lệnh THỦ CÔNG (~1-2
lệnh/ngày là thực tế) ưu tiên đúng lệnh. Nhãn sai thì cũng không mất gì.

**Triển khai (patch8):** hằng số + `dmh_atr`/`strong` trong detail + prefix
"⭐ MẠNH · " vào reason (UI/journal tự kế thừa) + dòng ⭐ riêng trong tin nhắn MỞ
Telegram. Nhãn nằm trong reason → journal ghi lại được → sau này đối chiếu nhóm
mạnh/thường trên dữ liệu THẬT bằng chính `load_trades()`.

Kiểm chứng: test_strong.py — 405 thời điểm cắt dữ liệu thật qua đúng
`_get_rule_signal`: nhãn nhất quán nội bộ, reason khớp cờ, tỷ lệ mạnh 9% (khớp
bậc với backtest 14%). Hồi quy test_lock/test_takeover/test_spam/render đều đạt.


## Phase 28i — Đóng lệnh tự động + SL/TP sàn thật (03/09/2026)

- [x] `close_position()`: dùng lại `ClosePosition()` JS thật của VPS, nối vào
      2 điểm thoát lệnh (`_check_position_exit()` + thoát vì dữ liệu dừng).
      Cố ý KHÔNG áp trần lệnh/ngày hay trần lỗ/ngày (đóng lệnh = giảm rủi ro).
      Test: `test_close_position.py` 22/22.
- [x] `_place_sltp()`: đặt SL/TP điều kiện THẬT trên sàn (`cmd: co.sltp.order.new`)
      ngay sau khi lệnh vào khớp — SL tồn tại độc lập với bot còn chạy hay
      không. Định dạng request xác nhận SỐNG bằng bắt lưu lượng mạng thật lúc
      user tự đặt tay (lệnh SHORT thật, VPS xác nhận "chờ khớp"). `extInfo`
      lấy từ `window.Fingerprint` (đọc thẳng, không phải `FingerprintJS.load()`
      — đã thử, ra giá trị khác). Mirror đúng SL/TP vị thế ảo, không tự thêm TP.
      Test: `test_sltp.py` 18/18.
- [ ] **CHƯA kiểm chứng full-flow thật** — cần quan sát trực tiếp lần vào lệnh
      thật đầu tiên sau khi restart Streamlit (module chỉ nạp code mới sau
      restart). Config hiện tại đã `enabled=true, dry_run=false,
      auto_all_signals=true` — sẽ tự bắn thật ngay lệnh kế tiếp sau restart.
- [ ] Revoke GitHub PAT đã dùng để push (`ghp_5eTa...`) — nhắc lại, chưa xác
      nhận user đã làm.

## Phase 28j — Vá lỗ hổng đặt lệnh + đối chiếu VPS thật (03/09/2026 chiều)

### Đối chiếu tài khoản thật (đọc CDP, bot đã park)
- **Không có API PnL** → đọc thẳng `assetPanel` / `table.tbl-status-danhmuc` / `#order_normal`.
- Thực tế hôm nay chỉ **5 lệnh** khớp trên VPS, KHÔNG phải ~13 như `autotrade_log`
  ghi ("✅ ĐÃ ĐẶT"/"✅ ĐÃ ĐÓNG" ở 09:42, 10:17, 10:44, 11:03, 11:07, 11:09,
  11:31 phần lớn KHÔNG tới VPS).
- Cuối phiên (14:25) bot code cũ gọi `close_position("SHORT")` đóng vị thế ẢO #41
  trong khi TK thật đang phẳng → lệnh 184790 LONG @1959.50 **mở vị thế trần trụi**.
  Kết thúc ngày: **LONG 1 mồ côi**, lỗ thực hiện −1.200.000đ, lãi tạm +400.000đ,
  nằm qua đêm (market đóng).

### Nguyên nhân lệnh ma (2 ảnh 09:03 vs 09:42)
- Phiếu lệnh VPS bị để ở chế độ **"Lệnh điều kiện / Stop Loss"** (thao tác tay lúc
  bắt request `_place_sltp`). `_submit()` bấm `#btn_long`/`#btn_short` không kiểm
  chế độ → tạo lệnh điều kiện; sức mua 0 HĐ → VPS từ chối; `except: pass` ở nút
  xác nhận nuốt lỗi → trả `None` = "thành công" → `_bump_orders_today()` + log "✅".
- `_place_sltp()` trả `(True, …)` với mọi HTTP 200 dù body có `code:"FOS-6012"`.

### Đã vá `vn_invest/auto_trader.py`
1. `_ticket_mode(page)` — đọc `#select_normal_order.select-active`. Guard trong
   `submit_signal()` + `close_position()`: chế độ ≠ normal → HỦY + log ⛔ +
   `_alert_ticket_mode()` (Telegram, cooldown 15′). **Không tự bấm UI** (quyết định user).
2. `_submit()` đổi trả `(bool, str)` — sau khi bấm phải XÁC NHẬN: lệnh mới trong
   `#order_normal` (`_newest_order_sig`) hoặc `Vị thế` đổi (`_read_position`), hoặc
   đọc `.bootbox`/`.toast-error` (`_read_error_popup`). Hết 6s → `(False, …)`.
   Nút xác nhận `#acceptCreateOrderNew` giờ dùng `wait_for_selector` + `click`
   (vẫn nuốt lỗi timeout — không bật xác nhận thì bỏ qua — nhưng chốt là bước
   verify). Caller chỉ `_bump_orders_today()` + log "✅ ĐÃ ĐẶT" khi `True`.
3. `_place_sltp()` — response chứa `FOS-\d` → `(False, …)`.
4. `submit_signal()` — chỉ gọi `_place_sltp` sau khi `_read_position()` xác nhận
   có vị thế đúng chiều (tránh lệnh điều kiện trần trụi).
5. `close_position()` — thêm guard: `_read_position()` ≠ `position_side` (NONE
   hoặc ngược chiều) → HỦY, không gửi lệnh (tránh sự cố 184790).

Selector chốt (dò DOM thật 03/09): `#select_normal_order` / `#select_condition_order`
`.select-active` · `#right_stock_cd_code` (sub-type) · `table.tbl-status-danhmuc`
(cột Vị thế: "-1"=short1, "1"=long1) · `#order_normal` (hàng dữ liệu đầu = mới nhất).

Test offline `scratchpad/test_autotrader_fixes.py` — fake browser, **33/33 PASS**.
Helper mới chạy trên trang VPS thật (chỉ đọc): `_ticket_mode→('normal',…)`,
`_read_position→('LONG',1)`, `_newest_order_sig→'184790|14:25:03'` — khớp DOM thật.

### CÒN LẠI
- [ ] **Đóng vị thế LONG 1 mồ côi** — market mở 09:00 mai. Bot (code mới) sẽ KHÔNG
      tự đụng vào nó vì `ps_state.position=null` → không có lệnh đóng nào fire;
      `close_position` guard mới cũng chặn. Phải đóng TAY trên VPS.
- [ ] Kiểm chứng full-flow thật với code mới — sau khi user bật lại
      `enabled=true` + restart Streamlit, quan sát lệnh vào đầu tiên.
- [ ] Cân nhắc: cửa sổ app thứ 2/3 (dự phòng) ghi đè `autotrade_config.json` —
      3 instance Streamlit đang chạy song song gây giằng co `enabled`. Đã kill hết.
- [ ] Revoke GitHub PAT (`ghp_5eTa...`) — vẫn chưa xác nhận.

## Phase 28k — Theo dõi song song "shadow trailing 4×ATR" (04/09/2026)

- [x] `_open_shadow_position()`/`_check_shadow_exit()` (phaisinh_tab.py) — trailing
      4×ATR, SL ban đầu giống lệnh thật, sống độc lập tới khi tự thoát.
- [x] `_save_ps_state()`/`_load_ps_state()`/`_reload_from_disk()` mở rộng thêm
      key `shadow` — sống qua restart.
- [x] `_append_journal(entry, file=None)`/`_is_duplicate_of_last(row, file=None)`
      tổng quát hoá — dùng chung cho journal thật + `data/shadow_journal_trailing4atr.csv`.
- [x] Hook vào `_live_panel_body()`: mở shadow cùng lúc lệnh thật (nếu chưa có
      shadow đang chạy), kiểm thoát shadow độc lập mỗi tick.
- [x] `daily_report.build_shadow_comparison()` + expander "🔬 So sánh" trong
      `_render_daily_report()`.
- [x] Test: `tests/test_phaisinh_shadow.py` 7/7. Toàn bộ `pytest tests/` 20/20.
- [ ] **Chưa có dữ liệu sống** — chờ vài ngày/tuần chạy thật rồi xem lại bảng
      so sánh trước khi cân nhắc đổi luật thoát lệnh thật.
