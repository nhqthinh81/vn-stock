"""Lay tin tuc thi truong thoi su tu RSS bao uy tin Viet Nam va quoc te.

Nguon Viet Nam  : VnEconomy, VnExpress
Nguon quoc te   : SCMP, Bloomberg, Financial Times
Cross-check     : Phat hien mau thuan giua tin VN va quoc te
"""
import re
import time
import unicodedata
import httpx

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120"
    )
}

# [tag, url, ngon_ngu]
_RSS_SOURCES = [
    # Viet Nam
    ("VnEconomy [VN]",  "https://vneconomy.vn/chung-khoan.rss",        "vi"),
    ("VnEconomy [VN]",  "https://vneconomy.vn/kinh-te.rss",             "vi"),
    ("VnExpress [VN]",  "https://vnexpress.net/rss/kinh-doanh.rss",     "vi"),
    # Quoc te
    ("SCMP [INT]",      "https://www.scmp.com/rss/91/feed",             "en"),
    ("Bloomberg [INT]", "https://feeds.bloomberg.com/markets/news.rss", "en"),
    ("FT [INT]",        "https://www.ft.com/rss/home/uk",               "en"),
]

_cache: dict = {}
_CACHE_TTL = 600  # 10 phut


# ── RSS Parser ───────────────────────────────────────────────────────────────

def _parse_rss(xml: str, source_tag: str, lang: str) -> list[dict]:
    items = re.findall(r"<item>(.*?)</item>", xml, re.DOTALL)
    results = []
    for item in items:
        def _tag(t):
            m = re.search(rf"<{t}><!\[CDATA\[(.*?)\]\]></{t}>", item, re.DOTALL)
            if m: return m.group(1).strip()
            m = re.search(rf"<{t}[^>]*>(.*?)</{t}>", item, re.DOTALL)
            return re.sub(r"<[^>]+>", "", m.group(1)).strip() if m else ""

        title = _tag("title")
        link  = _tag("link") or _tag("guid")
        pub   = _tag("pubDate")
        desc  = _tag("description")

        date_str = ""
        if pub:
            try:
                from email.utils import parsedate_to_datetime
                date_str = parsedate_to_datetime(pub).strftime("%Y-%m-%d %H:%M")
            except Exception:
                date_str = pub[:16]

        if title:
            results.append({
                "title":  title,
                "url":    link,
                "date":   date_str,
                "source": source_tag,
                "lang":   lang,
                "desc":   re.sub(r"<[^>]+>", "", desc)[:250] if desc else "",
            })
    return results


def _fetch_all_rss() -> list[dict]:
    now = time.time()
    if _cache.get("all") and now - _cache.get("all_t", 0) < _CACHE_TTL:
        return _cache["all"]

    all_articles: list[dict] = []
    for source_tag, url, lang in _RSS_SOURCES:
        try:
            r = httpx.get(url, headers=_HEADERS, timeout=10, follow_redirects=True)
            if r.status_code == 200:
                all_articles.extend(_parse_rss(r.text, source_tag, lang))
        except Exception:
            pass

    _cache["all"] = all_articles
    _cache["all_t"] = now
    return all_articles


# ── Keyword helpers ──────────────────────────────────────────────────────────

def _norm(text: str) -> str:
    text = text.lower()
    text = unicodedata.normalize("NFD", text)
    return "".join(c for c in text if unicodedata.category(c) != "Mn")


# Nganh → tu khoa Viet + English
_SECTOR_KEYWORDS: dict[str, dict] = {
    "steel":        {"vi": ["thep", "hrc", "can nong", "xuat khau thep", "gia thep", "hoa phat"],
                     "en": ["steel", "hrc", "coking coal", "iron ore", "tariff steel"]},
    "real estate":  {"vi": ["bat dong san", "nha o", "thi truong nha", "lai suat"],
                     "en": ["real estate", "property", "housing", "mortgage rate"]},
    "bank":         {"vi": ["ngan hang", "lai suat", "tin dung", "npl", "trai phieu"],
                     "en": ["bank", "interest rate", "credit", "npl", "fed rate"]},
    "seafood":      {"vi": ["thuy san", "tom", "ca tra", "xuat khau thuy san"],
                     "en": ["seafood", "shrimp", "catfish", "aquaculture", "tariff seafood"]},
    "timber":       {"vi": ["go", "lam san", "xuat khau go", "noi that go"],
                     "en": ["timber", "wood", "furniture", "lumber", "tariff wood"]},
    "textile":      {"vi": ["det may", "vai soi", "xuat khau det may"],
                     "en": ["textile", "garment", "apparel", "cotton", "tariff textile"]},
    "tech":         {"vi": ["cong nghe", "phan mem", "ai", "ban dan"],
                     "en": ["technology", "semiconductor", "ai", "chip", "software"]},
    "retail":       {"vi": ["ban le", "tieu dung", "suc mua", "sieu thi"],
                     "en": ["retail", "consumer", "spending", "e-commerce"]},
    "oil gas":      {"vi": ["dau khi", "xang dau", "gia dau"],
                     "en": ["oil", "gas", "crude", "petroleum", "energy"]},
    "pharma":       {"vi": ["duoc pham", "y te", "thuoc"],
                     "en": ["pharma", "drug", "healthcare", "medicine"]},
}

# Macro luon them
_MACRO_VI = ["thue quan my", "fed", "ty gia", "lam phat viet nam", "gdp viet nam",
              "xuat khau viet nam", "fdi", "vnindex", "lai suat viet nam"]
_MACRO_EN = ["vietnam tariff", "us tariff", "fed rate", "inflation", "vietnam gdp",
              "vietnam export", "vietnam fdi", "emerging market", "asia trade",
              "trump tariff", "us china trade"]


def _detect_sector_keys(sector: str) -> dict:
    """Map sector string -> {vi: [...], en: [...]}."""
    s = sector.lower()
    for key, kws in _SECTOR_KEYWORDS.items():
        if key in s:
            return kws
    # Fallback: empty
    return {"vi": [], "en": []}


# ── Scoring & search ─────────────────────────────────────────────────────────

def _score(article: dict, symbol: str, name_words: list,
           sector_kw: dict, lang: str) -> int:
    text = _norm(article["title"] + " " + article.get("desc", ""))
    art_lang = article.get("lang", "vi")
    score = 0

    # Ma co phieu: trong so cao nhat
    if symbol.lower() in text:
        score += 15

    # Ten cong ty
    for w in name_words:
        if _norm(w) in text:
            score += 6

    # Tu khoa nganh (theo ngon ngu bai bao)
    kws = sector_kw.get("en" if art_lang == "en" else "vi", [])
    for w in kws:
        if _norm(w) in text:
            score += 4

    # Macro
    macro = _MACRO_EN if art_lang == "en" else _MACRO_VI
    for w in macro:
        if _norm(w) in text:
            score += 2

    return score


def search_market_news(
    symbol: str,
    company_name: str = "",
    sector: str = "",
    max_results: int = 15,
) -> list[dict]:
    """Tim bai bao lien quan tu tat ca nguon (VN + quoc te).

    Returns: list of {title, url, date, source, lang, desc, score, is_intl}
    """
    articles = _fetch_all_rss()

    # Chuan bi tu khoa
    name_words = [w for w in company_name.split()
                  if len(w) > 2 and w.lower() not in
                  {"cong", "ty", "co", "phan", "tnhh", "tap", "doan", "joint", "stock", "company"}]
    sector_kw = _detect_sector_keys(sector)

    scored = []
    for art in articles:
        s = _score(art, symbol, name_words, sector_kw, art.get("lang", "vi"))
        if s > 0:
            scored.append({
                **art,
                "relevance_score": s,
                "is_intl": art.get("lang") == "en",
            })

    scored.sort(key=lambda x: -x["relevance_score"])
    return scored[:max_results]


# ── Cross-check ──────────────────────────────────────────────────────────────

def detect_conflicts(articles: list[dict]) -> list[str]:
    """Phat hien mau thuan giua tin VN va quoc te.

    Tra ve list cac cap mau thuan de dua vao prompt.
    """
    conflicts = []
    vi_arts  = [a for a in articles if not a.get("is_intl")]
    int_arts = [a for a in articles if a.get("is_intl")]

    if not vi_arts or not int_arts:
        return []

    # Cap keyword mau thuan pho bien
    conflict_pairs = [
        (["tang truong", "tang", "phuc hoi", "khoi sac", "tang manh"],
         ["decline", "fall", "drop", "slump", "slowdown", "recession"],
         "Tin VN tich cuc nhung tin quoc te cho thay su suy yeu"),
        (["xuat khau tang", "don hang tang", "thi phan tang"],
         ["tariff", "trade war", "sanction", "export ban", "quota"],
         "Tin VN bao xuat khau tang nhung co rui ro thue quan / thuong mai tu tin quoc te"),
        (["giam lai suat", "no long", "kich thich"],
         ["rate hike", "tightening", "inflation surge", "hawkish"],
         "Chinh sach tien te: tin VN va quoc te co the mau thuan ve huong lai suat"),
        (["gia tang", "gia cao", "gia on dinh"],
         ["price crash", "price slump", "oversupply", "glut"],
         "Tin gia ca: bao VN va quoc te co the trai chieu"),
    ]

    vi_text  = " ".join(_norm(a["title"] + " " + a.get("desc","")) for a in vi_arts)
    int_text = " ".join(_norm(a["title"] + " " + a.get("desc","")) for a in int_arts)

    for vi_kws, int_kws, note in conflict_pairs:
        vi_match  = any(k in vi_text  for k in vi_kws)
        int_match = any(k in int_text for k in int_kws)
        if vi_match and int_match:
            conflicts.append(note)

    return conflicts


# ── Format cho prompt ────────────────────────────────────────────────────────

def format_for_prompt(articles: list[dict], conflicts: list[str] = None) -> str:
    if not articles:
        return ""

    vi_arts   = [a for a in articles if not a.get("is_intl")]
    int_arts  = [a for a in articles if a.get("is_intl")]

    lines = [f"## Tin tuc thi truong thoi su (cap nhat {articles[0].get('date','')[:10] if articles else 'N/A'})\n"]

    if vi_arts:
        lines.append("### Nguon Viet Nam (VnEconomy, VnExpress)")
        for a in vi_arts[:7]:
            rel = a.get("relevance_score", 0)
            tag = "🔴" if rel >= 15 else "🟡" if rel >= 6 else "🔵"
            lines.append(f"{tag} [{a['date'][:10]}] **{a['title']}** — {a['source']}")
            if a.get("desc"):
                lines.append(f"   > {a['desc'][:180]}")
        lines.append("")

    if int_arts:
        lines.append("### Nguon Quoc Te (SCMP, Bloomberg, FT) — doi chieu, cross-check")
        for a in int_arts[:6]:
            rel = a.get("relevance_score", 0)
            tag = "🔴" if rel >= 15 else "🟡" if rel >= 6 else "🔵"
            lines.append(f"{tag} [{a['date'][:10]}] **{a['title']}** — {a['source']}")
            if a.get("desc"):
                lines.append(f"   > {a['desc'][:180]}")
        lines.append("")

    if conflicts:
        lines.append("### ⚠️ Phat hien mau thuan tiem an giua nguon VN va quoc te")
        for c in conflicts:
            lines.append(f"- {c}")
        lines.append("")

    return "\n".join(lines)
