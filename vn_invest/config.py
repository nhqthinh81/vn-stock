"""Cấu hình mặc định cho toàn bộ ứng dụng."""
from pathlib import Path

# Root project = thư mục chứa package vn_invest (luôn đúng dù CWD ở đâu)
_PROJECT_ROOT = Path(__file__).parent.parent

# Nguồn dữ liệu mặc định
DEFAULT_SOURCE = "KBS"  # hoặc "VCI"

# Danh sách mã theo dõi mặc định
DEFAULT_WATCHLIST = [
    "VNM", "HPG", "ACB", "VCB", "TCB", "MBB", "FPT",
    "VHM", "VIC", "MSN", "CTG", "BID", "SSI", "VND",
    "HDB", "TPB", "SHB", "EIB", "STB", "LPB",
]

# Ngưỡng RSI
RSI_OVERSOLD = 30
RSI_OVERBOUGHT = 70

# Ngưỡng tín hiệu
SCORE_BUY_A = 70   # BUY-A: >= 70
SCORE_BUY_B = 55   # BUY-B: 55–69
SCORE_SELL_B = 35  # SELL-B: 25–34
SCORE_SELL_A = 25  # SELL-A: < 25
# HOLD: 35–54

# Mã bị hạn chế/cảnh báo/kiểm soát — luôn loại khỏi tín hiệu BUY
# Cập nhật thủ công khi HoSE/HNX công bố danh sách mới
RESTRICTED_SYMBOLS: set[str] = {
    "BCG", "FLC", "ROS", "AMD", "HAI", "GAB", "TTB", "ART",
    "ITA", "HQC", "DLG", "OGC", "PPI",
}

# Cache — dùng đường dẫn tuyệt đối để tránh phụ thuộc CWD
CACHE_FILE = str(_PROJECT_ROOT / "data" / "scores_cache.json")
PRICE_CACHE_TTL = 300  # giây

# Số ngày lịch sử mặc định
DEFAULT_DAYS = 120
