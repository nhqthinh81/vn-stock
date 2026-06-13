"""Phan tich BCTC chuyen sau bang Claude AI.

Goi Anthropic API qua httpx truc tiep de tranh loi TypedDict Python 3.13.
"""
import os


def _call_claude(
    prompt: str,
    model: str = "claude-haiku-4-5-20251001",
    max_tokens: int = 8192,
    system: str = None,
    messages: list = None,
) -> str:
    """Goi Anthropic Messages API qua httpx. Khong dung anthropic SDK.

    - prompt: dùng khi gửi 1 user message đơn giản
    - messages: dùng khi muốn truyền multi-turn conversation [{role, content}, ...]
    - system: system prompt (ngữ cảnh / data) — dùng top-level system parameter
    """
    import httpx

    key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not key:
        raise ValueError("ANTHROPIC_API_KEY chua duoc cau hinh trong .env")

    _timeout = 300 if "sonnet" in model else 120

    msgs = messages if messages is not None else [{"role": "user", "content": prompt}]

    payload = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": msgs,
    }
    if system:
        payload["system"] = system

    resp = httpx.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json=payload,
        timeout=_timeout,
    )
    if resp.status_code != 200:
        body = resp.json()
        raise RuntimeError(body.get("error", {}).get("message", resp.text))
    data = resp.json()
    text = data["content"][0]["text"]
    if data.get("stop_reason") == "max_tokens":
        text += "\n\n---\n⚠️ _Phân tích bị cắt do giới hạn token. Vui lòng thử lại._"
    return text


def _fmt_b(val) -> str:
    if val is None:
        return "N/A"
    return f"{val/1e9:+,.1f} ty" if val < 0 else f"{val/1e9:,.1f} ty"


def _build_prompt(
    symbol: str,
    company_name: str,
    sector: str,
    profile: str,
    periods: list,
    income: dict,
    balance: dict,
    cashflow: dict,
    recent_news: list = None,
    recent_events: list = None,
    shareholders: dict = None,
    price_hist: list = None,
    macro: dict = None,
) -> str:
    def _row(store, iid, label=None):
        item = store.get(iid, {})
        if not item:
            return ""
        lbl = label or item.get("label", iid)
        vals = item.get("values", [])
        val_str = " | ".join(_fmt_b(v) for v in vals[:6])
        return f"  {lbl}: {val_str}"

    is_lines = "\n".join(filter(None, [
        _row(income, "isa1",  "Doanh thu"),
        _row(income, "isa3",  "Doanh thu thuan"),
        _row(income, "isa5",  "Loi nhuan gop"),
        _row(income, "isa7",  "Chi phi tai chinh"),
        _row(income, "isa8",  "Chi phi lai vay"),
        _row(income, "isa9",  "Chi phi ban hang"),
        _row(income, "isa10", "Chi phi QLDN"),
        _row(income, "isa11", "EBIT"),
        _row(income, "isa16", "Loi nhuan truoc thue"),
        _row(income, "isa20", "Loi nhuan sau thue"),
        _row(income, "isa23", "EPS (VND)"),
    ]))

    def _find(store, *keywords):
        """Tìm item_id đầu tiên có label chứa tất cả keywords (không dấu, lowercase)."""
        import unicodedata
        def _n(s):
            s = s.lower()
            s = unicodedata.normalize("NFD", s)
            return "".join(c for c in s if unicodedata.category(c) != "Mn")
        kws = [_n(k) for k in keywords]
        for iid, v in store.items():
            lbl = _n(v.get("label", ""))
            if all(k in lbl for k in kws):
                return _row(store, iid)
        return ""

    bs_lines = "\n".join(filter(None, [
        _row(balance, "bsa1",  "Tai san ngan han"),
        _row(balance, "bsa2",  "Tien & tuong duong tien"),
        _row(balance, "bsa5",  "Dau tu ngan han"),
        _row(balance, "bsa8",  "Phai thu"),
        _row(balance, "bsa9",  "Phai thu khach hang"),
        _row(balance, "bsa15", "Hang ton kho (rong)"),
        _row(balance, "bsa53", "Tong tai san"),
        _row(balance, "bsa54", "No phai tra"),
        _row(balance, "bsa55", "No ngan han"),
        _row(balance, "bsa56", "Vay ngan han"),           # nợ vay ngắn hạn
        _row(balance, "bsa57", "Phai tra nguoi ban"),
        _row(balance, "bsa67", "No dai han"),
        _row(balance, "bsa71", "Vay dai han"),             # nợ vay dài hạn
        _find(balance, "trai phieu"),                      # trái phiếu (nếu có)
        _row(balance, "bsa78", "Von chu so huu"),
        _row(balance, "bsa80", "Von gop"),
        _row(balance, "bsa90", "Loi nhuan chua phan phoi"),
        _row(balance, "bsa210","Loi ich co dong khong kiem soat"),
    ]))

    cf_lines = "\n".join(filter(None, [
        _row(cashflow, "cfa1",  "LNTT (dieu chinh)"),
        _row(cashflow, "cfa2",  "Khau hao TSCD"),
        _row(cashflow, "cfa7",  "Chi phi lai vay (CF)"),
        _row(cashflow, "cfa9",  "LN tu HDKD truoc thay doi von luu dong"),
        _row(cashflow, "cfa10", "(Tang)/giam phai thu"),
        _row(cashflow, "cfa11", "(Tang)/giam hang ton kho"),
        _row(cashflow, "cfa12", "Tang/(giam) phai tra"),
        _row(cashflow, "cfa14", "Lai vay da tra"),
        _row(cashflow, "cfa15", "Thue TNDN da nop"),
        _row(cashflow, "cfa18", "CFO - Luu chuyen tien HDKD"),
        _row(cashflow, "cfa19", "Mua sam TSCD / dau tu"),
        _row(cashflow, "cfa26", "CFI - Luu chuyen tien dau tu"),
        _row(cashflow, "cfa29", "Tien thu tu vay"),        # vốn vay mới
        _row(cashflow, "cfa30", "Tien tra no goc vay"),    # trả nợ gốc
        _row(cashflow, "cfa32", "Co tuc da tra"),
        _row(cashflow, "cfa34", "CFF - Luu chuyen tien tai chinh"),
        _row(cashflow, "cfa35", "Luu chuyen tien thuan trong ky"),
        _row(cashflow, "cfa38", "Tien cuoi ky"),
    ]))

    period_header = " | ".join(periods[:6])
    profile_short = str(profile).strip()[:800] if profile else "Khong co"

    # 1. Tin tức thời sự từ RSS báo uy tín + cross-check quốc tế
    news_block = ""
    rss_error = ""
    try:
        from vn_invest.news_fetcher import search_market_news, format_for_prompt, detect_conflicts
        rss_articles = search_market_news(
            symbol=symbol,
            company_name=company_name,
            sector=sector,
            max_results=15,
        )
        conflicts = detect_conflicts(rss_articles) if rss_articles else []
        if rss_articles:
            news_block = format_for_prompt(rss_articles, conflicts)
        else:
            rss_error = "(Khong tim thay bai bao lien quan tren RSS)"
    except Exception as e:
        rss_error = f"(Loi fetch RSS: {e})"

    # 2. Tin tức & sự kiện từ vnstock (bổ sung nếu có)
    vnstock_news = []
    if recent_news:
        for n in recent_news[:5]:
            date = n.get("date", "")[:10]
            title = n.get("title", "")
            if title:
                vnstock_news.append(f"- [{date}] {title} (vnstock)")
    if recent_events:
        for e in recent_events[:3]:
            date = e.get("date", "")[:10]
            title = e.get("title", "")
            if title:
                vnstock_news.append(f"- [{date}] {title} (su kien)")
    if vnstock_news:
        news_block += "\n\n### Su kien & tin cong ty (tu vnstock)\n" + "\n".join(vnstock_news)

    news_section = news_block if news_block else f"(Khong co tin tuc thoi su duoc cung cap. {rss_error} Hay su dung kien thuc cua ban nhung ghi ro 'thong tin co the cu, can kiem tra cap nhat')"

    # 3. Cổ đông lớn + ban lãnh đạo
    sh_block = ""
    if shareholders:
        sh_lines = []
        for sh in (shareholders.get("shareholders") or [])[:8]:
            name = sh.get("name", "")
            pct  = sh.get("percentage") or sh.get("share_own_percent", "")
            if name:
                sh_lines.append(f"  - {name}: {pct}%")
        if sh_lines:
            sh_block += "Co dong lon:\n" + "\n".join(sh_lines) + "\n"
        off_lines = []
        for of in (shareholders.get("officers") or [])[:5]:
            name = of.get("name", "")
            pos  = of.get("position") or of.get("title", "")
            own  = of.get("share_own_percent", "")
            if name:
                own_str = f" ({own}%)" if own else ""
                off_lines.append(f"  - {name} — {pos}{own_str}")
        if off_lines:
            sh_block += "Ban lanh dao:\n" + "\n".join(off_lines) + "\n"

    # 4. Bối cảnh vĩ mô (World Bank + spot rate)
    macro_block = ""
    if macro and not macro.get("error"):
        def _mv(key, label, unit=""):
            v = macro.get(key)
            if v is None:
                return ""
            return f"  {label}: {v:.1f}{unit}"
        m_lines = list(filter(None, [
            _mv("gdp_growth",    "GDP tang truong", "%"),
            _mv("cpi",           "Lam phat (CPI)",  "%"),
            _mv("fdi",           "FDI/GDP",         "%"),
            _mv("exports_pct",   "Xuat khau/GDP",   "%"),
            _mv("usdvnd_annual", "USD/VND (WB)",    ""),
            f"  USD/VND spot: {macro['usdvnd_spot']:,.0f}" if macro.get("usdvnd_spot") else "",
        ]))
        if m_lines:
            yr = macro.get("updated", "")
            macro_block = f"Du lieu vi mo Viet Nam (nam {yr}):\n" + "\n".join(m_lines)

    # 5. Lịch sử giá + khối lượng 20 phiên
    price_block = ""
    if price_hist:
        price_lines = []
        for p in price_hist[-10:]:  # 10 phiên gần nhất
            d = p.get("date", "")[:10]
            c = p.get("close")
            v = p.get("volume")
            c_str = f"{c:,.0f}" if c else "—"
            v_str = f"{v/1e6:.1f}M" if v else "—"
            price_lines.append(f"  {d}: Gia {c_str} | KL {v_str} cp")
        if price_lines:
            price_block = "Gia & khoi luong 10 phien gan nhat:\n" + "\n".join(price_lines)

    return f"""Ban la chuyen gia phan tich tai chinh chung khoan Viet Nam voi 15 nam kinh nghiem.

Hay phan tich BCTC cua **{symbol} - {company_name}** (nganh: {sector}).

**Ngay hom nay: thang 6/2026. Kien thuc cua ban co the chi den T8/2025.**

## Thong tin cong ty
{profile_short}

{sh_block}
{macro_block}

{price_block}

## TIN TUC THI TRUONG THOI SU (du lieu thuc te, uu tien nay khi phan tich)
{news_section}

## So lieu BCTC (don vi: ty dong, cac ky: {period_header})
Ky moi nhat dung dau, ky cu hon theo sau.

### Ket qua kinh doanh (KQKD)
{is_lines or "Khong co du lieu"}

### Can doi ke toan (CDKT)
{bs_lines or "Khong co du lieu"}

### Luu chuyen tien te (LCTT)
{cf_lines or "Khong co du lieu"}

## Yeu cau phan tich

Hay tra loi theo cau truc sau (dung markdown, tieng Viet co dau):

### 1. Mang kinh doanh chu yeu
Nhan dien 2-4 mang kinh doanh chinh cua cong ty dua tren profile va so lieu. Moi mang neu:
- Ten mang va ty trong uoc tinh trong doanh thu/loi nhuan
- Xu huong ky nay so ky truoc (tang/giam/on dinh, % neu tinh duoc)

### 2. Diem noi bat & rui ro
- **Diem tich cuc** (2-3 diem cu the tu so lieu)
- **Rui ro chinh** (2-3 diem, neu con so cu the)

### 3. Boi canh thi truong & nganh
Dua tren nganh **{sector}**, phan tich:
- Cac yeu to vi mo / thi truong dang anh huong (thue quan, ty gia, gia nguyen lieu, cau xuat khau...)
- So sanh tinh hinh cong ty voi xu huong chung cua nganh
- Neu cu the cac con so neu biet (vi du: "thue quan My tang X%, thi phan giam Y%")

### 4. Nhan dinh dau tu ngan gon
Mot doan 3-5 cau tom tat: mua/giu/ban va ly do chinh tu goc do fundamental.

Chu y quan trong:
- Chi dung so lieu co trong BCTC duoc cung cap de phan tich dinh luong
- Voi boi canh thi truong: uu tien dua tren tin tuc thuc te duoc cung cap o tren (neu co). Neu khong co tin tuc, su dung kien thuc ve nganh va ro rang ghi chu "thong tin tinh den T8/2025, can kiem tra cap nhat"
- Neu tin tuc thuc te mau thuan voi kien thuc cu, uu tien tin tuc thuc te
- **Khi phat hien mau thuan giua nguon Viet Nam va nguon quoc te (da danh dau ben tren), hay neu cu the trong phan Boi canh thi truong**: "Bao VN bao X, nhung SCMP/Bloomberg cho thay Y — can kiem tra them"
- Ngay hom nay la thang 6/2026"""


def analyze_bctc(
    symbol: str,
    company_name: str,
    sector: str,
    profile: str,
    periods: list,
    income: dict,
    balance: dict,
    cashflow: dict,
    recent_news: list = None,
    recent_events: list = None,
    shareholders: dict = None,
    price_hist: list = None,
    macro: dict = None,
    model: str = "claude-haiku-4-5-20251001",
) -> str:
    """Goi Claude de phan tich BCTC. Tra ve markdown string."""
    try:
        prompt = _build_prompt(
            symbol, company_name, sector, profile,
            periods, income, balance, cashflow,
            recent_news=recent_news,
            recent_events=recent_events,
            shareholders=shareholders,
            price_hist=price_hist,
            macro=macro,
        )
        return _call_claude(prompt, model=model, max_tokens=8192)

    except ValueError as e:
        return (f"⚠️ **Lỗi cấu hình:** {e}\n\n"
                "Thêm `ANTHROPIC_API_KEY=sk-ant-...` vào file `.env` rồi restart app.")
    except RuntimeError as e:
        err = str(e)
        if "credit balance is too low" in err.lower() or "credit" in err.lower():
            return ("⚠️ **Tài khoản Anthropic chưa có credit.**\n\n"
                    "Vui lòng nạp tiền tại: https://console.anthropic.com/settings/billing\n\n"
                    "Claude Haiku chi phí rất thấp (~$0.003/lần phân tích).")
        if "invalid" in err.lower() or "auth" in err.lower():
            return "⚠️ **API key không hợp lệ.** Kiểm tra lại `ANTHROPIC_API_KEY` trong `.env`."
        return f"⚠️ **Lỗi Anthropic API:** {err}"
    except Exception as e:
        err = str(e)
        if "rate" in err.lower():
            return "⚠️ **Rate limit.** Vui lòng thử lại sau vài giây."
        return f"⚠️ **Lỗi không xác định:** {err}"
