"""ดึงข่าวจาก RSS และ Google News แล้วแปลงให้อยู่ในรูปแบบเดียวกัน"""
from __future__ import annotations

import concurrent.futures as futures
import re
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import quote_plus

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
TIMEOUT_S = 20
RETRIES = 2
MAX_WORKERS = 8
GOOGLE_NEWS = "https://news.google.com/rss/search?q={q}&hl=th&gl=TH&ceid=TH:th"


def google_news_url(query: str) -> str:
    return GOOGLE_NEWS.format(q=quote_plus(query))


def build_targets(cfg) -> list[dict]:
    """รวมทุกแหล่งข่าวเป็นรายการเดียว พร้อมธงว่าเป็นแหล่งภูมิภาคหรือไม่"""
    targets: list[dict] = []
    for feed in cfg.get("feeds", []) or []:
        targets.append({"name": feed.get("name", "ไม่ทราบแหล่ง"),
                        "url": feed.get("url", ""), "regional": False})
    for feed in cfg.get("regional_feeds", []) or []:
        targets.append({"name": feed.get("name", "ไม่ทราบแหล่ง"),
                        "url": feed.get("url", ""), "regional": True})
    for query in cfg.get("google_news_queries", []) or []:
        targets.append({"name": "Google News", "url": google_news_url(str(query)),
                        "regional": False, "query": str(query)})
    for template in cfg.get("regional_query_templates", []) or []:
        for province in cfg.eastern:
            q = str(template).replace("{province}", province)
            targets.append({"name": "Google News", "url": google_news_url(q),
                            "regional": True, "query": q})
    return [t for t in targets if t["url"]]


def _fetch_bytes(url: str):
    import httpx
    last = None
    for attempt in range(RETRIES + 1):
        try:
            with httpx.Client(follow_redirects=True, timeout=TIMEOUT_S,
                              headers={"User-Agent": USER_AGENT}) as client:
                res = client.get(url)
                res.raise_for_status()
                return res.content, res.status_code
        except Exception as exc:            # noqa: BLE001 — แหล่งข่าวตายต้องไม่ทำให้ทั้งระบบล่ม
            last = exc
            if attempt < RETRIES:
                time.sleep(1.5 * (attempt + 1))
    raise last


_GOOGLE_SUFFIX = re.compile(r"\s+-\s+([^-]{2,60})$")


def split_google_title(title: str) -> tuple[str, str | None]:
    """Google News ต่อท้ายหัวข้อด้วย ' - ชื่อสำนักข่าว' — แยกออกมา"""
    match = _GOOGLE_SUFFIX.search(title or "")
    if not match:
        return (title or "").strip(), None
    return title[: match.start()].strip(), match.group(1).strip()


def _entry_time(entry) -> datetime | None:
    for key in ("published_parsed", "updated_parsed"):
        parsed = getattr(entry, key, None) or (entry.get(key) if hasattr(entry, "get") else None)
        if parsed:
            try:
                return datetime(*parsed[:6], tzinfo=timezone.utc)
            except (TypeError, ValueError):
                continue
    return None


def _clean(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = text.replace("&nbsp;", " ").replace("&amp;", "&").replace("&quot;", '"')
    return re.sub(r"\s+", " ", text).strip()


def parse_feed(raw: bytes, target: dict, now: datetime, window_from: datetime) -> list[dict]:
    """แปลง RSS/Atom เป็นรายการข่าวมาตรฐาน และตัดข่าวที่เก่าเกินช่วงเวลา"""
    import feedparser
    parsed = feedparser.parse(raw)
    is_google = "news.google.com" in target["url"]
    out: list[dict] = []

    for entry in parsed.entries:
        title = _clean(getattr(entry, "title", ""))
        link = (getattr(entry, "link", "") or "").strip()
        if not title or not link:
            continue

        source = target["name"]
        if is_google:
            title, publisher = split_google_title(title)
            src_detail = getattr(entry, "source", None)
            source = (getattr(src_detail, "title", None) or publisher or "Google News")
            if not title:
                continue

        published = _entry_time(entry) or now
        if published < window_from or published > now + timedelta(hours=6):
            continue

        snippet = _clean(getattr(entry, "summary", "") or getattr(entry, "description", ""))
        out.append({
            "title": title,
            "url": link,
            "source": source,
            "published_at": published.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            "snippet": snippet[:600],
            "regional": bool(target.get("regional")),
        })
    return out


def fetch_all(cfg, now: datetime, window_from: datetime, log=print) -> tuple[list[dict], list[str]]:
    """ดึงทุกแหล่งพร้อมกัน แหล่งที่ล้มจะถูกข้ามและบันทึกไว้ ไม่ทำให้ระบบหยุด"""
    targets = build_targets(cfg)
    items: list[dict] = []
    errors: list[str] = []

    def work(target):
        raw, _ = _fetch_bytes(target["url"])
        return parse_feed(raw, target, now, window_from)

    with futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        jobs = {pool.submit(work, t): t for t in targets}
        for job in futures.as_completed(jobs):
            target = jobs[job]
            label = target.get("query") or target["name"]
            try:
                found = job.result()
                items.extend(found)
            except Exception as exc:        # noqa: BLE001
                msg = f"{label}: {type(exc).__name__}"
                errors.append(msg)
                log(f"  ! ข้ามแหล่ง {msg}")
    return items, errors


def check_feeds(cfg, log=print) -> int:
    """คำสั่ง check-feeds — ทดสอบว่าแหล่งข่าวไหนใช้ได้บ้าง"""
    import feedparser
    targets = [t for t in build_targets(cfg) if "news.google.com" not in t["url"]]
    bad = 0
    log(f"ทดสอบแหล่งข่าว {len(targets)} แหล่ง\n")
    for target in targets:
        try:
            raw, status = _fetch_bytes(target["url"])
            n = len(feedparser.parse(raw).entries)
            if n == 0:
                bad += 1
                log(f"  [ว่างเปล่า] {target['name']} (HTTP {status}) -> ควรลบออก")
            else:
                log(f"  [ใช้ได้]   {target['name']} — {n} ข่าว")
        except Exception as exc:            # noqa: BLE001
            bad += 1
            log(f"  [เสีย]     {target['name']} — {type(exc).__name__} -> ควรลบออก")
    log(f"\nสรุป: ใช้ได้ {len(targets) - bad} / เสียหรือว่าง {bad}")
    return bad
