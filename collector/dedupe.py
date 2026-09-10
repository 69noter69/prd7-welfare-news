"""ทำ URL ให้เป็นมาตรฐาน และตัดข่าวซ้ำ

หมายเหตุเรื่องภาษาไทย: ภาษาไทยไม่เว้นวรรคระหว่างคำ การเทียบความคล้ายแบบ
"แยกคำด้วยช่องว่าง" จึงใช้ไม่ได้ผล โค้ดนี้เทียบด้วย "คู่ตัวอักษร" (bigram)
ซึ่งได้ผลดีกับภาษาไทย และใช้แค่ไลบรารีมาตรฐานของ Python ไม่ต้องลงอะไรเพิ่ม
"""
from __future__ import annotations

import hashlib
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

TRACKING_PREFIXES = ("utm_", "fb_", "gclid", "fbclid", "mc_", "igshid", "spm")
TITLE_SIMILARITY = 0.82   # 0-1 ยิ่งสูงยิ่งเข้มงวด (ต้องคล้ายกันมากถึงจะถือว่าซ้ำ)


def canonical_url(url: str) -> str:
    """ตัดพารามิเตอร์ติดตาม ทำ host เป็นตัวเล็ก ตัด / และ # ท้าย"""
    if not url:
        return ""
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return url.strip()
    scheme = (parts.scheme or "https").lower()
    try:
        host = (parts.hostname or "").lower()
    except ValueError:
        host = ""
    if host.startswith("www."):
        host = host[4:]
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=False)
             if not k.lower().startswith(TRACKING_PREFIXES)]
    path = re.sub(r"/+$", "", parts.path) or "/"
    return urlunsplit((scheme, host, path, urlencode(query), ""))


def url_id(url: str) -> str:
    """รหัสข่าว 8 ตัวอักษร คำนวณจาก URL ที่ทำมาตรฐานแล้ว"""
    return hashlib.sha1(canonical_url(url).encode("utf-8")).hexdigest()[:8]


def host_of(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


_PUNCT = re.compile(r"[\s​\"'`«»“”‘’\-–—_|:;,.!?()\[\]{}]+")


def normalize_title(title: str) -> str:
    return _PUNCT.sub("", (title or "").lower()).strip()


def _bigrams(text: str) -> set:
    return {text[i:i + 2] for i in range(len(text) - 1)} if len(text) > 1 else {text}


def similarity(a: str, b: str) -> float:
    """ความคล้ายของหัวข้อ 0-1 ด้วยวิธี Dice coefficient บนคู่ตัวอักษร"""
    sa, sb = _bigrams(a), _bigrams(b)
    if not sa or not sb:
        return 1.0 if a == b else 0.0
    return 2 * len(sa & sb) / (len(sa) + len(sb))


def _is_google_news(url: str) -> bool:
    return "news.google.com" in host_of(url)


def _sort_key(item: dict):
    """เก่ากว่า = ดีกว่า / ลิงก์ตรงสำนักข่าว = ดีกว่าลิงก์ Google News"""
    return (str(item.get("published_at") or ""), 1 if _is_google_news(item.get("url", "")) else 0)


def dedupe(items: list[dict]) -> list[dict]:
    """ตัดซ้ำ 2 ชั้น: URL เหมือนกัน แล้วค่อยดูหัวข้อที่คล้ายกันมาก"""
    by_url: dict[str, dict] = {}
    for item in items:
        key = canonical_url(item.get("url", ""))
        if not key:
            continue
        current = by_url.get(key)
        if current is None or _sort_key(item) < _sort_key(current):
            by_url[key] = item

    kept: list[dict] = []
    norms: list[str] = []
    for item in sorted(by_url.values(), key=_sort_key):
        norm = normalize_title(item.get("title", ""))
        if not norm:
            continue
        hit = next((i for i, other in enumerate(norms)
                    if similarity(norm, other) >= TITLE_SIMILARITY), None)
        if hit is None:
            kept.append(item)
            norms.append(norm)
        elif _sort_key(item) < _sort_key(kept[hit]):
            kept[hit] = item
            norms[hit] = norm
    return kept
