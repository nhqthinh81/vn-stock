# Bản sửa AutoTrade vận hành — 07/09/2026

## Luồng thực tế

- `vps_broker.py` đọc phiên đăng nhập Chrome hiện có, đối chiếu tài khoản phiếu với dữ liệu server, đọc vị thế, sổ lệnh thường và lệnh điều kiện. Đã đối chiếu schema bằng truy vấn chỉ đọc trên phiên SmartPro thực tế.
- Gửi một `co.sltp.order.new` gồm lệnh mở và SL/TP. Không gửi SL/TP như một lệnh mở thứ hai sau khi đã khớp. Lệnh đóng dùng payload MTL của hàm native SmartPro.
- `autotrade_runtime.py` ghi journal trước mỗi yêu cầu gửi/hủy, giữ khóa giữa các thread/process. ACK không được coi là đã khớp. Kết quả chưa rõ được giữ để đối soát, không tự gửi lại.
- Chỉ quản lý vị thế có lệnh mở được ghi trong journal. Kiểm tra mã, chiều, khối lượng, mã lệnh liên quan. Dữ liệu vị thế lệch với tổng khớp mở/trừ khớp đóng sẽ giữ khóa, tránh đóng trùng hoặc giải phóng chu kỳ quá sớm.
- Trước thoát: hủy phần mở chưa khớp, hủy bảo vệ, đợi xác nhận server/lệnh con; sau đó đóng khối lượng thực còn lại. Hết thời gian giữ vẫn được worker xử lý độc lập với vị thế mô phỏng.
- Trần lỗ dùng trường `vm` VPS hiển thị, khóa mở mới đến hết ngày và lưu qua restart. Không cộng thêm `unrelizeVM` khi chưa xác minh quan hệ để tránh cộng trùng. Chạm trần vẫn cho phép xử lý đóng vị thế bot quản lý. Đây là ngưỡng kích hoạt, không phải bảo đảm số tiền khớp thoát chính xác hay PnL sau mọi phí.
- Tín hiệu hết hạn không được gửi bù; có chống xử lý lại cùng signal ID, giới hạn số lần mở và kiểm tra hạn hợp đồng/giá. Không mở sát giờ nghỉ/ATC; MTL chỉ gửi trong phiên liên tục.
- Sửa MACD histogram theo tên cột, điều kiện SHORT phải thực sự dưới VWAP/histogram giảm; dữ liệu không hữu hạn trả WAIT. Không thay ngưỡng chiến lược để tuyên bố tăng lợi nhuận.

## Cách sử dụng bản sửa

1. Nạp lại ứng dụng để dùng mã mới, mở bảng Phái sinh/AutoTrade. Worker khởi động khi cấu hình enabled và gửi thật đang bật.
2. Duy trì Chrome AutoTrade với một tab SmartPro đúng tài khoản; tự đăng nhập/xác thực khi VPS yêu cầu. Nút kiểm tra phiên ở chế độ thật đã đọc server, không còn bắt buộc chuyển phiếu sang Lệnh thường.
3. Xem trạng thái chu kỳ, KL đã khớp, PnL VPS, khóa lỗ và lỗi đối soát trên panel. Nếu UNKNOWN, kiểm tra SmartPro và nguyên nhân; không xóa journal để ép mở lại.
4. Journal nằm trong `data/autotrade_live_state.json` (gitignore). Không chứa PIN/cookie/session; chỉ ghim tài khoản bằng SHA-256 và lưu thông tin lệnh cần đối soát.

Không thay cấu hình giới hạn 1.000.000 đồng của người dùng. Bỏ bước kiểm định chiến lược theo yêu cầu; vẫn kiểm thử phần mềm. Trong quá trình sửa không khởi động worker giao dịch thật, không gửi/hủy lệnh thật và không chủ động khởi động lại ứng dụng.

## Giới hạn cần nhận biết khi có sự cố

- Adapter dựa trên schema nội bộ SmartPro đã đối chiếu ngày nêu trên; thay đổi phía VPS hoặc phiên/PIN hết hiệu lực có thể khiến lệnh bị từ chối/chưa rõ. Không tự vượt qua xác thực.
- Lệnh chưa có broker ID được đối chiếu theo tập ID mới cùng mã/chiều/KL/giá. Nếu có nhiều ứng viên sẽ giữ khóa. Tránh giao dịch tay đồng thời trong tài khoản bot; thay đổi vị thế không giải thích được bằng lệnh quản lý sẽ dừng đối soát.
- Sổ lệnh thường lấy ngày hiện tại; chu kỳ chưa giải quyết từ ngày trước có thể cần đối chiếu thủ công. Không tự bỏ lệnh khỏi journal.
- Worker là thread trong ứng dụng, không phải dịch vụ Windows độc lập. Khi ứng dụng/Chrome mất kết nối, việc đóng theo thời gian hoặc trần lỗ phía bot không chạy; SL/TP đã được VPS xác nhận thuộc server.

## Kiểm thử

Dùng broker giả lập và Chrome headless chặn toàn bộ request, không gửi tới tài khoản thật. Bao gồm mất phản hồi, ACK chậm, restart, khớp một phần, hủy chưa rõ, SL/TP kích hoạt, dữ liệu vị thế cập nhật chậm, mất lệnh đã biết, khóa lỗ qua restart, đổi tài khoản, tín hiệu cũ, giá làm tròn và rule phái sinh.

Kết quả: 118 kiểm thử đạt (33,90 giây); py_compile bốn module đạt, git diff --check không có lỗi. Không chạy bài AppTest toàn ứng dụng vì bài đó chưa cô lập worker và các kết nối bên ngoài.
