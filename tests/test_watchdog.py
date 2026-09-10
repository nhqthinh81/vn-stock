"""Bộ canh bot chết im lặng — `vn_invest/watchdog.py`.

Ngày 10/09/2026 mất trắng cả phiên vì hai thứ hỏng không phát ra tiếng động:
AmiBroker treo 5 tiếng, và không trình duyệt nào mở tab Phái Sinh suốt 3 tiếng.
Test này chốt lại đúng hai tình huống đó cùng các trường hợp KHÔNG được báo.
"""
import json
from datetime import datetime, timedelta

import pytest

from vn_invest import watchdog as wd


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Trỏ mọi đường dẫn sang tmp + bịt Telegram; trả helper dựng dữ liệu."""
    data = tmp_path / "data"
    data.mkdir()
    feed = tmp_path / "vn30f1m_1min.csv"

    monkeypatch.setattr(wd, "_APP", tmp_path)
    monkeypatch.setattr(wd, "_STATE_FILE", data / "watchdog_state.json")
    monkeypatch.setattr(wd, "_feed_path", lambda: feed if feed.exists() else None)

    sent = []

    def fake_send(msg):
        sent.append(msg)
        return True

    class Env:
        def __init__(self):
            self.sent = sent

        def heartbeat(self, stamp):
            (data / "ps_state.json").write_text(
                json.dumps({"owner": {"heartbeat": stamp.isoformat()}}), encoding="utf-8")

        def no_heartbeat_file(self):
            (data / "ps_state.json").unlink(missing_ok=True)

        def bar(self, stamp):
            feed.write_text(
                "Date,Time,Open,High,Low,Close,Volume\n"
                f"{stamp:%d/%m/%Y},{stamp:%H:%M},1,1,1,1,1\n", encoding="utf-8")

        def run(self, now, feed_ok=False):
            """feed_ok=True: làm tươi nến ngay trước khi chạy, để test cô lập
            đúng một trục (engine) mà không bị feed cũ dần theo mốc thời gian."""
            if feed_ok:
                self.bar(now - timedelta(minutes=1))
            return wd.run(send=fake_send, now=now)

    return Env()


# ── Giờ giao dịch ────────────────────────────────────────────────────────────

def test_ngoai_gio_va_cuoi_tuan_khong_bao(env):
    """Ngoài phiên, engine đứng im và feed không có nến mới là BÌNH THƯỜNG."""
    env.heartbeat(datetime(2026, 9, 10, 6, 0))      # cũ 3 tiếng
    env.bar(datetime(2026, 9, 9, 14, 45))           # nến hôm qua
    for now, nhan in [
        (datetime(2026, 9, 10, 8, 30), "truoc phien"),
        (datetime(2026, 9, 10, 12, 0), "nghi trua"),
        (datetime(2026, 9, 10, 15, 30), "sau phien"),
        (datetime(2026, 9, 12, 10, 0), "thu bay"),
    ]:
        assert wd.check(now) == {}, f"khong duoc bao luc {nhan}"
    assert env.sent == []


def test_an_han_dau_moi_chang_phien(env):
    """13:00 vừa mở lại: nến cuối là 11:29 nên 'cũ 91 phút' — không phải lỗi."""
    env.heartbeat(datetime(2026, 9, 10, 13, 0, 30))
    env.bar(datetime(2026, 9, 10, 11, 29))
    assert wd.check(datetime(2026, 9, 10, 13, 1)) == {}
    # Nhưng nếu tới 13:10 vẫn chưa có nến mới thì đúng là feed chết.
    assert "feed" in wd.check(datetime(2026, 9, 10, 13, 10))


# ── Hai cái chết im lặng thật ────────────────────────────────────────────────

def test_bat_duoc_engine_chet(env):
    """10/09: nhịp tim đứng ở 10:50, tới 13:37 vẫn không ai mở tab."""
    env.heartbeat(datetime(2026, 9, 10, 10, 50, 21))
    env.bar(datetime(2026, 9, 10, 13, 36))          # feed vẫn tốt
    problems = wd.check(datetime(2026, 9, 10, 13, 37))
    assert set(problems) == {"engine"}, "chi engine chet, feed van song"
    assert "Engine phái sinh KHÔNG chạy" in problems["engine"]


def test_bat_duoc_feed_chet(env):
    """10/09 sáng: AmiBroker treo, nến cuối vẫn là 14:45 hôm trước."""
    env.heartbeat(datetime(2026, 9, 10, 10, 49, 55))   # engine vẫn quay
    env.bar(datetime(2026, 9, 9, 14, 45))
    problems = wd.check(datetime(2026, 9, 10, 10, 50))
    assert set(problems) == {"feed"}
    assert "Feed 1 phút đứng" in problems["feed"]


def test_ca_hai_cung_chet(env):
    env.heartbeat(datetime(2026, 9, 10, 9, 0))
    env.bar(datetime(2026, 9, 9, 14, 45))
    assert set(wd.check(datetime(2026, 9, 10, 10, 0))) == {"engine", "feed"}


def test_moi_thu_khoe_thi_im_lang(env):
    env.heartbeat(datetime(2026, 9, 10, 13, 36, 50))
    env.bar(datetime(2026, 9, 10, 13, 36))
    assert wd.check(datetime(2026, 9, 10, 13, 37)) == {}


def test_thieu_file_nhip_tim_van_bao(env):
    """App chưa từng chạy hôm nay → không có ps_state.json → vẫn phải báo."""
    env.no_heartbeat_file()
    env.bar(datetime(2026, 9, 10, 13, 36))
    assert "engine" in wd.check(datetime(2026, 9, 10, 13, 37))


# ── Nhịp gửi tin ─────────────────────────────────────────────────────────────

def test_khong_spam_va_co_nhac_lai(env):
    env.heartbeat(datetime(2026, 9, 10, 10, 50))
    env.bar(datetime(2026, 9, 10, 13, 36))

    assert len(env.run(datetime(2026, 9, 10, 13, 37), feed_ok=True)) == 1, "lan dau phai bao"
    assert env.run(datetime(2026, 9, 10, 13, 40), feed_ok=True) == [], "3 phut sau khong duoc gui lai"
    assert env.run(datetime(2026, 9, 10, 13, 55), feed_ok=True) == [], "18 phut sau van chua toi luot"

    again = env.run(datetime(2026, 9, 10, 14, 10), feed_ok=True)
    assert len(again) == 1 and "nhắc lại" in again[0], "qua 30 phut thi nhac lai"


def test_bao_hoi_phuc_kem_thoi_gian_gian_doan(env):
    env.heartbeat(datetime(2026, 9, 10, 10, 50))
    env.bar(datetime(2026, 9, 10, 13, 36))
    env.run(datetime(2026, 9, 10, 13, 37), feed_ok=True)

    env.heartbeat(datetime(2026, 9, 10, 13, 58, 28))
    msgs = env.run(datetime(2026, 9, 10, 13, 58, 30), feed_ok=True)
    assert len(msgs) == 1
    assert "đã chạy lại" in msgs[0] and "22 phút (tính từ lúc phát hiện)" in msgs[0]

    assert env.run(datetime(2026, 9, 10, 13, 59), feed_ok=True) == [], "hoi phuc chi bao 1 lan"


def test_khong_bao_hoi_phuc_neu_chua_tung_bao_chet(env):
    """Chết đúng 1 nhịp rồi tự khỏi trước khi kịp gửi → không được sinh tin 'đã chạy lại'."""
    env.heartbeat(datetime(2026, 9, 10, 13, 36, 50))
    env.bar(datetime(2026, 9, 10, 13, 36))
    assert env.run(datetime(2026, 9, 10, 13, 37)) == []
    assert env.run(datetime(2026, 9, 10, 13, 38)) == []


def test_telegram_that_bai_thi_gui_lai_lan_sau(env, monkeypatch):
    """Không được đánh dấu 'đã gửi' khi Telegram từ chối — nếu không sẽ im 30 phút oan."""
    env.heartbeat(datetime(2026, 9, 10, 10, 50))
    env.bar(datetime(2026, 9, 10, 13, 36))

    env.bar(datetime(2026, 9, 10, 13, 36))
    assert wd.run(send=lambda m: False, now=datetime(2026, 9, 10, 13, 37)) == []
    assert len(env.run(datetime(2026, 9, 10, 13, 38), feed_ok=True)) == 1, "lan sau phai thu lai ngay"
