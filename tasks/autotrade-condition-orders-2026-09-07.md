# Hoàn thiện lệnh điều kiện AutoTrade — 07/09/2026

## Kết luận nghiên cứu

Giữ SL/TP làm luồng vận hành chính. Rule hiện tại xác định chiều theo nến đóng/MACD/VWAP rồi gửi lệnh mở có bảo vệ. Stop Order là một cách vào lệnh khác, cần giá kích hoạt và giá đặt tách biệt; không tự có cặp bảo vệ.

Đối chiếu tài liệu VPS và mã JavaScript công khai đang được trang SmartPro tải:
- https://www.vps.com.vn/bai-viet/tong-hop-cac-lenh-dieu-kien-phai-sinh-tai-vps
- https://smartpro.vps.com.vn/v1/Templates/menuphai/js/menuphai.js?v=1.6.20260727
- https://smartpro.vps.com.vn/v1/Common/js/orderproc.js?v=1.6.20260616

`co.sltp.order.new` có các ngưỡng SL/TP; sau khớp mới sinh bảo vệ. Có thể chọn một hoặc hai nhánh, và nhiều lần khớp sinh nhiều cặp. `co.stop.order.new` có relation GTEQ/LTEQ, triggerPrice, priceType và placedPrice nhưng không có ngưỡng SL/TP. `co.oco.order.new` là yêu cầu riêng. Không có bằng chứng từ payload đã đọc rằng Stop Order có thể gắn bảo vệ trong cùng một yêu cầu.

Truy vấn chỉ đọc trên phiên thật xác nhận nhánh SL/TP có chiều ngược lệnh gốc, chiều trùng lệnh con; một SL đã kích hoạt/khớp và TP tương ứng đã hủy. Không dùng tài liệu SLTP cổ phiếu cơ sở để suy diễn chi tiết khớp lệnh phái sinh.

## Thay đổi hoàn tất

- Chỉ coi subtype SL/TP là nhánh bảo vệ; loại dòng gốc khỏi phép đếm.
- Cộng khối lượng từng nhánh qua nhiều phần khớp. Nếu bật TP, cả SL và TP phải phủ đủ khối lượng còn lại; một SL riêng không thể được báo thành cặp đầy đủ.
- Kiểm tra chiều, giá kích hoạt, khối lượng nguyên/hữu hạn, trạng thái chờ kích hoạt.
- Nếu nhánh thiếu/sai ngay từ đầu, chờ tối đa protection_timeout_seconds (mặc định 30 giây) cho server đồng bộ, rồi yêu cầu thoát qua chuỗi hủy/đối soát sẵn có. Nếu lệnh đã biết biến mất, giữ khóa để tránh suy đoán kết quả hủy.
- Lưu khối lượng khớp lệnh con SL/TP; không chấp nhận lùi dữ liệu qua restart. Đối soát với vị thế trước khi gửi thêm lệnh đóng.
- Nhánh đã kích hoạt chuyển chu kỳ sang xử lý thoát; đợi xác nhận lệnh con và hủy nhánh còn chờ.
- Panel hiển thị ngưỡng SL/TP và KL bảo vệ đã đối soát cho từng nhánh.

Ngưỡng chiến lược giữ nguyên: R=max(3×ATR14,1 điểm); SL cách giá vào R; TP cách giá vào 3R khi bật chốt lời. Không tự bật TP nếu người dùng đã tắt, không thay trần lỗ 1.000.000 đồng, không tuyên bố tối ưu lợi nhuận.

## Phạm vi Stop Order

Đã hoàn tất nghiên cứu, chưa triển khai/bật chế độ Stop Order. Việc chuyển sang Stop Order cần bổ sung một vòng đời riêng: nhận ID lệnh chờ, hủy khi tín hiệu hết hiệu lực, đối soát lúc kích hoạt, rồi gắn và xác nhận bảo vệ cho từng phần khớp bằng cơ chế dành cho vị thế đã có. Không dùng một lệnh SLTP mở mới để bảo vệ sau Stop Order vì có thể tăng vị thế lần nữa. Luồng SLTP hiện tại hoàn tất yêu cầu vận hành chính mà không tạo khoảng trống bảo vệ sau kích hoạt Stop.

Kiểm thử sử dụng broker giả lập hoặc trình duyệt cách ly chặn request. Không gửi/hủy lệnh thật, không thay cấu hình tài khoản, không chủ động khởi động lại ứng dụng đang chạy.

Kết quả xác nhận: 135/135 kiểm thử đạt trong 36,39 giây. py_compile đạt; git diff --check không có lỗi. Chưa chạy AppTest toàn ứng dụng vì chưa cách ly worker giao dịch đang bật.
