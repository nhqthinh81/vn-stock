# Telegram: trạng thái lệnh thật VPS

Báo cáo dùng adapter chỉ đọc để lấy sổ lệnh thường, lệnh điều kiện, vị thế và PnL vm. Không cộng PnL mô phỏng. Phạm vi là ngày hiện tại; không khẳng định số vm đã trừ mọi phí.

Nội dung Telegram có số hiệu lệnh, thời gian, mã, chiều, KL đặt/khớp, giá và trạng thái. Nhãn Tự động dựa trên broker ID trong journal cùng ngày và liên kết lệnh con SLTP. Dòng không ghép được ghi Thủ công/ngoài bot, có thể gồm lệnh từ phiên bản cũ hoặc công cụ khác. Điều kiện còn chờ được tách khỏi lệnh trong sổ thường.

Worker báo cáo độc lập với bật/tắt autotrade; không gọi thao tác giao dịch. Khi mở bảng Phái sinh và có Telegram, worker quét mỗi 30 giây: gửi khi lệnh/vị thế thay đổi, cập nhật PnL mỗi 15 phút trong giờ giao dịch và một lần sau phiên. Lần đầu gửi bản hiện tại. Công tắc báo cáo và nút Gửi trạng thái VPS ngay ở panel Telegram.

Lượt gửi lưu trong telegram_vps thuộc journal hiện có; khóa chỉ giữ khi dành lượt gửi, không giữ lúc gọi Telegram. Tin dài chia phần và escape HTML. Lỗi gửi thử lại sau 5 phút. Telegram không có idempotency key; mất phản hồi sau khi đã nhận tin có thể gây lặp khi thử lại. Không gửi dữ liệu VPS cũ như mới khi truy vấn lỗi.

Kiểm thử dùng broker/sender giả lập; dựng thử báo cáo trực tiếp bằng truy vấn chỉ đọc, không gửi thử Telegram, không gửi/hủy giao dịch. Tài liệu này không lưu chi tiết tài khoản, lệnh hay PnL thực tế.
