"""ชุดทดสอบ — รันด้วย  python -m pytest tests/ -q   (ไม่ต่อเน็ต ไม่เรียก AI)"""
import csv
import json
from datetime import datetime, timedelta, timezone

from collector import classify, config, dedupe, main, prefilter, writer


# ---------- URL ----------
def test_canonical_url_strips_tracking_and_case():
    a = dedupe.canonical_url("HTTPS://WWW.Thairath.co.th/news/1?utm_source=fb&id=9#top")
    assert a == "https://thairath.co.th/news/1?id=9"


def test_same_article_gets_same_id():
    assert dedupe.url_id("https://a.co/1") == dedupe.url_id("https://www.a.co/1/?fbclid=z")


# ---------- Google News ----------
def test_split_google_title():
    from collector.sources import split_google_title
    assert split_google_title("คลังเปิดลงทะเบียน - ไทยรัฐ") == ("คลังเปิดลงทะเบียน", "ไทยรัฐ")
    assert split_google_title("ไม่มีสำนักข่าว")[1] is None


# ---------- ตัดซ้ำ ----------
def test_dedupe_keeps_earliest_and_prefers_publisher():
    items = [
        {"title": "เบี้ยยังชีพเข้าบัญชีพรุ่งนี้", "url": "https://news.google.com/x",
         "published_at": "2026-09-10T05:00:00Z"},
        {"title": "เบี้ยยังชีพเข้าบัญชีพรุ่งนี้!", "url": "https://thairath.co.th/1",
         "published_at": "2026-09-10T04:00:00Z"},
        {"title": "ราคาน้ำมันปรับขึ้น", "url": "https://a.co/2",
         "published_at": "2026-09-10T06:00:00Z"},
    ]
    out = dedupe.dedupe(items)
    assert len(out) == 2
    assert any("thairath" in i["url"] for i in out)


# ---------- คำสำคัญ ----------
def test_keyword_score_and_exclude(tmp_path):
    cfg = config.load()
    score, strong = prefilter.keyword_score("บัตรสวัสดิการแห่งรัฐ ผู้สูงอายุ", cfg.keywords)
    assert score >= 5 and strong is True
    assert prefilter.has_excluded("ตรวจหวย งวดนี้", cfg.keywords) is True
    assert prefilter.has_excluded("เบี้ยยังชีพ", cfg.keywords) is False


def test_province_and_category_detection():
    assert prefilter.find_provinces("ชาวจันทบุรีและระยอง") == ["จันทบุรี", "ระยอง"]
    assert prefilter.find_provinces("ไม่มีชื่อจังหวัด") == ["ทั่วประเทศ"]
    assert prefilter.guess_category("เปิดลงทะเบียนบัตรสวัสดิการแห่งรัฐ") == "welfare_card"


# ---------- บัญชีดำ ----------
def test_blocklist_blocks_region7_but_keeps_head_office():
    cfg = config.load()
    assert prefilter.is_blocked({"url": "https://region7.prd.go.th/a", "source": "x"}, cfg)
    assert prefilter.is_blocked({"url": "https://x.co/a", "source": "สำนักงานประชาสัมพันธ์เขต 7"}, cfg)
    assert prefilter.is_blocked({"url": "https://thainews.prd.go.th/th/region/7/a", "source": "x"}, cfg)
    assert not prefilter.is_blocked({"url": "https://thainews.prd.go.th/news/1", "source": "กปส."}, cfg)


# ---------- คำตอบของ AI ----------
def test_extract_json_handles_fences_and_noise():
    assert classify._extract_json_array('```json\n[{"id":"a"}]\n```') == [{"id": "a"}]
    assert classify._extract_json_array('ตอบ [{"id":"b"}] จบ') == [{"id": "b"}]


def test_clean_provinces_normalises_and_falls_back():
    assert classify._clean_provinces(["จ.จันทบุรี", "Atlantis"], "") == ["จันทบุรี"]
    assert classify._clean_provinces([], "ข่าวระยอง") == ["ระยอง"]


# ---------- เขียนไฟล์ครบวงจร ----------
def _fake_items(date, n=3, eastern=True):
    base = datetime.fromisoformat(date).replace(tzinfo=timezone.utc)
    out = []
    for i in range(n):
        out.append({
            "id": f"id{i}", "title": f"ข่าวทดสอบที่ {i}", "summary": "สรุปสั้น",
            "url": f"https://example.co/{i}", "source": "ทดสอบ",
            "published_at": (base + timedelta(hours=i)).isoformat().replace("+00:00", "Z"),
            "category": "welfare_card", "provinces": ["จันทบุรี"] if eastern else ["ทั่วประเทศ"],
            "eastern": eastern, "score": 80 - i, "why": "ทดสอบ",
        })
    return out


def test_full_write_cycle(tmp_path):
    cfg = config.load()
    d1, d2 = "2026-09-09", "2026-09-10"
    win = {"from": "x", "to": "y"}

    seen = writer.load_seen(tmp_path)
    items = writer.apply_first_seen(_fake_items(d1), seen, d1)
    seen = writer.update_seen(seen, items, d1, cfg.lookback_days)
    writer.save_seen(seen, tmp_path)
    writer.write_day(d1, items, {"fetched": 100}, win, "t1", tmp_path)

    # วันที่ 2 เจอข่าวเดิม -> ต้องได้ first_seen
    items2 = writer.apply_first_seen(_fake_items(d2), seen, d2)
    assert all(i["first_seen"] == d1 for i in items2)
    seen = writer.update_seen(seen, items2, d2, cfg.lookback_days)
    writer.save_seen(seen, tmp_path)
    stats = writer.write_day(d2, items2, {"fetched": 120}, win, "t2", tmp_path)
    assert stats["runs"] == 1 and stats["followups"] == 3 and stats["new"] == 0

    # รันซ้ำวันเดิม -> runs ต้องเป็น 2 และข่าวไม่บวมขึ้น
    stats = writer.write_day(d2, items2, {"fetched": 130}, win, "t3", tmp_path)
    assert stats["runs"] == 2 and stats["kept"] == 3

    index = writer.rebuild_index("t3", seen, tmp_path)
    assert index["days"][0]["date"] == d2
    assert index["totals"]["days"] == 2
    assert index["totals"]["runs"] == 3
    assert index["totals"]["unique_items"] == 3
    assert index["totals"]["success_streak_days"] == 2

    # ลบ index แล้วสร้างใหม่ ต้องได้เท่าเดิม (ตัวเลขซ่อมตัวเองได้)
    (tmp_path / "index.json").unlink()
    again = writer.rebuild_index("t4", seen, tmp_path)
    assert again["totals"]["runs"] == index["totals"]["runs"]

    # CSV ต้องมี BOM ให้ Excel อ่านภาษาไทยออก
    writer.write_csv(again, tmp_path)
    raw = (tmp_path / writer.CSV_DAILY).read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")
    rows = list(csv.reader((tmp_path / writer.CSV_DAILY).read_text(encoding="utf-8-sig").splitlines()))
    assert rows[0][0] == "วันที่" and len(rows) == 3


def test_day_file_matches_frontend_contract(tmp_path):
    writer.write_day("2026-09-10", _fake_items("2026-09-10"), {"fetched": 9},
                     {"from": "a", "to": "b"}, "t", tmp_path)
    day = json.loads((tmp_path / "2026-09-10.json").read_text(encoding="utf-8"))
    assert set(day) == {"date", "generated_at", "window", "stats", "items"}
    for item in day["items"]:
        for field in ("id", "title", "summary", "url", "source", "published_at",
                      "category", "provinces", "eastern", "score"):
            assert field in item
        assert item["category"] in config.CATEGORIES
        assert "first_seen" not in item or item["first_seen"] is not None
    scores = [i["score"] for i in day["items"]]
    assert scores == sorted(scores, reverse=True)


def test_end_to_end_without_network(tmp_path, monkeypatch):
    """รันทั้งกระบวนการโดยป้อนข่าวปลอมแทนการต่อเน็ต และไม่เรียก AI"""
    monkeypatch.setattr(writer, "DATA_DIR", tmp_path)
    monkeypatch.setattr(main, "DATA_DIR", tmp_path)

    fake = [
        {"title": "คลังเปิดลงทะเบียนบัตรสวัสดิการแห่งรัฐรอบใหม่", "url": "https://a.co/1",
         "source": "ไทยรัฐ", "published_at": "2026-09-10T03:00:00Z",
         "snippet": "ผู้มีรายได้น้อยลงทะเบียนได้", "regional": False},
        {"title": "จันทบุรีเร่งช่วยเหลือกลุ่มเปราะบาง เบี้ยยังชีพผู้สูงอายุ",
         "url": "https://b.co/2", "source": "77ข่าวเด็ด",
         "published_at": "2026-09-10T04:00:00Z", "snippet": "พม.จันทบุรีลงพื้นที่", "regional": True},
        {"title": "ตรวจหวยงวดนี้", "url": "https://c.co/3", "source": "x",
         "published_at": "2026-09-10T04:30:00Z", "snippet": "", "regional": False},
        {"title": "สปข.7 จัดกิจกรรม", "url": "https://region7.prd.go.th/9", "source": "สปข.7",
         "published_at": "2026-09-10T04:40:00Z", "snippet": "เบี้ยยังชีพ", "regional": True},
    ]
    monkeypatch.setattr(main.sources, "fetch_all", lambda *a, **k: (fake, []))

    code = main.run(config.load(), "2026-09-10", no_ai=True, log=lambda *a: None)
    assert code == 0

    day = json.loads((tmp_path / "2026-09-10.json").read_text(encoding="utf-8"))
    urls = {i["url"] for i in day["items"]}
    assert "https://region7.prd.go.th/9" not in urls      # บัญชีดำทำงาน
    assert not any("หวย" in i["title"] for i in day["items"])  # คำต้องห้ามทำงาน
    assert any(i["eastern"] for i in day["items"])        # จับจังหวัดภาคตะวันออกได้
    assert day["stats"]["excluded_by_blocklist"] == 1
    assert (tmp_path / "index.json").exists()
    assert (tmp_path / "stats-daily.csv").exists()
    assert (tmp_path / "stats-summary.csv").exists()
    assert (tmp_path / "last_run.json").exists()
