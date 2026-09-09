# Handoff — VPS SmartPro AutoTrade và Telegram

Cập nhật: 08/09/2026, múi giờ Asia/Saigon. Thay thế bản handoff ngày 07/09. Đây là bản bàn giao kỹ thuật, không phải xác nhận đã khớp lệnh thật. Kiểm tra lại trạng thái chạy khi tiếp nhận; PID/cổng bên dưới là lần kiểm tra gần nhất.

## 1. Mục tiêu và lựa chọn của người dùng

- Sửa autotrade để lệnh đến VPS, đối soát với phiên thật tại https://smartpro.vps.com.vn/v1/.
- Giữ giới hạn lỗ ngày 1.000.000 VND. Người dùng yêu cầu bỏ bước kiểm định hiệu quả chiến lược; vẫn chạy kiểm thử phần mềm. Không tự nâng/tắt giới hạn này.
- SL/TP là luồng mở vị thế chính. Stop Order bảo vệ vị thế lệnh thường đã khớp ĐÃ triển khai. Người dùng xác định rõ Stop dùng để bảo vệ vị thế đang có; chế độ breakout mở vị thế mới không thuộc phần đã triển khai.
- Telegram cần báo lệnh thật gồm tự động/thủ công, trạng thái khớp, vị thế và tổng lãi/lỗ VPS.
- Người dùng yêu cầu tạo file handoff này để agent khác tiếp tục nếu cần.

## 2. Workspace và môi trường

Repo: `G:/Other computers/My Computer/BHDN/DHKD/Linhtinh/antigravity2/vn-invest-app`

Workspace phụ: `C:/Users/Admin/Documents/ChatGPT/GiaodichCK`

Python dùng được: `C:/Users/Admin/AppData/Local/Programs/Python/Python313/python.exe`.
Lệnh `python` mặc định có thể trỏ Windows alias không hoạt động. Shell PowerShell.
Chrome AutoTrade dùng CDP `http://127.0.0.1:9222`; launcher `Chay_Chrome_AutoTrade.bat`.
Ứng dụng dùng `Chay_App.bat`.

Skill karpathy-guidelines đã áp dụng: sửa đúng phạm vi, nghiên cứu trước, kiểm thử có ý nghĩa. Không tự thêm plugin hoặc phụ thuộc mới. Đã commit và push thành công lên GitHub; chưa merge vào master và chưa triển khai lên dịch vụ hosting.

## 3. Trạng thái vận hành được xác nhận lần cuối

- Người dùng nhiều lần đóng cửa sổ nhưng bản Streamlit cũ vẫn chạy nền. Health check chứng minh cả hai cổng từng còn phản hồi; điều này không chứng minh hai engine đều giao dịch.
- Theo yêu cầu trực tiếp của người dùng, đã dừng bản cũ: wrapper PID 34484 và Python PID 35204, cổng 8501. Phải dùng UAC vì Stop-Process ban đầu bị Access denied.
- Sau dừng, chỉ còn listener 8502/PID 4836. Health endpoint `http://127.0.0.1:8502/_stcore/health` trả 200/ok.
- Bản mới khởi động lúc 15:54 ngày 07/09, sau bản sửa đối soát SL/TP. Journal từng cập nhật liên tục, không báo lỗi đối soát.
- **Telegram và Stop bảo vệ được sửa sau lần khởi động được quan sát. Chưa xác nhận backend hiện tại đã nạp đầy đủ mã cuối của cả hai module.** Không suy ra từ health check hoặc Ctrl+Shift+R rằng toàn bộ Python module đã reload. Bước tiếp theo có thể cần người dùng khởi động lại bản mới rồi mở Phái sinh.
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

## 5. Stop Order bảo vệ — đã triển khai

Module `vn_invest/vps_stop_guard.py` được gọi từ tick runtime dưới cùng khóa với giao dịch. Dùng journal `normal_stops`; không tạo worker giao dịch cạnh tranh.

- Long đã khớp → Stop Short/LTEQ; Short đã khớp → Stop Long/GTEQ. KL bằng net thực đã xác minh, ngưỡng từ giá vốn ± max(3×ATR14,1 điểm).
- Payload `co.stop.order.new` dùng MTL, không có placedPrice; hủy qua `co.stop.order.delete`. Schema RELATION/PRICE_TYPE đã kiểm tra bằng truy vấn thật chỉ đọc.
- UI chuyển ATR từ nến đóng sang cache module qua `observe_closed_bars`. Dữ liệu mới trong 120 giây, đúng symbol, hữu hạn và gần giá VPS mới được tạo Stop mới. ATR cũ không ngăn đối soát Stop đã ghi nhận.
- Chỉ bảo vệ mã cấu hình khi sổ khớp có dấu trong ngày khớp với net VPS; giá vốn/hạn hợp đồng hợp lệ. Đọc lại snapshot ngay trước gửi. Vị thế qua đêm chưa giải thích được bằng sổ khớp ngày thì chưa tự bảo vệ.
- Không tạo Stop nếu bot đang quản lý bằng SLTP hoặc mã đã có lệnh điều kiện khác còn hiệu lực. Không suy diễn rằng mọi điều kiện đó đều bảo vệ đủ.
- Ghi UNKNOWN trước gửi, match duy nhất ID điều kiện mới với mã/chiều/KL/ngưỡng/relation/MTL. Timeout không gửi lại. Stop chưa giải quyết chặn bot mở chu kỳ mới.
- Net/giá vốn đổi, xuất hiện bảo vệ khác hoặc lệnh ngược chiều chờ: hủy Stop bot sở hữu rồi đợi xác nhận trước khi xét lại. Không hủy lệnh do người dùng đặt.
- Kích hoạt thì theo dõi lệnh con, không gửi thêm lệnh đóng thứ hai. Khớp một phần, mất phản hồi, đua hủy/kích hoạt, dữ liệu khớp lùi hoặc biến mất được giữ để đối soát.
- UI có công tắc `normal_stop_enabled` mặc định true, hoạt động khi gửi thật bật. Tắt riêng công tắc không tự hủy Stop còn hiệu lực; tắt autotrade/dry-run dừng hành động worker như trước.
- Telegram nhận diện lệnh con Stop do bot tạo là Tự động; nguồn lệnh thường gốc vẫn theo journal thực tế.

**Giới hạn:** Stop không có ràng buộc reduce-only trong payload đã xác minh. Đóng tay đúng lúc Stop kích hoạt vẫn có thể gây đảo vị thế trước vòng đối soát kế tiếp. Code không tự gửi lệnh đảo lại để sửa. MTL tạo trong phiên liên tục; nếu kích hoạt vào thời điểm VPS không chấp nhận loại lệnh, có thể bị từ chối. Không tuyên bố loại bỏ mọi rủi ro race.

Không phải Stop vào lệnh breakout, không thêm TP vào luồng Stop đơn và không phải trailing stop. Đọc chi tiết `tasks/stop-order-protection-2026-09-07.md`.

Nguồn xác minh:
- https://www.vps.com.vn/bai-viet/tong-hop-cac-lenh-dieu-kien-phai-sinh-tai-vps
- https://smartpro.vps.com.vn/v1/Templates/menuphai/js/menuphai.js?v=1.6.20260727
- https://smartpro.vps.com.vn/v1/Common/js/orderproc.js?v=1.6.20260616

## 6. Kiểm thử

Lần chạy cuối: **186 passed trong 42,21 giây**, gồm Telegram và Stop bảo vệ. Đây là kết quả lần chạy cuối ngày 07/09, không phải một lần chạy lại ngày 08/09. py_compile đạt, git diff --check không có lỗi (có cảnh báo chuyển LF/CRLF của vài file test).

Chạy từ repo bằng PowerShell:

```powershell
& 'C:\Users\Admin\AppData\Local\Programs\Python\Python313\python.exe' -m pytest tests/test_vps_stop_guard.py tests/test_vps_telegram.py tests/test_vps_broker.py tests/test_autotrade_runtime.py tests/test_autotrade_protection.py tests/test_autotrade_review.py tests/test_autotrade_dom.py tests/test_auto_trader_core.py tests/test_auto_trader_close.py tests/test_auto_trader_loss_cap.py tests/test_auto_trader_sltp.py tests/test_auto_trader_session_alert.py tests/test_phaisinh_strong_signal.py tests/test_phaisinh_spam.py tests/test_phaisinh_shadow.py tests/test_phaisinh_lock.py tests/test_phaisinh_takeover.py -q --disable-warnings --tb=short
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

Code, tests và tài liệu kỹ thuật đã được commit/push:
- Repo: https://github.com/nhqthinh81/vn-stock
- Nhánh: `phaisinh-v4-hardening` (không phải `master` đang hiển thị mặc định trên web).
- Commit: `422195dfa301f596803657d51adb2261001395ef`.
- Đã push thành công và xác minh `ls-remote` trùng HEAD. Không force push, không merge vào master.
- Kiểm tra local ngày 08/09 vẫn đang ở commit trên. Bản cập nhật handoff ngày 08/09 này được xuất sau commit đó, chưa push bản handoff cập nhật.
- Đừng reset/ghi đè thay đổi người dùng.
Bốn file đã thay đổi từ trước, không thuộc bản sửa: `data/alert_last_run.json`, `data/macro_cache.json`, `data/scores_cache.meta.json`, `data/vni_cache.csv`.

Các file mới quan trọng: bốn module runtime/broker/telegram/stop_guard; test runtime/broker/protection/telegram/review/DOM; `inspect_autotrade_session.py`; các tài liệu tasks.

Đọc theo thứ tự:
1. `tasks/autotrade-live-2026-09-07.md`
2. `tasks/autotrade-condition-orders-2026-09-07.md`
3. `tasks/stop-order-protection-2026-09-07.md`
4. `tasks/vps-telegram-status-2026-09-07.md`
5. `tasks/autotrade-review-2026-09-07.md` là file chỉ giữ local, không push vì có chi tiết phiên thực; đây chỉ là báo cáo ban đầu; các hạn chế journal/PnL/server trong đó đã được phần sau bổ sung.

### Xác thực Git và push

Người dùng đã xác nhận chính xác remote trên. Đăng nhập web thành công không đồng nghĩa Git CLI có token hợp lệ. Trước đó GitHub CLI báo token keyring không hợp lệ và có helper GitHub riêng ghi đè helper mặc định. Đã đăng nhập lại Git Credential Manager bằng browser rồi push thành công với helper chỉ áp dụng cho một lệnh:

```powershell
git -c credential.https://github.com.helper= -c credential.https://github.com.helper=manager push origin HEAD:phaisinh-v4-hardening
```

Không sửa cấu hình helper toàn máy, không đọc/in token. Nếu gặp lỗi mới, kiểm tra lại; không mặc định thông tin xác thực hiện còn hiệu lực. Repo ở ổ G là repo sản phẩm; thư mục workspace phụ ở ổ C là repo khác, lúc kiểm tra chưa có commit/remote.

## 9. Checklist tiếp nhận

1. Đọc file này và git status. Không yêu cầu người dùng mô tả lại toàn bộ.
2. Xác minh cổng/process hiện tại bằng truy vấn chỉ đọc. Không dùng PID cũ để dừng nếu chưa xác minh lại.
3. Xác nhận code Telegram và Stop bảo vệ mới được nạp. Nếu cần restart, phân biệt ứng dụng backend với tab Chrome SmartPro; không đăng xuất hoặc đóng Chrome đăng nhập của người dùng.
4. Kiểm tra timestamp journal và lỗi; không in account_ref, cookie, token, PIN hay nội dung cấu hình bí mật.
5. Xác nhận UI hiện công tắc Stop bảo vệ và báo cáo Telegram. Đối soát bằng truy vấn chỉ đọc trước. Khi được yêu cầu kiểm tra Telegram thực tế, dùng đường báo cáo chỉ đọc/sender đã có; không gọi tick mặc định hoặc gửi lệnh thử để kiểm tra kết nối; `tick()` có thể đặt/hủy lệnh khi cấu hình gửi thật bật. `tick(allow_actions=False)` không gửi lệnh nhưng vẫn ghi journal; ưu tiên Broker.snapshot() cho kiểm tra thuần đọc.
6. Nếu sửa tiếp, tái hiện bằng fake broker/sender trước; chạy bộ test liên quan rồi báo thay đổi và phần chưa xác nhận thật.

Công cụ shell từng gặp lỗi setup sandbox; require_escalated có justification đã hoạt động qua auto-review. Quyền này không đồng nghĩa Windows Administrator. Dừng tiến trình cũ cần UAC. Một lần ghi tài liệu chứa số liệu tài khoản thực bị auto-review từ chối; đã thay bằng tài liệu kỹ thuật không chứa số liệu thực. Giữ handoff này không chứa token, số tài khoản, ID lệnh hay PnL tài khoản thực tế.


## 10. Lời nhắc có thể đưa cho agent tiếp theo

> Đọc toàn bộ HANDOFF-autotrade-vps.md trước. Tiếp tục dự án vn-invest-app ở nhánh phaisinh-v4-hardening, commit nền 422195d. SLTP, Stop bảo vệ vị thế thường và báo cáo Telegram đã có code, 186 test đã đạt; chưa xác nhận backend đang chạy mã cuối hay end-to-end giao dịch thật. Trước tiên kiểm tra Git/workspace, phiên bản backend và đối soát chỉ đọc. Giữ trần lỗ 1.000.000 đồng, không tự quay lại chiến lược Stop breakout, không reset journal và không động vào cache người dùng. Báo rõ điều đã chứng minh và điều chưa xác nhận.


## 11. Bổ sung 2026-09-08: sửa báo cáo Telegram không có lịch sử gửi

- Nền Git hiện tại: 7476ae1, sau 8fc0dce của Claude Code và 422195d. Hai commit Claude đã được đối chiếu; giữ nguyên. Bản sửa Telegram trong mục này chưa commit/push.
- Bằng chứng: kiểm tra getMe/getChat trả HTTP 200 và ok=true, không ghi lại thông tin xác thực. Một lần đọc VPS bị timeout; lần kiểm tra kế tiếp đọc snapshot thành công. Phép thử báo cáo tiếp theo bị PermissionError; kiểm tra riêng xác định lỗi tại msvcrt.locking trong locked_state, do khóa đang bận.
- app.py nạp .env theo thư mục ứng dụng, khởi động worker báo cáo chỉ đọc ở entry point, không phụ thuộc mở tab Phái sinh. Không khởi động worker giao dịch từ hook mới này.
- Worker không còn bỏ qua âm thầm khi thiếu cấu hình; lỗi load_config được bắt trong poll. Worker tiếp tục kiểm tra sau lỗi và khi công tắc báo cáo đang tắt (không đọc VPS/gửi lúc tắt), để tự nhận lại khi bật.
- Chỉ Telegram dùng _report_state chờ lấy khóa journal tối đa 30 giây. Không sửa cơ chế khóa runtime; chỉ thử lại bước lấy khóa, không chạy lại phần thân hay gửi lại khi ghi lỗi. Đã kiểm chứng lấy khóa thực tế thành công với helper mới.
- Chẩn đoán lưu riêng tại data/vps_telegram_health.json (gitignored): thời điểm kiểm tra/gửi thành công, bước xử lý, loại lỗi, PID. Không chứa token, chat ID, dữ liệu lệnh hoặc PnL. Panel Telegram đọc trạng thái bền vững này. Timestamp của phép thử một lần không chứng minh worker backend còn sống; phải kiểm tra nó tiếp tục tăng sau khi nạp ứng dụng.
- Kiểm thử: 192 passed bộ rộng trước bổ sung chờ khóa; sau bổ sung, 22 passed Telegram và 93 passed bộ Telegram + runtime + Stop. git diff --check đạt. Kiểm thử dùng fake broker/sender, không phát lệnh giao dịch.
- Chưa xác nhận gửi báo cáo thật thành công. Phép thử sau sửa bị auto-review chặn vì chưa có xác nhận rõ nơi nhận Telegram cấu hình sẵn cho dữ liệu giao dịch. Không thử lại qua đường khác; cần người dùng xác nhận nơi nhận trước khi gửi thử. Các phép thử trước đó thất bại trước bước gửi.
- Tiếp tục: xác nhận backend nạp mã mới, quan sát heartbeat; khi người dùng xác nhận nơi nhận, thử poll báo cáo và kiểm tra SENT. Không reset journal, không gọi tick mặc định, không bật/tắt giao dịch để thử báo cáo. Cache và tài liệu Claude ngoài phạm vi được giữ nguyên.

### Xác nhận gửi thử 2026-09-08

Người dùng đã cho phép gửi thử tới nơi nhận Telegram cấu hình sẵn. Chạy poll(force=True) thành công lúc 10:06:21 giờ Việt Nam: kết quả SENT, error rỗng, Telegram xác nhận nhận báo cáo. Không đặt/hủy lệnh. Đây là phép thử một lần; chưa chứng minh worker trong backend đang chạy định kỳ với mã mới.


## 12. Sửa tuổi tín hiệu nến 1 phút — 2026-09-08

- Log mở lệnh bị từ chối bởi bộ kiểm tra tuổi tín hiệu, trước bước gửi VPS. UI truyền closed_ts (nhãn đầu nến 1 phút), khiến cả thời gian hình thành nến bị tính vào tuổi tín hiệu.
- phaisinh_tab._signal_available_at lấy đầu nến + 1 phút làm mốc tín hiệu khả dụng. UI truyền mốc đó qua signal_at. Không dùng giờ hiện tại, thời gian file hay nến kế tiếp để làm mới dữ liệu cũ. ID tín hiệu vẫn dựa vào nhãn nến cũ, bảo toàn chống gửi trùng qua restart.
- Giữ TTL mặc định 120 giây, kiểm tra lại sau đọc broker, các giới hạn phiên/giá/rủi ro và journal. Runtime ghi tuổi/giới hạn vào thông báo chặn và lưu signal_at cho cycle mới.
- Ví dụ tổng quát: nến 09:32 đóng 09:33, xử lý 09:34:07 có tuổi 67 giây. Sau 09:35:00 thì quá TTL. Nến đã bỏ lỡ không được gửi bù.
- Kiểm thử mới tests/test_autotrade_signal_time.py: ba trường hợp 127 giây từ đầu nến, biên TTL, hết hạn khi chờ broker, nến phiên cũ/tương lai, chống gửi trùng và đường gọi từ UI. 49 kiểm thử mục tiêu đã đạt; kết quả bộ rộng ghi tiếp bên dưới.
- Chưa xác minh backend đang chạy đã nạp mã mới; chưa thử phát lệnh thật. Bản sửa này chỉ xử lý mốc tuổi tín hiệu; tranh chấp khóa runtime và việc vị thế mô phỏng vẫn tồn tại khi lệnh thật bị từ chối là các vấn đề riêng, không được coi là đã xử lý trong bản sửa này. Giữ nguyên thay đổi Claude và cache.

Kết quả hồi quy cuối: 207 passed (49.73s), git diff --check đạt. Chưa commit/push bản sửa.


## 13. Test render tab lái GIAO DỊCH THẬT + xử lý vị thế qua đêm kẹt — 2026-09-09

### 13.1 Bug: `pytest` phát lệnh thật, phá journal thật (đã sửa)

- 4 test `tests/test_vps_overnight.py` fail flaky với `JSONDecodeError: Extra data` khi
  đọc `data/autotrade_live_state.json`. Chạy riêng file: 28/28 đạt; chạy full suite: 4 fail.
- Nguyên nhân gốc: `tests/test_phaisinh_render.py` render THẬT `render_phaisinh_tab()` mà
  không cô lập. `_live_panel_body()` gọi `ensure_worker()` khi `data/autotrade_config.json`
  thật đang `enabled=true, dry_run=false` (Phase 28h) → spawn daemon `VPS-reconcile` sống
  QUA test đó, lặp `tick()` mỗi 5s suốt phiên pytest: nối Chrome/VPS thật + ghi
  `rt.STATE_PATH` (biến module-global) trong lúc các test qua đêm monkeypatch biến này
  sang tmp_path → `os.replace` chéo ổ đĩa (`WinError 17`), `.tmp` mồ côi, hai luồng cùng
  `open('w')` một `.tmp` → nội dung ngắn + đuôi bản dài = "Extra data". Bằng chứng khớp
  trong `data/autotrade_log.txt` (timestamp trùng lúc chạy pytest) và
  `data/autotrade_live_state.tmp` chứa `account-hash` (giá trị `FakeBroker`).
- Hệ quả: chỉ cần `pytest` trên máy này khi Chrome AutoTrade mở là worker thật nối phiên
  VPS live và ghi đè journal giao dịch thật. State thật trước phiên đã mất qua các lần
  chạy pytest (cả của Codex).
- Sửa:
  - `test_phaisinh_render.py`: ép `at.load_config` trả `enabled=False, dry_run=True,
    telegram_vps_reports=False`, trỏ `rt.STATE_PATH` sang tmp, stub `rt.ensure_worker` và
    `tg.ensure_worker`. Xác minh: render 0 exception, không rò thread `VPS-reconcile`;
    full suite **287 passed × 3 lần**, mtime file thật không đổi.
  - `autotrade_runtime.ensure_worker()`: thêm chốt `if os.environ.get('PYTEST_CURRENT_TEST'): return`
    (phòng vệ lớp 2 — không test nào gọi `rt.ensure_worker` trực tiếp; production
    `streamlit run` không có biến này).
  - `tasks/lessons.md` mục 32.
- Chưa commit.

### 13.2 Vị thế LONG qua đêm kẹt — đã đóng tay

- 08/09 10:39 bot mở LONG 1 HĐ 41I1G9000 (giá vào ~1964–1966). Yêu cầu đóng 11:09 bị
  REJECTED không có mã phản hồi → vị thế còn nguyên net=1 qua đêm. Journal quản lý cycle
  đã mất (mục 13.1) nên bot KHÔNG tự tiếp quản: mỗi tick thấy net=1 không giải thích được
  bằng sổ khớp trong ngày → `note('Sổ khớp trong ngày chưa khớp vị thế')`, không hành động
  (đúng, an toàn).
- Đối soát chỉ đọc 09/09 08:11: `avg=1961.0` trên VPS là GIÁ THANH TOÁN 08/09 (nến ATC
  14:45 = 1961.0), không phải giá vào gốc. SL gốc không truy được từ file; dựng lại từ nến
  1 phút cho ~1959.7–1961.3 (sai số ±1.5đ, chồng thêm bất định giá vào).
- Cân nhắc dựng tay lại `overnight_checkpoint` để module `vps_overnight` phục hồi Stop —
  BỎ, vì phải đoán SL cho lệnh sàn tiền thật. Người dùng đóng tay lúc ~09:15–09:20, vị
  thế về phẳng (net=0).
- `data/autotrade_live_state.json` khi đó `{"version":1,"account_ref":null,"days":{},
  "cycles":[],"last_error":""}` — đúng với vị thế phẳng; worker của app tick sạch, không
  hành động. `account_ref` thật đọc được qua đối soát chỉ đọc khi cần; KHÔNG ghi vào tài
  liệu (hash SHA-256 của một số tài khoản ngắn là brute-force được).
- Công cụ đọc: `scratchpad/ro_snapshot.py` (chỉ `Broker.snapshot()`, không tick/gửi/hủy).
  `.claude/settings.local.json` có 1 allow-rule Bash hẹp cho đúng script này (gitignored).
- Còn lại: `data/autotrade_live_state.tmp` mồ côi (rác từ đua luồng pytest, vô hại — chờ
  phép xoá).

### 13.3 Journal chuyển khỏi ổ Google Drive (đã sửa, chưa commit)

- `data/autotrade_live_state.json` hỏng "Extra data" tái diễn (cả trong pytest lẫn app
  production): repo nằm trên **ổ ảo Google Drive** (`G:\Other computers\My Computer\...`
  = namespace backup "Computers"; `Get-Volume G:` không ra kết quả, `net use` trống).
  `save_state()` dùng `os.replace(tmp, STATE_PATH)` — KHÔNG nguyên tử trên Drive File
  Stream: driver sync giữ file đích / ghi lại bản cache → bản mới ngắn + đuôi bản cũ dài
  = JSON hỏng.
- Sửa: `autotrade_runtime.runtime_dir()` → `%LOCALAPPDATA%\VNInvest\runtime\` (override
  bằng `VNINVEST_RUNTIME_DIR`; fallback `data/` khi không phải Windows).
  `STATE_PATH`/`.tmp`/`.lock`/legacy và `vps_telegram.HEALTH_PATH` chuyển sang đó.
  `load_state()` migrate 1 lần từ file cũ nếu còn parse được (không xoá bản cũ). Chốt
  migrate chỉ khi `STATE_PATH == _DEFAULT_STATE_PATH` → test monkeypatch tmp_path không
  dính. Giữ hành vi raise-khi-corrupt (không tự "repair" — bản JSON đầu tiên trong file
  hỏng không chắc là mới nhất).
- Còn rủi ro thấp CHƯA đụng: `auto_trader.save_config()` cũng `os.replace` trên
  `data/autotrade_config.json` (user chỉnh tay ở đó nên không chuyển) — chỉ hỏng nếu ghi
  trùng lúc, mà config ghi rất hiếm.
- Kiểm thử: 287 passed × nhiều lần sau mỗi thay đổi. `runtime_dir()` xác minh trỏ
  `C:\Users\Admin\AppData\Local\VNInvest\runtime`; migrate copy state hợp lệ sang; app
  restart 09:54 đã nạp mã mới.
- Chuỗi vận hành đã làm trong phiên (vị thế phái sinh khi đó phẳng / do user tự quản trên
  SmartPro): kill + chạy lại `Chay_Chrome_AutoTrade.bat` (profile bền giữ phiên đăng
  nhập); kill PID 3800 (worker `VPS-reconcile` treo trong Playwright, giữ `_THREAD_LOCK`
  → `submit_signal`/`request_close` fail RuntimeError) và chạy lại Streamlit bằng
  `python.exe -m streamlit run app.py --server.port 8501 --server.headless true`
  (`Chay_App.bat` fail vì alias `python` hỏng). Lần restart cuối lúc 09:54 = PID 21908,
  có bản vá đường dẫn. **User cần F5 trình duyệt + mở tab Phái Sinh** để worker
  `VPS-reconcile` spawn lại (nó chỉ chạy khi tab đó render).
- `lessons.md` mục 33. `data/autotrade_live_state.json` cũ trên ổ sync giờ là file chết —
  xoá được sau khi xác nhận app mới ghi vào đường dẫn local.
