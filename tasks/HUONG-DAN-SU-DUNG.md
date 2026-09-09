# Hướng dẫn vận hành AutoTrade phái sinh

Viết 09/09/2026. Mọi điều dưới đây đã kiểm chứng trực tiếp trên máy này, không phải suy đoán.

---

## 1. Bốn mảnh phải cùng sống

Thiếu **bất kỳ** mảnh nào là hệ thống im lặng không giao dịch — và phần lớn trường hợp
nó **không báo lỗi gì cả**, chỉ đơn giản là không có gì xảy ra.

| # | Mảnh | Vai trò | Hỏng thì sao |
|---|---|---|---|
| 1 | **AmiBroker** xuất `C:\AmibrokerData\vn30f1m_1min.csv` | Nguồn nến 1 phút — toàn bộ tín hiệu sinh ra từ đây | Không có nến mới → chốt chặn dữ liệu cũ (>5 phút) chặn mọi lệnh |
| 2 | **Chrome AutoTrade** cổng 9222 + phiên SmartPro đã đăng nhập | Đường duy nhất để đặt lệnh | Không đặt được lệnh nào |
| 3 | **App Streamlit** | Chạy engine + worker đối soát | Không có gì chạy |
| 4 | **Tab "Phái Sinh" đang MỞ trong trình duyệt** | Worker `VPS-reconcile` chỉ sinh ra khi tab này render | App chạy nhưng **bot vẫn ngủ hoàn toàn** |

> ⚠️ Mảnh #4 là cái hay bị quên nhất. Mở app rồi để ở tab khác = **bot không hoạt động**.
> Code: `_live_panel_body()` mới gọi `ensure_worker()`; hàm này chỉ chạy khi tab Phái Sinh render.

---

## 2. Khởi động hằng ngày — đúng thứ tự

### Bước 1 — Chrome AutoTrade (làm TRƯỚC)

Chạy `Chay_Chrome_AutoTrade.bat` → cửa sổ Chrome riêng hiện ra → **đăng nhập SmartPro +
nhập PIN** → **để nguyên cửa sổ đó**, đừng đóng, đừng điều hướng đi trang khác.

- Profile riêng `%LOCALAPPDATA%\VNInvest\ChromeAutoTrade` — bạn đóng/mở Chrome chính
  hằng ngày **không ảnh hưởng** cửa sổ này.
- **Phiên VPS tự hết hạn sau 720 phút (12 tiếng)** kể từ lúc đăng nhập, không có
  cách gia hạn bằng hoạt động. Đăng nhập buổi sáng thì tối sẽ chết — bình thường,
  không phải lỗi.
- Kiểm nhanh đúng trang: chạy `Kiem_Tra_Chrome_AutoTrade.bat`. Nó đọc danh sách tab
  qua cổng 9222 và cảnh báo nếu không thấy `smartpro.vps.com.vn`.
  (Có tiền lệ: cửa sổ trôi sang **SmartOne** và nằm sai trang suốt 10 ngày mà
  không cảnh báo nào bắt được — xem `lessons.md` mục 31.)

### Bước 2 — App

Dùng shortcut **"App CK"** trên Desktop. Nó chạy:

```
C:\Users\Admin\AppData\Local\Programs\Python\Python313\Scripts\streamlit.exe
    run "g:\...\vn-invest-app\app.py"
```

> ❌ **KHÔNG dùng `Chay_App.bat`.** Đã kiểm chứng 09/09/2026: chạy xong nó **chết im**,
> không sinh tiến trình streamlit, không sinh cmd, không có pip. Shortcut mới là
> đường hoạt động.

Chờ **30–60 giây** (app nạp TensorFlow/Keras nên lâu). Xong khi
`http://127.0.0.1:8501` mở được.

### Bước 3 — Mở tab **Phái Sinh** trong trình duyệt

Bắt buộc. Xem mục 1 mảnh #4.

### Bước 4 — AmiBroker phải đang chạy và Explore phải cập nhật file

AFL chỉ ghi `vn30f1m_1min.csv` khi có lệnh **Explore** chạy.

> ❓ **Cần bạn xác nhận:** Task Scheduler `AmiBroker AutoExplore` hiện đang
> **Disabled**, `LastRunTime` dừng ở 28/08/2026 với `LastTaskResult = 0xFFFFFFFF`
> (treo, chưa bao giờ kết thúc — đúng sự cố ở `lessons.md` mục 30). Nhưng file
> **vẫn đang được cập nhật**. Nghĩa là hiện có cơ chế khác đang chạy Explore —
> bạn làm tay, hay có gì khác? Cần biết để đưa vào checklist.

---

## 3. Kiểm tra trước phiên (2 phút, nên làm mỗi sáng)

```powershell
& 'C:\Users\Admin\AppData\Local\Programs\Python\Python313\python.exe' `
  'C:\Users\Admin\AppData\Local\Temp\claude\...\scratchpad\ro_snapshot.py'
```

Công cụ **chỉ đọc** — không tick, không gửi/hủy lệnh gì. In ra:

- `session_alive: True | OK` → phiên SmartPro còn sống
- Vị thế đang có, sổ lệnh trong ngày, lệnh điều kiện
- **Phán quyết SL**: `VERDICT: OK` hoặc `VERDICT: NGUY HIEM - KHONG du SL tren san`

Ngoài ra kiểm nhanh **độ mới của nến** — đây là bẫy đã có tiền lệ:

```powershell
Get-Content C:\AmibrokerData\vn30f1m_1min.csv -Tail 1
```

⚠️ **Đừng tin ngày sửa file (mtime).** AFL vẫn ghi đè file đều đặn kể cả khi
AmiBroker đã mất kết nối nguồn dữ liệu — file "mới 2 phút" mà nến cuối bên trong
lại là của **hôm qua**. Phải nhìn **nến cuối trong nội dung file**.

| Triệu chứng | Nguyên nhân | Xử lý |
|---|---|---|
| mtime cũ + nến cũ | AFL chưa chạy Explore | Chạy lại Explore trong AmiBroker |
| **mtime mới + nến cũ** | Nguồn dữ liệu AmiBroker đã dừng | Kiểm tra kết nối / đăng nhập data feed |

---

## 4. Trong phiên — thế nào là bình thường

- Giờ giao dịch: **09:00–11:30** và **13:00–14:30**.
  Cửa sổ **mở lệnh mới** hẹp hơn: **09:00–11:25** và **13:00–14:25**.
- Panel AutoTrade trong tab Phái Sinh hiện: trạng thái VPS, PnL, số lệnh đã đặt/trần,
  SL/TP và khối lượng từng nhánh đã đối soát.
- Nhật ký lỗi: `data/autotrade_log.txt` (chỉ ghi khi lỗi **đổi**, nên im lặng kéo dài
  có thể là "vẫn đang lỗi y như cũ", không phải "đang ổn").
- Sổ trạng thái thật: `%LOCALAPPDATA%\VNInvest\runtime\autotrade_live_state.json`
  (**không** còn ở `data/` — đã chuyển khỏi ổ Google Drive vì `os.replace` không
  nguyên tử ở đó, xem `lessons.md` mục 33).

Các thông báo **bình thường, không phải lỗi**:

- `VPS đang có vị thế thật; không mở chồng` — bạn đang giữ lệnh tay, bot nhường.
- `Không có vị thế thật do bot quản lý khớp yêu cầu đóng` — bot từ chối đóng thứ
  nó không sở hữu. Đúng.
- `Tín hiệu đã cũ hoặc chưa hiệu lực; không gửi bù` — nến quá 120 giây. Đúng, bỏ lỡ
  còn hơn vào lệnh theo giá cũ.

---

## 5. Sự cố thường gặp

| Hiện tượng | Nguyên nhân đã gặp | Xử lý |
|---|---|---|
| App chạy nhưng bot không làm gì | Chưa mở tab Phái Sinh | Mở tab đó |
| `connect_over_cdp: Timeout 5000ms` lặp lại | Chrome CDP bắt tay chậm; budget 5s quá chặt | Đóng Chrome AutoTrade, chạy lại `Chay_Chrome_AutoTrade.bat`, đăng nhập lại |
| `RuntimeError: AutoTrade đang xử lý/đối soát một lệnh khác` | Một lời gọi Playwright treo, giữ khoá → chặn cả mở lẫn đóng lệnh | **Restart app** (Chrome giữ nguyên, phiên không mất) |
| Vị thế mở mà không có SL trên sàn | Đã sửa 09/09 (`lessons.md` 34). Nếu tái diễn: | **Đặt SL tay trên SmartPro ngay**, rồi báo để điều tra |
| `JSONDecodeError: Extra data` khi đọc journal | Journal nằm trên ổ sync (đã sửa) | Kiểm journal có đúng ở `%LOCALAPPDATA%\VNInvest\runtime\` không |
| Phiên VPS chết giữa phiên | Quá 720 phút | Đăng nhập lại trong cửa sổ Chrome AutoTrade |

**Cách restart app an toàn** (Chrome và phiên đăng nhập KHÔNG bị ảnh hưởng):

```powershell
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
  Where-Object { $_.CommandLine -like '*streamlit run*' } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
```
rồi bấm lại shortcut "App CK".

---

## 6. Tuyệt đối không

1. **Không chạy `pytest` khi Chrome AutoTrade đang mở và đang trong giờ giao dịch.**
   Đã có chốt chặn (`ensure_worker` bỏ qua khi thấy `PYTEST_CURRENT_TEST`), nhưng
   nguyên tắc vẫn giữ. Xem `lessons.md` mục 32.
2. **Không sửa tay** `autotrade_live_state.json`. Sổ này là nguồn sự thật cho đối soát;
   chế state giả có thể khiến bot đặt lệnh trùng trên tiền thật.
3. **Không nâng/tắt trần lỗ 1.000.000đ** mà không cân nhắc kỹ. Lưu ý: trần này chỉ
   **chặn mở lệnh mới**, nó **không** ép đóng vị thế đang có.
4. **Không đóng cửa sổ Chrome AutoTrade** khi đang có vị thế bot quản lý.
5. **Không tin "app còn sống" = "bot đang chạy"**. Health `:8501` chỉ chứng minh web
   server sống. Bằng chứng bot thật sự đối soát là `last_checked` trong journal
   nhảy liên tục.

---

## 7. Việc còn tồn (tính đến 09/09/2026)

Xem chi tiết ở `HANDOFF-autotrade-vps.md` §13.4. Tóm tắt cái quan trọng nhất:

> **Đường THOÁT lệnh chưa một lần thành công.** Cả 08/09 và 09/09, lệnh thoát do bot
> gửi đều bị VPS từ chối. Sau bản vá 09/09 bot không còn tự gỡ SL của mình nữa — nên
> vị thế sẽ có SL sàn bảo vệ kể cả khi đường thoát vẫn hỏng. Nhưng **"bot tự đóng
> được lệnh" vẫn chưa được chứng minh.** Trong lúc chờ, nên có người canh phiên.
