"""ส่งข่าวให้ Claude Haiku อ่าน จัดหมวด ให้คะแนน และเขียนสรุปภาษาไทย"""
from __future__ import annotations

import json
import re

from .config import CATEGORIES, NATIONWIDE, PROVINCES
from .prefilter import guess_category, find_provinces

SYSTEM_PROMPT = """You are a news desk assistant for a Thai government public-relations office. You receive Thai news headlines with short snippets. For each item decide whether it is relevant to LOW-INCOME CITIZENS or STATE WELFARE / social assistance in Thailand, and produce a concise Thai summary for journalists.

Return ONLY a JSON array, one object per input item, same order, same "id". No prose, no markdown fences.

Each object:
{
  "id": "<same id>",
  "relevant": true|false,
  "score": 0-100,
  "category": "welfare_card|elderly|disability|children|debt|housing|health|cost_of_living|employment|farmers|cash_transfer|education|disaster|other",
  "provinces": ["<Thai province name>", ...] or ["ทั่วประเทศ"],
  "summary": "<SUMMARY_WORDS words in formal Thai, factual, no opinion, include who/what/when/how much if present>",
  "why": "<one short Thai phrase explaining the score>"
}

Scoring: 90+ major national policy or money actually reaching citizens; 70-89 a concrete measure, deadline, or clear regional impact; 40-69 related but minor, opinion, or a follow-up; below 40 not relevant.

Rules: base everything only on the given text; do not invent numbers or dates. Use official province names only, e.g. "จันทบุรี" not "จ.จันทบุรี". Lottery, entertainment, sports, and gold-price items are never relevant. If the snippet is empty, summarise from the title and lower the score by 10. Keep summaries tight — do not exceed the stated word range."""


def system_prompt(cfg) -> str:
    return SYSTEM_PROMPT.replace("SUMMARY_WORDS", cfg.summary_words)


def _extract_json_array(text: str):
    """ดึง JSON array ออกมาแม้โมเดลจะเผลอใส่ข้อความอื่นปนมา"""
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start, end = text.find("["), text.rfind("]")
    if start != -1 and end > start:
        return json.loads(text[start:end + 1])
    raise ValueError("ไม่พบ JSON array ในคำตอบของโมเดล")


def _clean_provinces(raw, fallback_text: str) -> list[str]:
    out = []
    for p in (raw or []):
        name = re.sub(r"^(จังหวัด|จ\.)\s*", "", str(p)).strip()
        if name == NATIONWIDE or name in PROVINCES:
            if name not in out:
                out.append(name)
    if not out:
        out = find_provinces(fallback_text)
    if len(out) > 1 and NATIONWIDE in out:
        out = [p for p in out if p != NATIONWIDE]
    return out or [NATIONWIDE]


def fallback_item(item: dict, why: str = "AI ไม่พร้อมใช้งาน") -> dict:
    """ถ้า AI ล่ม ยังต้องได้ข่าวออกมา แค่คุณภาพต่ำกว่า"""
    text = f"{item.get('title', '')} {item.get('snippet', '')}"
    return {
        "category": guess_category(text),
        "provinces": find_provinces(text),
        "summary": (item.get("snippet") or item.get("title") or "")[:300],
        "score": min(100, int(item.get("prefilter_score", 0)) * 10),
        "why": why,
        "relevant": True,
    }


def _batch_payload(batch: list[dict]) -> str:
    rows = [{"id": it["id"], "title": it["title"], "source": it["source"],
             "snippet": (it.get("snippet") or "")[:400],
             "published_at": it["published_at"]} for it in batch]
    return "Items:\n" + json.dumps(rows, ensure_ascii=False)


def classify(items: list[dict], cfg, api_key: str, log=print) -> tuple[list[dict], int, int]:
    """คืน (รายการที่ AI ประมวลผลแล้ว, token ขาเข้า, token ขาออก)"""
    if not items:
        return [], 0, 0

    from anthropic import Anthropic
    client = Anthropic(api_key=api_key)
    prompt = system_prompt(cfg)
    size = cfg.batch_size
    in_tokens = out_tokens = 0
    results: list[dict] = []

    for start in range(0, len(items), size):
        batch = items[start:start + size]
        by_id = {it["id"]: it for it in batch}
        parsed = None

        for attempt in range(2):
            try:
                res = client.messages.create(
                    model=cfg.model,
                    max_tokens=2500,
                    temperature=0,
                    system=prompt,
                    messages=[{"role": "user", "content": _batch_payload(batch)}],
                )
                in_tokens += getattr(res.usage, "input_tokens", 0) or 0
                out_tokens += getattr(res.usage, "output_tokens", 0) or 0
                parsed = _extract_json_array("".join(
                    b.text for b in res.content if getattr(b, "type", "") == "text"))
                break
            except Exception as exc:        # noqa: BLE001 — AI ล้มต้องไม่ทำให้ทั้งระบบล่ม
                log(f"  ! ชุดที่ {start // size + 1} ล้มเหลว ({type(exc).__name__})"
                    + (" — ลองใหม่" if attempt == 0 else " — ใช้โหมดสำรอง"))

        if parsed is None:
            for it in batch:
                results.append({**it, **fallback_item(it)})
            continue

        seen = set()
        for row in parsed:
            if not isinstance(row, dict):
                continue
            item = by_id.get(str(row.get("id", "")))
            if item is None:
                continue
            seen.add(item["id"])
            text = f"{item.get('title', '')} {item.get('snippet', '')}"
            category = str(row.get("category", "other"))
            results.append({
                **item,
                "category": category if category in CATEGORIES else "other",
                "provinces": _clean_provinces(row.get("provinces"), text),
                "summary": str(row.get("summary") or item.get("snippet") or "")[:600],
                "score": max(0, min(100, int(row.get("score") or 0))),
                "why": str(row.get("why") or "")[:200],
                "relevant": bool(row.get("relevant", True)),
            })
        for it in batch:                    # ข่าวที่โมเดลลืมตอบ
            if it["id"] not in seen:
                results.append({**it, **fallback_item(it, "โมเดลไม่ได้ตอบข่าวนี้")})

        log(f"  อ่านแล้ว {min(start + size, len(items))}/{len(items)} ข่าว")

    return results, in_tokens, out_tokens


def classify_offline(items: list[dict]) -> list[dict]:
    """โหมด --no-ai : ใช้คำสำคัญล้วน ไม่เรียก AI ไม่เสียเงิน"""
    return [{**it, **fallback_item(it, "โหมดไม่ใช้ AI")} for it in items]
