# HANDOFF — trạng thái auto-trade phái sinh

> File này là ảnh chụp NGẮN "đang ở đâu / làm gì tiếp". Chi tiết đầy đủ ở
> `tasks/todo.md` (Phase 28x) và `tasks/lessons.md`. Cập nhật mỗi khi rời việc
> giữa chừng.

Cập nhật lần cuối: **2026-09-04 ~14:50** (sau Phase 28k + điều tra AmiBroker treo).

---

## Trạng thái ngay lúc này

| Mục | Giá trị |
|---|---|
| Streamlit | 1 tiến trình duy nhất, đã restart sau commit Phase 28k — code mới nhất đã chạy |
| `data/autotrade_config.json` | `enabled=true, dry_run=false, auto_all_signals=true, max_daily_loss_vnd=1000000` (gitignore) |
| Vị thế ẢO (`ps_state.json`) | **LONG #44** đang mở (entry 1979,5, mở 14:14 04/09), có shadow trailing 4×ATR song song |
| **Vị thế THẬT trên VPS** | **KHÔNG có lệnh thật cho #44** — bị `_ticket_mode()` từ chối từ 14:16 tới nay (xem dưới) |
| ⚠️ Phiếu lệnh VPS | Đang kẹt ở **"Lệnh điều kiện / Stop Loss/Take Profit"**, chưa chuyển về "Lệnh thường". Guard `_ticket_mode()` (Phase 28j) đang từ chối MỌI hành động auto-trade (cả mở lẫn đóng) vì lý do này |
| ⚠️ Dữ liệu Amibroker | Task Scheduler "AmiBroker AutoExplore" **đã bị TẮT** (04/09, xem lesson 30) vì treo 7+ ngày liên tục — AmiBroker **KHÔNG còn tự khởi động lúc mở máy**, cần tự mở tay + chạy Explorer tay từ giờ |
| Git | branch `phaisinh-v4-hardening`, commit mới nhất `d174989` (Phase 28k), **đã push origin** |
| GitHub auth | Đã push bằng PAT tạm 1 lần (`ghp_DnrlKk...`). User nói "để lại 1 cái" đang hoạt động nhưng KHÔNG rõ token nào — coi như **chưa có credential cố định**, phiên sau nhiều khả năng lại cần xin PAT mới |

## Việc phải làm phiên sau (ưu tiên từ trên)

1. **[NGƯỜI] Chuyển phiếu lệnh VPS về "Lệnh thường"** trong cửa sổ Chrome
   auto-trade — NẾU muốn dùng cơ chế hiện tại (vào lệnh thường → tự gắn SL/TP
   sau vài giây). Xem mục 2 nếu muốn đổi hẳn sang lệnh gộp điều kiện.
2. **Điều tra + xây "lệnh gộp điều kiện" cho lệnh VÀO** — xem chi tiết đầy đủ ở
   `tasks/todo.md` Phase 28l. User xác nhận: nên dùng panel "Lệnh điều kiện"
   (vào + SL + TP gộp 1 lệnh) thay vì "Lệnh thường" + gọi SL/TP riêng như hiện
   tại. Cần bắt request thật **trong phiên giao dịch bình thường** (không phải
   ATC) — thử 04/09 lúc 14:23 gặp đúng khung ATC nên chưa bắt được.
   - Phiên tiếp theo có giao dịch: kiểm tra lịch — 05-06/09 là T7/CN, nhiều khả
     năng phiên kế là **Thứ Hai 07/09/2026 09:00** (xác nhận lại nếu có lễ).
3. **[NGƯỜI] Xử lý AmiBroker treo tận gốc** — task tự động đã tắt (giải pháp
   tạm), nhưng chưa rõ NGUYÊN NHÂN `wscript.exe`/`AutoExplore.vbs` bị treo. Nếu
   muốn bật lại tự động: `schtasks /Change /TN "AmiBroker AutoExplore" /Enable`
   (cần Admin) — chỉ làm sau khi tìm hiểu vì sao nó treo.
4. Vị thế ảo #44 (LONG) sẽ tiếp tục đứng yên cho tới khi có nến 1 phút mới từ
   AmiBroker — không cần lo lắng, không phải lỗi, chỉ là chờ dữ liệu.

## Quy tắc an toàn khi điều tra (đã học, đừng quên)

- **KHÔNG nối CDP cổng 9222 bằng bất kỳ script nào khi `enabled=true`** (lesson 29).
  Muốn nghe mạng/điều tra: tạm đặt `enabled=false` trong config trước (nhớ lưu
  giá trị cũ để khôi phục sau), xong việc thì khôi phục lại.
- Script điều tra chạy bằng `C:\Users\Admin\AppData\Local\Programs\Python\Python313\python.exe`
  (có playwright 1.59). `python` trần trong PATH = stub System32, KHÔNG có playwright.
- Đọc trạng thái thật từ VPS (không có API): `assetPanel` / `table.tbl-status-danhmuc`
  (cột Vị thế: `-1`=short1, `1`=long1) / `#order_normal` (hàng dữ liệu đầu = mới nhất).
- Script điều tra chỉ-đọc: đặt trong scratchpad, KHÔNG bấm/điền, chỉ `page.evaluate()`.
- **Không tin mtime file feed AmiBroker** — đọc nến CUỐI CÙNG bên trong so với
  giờ thực tế. mtime mới + nến cũ = nguồn dữ liệu đã dừng (xem lesson 30).

## Đã sửa ở Phase 28k (commit d174989, 04/09/2026)

Theo dõi song song "vị thế ảo trailing 4×ATR" — xem chi tiết đầy đủ trong
CLAUDE.md mục "Theo dõi song song shadow trailing 4×ATR" + `tasks/todo.md`
Phase 28k. Tóm tắt: mở song song 1 vị thế ẢO dùng luật trailing (khác luật
thật), sống độc lập, ghi journal riêng, có báo cáo so sánh trong UI — CHƯA có
dữ liệu sống, cần vài ngày/tuần chạy thật.

## Đã sửa ở Phase 28j (commit 7aa9222, 03/09/2026)

`vn_invest/auto_trader.py`: `_ticket_mode()` guard (phiếu phải ở "Lệnh thường",
không thì HỦY + Telegram) · `_submit()` trả `(bool,str)` + xác nhận lệnh vào sàn
bằng `#order_normal`/`Vị thế`/popup lỗi (hết 6s = thất bại) · bỏ `except: pass` ở
nút xác nhận · `_place_sltp()` coi `FOS-\d` trong response là từ chối · chỉ đặt
SL/TP sàn khi `_read_position()` xác nhận vị thế đúng chiều · `close_position()`
HỦY nếu VPS không có vị thế đúng chiều (chặn lệnh mở trần trụi kiểu 184790).

⚠️ **Cập nhật 04/09**: giả định "chỉ Lệnh thường mới đúng" của guard này có thể
SAI — xem Phase 28l ở trên, cần thiết kế lại để chấp nhận lệnh gộp điều kiện.
