"""TAB 7 — TIN TỨC THỊ TRƯỜNG."""
import streamlit as st


def render(ctx: dict) -> None:
    from vn_invest.news_fetcher import _fetch_all_rss, _RSS_SOURCES

    st.header("📰 Tin Tức Thị Trường")
    st.caption(f"{len(_RSS_SOURCES)} nguồn: VnEconomy · VnExpress · CafeF · Investing.com VN · NguoiDuaTin · Reuters · SCMP · Mining.com · SteelOrbis")

    _nc1, _nc2, _nc3 = st.columns([2, 2, 1])
    _news_lang = _nc1.selectbox("Ngôn ngữ", ["Tất cả", "Tiếng Việt", "English"], key="news_lang")
    _news_src  = _nc2.selectbox(
        "Nguồn",
        ["Tất cả"] + sorted({s for s, _, _ in _RSS_SOURCES}),
        key="news_src",
    )
    _news_kw   = _nc3.text_input("Tìm kiếm", placeholder="vnindex, thep...", key="news_kw")

    _btn_reload = st.button("🔄 Tải lại tin tức", key="btn_news_reload")
    if _btn_reload:
        import vn_invest.news_fetcher as _nf
        _nf._cache.clear()

    try:
        with st.spinner("Đang tải tin tức..."):
            _all_news = _fetch_all_rss()
        st.write(f"DEBUG fetch OK: {len(_all_news)} bài")
    except Exception as _e:
        st.error(f"DEBUG fetch lỗi: {_e}")
        _all_news = []

    _filtered = list(_all_news)
    if _news_lang == "Tiếng Việt":
        _filtered = [a for a in _filtered if a.get("lang") == "vi"]
    elif _news_lang == "English":
        _filtered = [a for a in _filtered if a.get("lang") == "en"]
    if _news_src != "Tất cả":
        _filtered = [a for a in _filtered if a.get("source") == _news_src]
    if _news_kw.strip():
        _kw = _news_kw.strip().lower()
        _filtered = [
            a for a in _filtered
            if _kw in a.get("title", "").lower() or _kw in a.get("desc", "").lower()
        ]

    st.write(f"DEBUG filtered: {len(_filtered)} bài")

    _filtered.sort(key=lambda x: x.get("date", ""), reverse=True)

    st.markdown(f"**{len(_filtered)} bài** · Cache 10 phút")
    st.divider()

    _by_source: dict = {}
    for _art in _filtered:
        _by_source.setdefault(_art["source"], []).append(_art)

    if not _filtered:
        st.info("Không có bài viết nào phù hợp với bộ lọc.")
    else:
        for _src_name, _arts in _by_source.items():
            _lang_flag = "🇻🇳" if _arts[0].get("lang") == "vi" else "🌐"
            with st.expander(f"{_lang_flag} **{_src_name}** — {len(_arts)} bài", expanded=True):
                for _a in _arts[:20]:
                    _col_a, _col_b = st.columns([5, 1])
                    with _col_a:
                        if _a.get("url"):
                            st.markdown(f"**[{_a['title']}]({_a['url']})**")
                        else:
                            st.markdown(f"**{_a['title']}**")
                        if _a.get("desc"):
                            st.caption(_a["desc"][:200])
                    with _col_b:
                        st.caption(_a.get("date", "")[:10])
