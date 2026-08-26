# Lessons — vn-invest-app

## 1. Backtest đa khung thời gian: `resample().reindex(ffill)` gây lookahead bias

**Lỗi gặp:** Khi backtest engine phái sinh, ghép trend 15m/1h vào nến 1m bằng
`pd.Series(...).reindex(df_1m.index, method="ffill")`. Kết quả cho ra biến thể
"bắt buộc thuận trend" đạt +1,61đ/lệnh, win rate 67,9% — quá tốt để là thật.

**Nguyên nhân:** Nến 15m nhãn `09:00` chứa dữ liệu tới `09:14`. Khi ffill xuống
khung 1m, timestamp `09:05` nhận giá trị tính từ close của `09:14` → dùng dữ liệu
tương lai. Sau khi `.shift(1)` trên khung cao trước khi reindex, cùng cấu hình đó
rơi xuống **−0,39đ/lệnh** (âm) — tức toàn bộ "edge" là do rò rỉ.

**Rule phòng tránh:** Mọi series từ khung thời gian cao hơn PHẢI `.shift(1)` trước
khi `reindex` xuống khung thấp. Áp dụng cho cả `_get_trend_full()` (15m/1h/daily)
và bất kỳ chỗ nào resample. Dấu hiệu nghi ngờ: win rate > 60% trên khung phút,
hoặc kết quả tốt bất thường ở đúng biến thể vừa thêm bộ lọc khung cao.

---

## 2. Thị trường VN có drift mạnh — phải đo ALPHA, không đo gross return

**Lỗi gặp:** Đánh giá các điều kiện vào lệnh bằng lợi nhuận trung bình (gross).
Chia đôi dữ liệu VN30F1M thì nửa đầu drift **+0,467đ**, nửa sau **−0,166đ** —
gần như mọi điều kiện LONG đều "tốt" ở nửa đầu và "tệ" ở nửa sau, và ngược lại
với SHORT. Đó là đo beta chứ không phải kỹ năng.

**Rule phòng tránh:** Luôn trừ drift cùng kỳ:
- `alpha_long  = gross_long  − baseline_drift`
- `alpha_short = gross_short + baseline_drift`

Trùng với quy ước đã có cho cổ phiếu trong CLAUDE.md (`buy_a_alpha` = BUY-A avg −
market_avg). Áp dụng cho cả phái sinh.

---

## 3. Trọng số trong bảng điểm phải được kiểm chứng, không đặt theo trực giác

**Lỗi gặp:** Engine v3 tab Phái Sinh đặt RSI mean-reversion trọng số cao nhất
(2.0) và VWAP thấp nhất (0.5). Đo alpha từng thành phần: RSI **−0,287** (hại nhất),
VWAP **+0,191** (tốt nhất) — trọng số đặt ngược hoàn toàn.

**Rule phòng tránh:** Trước khi gộp N chỉ báo thành một điểm số, đo alpha **từng
thành phần riêng lẻ, tách theo chiều LONG/SHORT**, trên IS và OOS. Chỉ giữ thành
phần dương ở cả hai nửa. Gộp thành phần trung tính/âm vào chỉ làm loãng tín hiệu
và tăng tần suất giao dịch — mà tần suất là thứ nhân chi phí lên.

---

## 4. Edge có thể nằm ở CÁCH THOÁT, không chỉ ở tín hiệu vào

**Lỗi gặp:** Đo forward-return thấy alpha dương rõ (T+30), nhưng mô phỏng đầy đủ
với SL 1,5×ATR / TP 2R lại âm toàn bộ (win rate ~33%). Kết luận vội sẽ là "tín
hiệu vô dụng".

**Nguyên nhân:** SL quá chặt bị nhiễu quét trước khi xu hướng kịp hiện. Nới lên
3×ATR + thoát theo thời gian (30 nến) thì cùng tín hiệu cho +0,385đ/lệnh.

**Rule phòng tránh:** Khi một tín hiệu có alpha dương ở forward-return nhưng âm
khi mô phỏng TP/SL — quét tham số thoát lệnh TRƯỚC khi bỏ tín hiệu. Luôn kiểm tra
vùng tham số có dương **liên tục** hay chỉ là một ô may mắn.

---

## 5. Thống kê không bao giờ chạy = lỗi im lặng nguy hiểm nhất

**Lỗi gặp:** `_calc_session_stats()` lọc `act.startswith("ĐÓNG")` nhưng không chỗ
nào trong code sinh record đó. Win Rate và PnL luôn hiển thị **0** suốt nhiều
phiên → user giao dịch theo tín hiệu lỗ mà dashboard không hề báo động.

**Rule phòng tránh:** Với mọi panel thống kê, kiểm tra có ít nhất một đường code
thực sự ghi ra dữ liệu mà panel đó đọc. Panel hiển thị 0 mãi không đổi là dấu
hiệu lỗi, không phải "chưa có dữ liệu". Ưu tiên viết assert/test cho đường ghi.

---

## 6. Telegram `parse_mode="HTML"` — phải escape MỌI nội dung động

**Lỗi gặp:** User báo không nhận được cảnh báo Telegram nào. Credentials hợp lệ
(`getMe` / `getChat` đều 200), hàm gửi không ném lỗi, nhưng tin nhắn không tới.

**Nguyên nhân:** Tin nhắn gửi kèm `parse_mode: "HTML"`. Lý do tín hiệu v3 chứa
`RSI=36.3<40(+2)` → Telegram đọc `<40(+2)` là thẻ HTML mở và **từ chối cả tin
nhắn**:
```
HTTP 400 Bad Request: can't parse entities:
Unsupported start tag "40(+2)" at byte offset 80
```
Vì mọi tín hiệu đều có `RSI=…<40` hoặc `>60` trong phần điểm, **không tin nào
tới nơi suốt nhiều tháng**.

**Tại sao không ai phát hiện:** `_send_telegram` trả `False` rồi
`_send_telegram_async` vứt bỏ giá trị đó (fire-and-forget). Không log, không UI.

**Rule phòng tránh:**
- Mọi biến nội suy vào tin nhắn Telegram/HTML PHẢI qua `_tg_escape()`
  (`html.escape`). Chỉ giữ nguyên các thẻ `<b>`/`<i>` do mình chủ động viết.
- Hàm gửi PHẢI log `response.json()["description"]` khi thất bại — Telegram trả
  mô tả rất rõ, vứt đi là tự bịt mắt.
- KHÔNG fire-and-forget kênh thông báo mà không có nơi xem kết quả. Nếu gửi
  trong thread thì log vào biến module-level có lock để UI đọc lại.
- Cân nhắc bỏ hẳn `parse_mode` nếu không thực sự cần in đậm — text thuần không
  bao giờ lỗi parse.

---

## 7. mtime của file KHÔNG phải thước đo độ mới của dữ liệu

**Lỗi gặp:** Tab Phái Sinh báo "Dữ liệu cũ 1458 phút — AFL có thể chưa chạy
Explorer". Kiểm tra thực tế: file được ghi lúc 10:00:02, lúc đó là 10:02 — AFL
chạy hoàn toàn bình thường. Nhưng nến cuối **bên trong** file là `17/08 09:41`
(hôm trước). Nguồn dữ liệu intraday của Amibroker đã dừng; AFL vẫn cần mẫn xuất
lại đúng bộ nến cũ đó mỗi 5 phút.

Thông báo đổ lỗi cho AFL khiến người dùng đi tìm sai chỗ hoàn toàn.

**Hệ quả nặng hơn:** bot **đã mở lệnh thật trên dữ liệu hôm qua**. Lần đầu mở
tab trong ngày, `ps_last_time` rỗng nên `last_time != ps_last_time` → coi như
"có nến mới" → tính tín hiệu trên nến 09:40 hôm qua → vào lệnh theo giá đã hết
hạn 24 giờ. Và vị thế đó kẹt vĩnh viễn vì không có nến mới nào để kích hoạt
điều kiện thoát.

**Rule phòng tránh:**
- Đo độ mới bằng **timestamp của bản ghi cuối trong dữ liệu**, không bao giờ
  bằng `os.path.getmtime()`. Tiến trình ghi file và tiến trình sinh dữ liệu là
  hai thứ khác nhau.
- Mọi bot giao dịch phải có **rào chặn tuổi dữ liệu** trước khi vào lệnh
  (`_MAX_STALE_MIN`). "Đang trong giờ giao dịch" KHÔNG đồng nghĩa "dữ liệu mới".
- Khi cảnh báo, dùng cả hai chỉ số (mtime + timestamp dữ liệu) để chỉ đúng thủ
  phạm — chúng phân biệt được "bộ xuất chưa chạy" với "nguồn đã chết".
- Điều kiện thoát lệnh phụ thuộc dữ liệu mới thì phải có đường thoát dự phòng
  khi dữ liệu ngừng hẳn.


---

## 8. Tiến trình nền chết âm thầm — giao diện phải lộ ra

**Lỗi gặp:** Cảnh báo Telegram cho cổ phiếu cơ sở ngừng hoạt động. Kiểm tra:
logic chấm điểm khoẻ (dry-run vẫn ra 285 mã đủ điều kiện), Telegram gửi được,
`scan_result.csv` vẫn mới. Thủ phạm: tiến trình nền `alert_watcher.py` đã chết
từ **28 ngày trước** và không có gì khởi động lại.

Trong suốt 28 ngày đó, app không hề có dấu hiệu nào cho biết cảnh báo đã ngừng.

**Rule phòng tránh:**
- Mọi thành phần chạy TÁCH KHỎI app (watcher, cron, service) phải có **dấu vết
  sống** mà app đọc được — mtime của log, heartbeat file, hoặc bảng trạng thái.
- Giao diện phải hiển thị "lần chạy cuối" và **cảnh báo khi quá hạn**, thay vì
  im lặng. Không có tin nhắn nào ≠ không có tín hiệu nào.
- Ghi rõ trên UI cái nào là thủ công, cái nào là tự động — user dễ tưởng bấm nút
  trong app là đã bật cảnh báo tự động.
- Cùng họ với bài học 5 (thống kê không bao giờ chạy) và 6 (Telegram lỗi im
  lặng): **thất bại im lặng nguy hiểm hơn thất bại ồn ào**.


---

## 9. Xếp hạng tín hiệu hai chiều: điểm thô không phải thước đo độ mạnh

**Lỗi gặp:** `alerter.py` chọn top 10 cảnh báo bằng `sort(key=lambda x: -composite)`.
Với BUY (ngưỡng ≥65) thì điểm cao = mạnh, đúng. Với SELL (ngưỡng ≤35) thì điểm
**thấp** mới là mạnh — nên sort giảm dần chọn đúng các lệnh bán yếu nhất
(32,5–34,7) và vứt bỏ lệnh bán mạnh nhất (PVX −4,5, DVG −3,3).

**Bẫy thứ hai khi sửa:** đổi sang xếp theo độ mạnh
(`BUY: comp − ngưỡng` / `SELL: ngưỡng − comp`) thì phiên lệch một bên là chiều
đó chiếm sạch — 264 SELL vs 31 BUY, top 10 toàn SELL, mất hết BUY mạnh.

**Rule phòng tránh:**
- Với chỉ số hai chiều có vùng trung tính, **luôn quy về "khoảng cách vượt ngưỡng"**
  trước khi so sánh; không bao giờ sort trực tiếp điểm thô.
- Khi cắt top-N trên tập hai chiều, **chia suất cho từng chiều** rồi bù chỗ
  thừa, thay vì xếp chung một danh sách.
- Kiểm tra bằng dữ liệu của phiên **lệch mạnh một bên** — phiên cân bằng sẽ
  không lộ ra lỗi này.


---

## 10. `py_compile` KHÔNG bắt được NameError — phải render thật hoặc quét tĩnh

**Lỗi gặp:** Thêm panel tình trạng watcher vào `tabs/tab_model.py` dùng
`Path(__file__)`, chạy `py_compile` thấy "COMPILE OK" rồi báo hoàn thành.
User mở app thì vỡ ngay:
```
NameError: name 'Path' is not defined
  tabs/tab_model.py, line 234, in render
```

**Hai nguyên nhân chồng nhau:**

1. `py_compile` chỉ kiểm **cú pháp**, không phân giải tên. `NameError` chỉ xuất
   hiện lúc chạy. Tin vào nó là tự ru ngủ.
2. Script vá tự động kiểm tra `if 'from pathlib import Path' not in s` để quyết
   định có cần thêm import — chuỗi này **khớp nhầm** với
   `from pathlib import Path as _Path` nằm trong một hàm ở dòng 105, nên tưởng
   đã có import module-level rồi và bỏ qua.

**Rule phòng tránh:**
- Sau khi sửa code UI, PHẢI render thật (`streamlit.testing.v1.AppTest`) chứ
  không dừng ở `py_compile`. Với tab lẻ thì gọi thẳng `render(ctx)` trong AppTest.
- Chạy `python -m pyflakes <file>` để bắt undefined name tĩnh — nhanh, không cần
  môi trường đầy đủ.
- Guard "đã có import chưa" phải khớp **chính xác dòng import module-level**,
  không dùng `in` trên chuỗi con. Tốt hơn: dùng biến/đường dẫn đã có sẵn trong
  scope (ở đây là `_APP_DIR` mà file vốn đã dùng khắp nơi) thay vì thêm import mới.


---

## 11. `st.session_state` không phải nơi giữ trạng thái nghiệp vụ

**Lỗi gặp:** Toàn bộ vị thế đang mở, bộ đếm mã lệnh và nhật ký của bot phái sinh
nằm trong `st.session_state`. Người dùng bấm F5 → mất sạch. Không chỉ khó chịu
về hiển thị mà hỏng dữ liệu thật:
- Vị thế đang mở bị bỏ rơi: journal có dòng MỞ, không bao giờ có dòng ĐÓNG, nên
  lệnh đó biến mất khỏi báo cáo lãi/lỗ.
- Bộ đếm mã lệnh về 0 → lệnh mới trùng mã với lệnh cũ trong ngày → hàm ghép cặp
  theo `trade_id` nối giá vào của lệnh này với giá ra của lệnh kia ⇒ PnL sai.

**Rule phòng tránh:**
- `st.session_state` chỉ dùng cho trạng thái **giao diện** (tab đang mở, bộ lọc,
  cache hiển thị). Mọi thứ mang ý nghĩa nghiệp vụ — vị thế, số dư, bộ đếm ID —
  phải nằm trên đĩa/DB.
- ID nghiệp vụ không được sinh từ biến đếm trong bộ nhớ. Lấy `max(id)` từ nguồn
  bền vững làm sàn, hoặc dùng UUID.
- Test bắt buộc: **xoá sạch state rồi render lại** — mô phỏng F5. Nếu mất thông
  tin nào thì thông tin đó đang ở sai chỗ.


---

## 12. Không so kết quả giữa hai harness backtest khác nhau

**Lỗi gặp:** Thêm cổng biến động ATR, replay qua code được **+725,1đ**. Đem so
với **+818,2đ** ghi ở Phase 2 (không cổng) rồi kết luận "cổng làm giảm 11% lợi
nhuận, nên gỡ". Sai hoàn toàn.

Hai con số đến từ hai harness khác nhau: vòng lặp khác (`i=ex+1` vs `i+=1`), cửa
sổ dữ liệu khác (400 vs 750 nến), điểm bắt đầu khác (i=200 vs i=700). Chạy A/B
trong **cùng** harness thì "không cổng" chỉ cho **+672,5đ** — tức cổng thực ra
làm TĂNG 7,8%, ngược hẳn kết luận ban đầu.

Hệ quả phụ: còn đổ lỗi cho cổng về việc Q2/2026 gần như hoà vốn, trong khi cả 3
biến thể (không cổng / 0,7 / 0,9) đều có quý yếu nhất quanh +0,004–0,009 — đó là
đặc tính của quý đó.

**Rule phòng tránh:**
- Đánh giá một thay đổi PHẢI bằng A/B trong **cùng một lần chạy, cùng một hàm
  replay**, chỉ đổi đúng tham số đang xét. Không bao giờ so với số ghi trong tài
  liệu từ lần chạy trước.
- Số liệu cũ trong `todo.md` chỉ để tham khảo lịch sử, KHÔNG dùng làm mốc đối chứng.
- Khi bản backtest độc lập và replay qua code lệch nhau, tin **replay qua code**
  (đó là thứ chạy thật), và dựng A/B trong chính nó để quyết định.


---

## 13. "Dương mấy quý" không đủ — phải xem lợi ích PHÂN BỔ thế nào

**Lỗi gặp:** Đánh giá tuỳ chọn TP 3R bằng chỉ số "dương mấy quý". Thấy 4/5 (so
với 5/5 của bản gốc) nên kết luận "mất một quý, không đáng bật".

Xem chi tiết mới thấy sai ở cả hai đầu:
- Quý bị coi là "mất" (2026Q2) thực ra **gần như hoà vốn ở CẢ HAI cấu hình**
  (+2,0đ vs −2,4đ trên hơn 400 lệnh). Chênh 4,4đ suốt một quý là nhiễu, không
  phải bằng chứng về độ bền.
- Vấn đề THẬT lại nằm chỗ khác: toàn bộ +71,3đ cải thiện đến từ **một quý duy
  nhất** (2025Q3 +69,1đ). Bốn quý còn lại cộng lại +2,2đ ≈ 0.

**Rule phòng tránh:**
- Chỉ số đếm ("dương n/5 quý") che mất **độ lớn**. Một quý +0,005đ và một quý
  +0,7đ đều tính là "dương" nhưng ý nghĩa khác hẳn.
- Luôn xem **bảng chênh lệch từng kỳ** giữa hai cấu hình, không chỉ tổng và số
  kỳ dương. Câu hỏi đúng là *"lợi ích có lan toả không, hay dồn vào một giai đoạn?"*
- Cải tiến có >80% lợi ích tập trung vào một kỳ ⇒ coi như chưa được chứng minh,
  dù tổng đẹp.


---

## 14. Win rate là thuộc tính của LUẬT THOÁT, không phải của tín hiệu vào

**Bối cảnh:** User thấy win rate 45% quá thấp, đề nghị thử supply/demand và
price action. Đã test 8 khái niệm (pin bar, engulfing, BOS, liquidity sweep,
inside bar, S/D zone retest…) trên 67.432 nến, dùng chung một bộ luật thoát.

**Kết quả:** cả 9 phương pháp (kể cả engine gốc) đều cho win rate trong dải hẹp
**43,9–46,3%**. Chênh lệch lớn nhất chỉ 0,4 điểm phần trăm.

Trong khi đó chỉ cần đổi SL từ 3×ATR sang 8×ATR là win rate lên **48,2%** ngay —
nhưng mất 43% lợi nhuận.

**Rule phòng tránh:**
- Khi ai đó (kể cả user) muốn "nâng win rate", hỏi trước: nâng để làm gì? Win
  rate và kỳ vọng đi ngược nhau trong hầu hết hệ thống theo xu hướng.
- Muốn thay đổi win rate thì chỉnh **luật thoát** (SL/TP/thời gian giữ), không
  phải đi tìm tín hiệu vào lệnh mới. Tín hiệu vào quyết định **kỳ vọng**.
- Trước khi nghiên cứu lý thuyết mới, chạy một bảng so sánh nhanh giữ nguyên luật
  thoát — nếu win rate của mọi biến thể đều chụm lại thì đó là trần cấu trúc,
  đừng tốn công tìm tiếp.
- Kiểm tra thêm **chuỗi thua và sụt giảm vốn**: win rate cao hơn nhờ SL rộng
  thường đi kèm chuỗi thua DÀI hơn và drawdown SÂU hơn — tức tệ hơn về tâm lý,
  ngược với kỳ vọng trực giác.


---

## 15. Chỉ số toàn mẫu tăng đơn điệu vẫn có thể là khớp nhiễu — phải tách OOS

**Lỗi suýt mắc:** Thử kéo dài thời gian giữ lệnh (30 → 60 → 120 → cuối phiên).
Kết quả toàn mẫu tăng **đơn điệu hoàn hảo**: đ/lệnh 0,464 → 0,681 → 0,998 →
1,536; tổng 836 → 881 → 961 → 965. Tính đơn điệu thường được coi là bằng chứng
chống khớp nhiễu (đã dùng chính lập luận này để chấp nhận cổng biến động ATR).

Nhưng tách IS/OOS thì lộ ra: IS tăng đều (468→559→600→663) còn **OOS thì không
có quy luật** (368→322→361→302), và tỷ lệ OOS/IS suy giảm dần
(0,79 → 0,58 → 0,61 → 0,43). Toàn bộ ưu thế nằm ở nửa dữ liệu đầu.

**Rule phòng tránh:**
- Tính đơn điệu theo tham số là điều kiện **cần**, không phải **đủ**. Vẫn phải
  tách IS/OOS cho từng mức tham số, không chỉ cho mức được chọn.
- Nghi ngờ ngay khi **tỷ lệ OOS/IS giảm dần theo hướng tham số** — đó là dấu
  hiệu tham số đang hấp thụ đặc điểm riêng của giai đoạn IS.
- So sánh cuối cùng nên dùng **tổng lợi nhuận trên nửa OOS**, không dùng tổng
  toàn mẫu (toàn mẫu đã bị "nhiễm" IS).

## 16. Hai phiên Streamlit chạy song song làm hỏng mọi file dùng chung

**Lỗi gặp:** Đối chiếu journal thật (56 bản ghi v4, 5 phiên) phát hiện: 6 dòng
trùng lặp hoàn toàn, 4 mã lệnh bị cấp cho HAI lệnh khác nhau trong cùng ngày,
3 cặp vị thế chồng nhau — trong khi engine v4 quy định chỉ 1 vị thế tại 1 thời
điểm. Ngày 20/08 mã #1 vừa là SHORT lúc 09:20 vừa là LONG lúc 10:39.

**Nguyên nhân:** `st.session_state` là RIÊNG từng tab trình duyệt. Mở app ở hai
tab ⇒ hai engine chạy song song, mỗi bên có `ps_position` và `ps_trade_seq`
riêng, cùng ghi vào một `vn30_ai_journal.csv` và cùng ghi đè `ps_state.json`.
Cơ chế bền hoá ở mục 11 chỉ cứu được trường hợp F5 (một phiên chết, một phiên
sinh ra), hoàn toàn không chống được hai phiên SỐNG cùng lúc.

**Hậu quả thật:** `load_trades()` gộp theo `trade_id` rồi lấy `op.iloc[0]` +
`cl.iloc[-1]`, nên khi mã bị trùng nó ghép giá VÀO của lệnh này với giá RA của
lệnh kia. Một "lệnh" hiện ra giữ 118 nến (luật tối đa 30) — không phải bot chạy
sai luật, mà là hai lệnh bị dán vào nhau. Sau khi sửa: 0/26 lệnh vi phạm luật.

**Rule phòng tránh:**
1. Mã lệnh phải DUY NHẤT TOÀN CỤC và cấp từ ĐĨA (`_next_trade_id()` đọc max
   `trade_id` trên toàn journal), không lấy từ bộ đếm trong RAM. Không được reset
   theo ngày: `load_trades()` gộp theo `trade_id` nên báo cáo nhiều ngày sẽ ghép
   lệnh #1 của ngày A với lệnh #1 của ngày B.
2. Phải có khoá một-phiên-ghi (`_claim_ownership()` + nhịp tim trong
   `ps_state.json`). Phiên không giữ quyền chuyển sang CHỈ XEM: `_can_trade()`
   chặn tận gốc `_append_journal()`, `_save_ps_state()` và cả khối logic trading.
3. Hàm đọc phải chịu được dữ liệu đã hỏng sẵn: `_pair_by_time()` ghép MỞ↔ĐÓNG
   theo thứ tự thời gian trong nhóm, chứ không tin `trade_id` là duy nhất.
   Sửa file dữ liệu cũ thì mất lịch sử — làm hàm đọc khoan dung thì không.

---

## 17. Đếm thời gian giữ lệnh bằng đồng hồ là sai — phải đếm theo nến

**Lỗi gặp:** Báo cáo hiện "giữ 120 phút" trong khi luật là tối đa 30 nến, khiến
tưởng bot chạy sai luật.

**Nguyên nhân:** `hold_min = (exit - entry).total_seconds() // 60` tính theo
đồng hồ treo tường. Phiên nghỉ trưa 11:30–13:00 không sinh nến nào, nên lệnh vào
11:29 ra 13:29 chỉ trải qua 30 NẾN nhưng 120 PHÚT đồng hồ.

**Rule phòng tránh:** Với mọi thị trường có phiên nghỉ giữa ngày, đơn vị đo thời
gian giữ lệnh phải là NẾN (hoặc phút giao dịch), không phải phút đồng hồ. Dùng
`_trading_minutes()` trong `daily_report.py` — trừ đúng khoảng 11:30–13:00.

---

## 18. n nhỏ không xác nhận cũng không bác bỏ được edge — bootstrap trước khi sửa

**Lỗi gặp:** 26 lệnh thật cho win 34,6% và −0,365đ/lệnh so với kỳ vọng 45,6% và
+0,466đ. Rất dễ kết luận "thuật toán hỏng" rồi lao vào sửa.

**Đo được:** Bootstrap 200.000 lần rút 26 lệnh từ chính phân phối 1.862 lệnh
lịch sử — độ lệch chuẩn 7,16đ/lệnh:
```
Khoảng 90% thường gặp của PnL/lệnh khi n=26 : [-1,767đ ; +2,859đ]
Xác suất tệ bằng/hơn kết quả thật            : 28,4%
Xác suất VẪN ĐANG LỖ sau 26 lệnh (edge đúng) : 38,1%
Sau 200 lệnh (~29 phiên) vẫn còn 17,9% khả năng đang lỗ
```
Ngày 24/08 lỗ −41,65đ cũng không cá biệt: lịch sử 278 phiên có 2 phiên tệ bằng
hoặc hơn, tệ nhất −54,9đ.

**Bẫy đi kèm:** dữ liệu thật gợi ý "giờ 09h xấu" (6 lệnh, thắng 0%). Kiểm tra
273 phiên thì 09h là khung TỐT: +0,483đ/lệnh, 4/5 quý dương, IS +0,471 / OOS
+0,495. Ngược lại 11h thật sự xấu trong lịch sử nhưng lại là khung TỐT NHẤT trong
26 lệnh thật. n=26 không nói được gì về hiệu ứng theo giờ.

**Rule phòng tránh:** Trước khi sửa thuật toán vì kết quả thật kém, bootstrap
xem kết quả đó có nằm ngoài khoảng bình thường không. Nếu còn trong khoảng —
không có bằng chứng gì để sửa, và sửa lúc này chính là khớp nhiễu.

---

## 19. Lời giải thích "có vẻ cơ học" vẫn phải kiểm chứng bằng số

**Lỗi gặp:** Khung 11h lỗ đều (4/5 quý âm). Lời giải thích tự nhiên: lệnh mở
11:00–11:29 bị đóng bắt buộc lúc 11:30 nên lãi bị cắt cụt trong khi SL vẫn rộng
nguyên 3×ATR — bất đối xứng. Nghe rất thuyết phục.

**Đo được — lời giải thích SAI:** số nến thực sự giữ được theo giờ vào lệnh:
```
09h: 27,4 nến  +0,483đ      13h: 23,3 nến  +0,791đ
10h: 24,8 nến  -0,124đ      14h: 12,7 nến  +0,989đ  <- cụt NHẤT, tốt NHẤT
11h: 23,2 nến  -0,492đ                                  <- cụt ít, xấu nhất
```
Khung 14h bị cắt cụt nặng nhất (12,7 nến) lại lãi cao nhất. Cắt cụt không phải
nguyên nhân.

Thử luật có nguyên lý "không mở lệnh khi còn < K phút trước giờ đóng phiên",
quét K = 0/5/10/15/20/25/30/40 phút: **không đơn điệu** (+0,466 → +0,477 →
+0,504 → **+0,445** → +0,481 → +0,452) và **tỷ lệ OOS/IS giảm dần** theo K
(0,68 → 0,63 → 0,57 → 0,51 → 0,48) — đúng dấu vết khớp nhiễu ở mục 15.
⇒ KHÔNG thêm bộ lọc nào. Giữ nguyên v4.

**Rule phòng tránh:** Một cơ chế nghe hợp lý không phải bằng chứng. Phải chỉ ra
đại lượng mà cơ chế đó tiên đoán, rồi đo chính đại lượng ấy. Ở đây cơ chế tiên
đoán "giữ càng ít nến càng lỗ" — đo ra thì ngược hẳn, thế là xong.

## 20. `open(path, "w")` XOÁ SẠCH file trước khi ghi — mất trắng nếu lỗi giữa chừng

**Lỗi gặp (25/08/2026):** Script vá file kết thúc bằng
```python
io.open(P, "w", encoding="utf-8", newline="").write(src)
```
Chuỗi chèn vào chứa emoji viết sai dạng (`\ud83d\udee1` — cặp surrogate thay vì
`\U0001F6E1`). Python **mở file với mode `"w"` là truncate về 0 byte NGAY**, rồi
mới encode nội dung và ném `UnicodeEncodeError`. Kết quả: `phaisinh_tab.py`
(136.641 byte, 2.569 dòng, công sức 20+ phase) **mất sạch**, nội dung mới không
kịp ghi. Git chỉ có bản cũ, thiếu 24 hàm và 17 hằng số.

**Vì sao 5 patch trước không sao:** chúng chết ở `assert` — tức TRƯỚC khi mở file.
Lần này lỗi rơi đúng vào khe giữa truncate và write. Đây là bug ngủ đông: cùng
một mẫu code, chỉ khác chỗ lỗi xảy ra.

**Rule phòng tránh — mọi script sửa file BẮT BUỘC ghi nguyên tử:**
```python
# ❌ SAI — truncate truoc, encode sau
io.open(P, "w", encoding="utf-8").write(src)

# ✅ ĐÚNG — kiểm tra + encode xong mới chạm vào file đích
compile(src, P, "exec")            # cú pháp sai thì dừng ở đây
data = src.encode("utf-8")         # encode sai thì dừng ở đây
tmp = P + ".tmp"
with open(tmp, "wb") as f:
    f.write(data)
os.replace(tmp, P)                 # thay thế nguyên tử, không có trạng thái nửa vời
```
Thêm: **không viết emoji bằng `\uXXXX` cho ký tự ngoài BMP.** `\ud83d\udee1` là
cặp surrogate — hợp lệ trong chuỗi Python nhưng KHÔNG encode được sang UTF-8.
Viết thẳng ký tự (🛡️) hoặc dùng `\U0001F6E1`.

---

## 21. `.pyc` + transcript = khôi phục được file đã mất, KHỚP TỪNG BYTE

**Bối cảnh:** Google Drive giữ lịch sử phiên bản nhưng user không thao tác được;
`git HEAD` quá cũ; cache Drive bị nén; `linecache` trong tiến trình Streamlit đang
chạy chỉ còn 9 dòng (mảnh của traceback, không phải cả file).

**Cách khôi phục — 2 trụ cột:**

1. **`__pycache__/*.pyc` làm ORACLE.** Header pyc chứa `mtime` + **kích thước
   source gốc** (136.641). Bytecode cho: tên mọi hàm, `co_firstlineno` (vị trí
   dòng chính xác), docstring, và **toàn bộ hằng chuỗi** (154 chuỗi text UI/HTML).
   ⇒ Biết chính xác đích đến, và đo được tiến độ khách quan.

2. **Transcript `.jsonl` để PHÁT LẠI.** Mọi `Edit` (old/new), mọi heredoc Python,
   mọi patch script đều nằm trong transcript kèm nội dung đầy đủ. Bắt đầu từ
   `git HEAD` (đúng bằng điểm xuất phát của phiên) rồi phát lại **theo thứ tự
   thời gian**: 57 Edit + 24 lệnh bash + 3 script.

**Kết quả:** 57/57 Edit áp sạch → 90,4% → bù 5 chỗ thủ công → **100% khớp
bytecode**, `marshal.dumps()` trùng SHA-256, source đúng 136.641 byte,
pyflakes cảnh báo ở đúng 10 dòng cũ. Comment nguyên vẹn.

**Rule:** Trước khi chấp nhận mất file, kiểm tra theo thứ tự:
`.pyc` trong `__pycache__` → transcript phiên làm việc → git objects
(`git fsck --lost-found`) → lịch sử phiên bản của dịch vụ đồng bộ →
`linecache` của tiến trình đang chạy. Và **luôn lập oracle trước** (`.pyc`)
để biết khi nào thì xong, thay vì dựng mò rồi đoán.

⚠️ Đừng lặp lại lỗi cùng loại khi đang sửa lỗi: script khôi phục lúc đầu cũng
dùng `open(...,"w")`. Ghi nguyên tử áp dụng cho CẢ script cứu hộ.

---

## 22. Hai cửa sổ không phải lúc nào cũng là sai — hỏi user trước khi khoá cứng

**Lỗi gặp:** Phát hiện journal hỏng do 2 phiên Streamlit song song (mục 16), tôi
thiết kế khoá coi cửa sổ thứ hai là **lỗi**: banner đỏ ⛔, khuyên "đóng bớt một
cửa sổ". Hỏi ra thì user **cố ý** mở 2 cửa sổ vì một trình duyệt hay bị đóng khi
lấy cookie đăng nhập — cửa sổ thứ hai là **dự phòng**, không phải nhầm lẫn.

**Bug thật nằm ở chỗ khác:** trạng thái chỉ nạp từ đĩa lúc `_fresh` (khởi động
phiên). Cửa sổ dự phòng đứng chờ vài giờ rồi tiếp quản sẽ dùng `ps_position`
trong RAM đã cũ ⇒ không thấy lệnh phiên kia đang mở ⇒ mở thêm lệnh thứ hai, còn
lệnh cũ kẹt với dòng MỞ không bao giờ có ĐÓNG. Đúng kiểu hỏng vừa sửa xong ở mục
16, quay lại qua đường tiếp quản.

**Cách làm đúng:** `_reload_from_disk()` gọi ngay khi chuyển từ "chỉ xem" sang
"điều khiển". Banner chuyển từ ⛔ đỏ sang 🛡️ "Cửa sổ DỰ PHÒNG", nói rõ sẽ tự tiếp
quản sau bao nhiêu giây, kèm nút giành quyền ngay.

**Rule phòng tránh:** Khi dữ liệu cho thấy user đang làm điều "sai", hỏi vì sao
trước khi chặn. Thói quen lạ thường có lý do vận hành thật; chặn nó đi là ép user
bỏ một cơ chế an toàn họ đang dựa vào. Thiết kế nên **hỗ trợ ý định đó cho an
toàn**, không phải cấm nó.

## 23. Chốt chặn "làm một lần" phải commit TRƯỚC tác dụng phụ, không phải sau

**Bối cảnh:** Rà soát spam Telegram tab Phái Sinh. Khối xử lý nến mới có dạng:
```python
if last_time != st.session_state["ps_last_time"]:      # chốt chặn
    ... _send_telegram_async()  x3
    ... _append_journal()
st.session_state["ps_last_time"] = last_time           # commit — ĐẶT CUỐI
```
Fragment chạy `run_every=1`. Chỉ cần một exception bất kỳ giữa chốt chặn và dòng
commit là nến cũ vẫn bị coi là "mới" ở lần render kế tiếp ⇒ gửi lại toàn bộ tin
nhắn, **mỗi giây một lần**, tới ~60 tin/phút cho đến khi có nến mới. Exception
lại bị nuốt vào `ps_errors` nên hoàn toàn im lặng.

**Rule phòng tránh:** Chốt chặn kiểu "mỗi X một lần" phải **ghi nhận đã nhận X
ngay sau khi kiểm tra**, trước mọi tác dụng phụ (gửi tin, ghi file, gọi API).
Bỏ lỡ một lần còn hơn lặp vô hạn. Đây là mẫu "claim the work item before doing
it" — giống hệt lý do hàng đợi đánh dấu message là in-flight trước khi xử lý.

**Chống trùng phải có lớp cuối, độc lập tầng trên.** `_send_telegram()` trước đây
không kiểm tra gì — gọi bao nhiêu gửi bấy nhiêu. Đã thêm `_tg_is_duplicate()`:
băm nội dung, bỏ qua tin y hệt trong `_TG_DEDUP_SEC = 90` giây. Hai lần lỗi tầng
trên đã gây gửi trùng (hai cửa sổ; chốt chặn commit muộn) ⇒ cần chốt chặn cuối.

---

## 24. Đọc dấu vết để phân biệt nguyên nhân, đừng sửa theo giả thuyết

**Bối cảnh:** Có hai giả thuyết cho spam Telegram — (a) hai cửa sổ cùng chạy
engine, (b) chốt chặn commit muộn gây lặp mỗi giây. Cả hai đều có thật trong code.

**Cách phân biệt bằng dữ liệu:** đếm số lần lặp của mỗi bản ghi journal.
```
Ban ghi v4: 59 · nhom bi lap: 3
   2 lan  |  3 nhom      <- KHONG co nhom nao >2
```
Lặp **đúng 2 lần, không bao giờ hơn** = dấu vết hai cửa sổ. Bão lỗi mỗi giây sẽ
cho 10–60 bản. ⇒ Nguyên nhân thực tế là (a); (b) là lỗ hổng tiềm ẩn chưa kích hoạt.

**Rule phòng tránh:** Khi nhiều nguyên nhân cùng có thể giải thích triệu chứng,
tìm **đại lượng phân biệt được chúng** (ở đây: phân bố số lần lặp) rồi đo. Sửa cả
hai vẫn đúng, nhưng phải biết cái nào đã thật sự xảy ra — nếu không sẽ báo cáo
sai cho user và không biết bản sửa nào mới thực sự có tác dụng.

