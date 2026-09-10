"""เขียนไฟล์ผลลัพธ์ทั้งหมดลงโฟลเดอร์ data/"""
from __future__ import annotations

import csv
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import DATA_DIR

SEEN_FILE = "_seen.json"          # ทะเบียนรหัสข่าว -> วันแรกที่เจอ (ใช้ทำป้าย "ตามต่อจาก")
INDEX_FILE = "index.json"
LAST_RUN_FILE = "last_run.json"
CSV_DAILY = "stats-daily.csv"
CSV_SUMMARY = "stats-summary.csv"

DAILY_HEADER = ["วันที่", "ครั้งที่ค้นหา", "ข่าวดิบที่อ่าน", "ข่าวที่คัดมาแสดง",
                "ข่าวใหม่", "ข่าวตามต่อ", "ภูมิภาค", "ทั่วประเทศ", "จำนวนแหล่งข่าว",
                "ถูกตัดตามบัญชีดำ", "token เข้า", "token ออก", "ค่าใช้จ่ายโดยประมาณ (บาท)"]
SUMMARY_HEADER = ["เก็บข้อมูลตั้งแต่", "จำนวนวัน", "ครั้งที่ค้นหาทั้งหมด", "ข่าวดิบที่อ่านทั้งหมด",
                  "ข่าวที่แสดงทั้งหมด", "ข่าวไม่ซ้ำ", "ภูมิภาค", "ค่าใช้จ่ายสะสม (บาท)",
                  "รันสำเร็จติดต่อกัน (วัน)", "รันไม่สำเร็จครั้งล่าสุด"]


def _dump(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def _load(path: Path, default):
    if not path.exists():
        return default
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError):
        return default


def _day_files(data_dir: Path) -> list[Path]:
    return sorted(p for p in data_dir.glob("*.json")
                  if len(p.stem) == 10 and p.stem[4] == "-" and p.stem[7] == "-")


# ---------- ทะเบียนข่าวที่เคยเจอ ----------
def load_seen(data_dir: Path) -> dict:
    return _load(data_dir / SEEN_FILE, {}) or {}


SEEN_RETENTION_DAYS = 400   # เก็บทะเบียนข่าวไว้ราว 1 ปี เพื่อให้นับ "ข่าวไม่ซ้ำ" ได้ตลอดปี


def update_seen(seen: dict, items: list[dict], date: str, lookback_days: int = 7) -> dict:
    """บันทึกวันแรกที่เจอข่าวแต่ละชิ้น และตัดรายการที่เก่ากว่า 400 วันทิ้งกันไฟล์บวม"""
    for item in items:
        seen.setdefault(item["id"], date)
    cutoff = (datetime.fromisoformat(date) - timedelta(days=SEEN_RETENTION_DAYS)).date().isoformat()
    return {k: v for k, v in seen.items() if v >= cutoff}


def apply_first_seen(items: list[dict], seen: dict, date: str) -> list[dict]:
    """ใส่ first_seen เฉพาะข่าวที่เคยเจอในวันก่อน ๆ (ห้ามใส่ null)"""
    out = []
    for item in items:
        row = dict(item)
        first = seen.get(item["id"])
        if first and first != date:
            row["first_seen"] = first
        else:
            row.pop("first_seen", None)
        out.append(row)
    return out


# ---------- ไฟล์ข่าวรายวัน ----------
PUBLIC_FIELDS = ("id", "title", "summary", "url", "source", "published_at",
                 "category", "provinces", "eastern", "score", "why", "first_seen")


def _public(item: dict) -> dict:
    return {k: item[k] for k in PUBLIC_FIELDS if k in item and item[k] is not None}


def write_day(date: str, items: list[dict], stats: dict, window: dict,
              generated_at: str, data_dir: Path = DATA_DIR) -> dict:
    """เขียน data/YYYY-MM-DD.json — ถ้ามีไฟล์เดิมอยู่จะรวมกัน (รันซ้ำได้ไม่พัง)"""
    path = data_dir / f"{date}.json"
    existing = _load(path, None)

    merged: dict[str, dict] = {}
    runs = 0
    if isinstance(existing, dict):
        runs = int((existing.get("stats") or {}).get("runs", 0) or 0)
        for old in existing.get("items", []) or []:
            if old.get("id"):
                merged[old["id"]] = old
    for item in items:                       # ผลใหม่ทับผลเก่าเสมอ
        merged[item["id"]] = _public(item)

    # เรียงคะแนนมากไปน้อย ถ้าคะแนนเท่ากันให้ข่าวใหม่กว่าอยู่บน
    # (Python เรียงแบบเสถียร จึงเรียงเวลาก่อน แล้วค่อยเรียงคะแนนทับ)
    rows = sorted(merged.values(), key=lambda it: str(it.get("published_at") or ""), reverse=True)
    rows.sort(key=lambda it: -int(it.get("score") or 0))

    eastern = sum(1 for r in rows if r.get("eastern"))
    followups = sum(1 for r in rows if r.get("first_seen"))
    full_stats = {
        **stats,
        "runs": runs + 1,
        "kept": len(rows),
        "eastern": eastern,
        "national": len(rows) - eastern,
        "new": len(rows) - followups,
        "followups": followups,
        "sources": len({r.get("source") for r in rows if r.get("source")}),
    }
    _dump(path, {"date": date, "generated_at": generated_at, "window": window,
                 "stats": full_stats, "items": rows})
    return full_stats


# ---------- สารบัญและยอดสะสม ----------
def rebuild_index(generated_at: str, seen: dict, data_dir: Path = DATA_DIR) -> dict:
    """คำนวณสารบัญและยอดสะสมใหม่จากไฟล์จริงทุกครั้ง — ตัวเลขจึงซ่อมตัวเองได้"""
    days, totals = [], {
        "fetched": 0, "kept": 0, "eastern": 0, "runs": 0,
        "ai_input_tokens": 0, "ai_output_tokens": 0, "cost_thb": 0.0,
    }
    cost_known = False

    for path in _day_files(data_dir):
        day = _load(path, None)
        if not isinstance(day, dict):
            continue
        stats = day.get("stats") or {}
        items = day.get("items") or []
        eastern = int(stats.get("eastern") or sum(1 for i in items if i.get("eastern")))
        followups = int(stats.get("followups") or sum(1 for i in items if i.get("first_seen")))
        days.append({
            "date": day.get("date", path.stem),
            "total": len(items),
            "eastern": eastern,
            "runs": int(stats.get("runs") or 1),
            "fetched": int(stats.get("fetched") or 0),
            "new": len(items) - followups,
            "followups": followups,
        })
        totals["fetched"] += int(stats.get("fetched") or 0)
        totals["kept"] += len(items)
        totals["eastern"] += eastern
        totals["runs"] += int(stats.get("runs") or 1)
        totals["ai_input_tokens"] += int(stats.get("ai_input_tokens") or 0)
        totals["ai_output_tokens"] += int(stats.get("ai_output_tokens") or 0)
        if stats.get("cost_thb") is not None:
            totals["cost_thb"] += float(stats.get("cost_thb") or 0)
            cost_known = True

    days.sort(key=lambda d: d["date"], reverse=True)
    dates = [d["date"] for d in days]

    previous = _load(data_dir / INDEX_FILE, {}) or {}
    prev_totals = previous.get("totals") or {}
    totals.update({
        "since": dates[-1] if dates else None,
        "days": len(days),
        "unique_items": len(seen),
        "cost_thb": round(totals["cost_thb"], 2) if cost_known else None,
        "last_success": dates[0] if dates else None,
        "success_streak_days": _streak(dates),
        "last_failure": prev_totals.get("last_failure"),
    })
    index = {"generated_at": generated_at, "totals": totals, "days": days}
    _dump(data_dir / INDEX_FILE, index)
    return index


def _streak(dates_desc: list[str]) -> int:
    """นับว่ามีข้อมูลต่อเนื่องกันกี่วันนับจากวันล่าสุดย้อนลงมา"""
    if not dates_desc:
        return 0
    streak, cursor = 1, datetime.fromisoformat(dates_desc[0]).date()
    for value in dates_desc[1:]:
        day = datetime.fromisoformat(value).date()
        if (cursor - day).days == 1:
            streak += 1
            cursor = day
        else:
            break
    return streak


# ---------- ไฟล์ CSV สำหรับเปิดด้วย Excel ----------
def _csv(path: Path, header: list[str], rows: list[list]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # utf-8-sig = ใส่ BOM ถ้าไม่ใส่ Excel บน Windows จะแสดงภาษาไทยเป็นตัวขยะ
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(header)
        writer.writerows(rows)


def _cell(value):
    return "-" if value is None else value


def write_csv(index: dict, data_dir: Path = DATA_DIR) -> None:
    rows = []
    for day in sorted(index.get("days", []), key=lambda d: d["date"]):
        stats = (_load(data_dir / f"{day['date']}.json", {}) or {}).get("stats") or {}
        rows.append([
            day["date"], day.get("runs", 1), day.get("fetched", 0), day.get("total", 0),
            day.get("new", 0), day.get("followups", 0), day.get("eastern", 0),
            day.get("total", 0) - day.get("eastern", 0),
            _cell(stats.get("sources")), _cell(stats.get("excluded_by_blocklist")),
            _cell(stats.get("ai_input_tokens")), _cell(stats.get("ai_output_tokens")),
            _cell(stats.get("cost_thb")),
        ])
    _csv(data_dir / CSV_DAILY, DAILY_HEADER, rows)

    t = index.get("totals") or {}
    _csv(data_dir / CSV_SUMMARY, SUMMARY_HEADER, [[
        _cell(t.get("since")), t.get("days", 0), t.get("runs", 0), t.get("fetched", 0),
        t.get("kept", 0), t.get("unique_items", 0), t.get("eastern", 0),
        _cell(t.get("cost_thb")), t.get("success_streak_days", 0),
        _cell(t.get("last_failure")),
    ]])


def write_last_run(payload: dict, data_dir: Path = DATA_DIR) -> None:
    _dump(data_dir / LAST_RUN_FILE, payload)


def save_seen(seen: dict, data_dir: Path = DATA_DIR) -> None:
    _dump(data_dir / SEEN_FILE, seen)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
