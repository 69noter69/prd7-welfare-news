"""ให้คะแนนด้วยคำสำคัญ และตัดแหล่งข่าวในบัญชีดำ — ทำก่อนส่งให้ AI เพื่อประหยัดค่าใช้จ่าย"""
from __future__ import annotations

import fnmatch
import re

from .config import NATIONWIDE, PROVINCES
from .dedupe import canonical_url, host_of

WEIGHTS = {"strong": 3, "medium": 2, "weak": 1}
REGIONAL_BONUS = 2

CATEGORY_HINTS = [
    ("welfare_card", ["บัตรสวัสดิการ", "บัตรคนจน", "ผู้มีรายได้น้อย"]),
    ("elderly", ["เบี้ยยังชีพ", "ผู้สูงอายุ", "เบี้ยผู้สูงอายุ"]),
    ("disability", ["คนพิการ", "เบี้ยความพิการ", "ผู้พิการ"]),
    ("children", ["เงินอุดหนุนบุตร", "เงินอุดหนุนเด็ก", "เด็กแรกเกิด"]),
    ("debt", ["หนี้นอกระบบ", "หนี้ครัวเรือน", "พักหนี้", "แก้หนี้"]),
    ("housing", ["การเคหะ", "ที่อยู่อาศัย", "บ้านเพื่อคนไทย", "ที่ดินทำกิน"]),
    ("health", ["บัตรทอง", "สปสช", "ประกันสังคม", "สิทธิรักษา", "30 บาท"]),
    ("cost_of_living", ["ค่าครองชีพ", "ราคาน้ำมัน", "ค่าไฟ", "ราคาสินค้า", "พลังงาน"]),
    ("employment", ["ค่าแรงขั้นต่ำ", "แรงงาน", "ค่าจ้าง", "ตกงาน", "จ้างงาน"]),
    ("farmers", ["เกษตรกร", "ชาวนา", "ชาวประมง", "ไร่ละ", "ประกันรายได้"]),
    ("cash_transfer", ["เงินดิจิทัล", "ดิจิทัลวอลเล็ต", "คนละครึ่ง", "เราชนะ", "แจกเงิน"]),
    ("education", ["กยศ", "ทุนการศึกษา", "เรียนฟรี"]),
    ("disaster", ["เยียวยา", "น้ำท่วม", "ภัยพิบัติ", "ภัยแล้ง", "วาตภัย"]),
]


def _normalize_source(name: str) -> str:
    return re.sub(r"\s+", " ", (name or "")).strip().lower()


def is_blocked(item: dict, cfg) -> bool:
    """True = เป็นแหล่งข่าวที่เจ้าของโปรเจกต์สั่งไม่ให้แสดง"""
    url = canonical_url(item.get("url", ""))
    host = host_of(url)

    for domain in cfg.get("exclude_domains", []) or []:
        d = str(domain).lower().strip()
        if d and (host == d or host.endswith("." + d)):
            return True

    for pattern in cfg.get("exclude_url_patterns", []) or []:
        if pattern and fnmatch.fnmatch(url, str(pattern)):
            return True

    source = _normalize_source(item.get("source", ""))
    if source:
        for bad in cfg.get("exclude_sources", []) or []:
            b = _normalize_source(str(bad))
            if b and b in source:
                return True
    return False


def keyword_score(text: str, keywords: dict) -> tuple[int, bool]:
    """คืน (คะแนน, เจอคำแรง) — คำแรง 3 คะแนน กลาง 2 อ่อน 1"""
    low = (text or "").lower()
    score = 0
    strong_hit = False
    for group, weight in WEIGHTS.items():
        for word in keywords.get(group, []) or []:
            w = str(word).lower().strip()
            if w and w in low:
                score += weight
                if group == "strong":
                    strong_hit = True
    return score, strong_hit


def has_excluded(text: str, keywords: dict) -> bool:
    low = (text or "").lower()
    return any(str(w).lower().strip() in low
               for w in (keywords.get("exclude") or []) if str(w).strip())


def find_provinces(text: str) -> list[str]:
    """หาชื่อจังหวัดที่ปรากฏในข้อความ ถ้าไม่เจอเลยถือว่าเป็นข่าวทั่วประเทศ"""
    found = [p for p in PROVINCES if p in (text or "")]
    return found or [NATIONWIDE]


def guess_category(text: str) -> str:
    low = (text or "").lower()
    for key, hints in CATEGORY_HINTS:
        if any(h.lower() in low for h in hints):
            return key
    return "other"


def prefilter(items: list[dict], cfg) -> tuple[list[dict], int]:
    """คัดกรองและเรียงลำดับ คืน (รายการที่ผ่าน, จำนวนที่ถูกบัญชีดำตัด)"""
    keywords = cfg.keywords
    blocked = 0
    kept: list[dict] = []

    for item in items:
        if is_blocked(item, cfg):
            blocked += 1
            continue
        text = f"{item.get('title', '')} {item.get('snippet', '')}"
        score, strong = keyword_score(text, keywords)
        if has_excluded(text, keywords) and not strong:
            continue
        if item.get("regional"):
            score += REGIONAL_BONUS
        if score < cfg.min_prefilter_score:
            continue
        item["prefilter_score"] = score
        kept.append(item)

    kept.sort(key=lambda it: (-it["prefilter_score"], str(it.get("published_at") or "")))
    return kept[: cfg.max_ai_items], blocked
