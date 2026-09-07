# Stop Order bảo vệ lệnh thường — 07/09/2026

## Phạm vi đã triển khai

Stop Order dùng để cắt lỗ cho vị thế đã khớp, không dùng làm tín hiệu phá đỉnh/đáy mở vị thế mới. Mã theo `symbol_code` cấu hình hiện tại. Long được bảo vệ bằng Stop Short/LTEQ; Short bằng Stop Long/GTEQ, khối lượng bằng net đã xác minh. Ngưỡng = giá vốn VPS ± max(3×ATR14,1 điểm), làm tròn 0,1. Stop gửi loại MTL khi kích hoạt; không tạo thêm TP trong luồng này. SLTP mở kèm bảo vệ của bot vẫn giữ nguyên.

Payload đã đối chiếu mã công khai SmartPro: `co.stop.order.new` (MTL không gửi placedPrice), `co.stop.order.delete` (orderId/pin). Phản hồi thật chỉ đọc xác nhận trường RELATION và PRICE_TYPE. Không gửi/hủy lệnh thật trong quá trình triển khai.

## Module và vận hành

`vn_invest/vps_stop_guard.py` chạy trong tick runtime, dưới cùng khóa thread/process với autotrade. Journal `normal_stops` dùng file trạng thái đã gitignore; UNKNOWN được ghi trước gửi. Một Stop chưa giải quyết giữ khóa mở lệnh bot mới, kể cả Stop chưa hiện trong sổ VPS.

Tạo Stop cần bật gửi thật và `normal_stop_enabled` (mặc định true). Công tắc có trong panel AutoTrade. Nếu tắt công tắc tạo Stop, các Stop đã ghi nhận vẫn được đối soát khi worker autotrade còn chạy; không tự hủy bảo vệ chỉ vì tắt công tắc. Tắt hẳn autotrade/dry-run làm ngừng hành động worker như trước.

ATR được UI chuyển từ nến đã đóng sang cache module, không cần import UI từ worker. Dữ liệu phải mới trong 120 giây, hữu hạn, dương và giá gần VPS. Không có ATR hợp lệ thì chưa tạo Stop mới, hiển thị lý do. Stop đã tạo giữ ngưỡng và vẫn được đối soát khi ATR thiếu/cũ sau restart. Không phải trailing stop.

## Đối soát

- Chỉ xét khi tổng khớp có dấu trong sổ ngày khớp net VPS, giá vốn/hợp đồng hợp lệ. Chưa hỗ trợ tự bảo vệ vị thế qua đêm không thể giải thích bằng sổ khớp ngày hiện tại.
- Không chồng Stop nếu đã có lệnh điều kiện khác còn hiệu lực trên mã; không khẳng định mọi lệnh điều kiện đó bảo vệ đủ. Nếu đã có chu kỳ bot, để luồng SLTP quản lý.
- Đọc lại snapshot ngay trước gửi; vị thế/tài khoản/lệnh thay đổi thì xét lại tick sau. Không lấy ACK làm xác nhận Stop đã tồn tại.
- Match ID điều kiện mới với mã, chiều, KL, ngưỡng, relation, loại MTL. Nhiều ứng viên/thiếu lệnh đã biết giữ UNKNOWN, không gửi lại.
- Net/giá vốn thay đổi, xuất hiện bảo vệ khác hoặc có lệnh ngược chiều chờ: chỉ hủy Stop do bot tạo, đợi VPS xác nhận hủy rồi mới xét lại. Không hủy lệnh do người dùng đặt.
- Stop kích hoạt: theo dõi lệnh con, khối lượng khớp không được lùi. Không gửi lệnh đóng thứ hai. Khi có nguy cơ quá khối lượng hoặc có lệnh đóng khác, yêu cầu hủy phần lệnh con Stop còn chờ rồi đối soát.
- Nếu Stop/lệnh con bị từ chối, giữ trạng thái để xử lý trên SmartPro; không lặp yêu cầu vô hạn. Lệnh đã biết biến mất giữ khóa.
- Telegram nhận diện lệnh con Stop do bot sinh là Tự động; lệnh thường gốc vẫn giữ nguồn thủ công/ngoài bot nếu không thuộc journal bot.

## Giới hạn thực thi

Stop không có ràng buộc reduce-only/parent tự động trong payload đã xác minh. Việc người dùng đóng tay và Stop kích hoạt đồng thời vẫn có thể gây đảo vị thế trước khi lần đối soát kế tiếp nhìn thấy. Code giảm rủi ro bằng hủy/xác nhận và kiểm tra lệnh con, không bảo đảm loại bỏ mọi đua giữa các thao tác ngoài bot. Không tự gửi lệnh đảo lại để sửa vị thế ngoài dự kiến.

Chỉ tạo Stop MTL trong phiên liên tục; không tạo ở ATC. Stop đã đặt có thể bị VPS từ chối lúc kích hoạt nếu loại lệnh/phiên không phù hợp. Worker cần ứng dụng và Chrome; mất kết nối không có nghĩa đã hủy. Theo dõi panel/Telegram và SmartPro khi có lỗi.

## Xác minh và bàn giao

Tests dùng fake broker hoặc Chrome headless chặn request; không thực hiện giao dịch thật. Cần nạp lại backend sau cập nhật; health check không chứng minh module đã reload. Không chủ động restart backend đang bật giao dịch trong quá trình sửa.

Kết quả cuối: 186/186 kiểm thử đạt trong 42,21 giây. py_compile các module thay đổi đạt; git diff --check không có lỗi. Không chạy AppTest toàn ứng dụng do worker thực chưa được cách ly trong bài render đó.
