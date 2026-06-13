"""Cấu hình mặc định cho toàn bộ ứng dụng."""

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
SCORE_BUY_A = 75   # BUY-A: >= 75
SCORE_BUY_B = 55   # BUY-B: 55–74
SCORE_SELL_B = 35  # SELL-B: 25–34
SCORE_SELL_A = 25  # SELL-A: < 25
# HOLD: 35–54

# Cache
CACHE_FILE = "data/scores_cache.json"
PRICE_CACHE_TTL = 300  # giây

# Số ngày lịch sử mặc định
DEFAULT_DAYS = 120
