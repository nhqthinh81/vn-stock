> Xác minh mới nhất: 186 kiểm thử đạt; chưa gửi/hủy Stop thật.

> Bổ sung mới: Stop Order **bảo vệ vị thế lệnh thường đã khớp** đã triển khai theo yêu cầu người dùng. Đọc `tasks/stop-order-protection-2026-09-07.md` và `vn_invest/vps_stop_guard.py` trước. Các đoạn bên dưới nói Stop Order chưa triển khai là trạng thái trước thay đổi này; chế độ Stop để mở vị thế theo breakout vẫn chưa có. Cần nạp lại backend; chưa có thử gửi/hủy Stop thật.

# Handoff — VPS SmartPro AutoTrade và Telegram

Cập nhật: 07/09/2026, múi giờ Asia/Saigon. Đây là bản bàn giao kỹ thuật, không phải xác nhận đã khớp lệnh thật. Kiểm tra lại trạng thái chạy khi tiếp nhận; PID/cổng bên dưới là lần kiểm tra gần nhất.

## 1. Mục tiêu và lựa chọn của người dùng

- Sửa autotrade để lệnh đến VPS, đối soát với phiên thật tại https://smartpro.vps.com.vn/v1/.
- Giữ giới hạn lỗ ngày 1.000.000 VND. Người dùng yêu cầu bỏ bước kiểm định hiệu quả chiến lược; vẫn chạy kiểm thử phần mềm. Không tự nâng/tắt giới hạn này.
- Đã nghiên cứu SL/TP và Stop Order, chọn SL/TP làm luồng chính. Stop Order CHƯA triển khai, không diễn đạt là đã hoàn thành cả hai chế độ.
- Telegram cần báo lệnh thật gồm tự động/thủ công, trạng thái khớp, vị thế và tổng lãi/lỗ VPS.
- Người dùng yêu cầu tạo file handoff này để agent khác tiếp tục nếu cần.

## 2. Workspace và môi trường

Repo: `G:/Other computers/My Computer/BHDN/DHKD/Linhtinh/antigravity2/vn-invest-app`

Workspace phụ: `C:/Users/Admin/Documents/ChatGPT/GiaodichCK`

Python dùng được: `C:/Users/Admin/AppData/Local/Programs/Python/Python313/python.exe`.
Lệnh `python` mặc định có thể trỏ Windows alias không hoạt động. Shell PowerShell.
Chrome AutoTrade dùng CDP `http://127.0.0.1:9222`; launcher `Chay_Chrome_AutoTrade.bat`.
Ứng dụng dùng `Chay_App.bat`.

Skill karpathy-guidelines đã áp dụng: sửa đúng phạm vi, nghiên cứu trước, kiểm thử có ý nghĩa. Không tự thêm plugin hoặc phụ thuộc mới. Không có commit/push/deploy trong công việc này.

## 3. Trạng thái vận hành được xác nhận lần cuối

- Người dùng nhiều lần đóng cửa sổ nhưng bản Streamlit cũ vẫn chạy nền. Health check chứng minh cả hai cổng từng còn phản hồi; điều này không chứng minh hai engine đều giao dịch.
- Theo yêu cầu trực tiếp của người dùng, đã dừng bản cũ: wrapper PID 34484 và Python PID 35204, cổng 8501. Phải dùng UAC vì Stop-Process ban đầu bị Access denied.
- Sau dừng, chỉ còn listener 8502/PID 4836. Health endpoint `http://127.0.0.1:8502/_stcore/health` trả 200/ok.
- Bản mới khởi động lúc 15:54 ngày 07/09, sau bản sửa đối soát SL/TP. Journal từng cập nhật liên tục, không báo lỗi đối soát.
- **Telegram được sửa sau đó, khoảng 16:02. Chưa xác nhận tiến trình hiện tại đã nạp module Telegram mới.** Không suy ra từ health check hoặc Ctrl+Shift+R rằng toàn bộ Python module đã reload. Bước tiếp theo có thể cần người dùng khởi động lại bản mới rồi mở Phái sinh.
- Trợ lý chưa chủ động chạy worker đặt lệnh, chưa gửi/hủy lệnh thật trong kiểm thử. Truy vấn phiên VPS thật chỉ đọc. Chưa gửi thử Telegram thật cho tính năng báo cáo mới.
- Script dừng bản cũ còn ở workspace phụ: `stop-old-streamlit.ps1` và `stop-old-streamlit-result.txt`. Không chạy lại theo PID cũ: phải xác minh danh tính tiến trình mới.

## 4. Kiến trúc và các file chính

### vn_invest/vps_broker.py

Adapter qua phiên Chrome đã đăng nhập; credentials ở trong trang, không ghi ra journal/log.

Truy vấn đã đối chiếu bằng dữ liệu server thật:
- `Web.Portfolio.AccountStatus`: PnL `vm`.
- `Web.Portfolio.PortfolioStatus2`: tài khoản, symbol, net, lastPrice, avg_remain, duedate.
- `Web.Order.FullAllOrder`: đọc tất cả trạng thái, phân trang.
- `list_condition_order`: lệnh điều kiện, liên kết gốc/con, phân trang.

Kiểm tra cùng tài khoản xuyên các phản hồi. Phiếu tài khoản phải khớp tài khoản mà giao diện danh mục native sử dụng. Lưu binding SHA-256, không lưu số tài khoản. Schema lỗi/thiếu số liệu không được chuyển thành 0.

Gửi lệnh trong code:
- Mở: một `co.sltp.order.new` chứa lệnh gốc và ngưỡng SL/TP.
- Đóng: dùng `orderNormalParseData` native để tạo payload MTL/checksum/refId.
- Hủy: chỉ ID lệnh thuộc chu kỳ được quản lý.
- ACK chỉ là nhận yêu cầu, không phải khớp. Timeout là UNKNOWN, không tự gửi lại.

### vn_invest/autotrade_runtime.py

Journal: `data/autotrade_live_state.json`, file tạm và lock cùng thư mục; đã gitignore.

- Thread lock và OS file lock; ghi intent UNKNOWN + fsync trước khi gửi yêu cầu.
- Một chu kỳ vị thế bot tại một thời điểm; dành lượt mở trước khi gửi; dedup signal ID qua restart.
- Đối soát entry/exits với broker IDs; chỉ đóng vị thế khớp khối lượng/direction bot đã xác nhận.
- So net với khớp mở trừ khớp thoát/SLTP; dữ liệu vị thế chưa đồng bộ không được gây đóng trùng hoặc giải phóng chu kỳ sớm.
- Hủy phần mở chưa khớp, hủy nhánh bảo vệ chờ, đợi lệnh con và xác nhận hủy, rồi mới gửi thoát phần còn lại.
- `protection_status`: chỉ đếm subtype SL/TP, kiểm tra chiều/giá/KL; cộng từng nhánh qua nhiều phần khớp. Bật TP thì cả hai nhánh phải đủ, không chỉ thấy một SL là kết luận đã bảo vệ đủ.
- Nhánh thiếu/sai từ phản hồi đầu được chờ đồng bộ 30 giây mặc định rồi yêu cầu thoát có đối soát. Lệnh đã biết biến mất thì giữ khóa, không suy đoán đã hủy.
- Lưu `protection_fills` để chặn khối lượng lệnh con lùi qua restart.
- Giới hạn lỗ dùng `vm`, khóa mở mới đến hết ngày, vẫn cho quản lý thoát. Không cộng thêm unrelizeVM hoặc PnL mô phỏng. Không khẳng định vm là lãi/lỗ sau mọi phí hay giới hạn bảo đảm tuyệt đối giá khớp.
- Cửa sổ mở 09:00–11:25, 13:00–14:25; MTL chỉ trong phiên liên tục. Hạn giữ 30 phút, giới hạn trước giờ nghỉ/cuối phiên.
- Worker thuộc tiến trình ứng dụng, không phải Windows service. Dừng ứng dụng/Chrome làm gián đoạn quản lý phía bot.

### vn_invest/auto_trader.py

API public submit/close chuyển luồng gửi thật sang runtime. Dry-run/điền sẵn vẫn dùng DOM. `_place_sltp` kiểu gọi sau một lệnh mở riêng bị vô hiệu hóa vì sẽ mở thêm vị thế. `check_session` ở chế độ thật đọc server, không bắt buộc phiếu DOM ở Lệnh thường. Một số helper legacy còn tồn tại; không tự refactor ngoài phạm vi.

### vn_invest/phaisinh_tab.py

- Sửa MACD: chọn `MACDh_12_26_9` theo tên; cột thứ ba trước đây là signal, không phải histogram.
- SHORT phải thực sự dưới VWAP và histogram giảm; giá trị bằng nhau không tạo SHORT. Hai nến cuối không hữu hạn trả WAIT.
- Giữ ngưỡng R=max(3×ATR14,1 điểm), SL cách giá vào R; TP=3R nếu bật.
- Signal ID gắn nến đóng/chiều; yêu cầu đóng mô phỏng chỉ nối đúng chu kỳ thật có ID tương ứng.
- UI hiện trạng thái VPS, PnL, khóa ngày, SL/TP và KL từng nhánh đã đối soát.
- Thông báo mô phỏng đã gắn nhãn MÔ PHỎNG. Báo cáo VPS mới là luồng riêng; không tuyên bố mọi thông báo Telegram cũ đã được thay thế.

### vn_invest/vps_telegram.py

Worker **chỉ đọc**, độc lập công tắc đặt lệnh. Quét 30 giây khi mở Phái sinh và có cấu hình Telegram.

- Gửi snapshot khi sổ lệnh/khối lượng khớp/vị thế thay đổi; PnL cập nhật mỗi 15 phút trong giờ giao dịch và một lần sau phiên. Lần đầu gửi trạng thái hiện tại.
- Báo toàn bộ sổ lệnh ngày hiện tại, giá/KL đặt và khớp, trạng thái, vị thế, PnL vm; điều kiện còn chờ được tách khỏi lệnh trong sổ thường.
- Tự động = ghép được broker ID trong journal cùng ngày hoặc lệnh con SLTP liên quan. Còn lại ghi **Thủ công/ngoài bot**, có thể gồm lệnh bot cũ/công cụ khác; không khẳng định đều do bấm tay.
- Fingerprint bỏ biến động PnL/giá từng tick để tránh spam. Tin dài chia phần, escape HTML.
- Trạng thái gửi nằm trong `telegram_vps` của journal. Dành lượt bằng lock ngắn trước gửi, không giữ lock giao dịch khi gọi Telegram.
- Lỗi gửi thử lại sau 5 phút. Telegram không có idempotency key; mất phản hồi sau nhận tin có thể gây lặp. Không bảo đảm exactly-once.
- Panel Telegram có công tắc `telegram_vps_reports` và nút Gửi trạng thái VPS ngay; tối thiểu 30 giây giữa lượt gửi.
- Không log URL chứa token. Lỗi đọc VPS không gửi dữ liệu cũ như dữ liệu mới.

## 5. Stop Order: phần chưa triển khai

Đã đọc payload công khai trên SmartPro: `co.stop.order.new` có relation GTEQ/LTEQ, triggerPrice, placedPrice/priceType; không có ngưỡng SLTP. `co.oco.order.new` là yêu cầu riêng.

Không dùng lệnh SLTP mở mới để gắn bảo vệ sau khi Stop Order đã khớp. Nếu người dùng muốn làm tiếp Stop Order, cần thiết kế đầy đủ vòng đời lệnh chờ, hết hạn tín hiệu, hủy/kích hoạt đua nhau, từng phần khớp và bảo vệ vị thế đã có. Chưa có chế độ Stop Order trong UI/runtime.

Nguồn:
- https://www.vps.com.vn/bai-viet/tong-hop-cac-lenh-dieu-kien-phai-sinh-tai-vps
- https://smartpro.vps.com.vn/v1/Templates/menuphai/js/menuphai.js?v=1.6.20260727
- https://smartpro.vps.com.vn/v1/Common/js/orderproc.js?v=1.6.20260616

## 6. Kiểm thử

Lần chạy cuối: **148 passed trong 39,15 giây**, gồm test Telegram mới. py_compile đạt, git diff --check không có lỗi (có cảnh báo chuyển LF/CRLF của vài file test).

Chạy từ repo bằng PowerShell:

```powershell
& 'C:\Users\Admin\AppData\Local\Programs\Python\Python313\python.exe' -m pytest tests/test_vps_telegram.py tests/test_vps_broker.py tests/test_autotrade_runtime.py tests/test_autotrade_protection.py tests/test_autotrade_review.py tests/test_autotrade_dom.py tests/test_auto_trader_core.py tests/test_auto_trader_close.py tests/test_auto_trader_loss_cap.py tests/test_auto_trader_sltp.py tests/test_auto_trader_session_alert.py tests/test_phaisinh_strong_signal.py tests/test_phaisinh_spam.py tests/test_phaisinh_shadow.py tests/test_phaisinh_lock.py tests/test_phaisinh_takeover.py -q --disable-warnings --tb=short
```

- Runtime dùng FakeBroker, temp journal, fake clock; Telegram dùng fake sender.
- Browser contract tests dùng headless Chrome chặn toàn bộ request bằng route, không đến VPS thật.
- Có dựng thử báo cáo bằng snapshot VPS chỉ đọc, chưa gửi tin thật.
- **Chưa chạy full AppTest/full suite** vì test render toàn ứng dụng chưa cô lập worker và các kết nối thật. Không chạy tùy tiện với cấu hình giao dịch đang bật. Bổ sung cách ly trước nếu cần test UI.

## 7. Rủi ro kỹ thuật cần biết khi tiếp tục

- API nội bộ VPS có thể thay schema; cần xác minh lại khi lỗi, không đoán endpoint mutation.
- Khi chưa có broker ID, matching lệnh mở dựa tập ID mới + mã/chiều/KL/giá. Nhiều ứng viên sẽ giữ khóa; không phải correlation hoàn hảo nếu có thao tác tay đồng thời.
- Sổ lệnh thường là ngày hiện tại. Chu kỳ chưa giải quyết từ ngày trước có thể cần đối chiếu thủ công. Không xóa/reset journal để ép bot gửi tiếp.
- Cảnh báo/timeout giữ an toàn có thể cần người dùng xử lý trên SmartPro; không suy ra thiếu dữ liệu = vị thế phẳng.
- Health 200 chỉ xác nhận server web; cần timestamp journal mới và trạng thái lỗi để xác nhận đối soát.
- Backend đang chạy có thể giữ module cũ dù trình duyệt đã refresh; đặc biệt phần Telegram vừa bổ sung.
- Báo cáo Telegram cần ứng dụng sống và tab Phái sinh đã khởi tạo worker. Không phải lịch chạy độc lập trong Codex hay Windows.

## 8. Git và tài liệu

Toàn bộ sửa đổi chưa commit. Đừng reset/ghi đè thay đổi người dùng.
Bốn file đã thay đổi từ trước, không thuộc bản sửa: `data/alert_last_run.json`, `data/macro_cache.json`, `data/scores_cache.meta.json`, `data/vni_cache.csv`.

Các file mới quan trọng: ba module runtime/broker/telegram; test runtime/broker/protection/telegram/review/DOM; `inspect_autotrade_session.py`; các tài liệu tasks.

Đọc theo thứ tự:
1. `tasks/autotrade-live-2026-09-07.md`
2. `tasks/autotrade-condition-orders-2026-09-07.md`
3. `tasks/vps-telegram-status-2026-09-07.md`
4. `tasks/autotrade-review-2026-09-07.md` chỉ là báo cáo ban đầu; các hạn chế journal/PnL/server trong đó đã được phần sau bổ sung.

## 9. Checklist tiếp nhận

1. Đọc file này và git status. Không yêu cầu người dùng mô tả lại toàn bộ.
2. Xác minh cổng/process hiện tại bằng truy vấn chỉ đọc. Không dùng PID cũ để dừng nếu chưa xác minh lại.
3. Xác nhận code Telegram mới được nạp. Nếu cần restart, phân biệt ứng dụng backend với tab Chrome SmartPro; không đăng xuất hoặc đóng Chrome đăng nhập của người dùng.
4. Kiểm tra timestamp journal và lỗi; không in account_ref, cookie, token, PIN hay nội dung cấu hình bí mật.
5. Khi được yêu cầu kiểm tra Telegram thực tế, dùng đường báo cáo chỉ đọc/sender đã có; không gọi tick giao dịch hoặc gửi lệnh thử để kiểm tra kết nối.
6. Nếu sửa tiếp, tái hiện bằng fake broker/sender trước; chạy bộ test liên quan rồi báo thay đổi và phần chưa xác nhận thật.

Công cụ shell từng gặp lỗi setup sandbox; require_escalated có justification đã hoạt động qua auto-review. Quyền này không đồng nghĩa Windows Administrator. Dừng tiến trình cũ cần UAC. Một lần ghi tài liệu chứa số liệu tài khoản thực bị auto-review từ chối; đã thay bằng tài liệu kỹ thuật không chứa số liệu thực. Giữ handoff này không chứa token, số tài khoản, ID lệnh hay PnL tài khoản thực tế.
