# HANDOFF — trạng thái auto-trade phái sinh

> File này là ảnh chụp NGẮN "đang ở đâu / làm gì tiếp". Chi tiết đầy đủ ở
> `tasks/todo.md` (Phase 28x) và `tasks/lessons.md`. Cập nhật mỗi khi rời việc
> giữa chừng.

Cập nhật lần cuối: **2026-09-03 ~16:00** (sau Phase 28j).

---

## Trạng thái ngay lúc này

| Mục | Giá trị |
|---|---|
| Bot auto-trade | **TẮT HẲN** — 0 tiến trình Streamlit (đã `taskkill`), `data/autotrade_config.json` → `enabled=false` |
| `data/autotrade_config.json` | `enabled=false, dry_run=false, auto_all_signals=true, max_daily_loss_vnd=1000000` (file gitignore) |
| Vị thế ẢO (`ps_state.json`) | `position: null` (SHORT #41 đã đóng cuối phiên 14:25), `trade_seq=41` |
| **Vị thế THẬT trên VPS** | ⚠️ **LONG 1 `41I1G9000` MỒ CÔI** — bot code cũ mở lúc 14:25 (lệnh 184790 @1959.50) khi TK thật đang phẳng. Lãi tạm +400k lúc xem cuối. **PHẢI ĐÓNG TAY** khi market mở (09:00). |
| Lãi/lỗ thật hôm nay 03/09 | Đã thực hiện −1.200.000đ · chưa đóng ~+400.000đ · Tổng TS ~63,76tr |
| Bộ đếm lệnh (`autotrade_state.json`) | `{"date":"2026-09-03","count":5}` (max 6/ngày) |
| Git | branch `phaisinh-v4-hardening`, commit mới nhất `7aa9222` (Phase 28j), **đã push origin** |
| GitHub auth | credential manager + gh token đều HỎNG. Push cần `gh auth login` hoặc PAT mới. |

## Việc phải làm phiên sau (ưu tiên từ trên)

1. **[NGƯỜI] Đóng tay vị thế LONG 1 mồ côi** trên VPS SmartPro sau 09:00. Bot code
   mới KHÔNG tự đụng (guard `_read_position` trong `close_position` + `ps_state`
   không có position).
2. **Kiểm chứng full-flow lệnh thật với code Phase 28j** — chưa từng chạy live.
   Cách an toàn: bật lại `enabled=true`, `dry_run=true` trước, chạy **1 instance**
   Streamlit duy nhất, quan sát 1-2 tín hiệu; rồi mới `dry_run=false`.
3. **[NGƯỜI] Revoke** GitHub PAT tạm `ghp_wWXa...` (full repo scope, dùng 1 lần
   để push 03/09) + PAT cũ `ghp_5eTa...` (Phase 28i).
4. Xử lý gốc: **nhiều instance Streamlit chạy song song** (cửa sổ dự phòng
   Phase 25–26) cùng ghi `autotrade_config.json` từ `session_state` riêng → giằng
   co `enabled`. Cân nhắc: chỉ mở 1 instance, hoặc panel auto-trade đọc file làm
   nguồn sự thật thay vì ghi đè từ toggle mỗi rerun.

## Quy tắc an toàn khi điều tra (đã học, đừng quên)

- **KHÔNG nối CDP cổng 9222 bằng bất kỳ script nào khi `enabled=true`** (lesson 29).
  Muốn park bot để soi: **kill hết tiến trình Streamlit**, đừng chỉ sửa file
  (3 instance sẽ ghi đè lại `enabled=true` trong ~1 giây).
- Script điều tra chạy bằng `C:\Users\Admin\AppData\Local\Programs\Python\Python313\python.exe`
  (có playwright 1.59). `python` trần trong PATH = stub System32, KHÔNG có playwright.
- Đọc trạng thái thật từ VPS (không có API): `assetPanel` / `table.tbl-status-danhmuc`
  (cột Vị thế: `-1`=short1, `1`=long1) / `#order_normal` (hàng dữ liệu đầu = mới nhất).
- Script điều tra chỉ-đọc: đặt trong scratchpad, KHÔNG bấm/điền, chỉ `page.evaluate()`.

## Đã sửa ở Phase 28j (commit 7aa9222)

`vn_invest/auto_trader.py`: `_ticket_mode()` guard (phiếu phải ở "Lệnh thường",
không thì HỦY + Telegram) · `_submit()` trả `(bool,str)` + xác nhận lệnh vào sàn
bằng `#order_normal`/`Vị thế`/popup lỗi (hết 6s = thất bại) · bỏ `except: pass` ở
nút xác nhận · `_place_sltp()` coi `FOS-\d` trong response là từ chối · chỉ đặt
SL/TP sàn khi `_read_position()` xác nhận vị thế đúng chiều · `close_position()`
HỦY nếu VPS không có vị thế đúng chiều (chặn lệnh mở trần trụi kiểu 184790).
Test: `scratchpad/test_autotrader_fixes.py` 33/33 (fake browser; scratchpad = tạm,
có thể phải viết lại phiên sau).
