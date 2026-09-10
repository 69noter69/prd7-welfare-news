# ข่าวสวัสดิการประชาชน · สปข.7

เว็บ static (ไม่มีเซิร์ฟเวอร์) แสดงข่าวสวัสดิการที่ระบบเก็บอัตโนมัติทุกวัน 08:00 น. (เวลาไทย)
โฮสต์บน GitHub Pages — หน้าเว็บอ่านไฟล์ JSON ใน `data/` ที่ workflow เขียนไว้

## ไฟล์ในโปรเจกต์

```
index.html              หน้าเดียวจบ
assets/style.css        สไตล์ (light/dark)
assets/app.js           ตรรกะทั้งหมด — ค่าที่แก้บ่อยอยู่บนสุดของไฟล์
assets/icon*.png|svg    ไอคอน PWA
manifest.webmanifest    ติดตั้งเป็นแอปบนมือถือได้
data/index.json         สารบัญวัน — ตอนนี้ว่าง รอ workflow รันครั้งแรก
data/YYYY-MM-DD.json    ข่าวของแต่ละวัน (workflow สร้างเอง)
robots.txt, .nojekyll   ให้ Pages เสิร์ฟไฟล์ตรง ๆ ไม่ผ่าน Jekyll
```

## ติดตั้งบน GitHub Pages

1. push ไฟล์ทั้งหมดขึ้น repo (ให้ `index.html` อยู่ราก)
2. Settings → Pages → Source: **Deploy from a branch** → branch `main`, folder `/ (root)`
3. เปิด `https://<owner>.github.io/<repo>/` — จะเห็นหน้า “ยังไม่มีข่าวในระบบ”
4. รัน workflow เก็บข่าวครั้งแรก (จากหน้า Actions หรือกดปุ่มในเว็บ) แล้วรีเฟรช

## ปุ่ม “ค้นหาข่าวเดี๋ยวนี้”

เว็บเป็น static จึงสั่งงานผ่าน GitHub API (`workflow_dispatch`) โดยตรง แล้วคอยเช็ก `data/index.json`
ทุก 15 วินาที จนกว่า `generated_at` เปลี่ยน (หมดเวลาที่ 10 นาที)

- ค่า owner/repo เดาจาก URL `<owner>.github.io/<repo>/` โดยอัตโนมัติ
  ถ้าย้ายไป custom domain ให้กรอก `GITHUB_REPO` ใน `assets/app.js` เอง
- ชื่อไฟล์ workflow ตั้งไว้ที่ `collect.yml` — แก้ได้ที่ค่าเดียวกัน
- ผู้ใช้ต้องใส่ **fine-grained token** (สิทธิ์ Actions: Read and write ของ repo นี้) ครั้งเดียว
  token เก็บใน localStorage ของเบราว์เซอร์เครื่องนั้น และส่งไปที่ github.com เท่านั้น
  ไม่ต้องใส่ token ก็ได้ — กด “เปิดหน้า Actions แทน” เพื่อไปกด Run workflow เอง

## ค่าที่แก้ได้ (บนสุดของ `assets/app.js`)

| ค่า | ความหมาย |
| --- | --- |
| `GITHUB_REPO` | owner / repo / ชื่อไฟล์ workflow / branch |
| `REFRESH_TIMEOUT_S` | รอผลรันนานสุดกี่วินาที (600) |
| `ARCHIVE_DAYS` | หน้าค้นย้อนหลังค้นกี่วัน (30) |
| `STALE_HOURS` | เกินกี่ชั่วโมงถือว่าข้อมูลค้าง แล้วขึ้นแถบเตือน (26) |
| `EASTERN_PROVINCES` | จังหวัดที่นับเป็น “ข่าวในภูมิภาค” |
| `CATEGORIES` | รหัสหมวด → ชื่อไทย (สีอยู่ใน `style.css` ใช้รหัสเดียวกัน) |

## รูปแบบข้อมูลที่ workflow ต้องเขียน

`data/index.json`

```json
{
  "generated_at": "2026-09-07T01:07:42Z",
  "days": [{ "date": "2026-09-07", "total": 8, "eastern": 3, "national": 5 }]
}
```

`days` เรียงใหม่สุดก่อน · `generated_at` ต้องเปลี่ยนทุกครั้งที่รันเสร็จ (เว็บใช้ค่านี้จับว่ารันใหม่แล้ว)

`data/YYYY-MM-DD.json`

```json
{
  "date": "2026-09-07",
  "generated_at": "2026-09-07T01:07:42Z",
  "items": [{
    "id": "…", "title": "…", "summary": "…", "url": "https://…",
    "source": "…", "published_at": "2026-09-07T06:10:00+07:00",
    "category": "welfare_card", "provinces": ["จันทบุรี"],
    "eastern": true, "score": 78, "why": "…",
    "first_seen": "2026-09-05"
  }]
}
```

`first_seen` ใส่เฉพาะข่าวที่เคยขึ้นในวันก่อน — เว็บจะติดป้าย “ตามต่อจาก …” และมีตัวเลือกซ่อนข่าวเหล่านี้
`category` ต้องเป็นรหัสใน `CATEGORIES` (ไม่รู้จักจะแสดงเป็น “อื่น ๆ”)
