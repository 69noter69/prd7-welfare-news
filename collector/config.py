"""อ่านไฟล์ตั้งค่า config/sources.yaml และเก็บรายชื่อจังหวัด 77 จังหวัด"""
from __future__ import annotations

import os
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config" / "sources.yaml"
DATA_DIR = ROOT / "data"

CATEGORIES = [
    "welfare_card", "elderly", "disability", "children", "debt", "housing",
    "health", "cost_of_living", "employment", "farmers", "cash_transfer",
    "education", "disaster", "other",
]

NATIONWIDE = "ทั่วประเทศ"

PROVINCES = [
    "กรุงเทพมหานคร", "กระบี่", "กาญจนบุรี", "กาฬสินธุ์", "กำแพงเพชร", "ขอนแก่น",
    "จันทบุรี", "ฉะเชิงเทรา", "ชลบุรี", "ชัยนาท", "ชัยภูมิ", "ชุมพร", "เชียงราย",
    "เชียงใหม่", "ตรัง", "ตราด", "ตาก", "นครนายก", "นครปฐม", "นครพนม",
    "นครราชสีมา", "นครศรีธรรมราช", "นครสวรรค์", "นนทบุรี", "นราธิวาส", "น่าน",
    "บึงกาฬ", "บุรีรัมย์", "ปทุมธานี", "ประจวบคีรีขันธ์", "ปราจีนบุรี", "ปัตตานี",
    "พระนครศรีอยุธยา", "พังงา", "พัทลุง", "พิจิตร", "พิษณุโลก", "เพชรบุรี",
    "เพชรบูรณ์", "แพร่", "พะเยา", "ภูเก็ต", "มหาสารคาม", "มุกดาหาร", "แม่ฮ่องสอน",
    "ยะลา", "ยโสธร", "ร้อยเอ็ด", "ระนอง", "ระยอง", "ราชบุรี", "ลพบุรี", "ลำปาง",
    "ลำพูน", "เลย", "ศรีสะเกษ", "สกลนคร", "สงขลา", "สตูล", "สมุทรปราการ",
    "สมุทรสงคราม", "สมุทรสาคร", "สระแก้ว", "สระบุรี", "สิงห์บุรี", "สุโขทัย",
    "สุพรรณบุรี", "สุราษฎร์ธานี", "สุรินทร์", "หนองคาย", "หนองบัวลำภู", "อ่างทอง",
    "อำนาจเจริญ", "อุดรธานี", "อุตรดิตถ์", "อุทัยธานี", "อุบลราชธานี",
]


class Config:
    """ห่อ dict ที่อ่านจาก YAML ให้เรียกใช้ง่ายและมีค่าเริ่มต้นเสมอ"""

    def __init__(self, raw: dict):
        self.raw = raw or {}

    def get(self, key, default=None):
        value = self.raw.get(key)
        return default if value is None else value

    # --- ค่าที่ใช้บ่อย ---
    @property
    def window_hours(self) -> int: return int(self.get("window_hours", 26))
    @property
    def max_ai_items(self) -> int: return int(self.get("max_ai_items", 60))
    @property
    def min_prefilter_score(self) -> int: return int(self.get("min_prefilter_score", 2))
    @property
    def keep_min_score(self) -> int: return int(self.get("keep_min_score", 45))
    @property
    def keep_min_score_eastern(self) -> int: return int(self.get("keep_min_score_eastern", 25))
    @property
    def batch_size(self) -> int: return max(1, int(self.get("batch_size", 10)))
    @property
    def summary_words(self) -> str: return str(self.get("summary_words", "40-70"))
    @property
    def model(self) -> str: return str(self.get("model", "claude-haiku-4-5"))
    @property
    def lookback_days(self) -> int: return int(self.get("first_seen_lookback_days", 7))
    @property
    def eastern(self) -> list: return list(self.get("eastern_provinces", []))
    @property
    def keywords(self) -> dict: return dict(self.get("keywords", {}))
    @property
    def pricing(self) -> dict: return dict(self.get("pricing", {}))

    def cost_thb(self, in_tokens: int, out_tokens: int):
        """คืนค่าใช้จ่ายเป็นบาท หรือ None ถ้ายังไม่ได้ตั้งราคา"""
        p = self.pricing
        pin = float(p.get("input_per_mtok_usd", 0) or 0)
        pout = float(p.get("output_per_mtok_usd", 0) or 0)
        rate = float(p.get("usd_to_thb", 0) or 0)
        if pin <= 0 or pout <= 0 or rate <= 0:
            return None
        usd = in_tokens / 1_000_000 * pin + out_tokens / 1_000_000 * pout
        return round(usd * rate, 2)


def load(path: Path | None = None) -> Config:
    path = path or CONFIG_PATH
    if not path.exists():
        raise SystemExit(f"ไม่พบไฟล์ตั้งค่า: {path}")
    with open(path, encoding="utf-8") as fh:
        return Config(yaml.safe_load(fh))


def api_key() -> str | None:
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    return key or None
