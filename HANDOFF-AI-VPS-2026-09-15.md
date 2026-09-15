# Handoff — AutoTrade VPS: lệnh thủ công × bot, launcher, shortcut

Cập nhật: 15/09/2026 (khoảng 11:15), múi giờ Asia/Saigon. Nối tiếp
[HANDOFF-AI-VPS-2026-09-08.md](HANDOFF-AI-VPS-2026-09-08.md) và
[tasks/backlog-2026-09-13.md](tasks/backlog-2026-09-13.md). Không chứa token, số
tài khoản, ID lệnh hay PnL tài khoản thật. PID/cổng bên dưới là lần kiểm tra
cuối, phải kiểm tra lại khi tiếp nhận.

## 0. Tóm tắt 1 phút

- **Sự cố:** Telegram vẫn báo tín hiệu nhưng bot không vào lệnh thật từ ~09:30.
- **Nguyên nhân (đã chứng minh bằng snapshot VPS chỉ đọc):** user đóng tay vị thế
  LONG bot mở lúc 09:01. Chu kỳ bot kẹt `UNKNOWN` vĩnh viễn → chặn mọi lệnh mở mới.
- **Đã sửa + đã xác nhận thật:** sau khi nạp code, chu kỳ kẹt tự nhả với lý do
  "Vị thế đã đóng ngoài bot (lệnh thủ công)", bot vào lệnh LONG tín hiệu 10:40,
  SL sàn khớp 10:51, đối soát đóng chu kỳ đúng.
- **Sự cố phụ:** shortcut Desktop "App CK" gọi thẳng `streamlit.exe` → mỗi lần
  "restart" thêm 1 server (8501/8502/8503 cùng chạy, 3 worker tranh khoá).
  `Chay_App.bat` lại chọn nhầm Python của `hermes-agent`. Đã sửa cả hai.
- **Trạng thái lúc bàn giao:** 1 server duy nhất 8501 (PID 17724, mở 11:01:58
  qua `Chay_App.bat`), Chrome AutoTrade cổng 9333, worker đối soát/Telegram không
  lỗi, không có vị thế bot, không lệnh/điều kiện treo, chưa chạm trần lỗ ngày.

## 1. Lệnh thủ công × bot — cơ chế và bản sửa

### Diễn biến 15/09 (đã đối chiếu snapshot VPS thật, chỉ đọc)

| Giờ | Sự kiện |
|---|---|
| 09:01 | Bot mở LONG 1 HĐ (SLTP gốc + SL/TP sàn) |
| 09:23 | Lệnh BÁN MAK **không do bot gửi** khớp → VPS tự hủy cặp SL/TP, net=0 |
| 09:32 | Engine yêu cầu đóng; log "Không có vị thế thật do bot quản lý khớp yêu cầu đóng" |
| 09:34, 10:10 | Tín hiệu mới (10:09 là ⭐ MẠNH) → "Đang có vị thế/lệnh chưa đối soát xong" |

Chứng cứ lệnh là thủ công: runtime ghi intent `UNKNOWN` xuống journal **trước**
mọi lệnh gửi VPS, mà `cycle.exits` rỗng.

### Vì sao kẹt

`reconcile()` tính `remaining = entry.filled − Σ(lệnh thoát bot biết + lệnh con
SL/TP)` = 1, VPS báo `net=0` ⇒ nhánh `abs(net)!=remaining` đặt `UNKNOWN` + return.
Tick sau lặp lại y hệt, không có đường thoát. `active_cycle()` chỉ cho 1 chu kỳ
chưa CLOSED ⇒ `submit()` từ chối mọi tín hiệu. Telegram không bị ảnh hưởng vì là
luồng riêng — nên "Telegram báo mà bot không vào".

### Bản sửa — `_release_after_outside_close()` trong `vn_invest/autotrade_runtime.py`

Gọi ngay trước nhánh `UNKNOWN` khi `net==0 and remaining>0` và lệnh mở đã TERMINAL.
Chỉ nhả khoá khi có **bằng chứng**:
- Lệnh ngược chiều cùng mã, `filled>0`, **không thuộc bot** (không nằm trong
  `entry.before_ids`, `entry.broker_id`, `exits[].broker_id`, lệnh con SL/TP đã
  biết) có tổng khớp **đúng bằng** `remaining`.
- Không có lệnh thoát bot đang chờ, không có điều kiện SL/TP đang `TRIGGERED`.
- Nếu SL/TP của bot còn treo (chưa COND_TERMINAL): chu kỳ `CLOSING`, **hủy từng
  nhánh** (chỉ khi gửi thật bật và `allow_actions`), không bao giờ gửi lệnh thoát.
  Lý do: SL treo sau khi phẳng sẽ MỞ vị thế mới khi kích hoạt.
- Hết điều kiện treo → `CLOSED`, ghi `outside_close_ids`, `close_reason` =
  "Vị thế đã đóng ngoài bot (lệnh thủ công)".

Giữ nguyên khoá (không đổi hành vi cũ) khi: `net=0` mà không có lệnh khớp giải
thích được (portfolio trễ so với sổ lệnh), lệnh tay chưa khớp, hoặc lệnh tay khớp
lệch khối lượng.

### Kiểm chứng

- `tests/test_autotrade_manual_close.py` (4 ca): đóng tay + VPS đã hủy SL/TP →
  FLAT và tín hiệu kế tiếp vào được; đóng tay khi SL/TP còn treo → hủy 2 nhánh,
  không gửi exit; net=0 không có khớp → giữ khoá; lệnh tay chưa khớp → giữ khoá.
  Hai ca đầu **đỏ trước khi sửa** đúng triệu chứng `UNKNOWN`.
- Toàn bộ: **356 passed** (`python -m pytest tests -q`).
- Replay `reconcile(allow_actions=False)` trên **bản sao** journal thật + snapshot
  VPS thật (broker giả ném lỗi nếu có send): chu kỳ kẹt → CLOSED, nhận đúng lệnh tay.
- Sống: server nạp code nhả chu kỳ 10:42:39, bot vào lệnh tín hiệu 10:40 lúc
  10:42:41, SL sàn kích hoạt 10:51, chu kỳ CLOSED, `last_error` rỗng.

### Chưa xử lý (cố ý)

- **Đóng tay một phần** (bot giữ N>1, user đóng <N): vẫn giữ khoá `UNKNOWN`.
  Hiện `max_qty=1` nên chưa gặp.
- **User mở thêm tay cùng chiều** khi bot đang giữ: nhánh
  "Vị thế VPS chưa khớp khối lượng bot đã xác nhận" vẫn giữ khoá như cũ.
- **Chu kỳ kẹt qua ngày** đi `vps_overnight.reconcile()` — nhánh đó KHÔNG gọi hàm
  nhả khoá mới. Đóng tay trong ngày thì nạp code/tick trong ngày sẽ nhả; kẹt qua
  đêm cần kiểm tra riêng.

## 2. Launcher và shortcut

### Nhiều server Streamlit (lặp lại lessons 35)

Shortcut Desktop **"App CK"** trỏ `...\Python313\Scripts\streamlit.exe run app.py`
— đi vòng chốt chặn "8501 đã LISTENING thì không mở server thứ hai" trong
`Chay_App.bat`. Streamlit tự nhảy cổng 8502, 8503. Đóng cửa sổ trình duyệt
**không** tắt server. Log dấu vết: "Chờ khoá trạng thái Xs — bên giữ trước đó:
[PID:VPS-reconcile]" với nhiều PID khác nhau.

Đã đổi "App CK" → `Chay_App.bat`. Chẩn đoán nhanh:
```powershell
netstat -ano | findstr :850
Get-CimInstance Win32_Process | ? { $_.CommandLine -match 'streamlit' } | select ProcessId,CreationDate
```

### `Chay_App.bat` chọn nhầm Python

`python` trong PATH trỏ `C:\Users\Admin\AppData\Local\hermes\hermes-agent\venv`
(không streamlit, không pip) ⇒ "Can Streamlit 1.55... No module named pip".
Nay chọn tường minh `%PY%`: `.venv` → `venv` → `%LOCALAPPDATA%\Programs\Python\Python313\python.exe`
→ `python`; mọi lệnh dùng `"%PY%" -c`, `"%PY%" -m pip`, `"%PY%" -m streamlit run`.
Đã chạy riêng đoạn chọn Python trong cmd (đúng Python313, kiểm phiên bản đạt) và
user đã khởi động thành công bằng shortcut.

### Lưu trữ shortcut — `Tao_Shortcut_Desktop.ps1`

Tạo lại 3 shortcut, đường dẫn tính theo thư mục chứa script:
```powershell
powershell -ExecutionPolicy Bypass -File Tao_Shortcut_Desktop.ps1                 # ghi vào Desktop
powershell -ExecutionPolicy Bypass -File Tao_Shortcut_Desktop.ps1 -Destination X  # thử ở thư mục khác
```

| Shortcut | Target | Ghi chú |
|---|---|---|
| App CK | `Chay_App.bat` | Mở 1 server 8501 + Chrome AutoTrade nếu chưa chạy. **Không** trỏ `streamlit.exe` |
| Chrome AutoTrade VPS | `Chay_Chrome_AutoTrade.bat` | Icon Chrome; profile riêng, cổng 9333 |
| Kiem Tra Chrome AutoTrade | `Kiem_Tra_Chrome_AutoTrade.bat` | Chỉ đọc, chạy trước mỗi phiên |

Working directory của cả 3 = thư mục repo. Đã thử script ra thư mục tạm: thuộc
tính khớp shortcut hiện có. Chưa chạy script đè lên Desktop thật (mô tả trên
Desktop hiện còn ghi cổng 9222 cũ — chỉ là text, target đúng).

## 3. Công cụ chẩn đoán chỉ đọc đã dùng

Snapshot VPS (không tick, không send) — nội dung script:
```python
import sys; sys.path.insert(0, '.')
from vn_invest.auto_trader import load_config
from vn_invest import vps_broker
cfg = load_config()
with vps_broker.connect(cfg) as b:
    s = b.snapshot()          # positions / orders / conditions hôm nay, account đã băm
```
Journal runtime: `%LOCALAPPDATA%\VNInvest\runtime\autotrade_live_state.json`
(xem `cycles[-1].state`, `last_error`, `last_checked`). Log:
`data/autotrade_log.txt` (lọc bỏ "Chờ khoá" khi tìm sự kiện). Journal lệnh ảo:
`C:\AmibrokerData\vn30_ai_journal.csv`.

⚠️ Git Bash: `cat > file` không có heredoc sẽ treo chờ stdin — trông như
"kết nối CDP treo". Đã mất ~4 phút vì lỗi này hôm nay.

## 4. Git

Nhánh `phaisinh-v4-hardening`, **chưa push**, chưa merge master.
- `f17a7e7` — việc tồn 13/09 (Stop khôi phục, READ_ONLY, cổng 9333, `width="stretch"`).
  Trước đó chưa commit; tách riêng khỏi thay đổi 15/09.
- Commit kế tiếp — bản sửa đóng tay × bot, `Chay_App.bat` chọn Python,
  `Tao_Shortcut_Desktop.ps1`, lessons 38, handoff này.

Không commit (cố ý): `data/alert_last_run.json`, `data/macro_cache.json`,
`data/scores_cache.meta.json`, `data/vni_cache.csv` (cache, handoff cũ dặn giữ
nguyên); `tasks/autotrade-review-2026-09-07.md` (chỉ giữ local, chi tiết phiên thật).

## 5. Checklist tiếp nhận

1. `netstat -ano | findstr :850` — chỉ được 1 dòng 8501. Nhiều hơn → user tắt
   hết server (cần quyền admin; trợ lý bị chặn Stop-Process) rồi bấm "App CK" 1 lần.
2. `last_checked` trong journal runtime phải mới (< 1 phút) và `last_error` rỗng.
3. Nếu bot im lặng dù Telegram báo tín hiệu: xem `cycles[-1].state`. Khác CLOSED
   → snapshot chỉ đọc, so `orders` với `entry.before_ids`/`exits` để tìm lệnh ngoài bot.
4. Chưa có chu kỳ nào chạy trọn trên server 17724 tính tới lúc bàn giao — kiểm
   log sau lệnh đầu tiên phiên chiều.
5. Sửa code runtime khi app đang chạy phải RESTART server (lessons 36), tốt nhất
   giờ nghỉ trưa 11:30–13:00 hoặc sau phiên, lúc không có vị thế.
6. Giữ trần lỗ 1.000.000đ/ngày; không reset/sửa tay journal (memory
   no-fabricated-trading-state); không chạy pytest mà thiếu guard worker.

## 6. Lời nhắc cho agent tiếp theo

> Đọc HANDOFF-AI-VPS-2026-09-15.md rồi tasks/lessons.md mục 35–38. Nhánh
> phaisinh-v4-hardening. Bản sửa "đóng tay vị thế bot làm kẹt chu kỳ" đã chạy thật
> 15/09. Việc mở: đóng tay một phần, chu kỳ kẹt qua đêm (vps_overnight), xác nhận
> 1 server duy nhất. Kiểm tra chỉ đọc trước mọi thao tác; không tự tắt/mở server
> hay gửi lệnh thật khi user chưa xác nhận.
