"""จุดเริ่มต้นของโปรแกรม — python -m collector.main run"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone

from . import classify, config, prefilter, sources, writer
from .config import DATA_DIR
from .dedupe import dedupe, url_id

BANGKOK = timezone(timedelta(hours=7))


def _bangkok_date(moment: datetime) -> str:
    return moment.astimezone(BANGKOK).date().isoformat()


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def run(cfg, date: str | None, no_ai: bool, log=print) -> int:
    now = datetime.now(timezone.utc)
    target_date = date or _bangkok_date(now)
    window_from = now - timedelta(hours=cfg.window_hours)
    window = {"from": _iso(window_from), "to": _iso(now)}

    log(f"=== เก็บข่าววันที่ {target_date} (ย้อนหลัง {cfg.window_hours} ชม.) ===")

    key = config.api_key()
    if not no_ai and not key:
        log("ไม่พบ ANTHROPIC_API_KEY — ตั้งค่าใน Settings > Secrets ของ repo")
        log("หรือรันด้วย --no-ai เพื่อทดสอบโดยไม่ใช้ AI")
        return 2

    log("[1/5] กำลังอ่านแหล่งข่าว...")
    raw, errors = fetch_or_empty(cfg, now, window_from, log)
    log(f"      ได้ข่าวดิบ {len(raw)} ชิ้น (แหล่งที่มีปัญหา {len(errors)})")

    log("[2/5] ตัดข่าวซ้ำ...")
    for item in raw:
        item["id"] = url_id(item["url"])
    unique = dedupe(raw)
    log(f"      เหลือ {len(unique)} ชิ้น")

    log("[3/5] คัดกรองด้วยคำสำคัญ...")
    candidates, blocked = prefilter.prefilter(unique, cfg)
    log(f"      ส่งต่อ {len(candidates)} ชิ้น (ถูกบัญชีดำตัด {blocked})")

    in_tok = out_tok = 0
    if no_ai:
        log("[4/5] โหมดไม่ใช้ AI — ใช้คำสำคัญล้วน")
        scored = classify.classify_offline(candidates)
    else:
        log(f"[4/5] ให้ AI อ่านและสรุป ({cfg.model})...")
        scored, in_tok, out_tok = classify.classify(candidates, cfg, key, log)

    kept = []
    for item in scored:
        text = f"{item.get('title', '')} {item.get('snippet', '')}"
        provinces = item.get("provinces") or []
        eastern = any(p in cfg.eastern for p in provinces) or any(p in text for p in cfg.eastern)
        item["eastern"] = eastern
        floor = cfg.keep_min_score_eastern if eastern else cfg.keep_min_score
        if item.get("relevant", True) and int(item.get("score") or 0) >= floor:
            kept.append(item)
    log(f"      ผ่านเกณฑ์ {len(kept)} ชิ้น")

    log("[5/5] เขียนไฟล์...")
    seen = writer.load_seen(DATA_DIR)
    kept = writer.apply_first_seen(kept, seen, target_date)
    seen = writer.update_seen(seen, kept, target_date, cfg.lookback_days)
    writer.save_seen(seen, DATA_DIR)

    generated_at = writer.utc_now_iso()
    stats = writer.write_day(target_date, kept, {
        "fetched": len(raw),
        "excluded_by_blocklist": blocked,
        "ai_input_tokens": in_tok,
        "ai_output_tokens": out_tok,
        "cost_thb": cfg.cost_thb(in_tok, out_tok),
    }, window, generated_at, DATA_DIR)

    index = writer.rebuild_index(generated_at, seen, DATA_DIR)
    writer.write_csv(index, DATA_DIR)
    writer.write_last_run({
        "ok": True, "date": target_date, "fetched": len(raw), "kept": stats["kept"],
        "eastern": stats["eastern"], "runs_today": stats["runs"],
        "ai_input_tokens": in_tok, "ai_output_tokens": out_tok,
        "cost_thb": stats.get("cost_thb"), "generated_at": generated_at,
        "errors": errors[:40],
    }, DATA_DIR)

    cost = stats.get("cost_thb")
    log(f"\nเสร็จแล้ว: แสดง {stats['kept']} ข่าว (ภูมิภาค {stats['eastern']})"
        + (f" · ค่าใช้จ่ายวันนี้ ~{cost} บาท" if cost is not None else ""))
    return 0


def fetch_or_empty(cfg, now, window_from, log):
    try:
        return sources.fetch_all(cfg, now, window_from, log)
    except Exception as exc:                # noqa: BLE001
        log(f"      ! อ่านแหล่งข่าวไม่สำเร็จทั้งหมด: {type(exc).__name__}")
        return [], [f"fetch_all: {type(exc).__name__}"]


def backfill(cfg, days: int, no_ai: bool, log=print) -> int:
    today = datetime.now(timezone.utc).astimezone(BANGKOK).date()
    for offset in range(days - 1, -1, -1):
        target = (today - timedelta(days=offset)).isoformat()
        log(f"\n----- ย้อนหลังวันที่ {target} -----")
        code = run(cfg, target, no_ai, log)
        if code != 0:
            return code
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="collector", description="ระบบเก็บข่าวสวัสดิการ สปข.7")
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="เก็บข่าวประจำวัน")
    p_run.add_argument("--date", help="วันที่ต้องการ (YYYY-MM-DD)")
    p_run.add_argument("--no-ai", action="store_true", help="ไม่เรียก AI ใช้คำสำคัญล้วน")

    p_back = sub.add_parser("backfill", help="เก็บย้อนหลังหลายวัน")
    p_back.add_argument("--days", type=int, default=3)
    p_back.add_argument("--no-ai", action="store_true")

    sub.add_parser("check-feeds", help="ทดสอบว่าแหล่งข่าวไหนใช้ได้")

    args = parser.parse_args(argv)
    cfg = config.load()

    if args.command == "check-feeds":
        sources.check_feeds(cfg)
        return 0
    if args.command == "backfill":
        return backfill(cfg, max(1, args.days), args.no_ai)
    return run(cfg, args.date, args.no_ai)


if __name__ == "__main__":
    sys.exit(main())
