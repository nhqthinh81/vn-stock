import pandas as pd
import datetime as dt
from curl_cffi import requests

# Dictionary chứa Pair ID của các loại hàng hóa, tiền tệ và chỉ số phổ biến
# Dựa trên Investing.com (Có thể tra cứu thêm bằng cách F12 xem Network)
COMMON_PAIRS = {
    "GOLD": 8830,          # Gold Futures
    "XAU/USD": 68,         # Gold Spot
    "WTI": 8849,           # Crude Oil WTI Futures
    "BRENT": 8833,         # Brent Oil Futures
    "SP500": 166,          # S&P 500
    "DOW": 169,            # Dow Jones Industrial Average
    "NASDAQ": 14958,       # NASDAQ Composite
    "DXY": 942611,         # US Dollar Index
    "EUR/USD": 1,          # EUR/USD
    "USD/VND": 2214,       # USD/VND
    "BTC/USD": 945629      # Bitcoin USD
}

class InvestingAPI:
    def __init__(self):
        # Sử dụng impersonate mới hơn để bypass Cloudflare
        self.session = requests.Session(impersonate="chrome120")
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "domain-id": "www",
            "Origin": "https://www.investing.com",
            "Referer": "https://www.investing.com/"
        })
        
        # Khởi tạo cookie session bằng cách truy cập trang chủ trước
        try:
            print("Initializing secure session with Investing.com...")
            self.session.get("https://www.investing.com/", timeout=15)
        except Exception as e:
            print(f"Session init error (can be ignored): {e}")

    def search_symbol(self, query: str):
        """
        Tìm kiếm mã/pair_id trên Investing.com
        """
        url = f"https://api.investing.com/api/search/v2/search?q={query}"
        try:
            resp = self.session.get(url, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                results = []
                for item in data.get("quotes", []):
                    results.append({
                        "id": item.get("id"),
                        "name": item.get("name"),
                        "symbol": item.get("symbol"),
                        "type": item.get("type"),
                        "exchange": item.get("exchange")
                    })
                return pd.DataFrame(results)
            else:
                print(f"Query error: {resp.status_code}")
                return None
        except Exception as e:
            print(f"Error: {e}")
            return None

    def get_historical_data(self, pair_id: int, start_date: str, end_date: str, timeframe: str = "Daily") -> pd.DataFrame:
        """
        Lấy dữ liệu lịch sử giá từ Investing.com
        pair_id: ID của tài sản (vd: 166 cho S&P 500)
        start_date, end_date: Định dạng 'YYYY-MM-DD'
        timeframe: 'Daily', 'Weekly', 'Monthly'
        """
        url = f"https://api.investing.com/api/financialdata/historical/{pair_id}"
        params = {
            "start-date": start_date,
            "end-date": end_date,
            "time-frame": timeframe,
            "add-missing-rows": "false"
        }
        
        try:
            resp = self.session.get(url, params=params, timeout=15)
            if resp.status_code == 200:
                data = resp.json()
                if "data" in data:
                    df = pd.DataFrame(data["data"])
                    
                    # Loại bỏ cột 'volume' dạng chuỗi (nếu có) để tránh trùng lặp khi rename 'volumeRaw'
                    if 'volume' in df.columns:
                        df = df.drop(columns=['volume'])
                        
                    # Đổi tên cột chuẩn xác
                    df = df.rename(columns={
                        "rowDateRaw": "time",
                        "last_close": "close",
                        "last_open": "open",
                        "last_max": "high",
                        "last_min": "low",
                        "volumeRaw": "volume"
                    })
                    
                    if "time" in df.columns:
                        # Convert Unix timestamp (tính bằng giây)
                        df["time"] = pd.to_datetime(df["time"], unit='s').dt.date
                        
                        # Chỉ giữ lại các cột cần thiết
                        cols_to_keep = [c for c in ["time", "open", "high", "low", "close", "volume"] if c in df.columns]
                        df = df[cols_to_keep]
                        
                        # Chuyển đổi định dạng số (nhằm loại bỏ các string format như '4,163.40')
                        for col in ["open", "high", "low", "close", "volume"]:
                            if col in df.columns:
                                # Nếu dữ liệu dạng chuỗi có dấu phẩy, loại bỏ phẩy rồi mới parse
                                if df[col].dtype == object:
                                    df[col] = df[col].astype(str).str.replace(',', '', regex=False)
                                df[col] = pd.to_numeric(df[col], errors='coerce')
                        
                        df = df.sort_values("time").reset_index(drop=True)
                    return df
                else:
                    print("Invalid data format.")
                    return pd.DataFrame()
            else:
                print(f"API returned error code: {resp.status_code}")
                return pd.DataFrame()
        except Exception as e:
            print(f"Error fetching data: {e}")
            return pd.DataFrame()

# Khởi tạo instance toàn cục
_api = InvestingAPI()

def get_global_price(symbol: str, start: str = "2023-01-01", end: str = None) -> pd.DataFrame:
    """
    Hàm tiện ích tương tự get_price_history của vnstock.
    Hỗ trợ nhận string (VD: "GOLD", "SP500") hoặc ID số (VD: 166).
    """
    if end is None:
        end = dt.date.today().isoformat()
        
    pair_id = None
    symbol_upper = str(symbol).upper().strip()
    
    if symbol_upper in COMMON_PAIRS:
        pair_id = COMMON_PAIRS[symbol_upper]
    elif str(symbol).isdigit():
        pair_id = int(symbol)
    else:
        # Thử tìm tự động
        print(f"Searching ID for '{symbol}' on Investing.com...")
        res = _api.search_symbol(symbol)
        if res is not None and not res.empty:
            pair_id = int(res.iloc[0]["id"])
            print(f"Found ID: {pair_id} ({res.iloc[0]['name']})")
        else:
            print(f"Could not find ID for {symbol}. Please provide ID directly.")
            return pd.DataFrame()
            
    print(f"Fetching data for ID: {pair_id} from {start} to {end}...")
    df = _api.get_historical_data(pair_id, start_date=start, end_date=end)
    return df
