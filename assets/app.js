/* ข่าวสวัสดิการประชาชน — front-end logic (vanilla JS, no build step).
 * Everything a maintainer is likely to change lives in the constants below. */
'use strict';

// ===================== Editable constants =====================
const DATA_DIR = './data/';          // relative: the site lives under /<repo>/ on GitHub Pages
// "ค้นหาข่าวเดี๋ยวนี้" asks GitHub to run the collector workflow right now (workflow_dispatch).
// Leave owner/name empty to auto-detect from the GitHub Pages URL (<owner>.github.io/<name>/);
// set them explicitly if the site is served from a custom domain.
const GITHUB_REPO = { owner: '', name: '', workflow: 'collect.yml', branch: 'main' };
const REFRESH_TIMEOUT_S = 600;       // stop waiting after this long (the run keeps going on GitHub)
const GITHUB_TOKEN_KEY = 'prd7.githubToken';   // localStorage key; each person pastes their own token once
const ARCHIVE_DAYS = 30;             // how many days the "ค้นย้อนหลัง" view searches
const STALE_HOURS = 26;              // show the yellow warning if newest data is older than this
const TIME_ZONE = 'Asia/Bangkok';
const NATIONWIDE = 'ทั่วประเทศ';

// สปข.7 coverage area — items mentioning any of these provinces go to the regional tab
const EASTERN_PROVINCES = ['จันทบุรี', 'ตราด', 'ระยอง', 'ชลบุรี', 'ฉะเชิงเทรา', 'ปราจีนบุรี', 'สระแก้ว', 'นครนายก'];

// Category key -> Thai label. Colors are defined in style.css by the same key.
const CATEGORIES = {
  welfare_card:   'บัตรสวัสดิการแห่งรัฐ',
  elderly:        'เบี้ยยังชีพ / ผู้สูงอายุ',
  disability:     'คนพิการ',
  children:       'เด็ก / เงินอุดหนุนบุตร',
  debt:           'หนี้สิน / หนี้นอกระบบ',
  housing:        'ที่อยู่อาศัย / ที่ดินทำกิน',
  health:         'สิทธิรักษาพยาบาล / บัตรทอง / ประกันสังคม',
  cost_of_living: 'ค่าครองชีพ / ราคาสินค้า / พลังงาน',
  employment:     'อาชีพ / แรงงาน / ค่าแรง',
  farmers:        'เกษตรกร / ประมง',
  cash_transfer:  'เงินช่วยเหลือ / เงินดิจิทัล / มาตรการกระตุ้น',
  education:      'ทุนการศึกษา / กยศ.',
  disaster:       'ภัยพิบัติ / เยียวยา',
  other:          'อื่น ๆ',
};
// ==============================================================

const state = {
  index: null,        // contents of data/index.json
  date: null,         // selected YYYY-MM-DD
  day: null,          // contents of data/<date>.json
  tab: 'region',      // 'region' | 'national'
  view: 'digest',     // 'digest' | 'archive'
  filters: { q: '', cats: new Set(), province: '', source: '', sort: 'score', hideSeen: false },
  archive: { loaded: new Map(), loading: false, q: '' },
  running: false,     // true while a "ค้นหาข่าวเดี๋ยวนี้" run is in flight
};
const cache = new Map(); // filename -> parsed JSON (avoids refetching when switching days)

// ---------- Small helpers ----------
const $ = (id) => document.getElementById(id);
const catLabel = (key) => CATEGORIES[key] || CATEGORIES.other;
const catKey = (key) => (CATEGORIES[key] ? key : 'other');

/** Safe DOM builder: children are always inserted as text nodes, never HTML. */
function h(tag, attrs, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === 'class') el.className = v;
    else if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2).toLowerCase(), v);
    else if (k === 'dataset') Object.assign(el.dataset, v);
    else if (v === true) el.setAttribute(k, '');
    else el.setAttribute(k, String(v));
  }
  for (const c of children.flat(Infinity)) {
    if (c === null || c === undefined || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}
function clear(el) { while (el.firstChild) el.removeChild(el.firstChild); }

// ---------- Date / time formatting (always Asia/Bangkok, Buddhist year) ----------
const dtf = (opts) => new Intl.DateTimeFormat('th-TH', { timeZone: TIME_ZONE, calendar: 'buddhist', ...opts });
/** 'YYYY-MM-DD' -> Date at noon Bangkok time (safe for formatting in the same zone). */
const dateOnly = (s) => new Date(`${s}T12:00:00+07:00`);
const parts = (fmt, d) => Object.fromEntries(fmt.formatToParts(d).map((p) => [p.type, p.value]));

const fmt = {
  /** ศุกร์ 4 กันยายน 2569 */
  longDate(s) {
    const p = parts(dtf({ weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' }), dateOnly(s));
    return `${p.weekday.replace(/^วัน/, '')} ${p.day} ${p.month} ${p.year}`;
  },
  /** 4 ก.ย. 2569 */
  shortDate(s) {
    const p = parts(dtf({ day: 'numeric', month: 'short', year: 'numeric' }), dateOnly(s));
    return `${p.day} ${p.month} ${p.year}`;
  },
  /** 21:20 */
  time(iso) {
    const p = parts(dtf({ hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }), new Date(iso));
    return `${p.hour}:${p.minute}`;
  },
  /** Date -> 'YYYY-MM-DD' in Bangkok. */
  bkkDate(d) {
    const p = parts(new Intl.DateTimeFormat('en-CA', { timeZone: TIME_ZONE, year: 'numeric', month: '2-digit', day: '2-digit' }), d);
    return `${p.year}-${p.month}-${p.day}`;
  },
  /** "วันนี้ 06:10 น." / "เมื่อวาน 21:20 น." / "2 ก.ย. 2569 10:15 น." relative to the selected day. */
  relTime(iso, relativeTo) {
    const pub = fmt.bkkDate(new Date(iso));
    const yesterday = fmt.bkkDate(new Date(dateOnly(relativeTo).getTime() - 86400000));
    const label = pub === relativeTo ? 'วันนี้' : pub === yesterday ? 'เมื่อวาน' : fmt.shortDate(pub);
    return `${label} ${fmt.time(iso)} น.`;
  },
};

// ---------- Data loading ----------
async function loadJSON(name) {
  if (cache.has(name)) return cache.get(name);
  const res = await fetch(`${DATA_DIR}${name}`, { cache: 'no-cache' });
  if (!res.ok) throw new Error(`${name}: HTTP ${res.status}`);
  const json = await res.json();
  cache.set(name, json);
  return json;
}

// ---------- URL hash: #YYYY-MM-DD/region | #YYYY-MM-DD/national | #archive ----------
function parseHash() {
  const hash = location.hash.replace(/^#/, '');
  if (hash === 'archive') return { view: 'archive' };
  const m = hash.match(/^(\d{4}-\d{2}-\d{2})(?:\/(region|national))?$/);
  return m ? { view: 'digest', date: m[1], tab: m[2] || null } : { view: 'digest' };
}
function writeHash() {
  const next = state.view === 'archive' ? '#archive' : `#${state.date}/${state.tab}`;
  if (location.hash !== next) history.replaceState(null, '', next);
}

// ---------- Derived data ----------
const regionItems = () => (state.day ? state.day.items.filter((it) => it.eastern) : []);
const nationalItems = () => (state.day ? state.day.items.filter((it) => !it.eastern) : []);
const activeItems = () => (state.tab === 'region' ? regionItems() : nationalItems());

function applyFilters(items) {
  const f = state.filters;
  const q = f.q.trim().toLowerCase();
  const out = items.filter((it) =>
    (f.cats.size === 0 || f.cats.has(it.category)) &&
    (!f.province || (it.provinces || []).includes(f.province)) &&
    (!f.source || it.source === f.source) &&
    (!q || [it.title, it.summary, it.source, ...(it.provinces || [])].join(' ').toLowerCase().includes(q)) &&
    (!f.hideSeen || !isFollowUp(it)));
  out.sort(f.sort === 'latest'
    ? (a, b) => String(b.published_at).localeCompare(String(a.published_at))
    : (a, b) => (b.score - a.score) || String(b.published_at).localeCompare(String(a.published_at)));
  return out;
}
const hasActiveFilters = () => {
  const f = state.filters;
  return Boolean(f.q.trim() || f.cats.size || f.province || f.source || f.sort !== 'score' || f.hideSeen);
};
/** True when the backend says this story was already collected on an earlier day. */
const isFollowUp = (it) => Boolean(it.first_seen && it.first_seen !== state.date);
const anyFollowUps = () => (state.day ? state.day.items.some(isFollowUp) : false);

// ---------- "คุณกำลังดูข่าวย้อนหลัง" strip ----------
/* A bookmarked URL keeps its #YYYY-MM-DD hash, so reopening it lands on an old
   day even when newer data exists. We still honour the hash (shared links must
   work) but say so plainly and offer one click back to the latest day. */
function olderDayStrip() {
  let el = $('older-strip');
  if (!el) {
    el = h('div', { class: 'wrap', id: 'older-strip', hidden: true },
      h('div', { class: 'warn', role: 'status' },
        h('span', { id: 'older-msg' }, ''), ' ',
        h('button', {
          type: 'button', class: 'link', id: 'btn-go-latest',
          onClick: () => { const d = state.index.days[0]; if (d) loadDay(d.date, state.tab); },
        }, 'ไปที่ข่าวล่าสุด')));
    const stale = $('stale-strip');
    stale.parentNode.insertBefore(el, stale);
  }
  return el;
}
function renderOlderStrip() {
  const strip = olderDayStrip();
  const days = state.index.days;
  const newest = days[0] && days[0].date;
  const isOlder = Boolean(newest && state.date && state.date !== newest);
  strip.hidden = !isOlder;
  if (isOlder) {
    $('older-msg').textContent =
      `คุณกำลังดูข่าวย้อนหลังของ ${fmt.shortDate(state.date)} — ข่าวล่าสุดคือ ${fmt.shortDate(newest)} (${days[0].total} ข่าว)`;
  }
}

// ---------- Rendering: header / date bar ----------
function renderDateBar() {
  const days = state.index.days;
  const i = days.findIndex((d) => d.date === state.date);
  $('current-date').textContent = fmt.longDate(state.date);

  const gen = state.day && state.day.generated_at;
  if (gen) {
    const sameDay = fmt.bkkDate(new Date(gen)) === state.date;
    $('updated-at').textContent = sameDay
      ? `อัปเดตล่าสุด ${fmt.time(gen)} น.`
      : `อัปเดตล่าสุด ${fmt.shortDate(fmt.bkkDate(new Date(gen)))} ${fmt.time(gen)} น.`;
  } else $('updated-at').textContent = '';

  const sel = $('date-select');
  clear(sel);
  days.forEach((d) => sel.append(h('option', { value: d.date, selected: d.date === state.date }, `${fmt.shortDate(d.date)} (${d.total} ข่าว)`)));
  $('btn-prev').disabled = i < 0 || i >= days.length - 1; // days sorted newest first
  $('btn-next').disabled = i <= 0;

  // stale warning: newest run older than STALE_HOURS
  renderOlderStrip();
  if (!days.length) { $('stale-strip').hidden = true; return; }
  const newest = state.index.generated_at || (days[0] && days[0].date && `${days[0].date}T01:00:00Z`);
  const stale = newest && (Date.now() - new Date(newest).getTime()) > STALE_HOURS * 3600000;
  $('stale-strip').hidden = !stale;
}

// ---------- Rendering: summary strip ----------
function renderSummary() {
  const root = $('summary');
  clear(root);
  const items = state.day.items;
  const sources = new Set(items.map((it) => it.source)).size;
  const tile = (num, label) => h('div', { class: 'tile' }, h('div', { class: 'tile__num' }, num), h('div', { class: 'tile__label' }, label));
  root.append(tile(items.length, 'ข่าวทั้งหมด'), tile(regionItems().length, 'ภาคตะวันออก'), tile(sources, 'แหล่งข่าว'));

  const counts = countBy(items, (it) => catKey(it.category));
  const top = Object.entries(counts).sort((a, b) => b[1] - a[1]).slice(0, 3);
  if (top.length) {
    root.append(h('div', { class: 'tile tile--cats' },
      h('div', { class: 'tile__label' }, 'หมวดเด่นวันนี้'),
      h('div', { class: 'chips' }, top.map(([key, n]) => h('button', {
        type: 'button', class: 'chip chip--cat', 'data-cat': key,
        'aria-pressed': String(state.filters.cats.has(key)),
        onClick: () => { toggleCategory(key, true); },
      }, catLabel(key), h('span', { class: 'chip__n' }, String(n)))))));
  }
}
function countBy(list, fn) {
  return list.reduce((acc, x) => { const k = fn(x); acc[k] = (acc[k] || 0) + 1; return acc; }, {});
}

// ---------- Rendering: tabs ----------
function renderTabs() {
  const r = regionItems().length, n = nationalItems().length;
  setTabLabel($('tab-region'), 'ข่าวในภูมิภาค', r);
  setTabLabel($('tab-national'), 'ข่าวทั่วประเทศ', n);
  $('tab-region').setAttribute('aria-selected', String(state.tab === 'region'));
  $('tab-national').setAttribute('aria-selected', String(state.tab === 'national'));
  $('panel-region').hidden = state.tab !== 'region';
  $('panel-national').hidden = state.tab !== 'national';
  $('count-region').textContent = String(r);
  $('count-national').textContent = String(n);
}
function setTabLabel(btn, text, n) {
  clear(btn);
  btn.append(text, ' ', h('span', { class: 'count' }, String(n)));
}

// ---------- Rendering: filter controls ----------
function renderFilterOptions() {
  const items = state.day.items;
  const fillSelect = (sel, allLabel, values, current) => {
    clear(sel);
    sel.append(h('option', { value: '' }, allLabel));
    values.forEach((v) => sel.append(h('option', { value: v, selected: v === current }, v)));
    if (current && !values.includes(current)) sel.value = '';
  };
  const provinces = [...new Set(items.flatMap((it) => it.provinces || []))]
    .sort((a, b) => (a === NATIONWIDE ? -1 : b === NATIONWIDE ? 1 : a.localeCompare(b, 'th')));
  fillSelect($('f-province'), 'ทุกจังหวัด', provinces, state.filters.province);
  if (!provinces.includes(state.filters.province)) state.filters.province = '';
  const sources = [...new Set(items.map((it) => it.source))].sort((a, b) => a.localeCompare(b, 'th'));
  fillSelect($('f-source'), 'ทุกแหล่งข่าว', sources, state.filters.source);
  if (!sources.includes(state.filters.source)) state.filters.source = '';
  $('f-sort').value = state.filters.sort;
  $('f-search').value = state.filters.q;
}
function renderCategoryChips() {
  const root = $('f-cats');
  clear(root);
  const counts = countBy(state.day.items, (it) => catKey(it.category));
  root.append(h('button', {
    type: 'button', class: 'chip', 'aria-pressed': String(state.filters.cats.size === 0),
    onClick: () => { state.filters.cats.clear(); refresh(); },
  }, 'ทุกหมวด'));
  Object.keys(CATEGORIES).filter((k) => counts[k]).forEach((key) => root.append(h('button', {
    type: 'button', class: 'chip chip--cat', 'data-cat': key, 'aria-pressed': String(state.filters.cats.has(key)),
    onClick: () => toggleCategory(key, false),
  }, catLabel(key), h('span', { class: 'chip__n' }, String(counts[key])))));
}
function toggleCategory(key, exclusive) {
  const cats = state.filters.cats;
  if (exclusive) { const only = cats.size === 1 && cats.has(key); cats.clear(); if (!only) cats.add(key); }
  else if (cats.has(key)) cats.delete(key); else cats.add(key);
  refresh();
}
function renderRegionProvinceChips() {
  const root = $('region-provinces');
  clear(root);
  const counts = countBy(regionItems().flatMap((it) => (it.provinces || []).filter((p) => EASTERN_PROVINCES.includes(p))), (p) => p);
  EASTERN_PROVINCES.forEach((p) => root.append(h('button', {
    type: 'button', class: 'chip', 'aria-pressed': String(state.filters.province === p),
    onClick: () => { state.filters.province = state.filters.province === p ? '' : p; refresh(); },
  }, p, h('span', { class: 'chip__n' }, String(counts[p] || 0)))));
}
function renderFilterMeta() {
  const all = activeItems().length;
  const shown = applyFilters(activeItems()).length;
  $('result-count').textContent = `แสดง ${shown} จาก ${all} ข่าว`;
  $('btn-clear-filters').hidden = !hasActiveFilters();
  const seenWrap = $('f-hide-seen-wrap');
  if (seenWrap) seenWrap.hidden = !anyFollowUps();
  const n = (state.filters.q.trim() ? 1 : 0) + state.filters.cats.size + (state.filters.province ? 1 : 0) + (state.filters.source ? 1 : 0) + (state.filters.hideSeen ? 1 : 0);
  $('filters-badge').hidden = n === 0;
  $('filters-badge').textContent = String(n);
}

// ---------- Rendering: lists & cards ----------
function renderLists() {
  renderList($('list-region'), applyFilters(regionItems()), regionItems().length, true);
  renderList($('list-national'), applyFilters(nationalItems()), nationalItems().length, false);
}
function renderList(root, items, totalInTab, isRegion) {
  clear(root);
  if (totalInTab === 0 && isRegion) {
    root.append(h('div', { class: 'empty' },
      'วันนี้ยังไม่พบข่าวในพื้นที่ภาคตะวันออก — ',
      h('button', { type: 'button', class: 'link', onClick: () => setTab('national') }, 'ดูข่าวทั่วประเทศแทน')));
    return;
  }
  if (totalInTab === 0) { root.append(h('div', { class: 'empty' }, 'วันนี้ไม่มีข่าวทั่วประเทศ')); return; }
  if (items.length === 0) {
    root.append(h('div', { class: 'empty' }, 'ไม่พบข่าวที่ตรงกับตัวกรอง',
      h('button', { type: 'button', class: 'btn btn--sm', onClick: clearFilters }, 'ล้างตัวกรอง')));
    return;
  }
  items.forEach((it) => root.append(renderCard(it, { region: isRegion, relativeTo: state.date })));
}

function renderCard(it, { region = false, relativeTo }) {
  const provinces = (it.provinces || []).map((p) => h('span', { class: 'tag' + (EASTERN_PROVINCES.includes(p) ? ' tag--east' : '') }, p));
  const provWrap = provinces.length ? h('span', { class: 'card__provs' }, provinces) : null;
  const badge = h('span', { class: 'badge', 'data-cat': catKey(it.category) }, catLabel(it.category));
  const followUp = it.first_seen && it.first_seen !== relativeTo
    ? h('span', { class: 'tag tag--seen', title: 'ระบบเคยเก็บข่าวนี้ตั้งแต่ ' + fmt.shortDate(it.first_seen) }, 'ตามต่อจาก ' + fmt.shortDate(it.first_seen))
    : null;
  const meta = h('div', { class: 'card__meta' },
    region ? [provWrap, badge] : [badge, provWrap],
    h('span', { class: 'dot' }), h('span', null, it.source),
    h('span', { class: 'dot' }), h('span', null, fmt.relTime(it.published_at, relativeTo)),
    followUp);

  const title = h('h3', { class: 'card__title clamp-2' }, h('a', { href: it.url, target: '_blank', rel: 'noopener noreferrer' }, it.title));
  const summary = h('p', { class: 'card__summary clamp-3' }, it.summary);
  const why = it.why ? h('p', { class: 'card__why' }, `เหตุผลจากระบบ: ${it.why}`) : null;
  const more = h('button', { type: 'button', class: 'card__more' }, 'อ่านเพิ่ม');
  const card = h('article', { class: 'card' + (region ? ' card--region' : '') });
  more.addEventListener('click', () => {
    const open = card.classList.toggle('card--expanded');
    more.textContent = open ? 'ย่อ' : 'อ่านเพิ่ม';
  });

  const dots = h('span', { class: 'score', title: `คะแนน ${it.score}/100${it.why ? ' · ' + it.why : ''}`, 'aria-label': `ความน่าสนใจ ${it.score} จาก 100` });
  const filled = Math.max(1, Math.min(5, Math.ceil((Number(it.score) || 0) / 20)));
  for (let i = 0; i < 5; i++) dots.append(h('i', { class: i < filled ? 'on' : '' }));

  const actions = h('div', { class: 'card__actions' },
    h('a', { class: 'btn btn--sm btn--primary', href: it.url, target: '_blank', rel: 'noopener noreferrer' }, 'อ่านต้นฉบับ ↗'),
    h('button', { type: 'button', class: 'btn btn--sm', onClick: () => copyText(`${it.title}\n${it.summary}\n${it.url}`) }, 'คัดลอก'));

  card.append(meta, title, summary, why, more, h('div', { class: 'card__foot' }, dots, actions));
  // Hide "อ่านเพิ่ม" when nothing is actually clamped
  requestAnimationFrame(() => {
    if (summary.scrollHeight <= summary.clientHeight + 2 && title.scrollHeight <= title.clientHeight + 2 && !it.why) more.hidden = true;
  });
  return card;
}

function renderSkeleton(root, n) {
  clear(root);
  for (let i = 0; i < n; i++) root.append(h('div', { class: 'skeleton', 'aria-hidden': 'true' }));
}
function renderError(root, retry) {
  clear(root);
  root.append(h('div', { class: 'error' }, 'โหลดข้อมูลไม่สำเร็จ', h('button', { type: 'button', class: 'btn btn--sm', onClick: retry }, 'ลองอีกครั้ง')));
}

// ---------- Orchestration ----------
/** Re-render everything that depends on filters/tab (cheap, day data already loaded). */
function refresh() {
  if (!state.day) return;
  renderTabs();
  renderCategoryChips();
  renderRegionProvinceChips();
  renderSummary();
  renderFilterMeta();
  $('f-province').value = state.filters.province;
  renderLists();
  writeHash();
}
function setTab(tab) {
  state.tab = tab; refresh();
  // On phones the tabs are sticky; bring the list top into view after switching
  if (window.matchMedia('(max-width: 759px)').matches) {
    const top = $('filters').getBoundingClientRect().top + window.scrollY - 56;
    if (window.scrollY > top) window.scrollTo({ top, behavior: 'smooth' });
  }
}
function clearFilters() {
  state.filters = { q: '', cats: new Set(), province: '', source: '', sort: 'score', hideSeen: false };
  $('f-search').value = ''; $('f-province').value = ''; $('f-source').value = ''; $('f-sort').value = 'score';
  const hs = $('f-hide-seen'); if (hs) hs.checked = false;
  refresh();
}

async function loadDay(date, explicitTab) {
  state.date = date;
  state.day = null;
  renderDateBar();
  renderSkeleton($('list-region'), 2);
  renderSkeleton($('list-national'), 4);
  clear($('summary'));
  try {
    state.day = await loadJSON(`${date}.json`);
  } catch (err) {
    console.error(err);
    const retry = () => loadDay(date, explicitTab);
    renderError($('list-region'), retry);
    renderError($('list-national'), retry);
    return;
  }
  state.tab = explicitTab || (regionItems().length > 0 ? 'region' : 'national');
  renderDateBar();
  renderFilterOptions();
  refresh();
}

function stepDay(delta) {
  const days = state.index.days;
  const i = days.findIndex((d) => d.date === state.date);
  const next = days[i + delta];
  if (next) loadDay(next.date, state.tab);
}

// ---------- Copy / digest ----------
async function copyText(text, msg = 'คัดลอกแล้ว') {
  try {
    if (navigator.clipboard && window.isSecureContext) await navigator.clipboard.writeText(text);
    else {
      const ta = h('textarea', { style: 'position:fixed;opacity:0', readonly: true });
      ta.value = text; document.body.append(ta); ta.select(); document.execCommand('copy'); ta.remove();
    }
    toast(msg);
  } catch (e) { console.error(e); toast('คัดลอกไม่สำเร็จ'); }
}
let toastTimer;
function toast(msg) {
  const el = $('toast');
  el.textContent = msg; el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, 1800);
}

function buildDigest() {
  const d = state.day;
  const lines = [
    `ข่าวสวัสดิการประชาชน — ${fmt.longDate(d.date)}`,
    `รวบรวม ${d.items.length} ข่าว (ภาคตะวันออก ${regionItems().length})`,
    '',
  ];
  let n = 1;
  const section = (title, items) => {
    lines.push(`■ ${title}`);
    if (!items.length) lines.push('   (ไม่มี)');
    items.forEach((it) => {
      lines.push(`${n++}. [${catLabel(it.category)}] ${it.title} (${it.source})`);
      lines.push(`   สรุป: ${it.summary}`);
      lines.push(`   จังหวัด: ${(it.provinces || []).join(', ') || NATIONWIDE}`);
      lines.push(`   ลิงก์: ${it.url}`);
      lines.push('');
    });
  };
  section('ข่าวในภูมิภาค (พื้นที่ สปข.7)', applyFilters(regionItems()));
  section('ข่าวทั่วประเทศ', applyFilters(nationalItems()));
  return lines.join('\n').trimEnd() + '\n';
}
function downloadDigest() {
  const blob = new Blob([buildDigest()], { type: 'text/plain;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = h('a', { href: url, download: `welfare-news-${state.date}.txt` });
  document.body.append(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

// ---------- Archive view (last ARCHIVE_DAYS days, loaded lazily) ----------
function archiveDays() {
  const days = state.index.days;
  if (!days.length) return [];
  const newest = dateOnly(days[0].date).getTime();
  return days.filter((d) => newest - dateOnly(d.date).getTime() < ARCHIVE_DAYS * 86400000).slice(0, ARCHIVE_DAYS);
}
async function openArchive() {
  state.view = 'archive';
  $('view-digest').hidden = true;
  $('view-archive').hidden = false;
  writeHash();
  const days = archiveDays();
  $('archive-range').textContent = days.length ? `${fmt.shortDate(days[days.length - 1].date)} – ${fmt.shortDate(days[0].date)} · ${days.length} วัน` : '';
  $('archive-search').focus();
  if (state.archive.loading || state.archive.loaded.size >= days.length) { renderArchive(); return; }
  state.archive.loading = true;
  let done = 0;
  const queue = days.filter((d) => !state.archive.loaded.has(d.date));
  const worker = async () => {
    while (queue.length) {
      const d = queue.shift();
      try { state.archive.loaded.set(d.date, await loadJSON(`${d.date}.json`)); }
      catch (e) { console.warn(e); state.archive.loaded.set(d.date, null); }
      done++;
      $('archive-progress').textContent = `กำลังโหลด ${done}/${queue.length + done} วัน…`;
      renderArchive();
    }
  };
  await Promise.all([worker(), worker(), worker(), worker()]);
  state.archive.loading = false;
  renderArchive();
}
function closeArchive() {
  state.view = 'digest';
  $('view-archive').hidden = true;
  $('view-digest').hidden = false;
  writeHash();
}
function renderArchive() {
  const root = $('archive-results');
  clear(root);
  const q = state.archive.q.trim().toLowerCase();
  const days = archiveDays();
  const loadedCount = days.filter((d) => state.archive.loaded.has(d.date)).length;
  if (!state.archive.loading) {
    $('archive-progress').textContent = `พร้อมค้นหา ${loadedCount} วัน · ${days.reduce((s, d) => s + (d.total || 0), 0)} ข่าว`;
  }
  if (!q) {
    root.append(h('div', { class: 'empty' }, 'พิมพ์คำค้นเพื่อค้นหาหัวข้อ สรุป จังหวัด หรือแหล่งข่าวย้อนหลัง'));
    return;
  }
  let total = 0;
  days.forEach((d) => {
    const day = state.archive.loaded.get(d.date);
    if (!day) return;
    const hits = day.items.filter((it) => [it.title, it.summary, it.source, ...(it.provinces || [])].join(' ').toLowerCase().includes(q));
    if (!hits.length) return;
    total += hits.length;
    root.append(h('h3', { class: 'archive__day' }, fmt.longDate(d.date), h('span', { class: 'count' }, String(hits.length))));
    root.append(h('div', { class: 'list' }, hits.map((it) => renderCard(it, { region: it.eastern, relativeTo: d.date }))));
  });
  if (!total) root.append(h('div', { class: 'empty' }, state.archive.loading ? 'ยังไม่พบ — กำลังโหลดข้อมูลเพิ่ม…' : 'ไม่พบข่าวที่ตรงกับคำค้นในช่วง 30 วัน'));
}

// ---------- "ค้นหาข่าวเดี๋ยวนี้": ask the backend for a fresh crawl, then wait for the new data ----------
function setRunning(on, msg) {
  state.running = on;
  const btn = $('btn-run-now');
  btn.disabled = on;
  btn.classList.toggle('btn--busy', on);
  $('btn-run-now-label').textContent = on ? 'กำลังค้นหา…' : 'ค้นหาข่าวเดี๋ยวนี้';
  $('run-strip').hidden = !on;
  if (msg) $('run-msg').textContent = msg;
}
/** index.json, always fresh (bypasses the in-page cache used elsewhere). */
async function fetchIndexFresh() {
  const res = await fetch(DATA_DIR + 'index.json?t=' + Date.now(), { cache: 'no-store' });
  if (!res.ok) throw new Error('index.json: HTTP ' + res.status);
  return res.json();
}
// --- GitHub helpers (the page is static: the only thing it can do is ask GitHub to start the run) ---
function ghRepo() {
  const r = { ...GITHUB_REPO };
  if (!r.owner || !r.name) {
    const m = location.hostname.match(/^([^.]+)\.github\.io$/);
    const seg = location.pathname.split('/').filter(Boolean)[0];
    if (m && seg) { r.owner = r.owner || m[1]; r.name = r.name || seg; }
  }
  return r;
}
const ghActionsUrl = () => { const r = ghRepo(); return r.owner ? `https://github.com/${r.owner}/${r.name}/actions/workflows/${r.workflow}` : 'https://github.com'; };
const ghGetToken = () => { try { return localStorage.getItem(GITHUB_TOKEN_KEY) || ''; } catch (e) { return ''; } };
const ghSetToken = (t) => { try { t ? localStorage.setItem(GITHUB_TOKEN_KEY, t) : localStorage.removeItem(GITHUB_TOKEN_KEY); } catch (e) { /* ignore */ } };

async function ghDispatch(token) {
  const r = ghRepo();
  if (!r.owner || !r.name) throw new Error('ไม่ทราบชื่อ repo — ตั้งค่า GITHUB_REPO ใน assets/app.js');
  const res = await fetch(`https://api.github.com/repos/${r.owner}/${r.name}/actions/workflows/${r.workflow}/dispatches`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${token}`, Accept: 'application/vnd.github+json', 'Content-Type': 'application/json', 'X-GitHub-Api-Version': '2022-11-28' },
    body: JSON.stringify({ ref: r.branch, inputs: {} }),
  });
  if (res.status === 204) return;
  if (res.status === 401 || res.status === 403) { ghSetToken(''); throw new Error('token ไม่ถูกต้องหรือไม่มีสิทธิ์ — กรุณาใส่ใหม่'); }
  if (res.status === 404) throw new Error('ไม่พบ repo/workflow หรือ token ไม่มีสิทธิ์เข้าถึง repo นี้');
  throw new Error('GitHub ตอบ ' + res.status);
}

/** Opens the token dialog; resolves with a token, or '' if cancelled. */
function askToken() {
  return new Promise((resolve) => {
    const dlg = $('run-dialog'), input = $('run-token');
    const has = !!ghGetToken();
    input.value = '';
    input.placeholder = has ? 'มี token บันทึกไว้แล้ว — เว้นว่างเพื่อใช้ token เดิม' : 'github_pat_…';
    input.required = !has;
    $('run-open-actions').href = ghActionsUrl();
    const done = (v) => { dlg.removeEventListener('close', onClose); resolve(v); };
    const onClose = () => done('');
    dlg.addEventListener('close', onClose);
    $('run-form').onsubmit = (ev) => { ev.preventDefault(); const t = input.value.trim() || ghGetToken(); dlg.removeEventListener('close', onClose); dlg.close(); resolve(t); };
    $('run-cancel').onclick = () => dlg.close();
    dlg.showModal();
  });
}

async function runNow() {
  if (state.running) return;
  const token = await askToken();
  if (!token) return;
  const before = (state.index && state.index.generated_at) || '';
  setRunning(true, 'ส่งคำสั่งให้ GitHub เริ่มค้นหาข่าว…');
  try {
    await ghDispatch(token);
    ghSetToken(token);
  } catch (err) {
    console.error(err);
    setRunning(false);
    toast(err.message || 'สั่งค้นหาข่าวไม่สำเร็จ — ลองอีกครั้ง');
    return;
  }
  // Poll index.json until the workflow commits a newer run (GitHub Pages republishes within ~1 min).
  const started = Date.now();
  let fresh = null;
  while (Date.now() - started < REFRESH_TIMEOUT_S * 1000) {
    const waited = Math.round((Date.now() - started) / 1000);
    setRunning(true, 'ระบบกำลังอ่านข่าวจากแหล่งต่าง ๆ… (' + waited + ' วิ) ปกติใช้เวลา 2–4 นาที');
    await new Promise((r) => setTimeout(r, 15000));
    try {
      const idx = await fetchIndexFresh();
      if ((idx.generated_at || '') !== before) { fresh = idx; break; }
    } catch (e) { console.warn(e); }
  }
  setRunning(false);
  if (!fresh) { toast('ระบบยังค้นหาไม่เสร็จ — รีเฟรชหน้าอีกสักครู่ หรือดูที่หน้า Actions'); return; }
  cache.clear();
  state.index = fresh;
  if (!Array.isArray(fresh.days) || !fresh.days.length) { toast('รันเสร็จแล้ว แต่ยังไม่พบข่าวที่เข้าเกณฑ์'); return; }
  hideFirstRun();
  await loadDay(fresh.days[0].date, state.tab);
  const total = (state.day && state.day.items.length) || 0;
  const fresh_n = state.day ? state.day.items.filter((it) => !isFollowUp(it)).length : 0;
  toast(fresh_n ? 'อัปเดตแล้ว — ข่าวใหม่ ' + fresh_n + ' จาก ' + total + ' ข่าว' : 'อัปเดตแล้ว — ยังไม่มีข่าวใหม่');
}

// ---------- Theme toggle (light/dark; follows the OS until the user picks) ----------
function currentTheme() {
  const set = document.documentElement.dataset.theme;
  if (set === 'light' || set === 'dark') return set;
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
}
function toggleTheme() {
  const next = currentTheme() === 'dark' ? 'light' : 'dark';
  document.documentElement.dataset.theme = next;
  try { localStorage.setItem('theme', next); } catch (e) { /* storage may be unavailable */ }
}

// ---------- Wiring ----------
function bind() {
  $('btn-theme').addEventListener('click', toggleTheme);
  $('btn-prev').addEventListener('click', () => stepDay(1));   // days are newest-first
  $('btn-next').addEventListener('click', () => stepDay(-1));
  $('date-select').addEventListener('change', (e) => loadDay(e.target.value, state.tab));
  $('tabs').addEventListener('click', (e) => { const b = e.target.closest('[data-tab]'); if (b) setTab(b.dataset.tab); });

  $('f-search').addEventListener('input', (e) => { state.filters.q = e.target.value; refresh(); });
  $('f-province').addEventListener('change', (e) => { state.filters.province = e.target.value; refresh(); });
  $('f-source').addEventListener('change', (e) => { state.filters.source = e.target.value; refresh(); });
  $('f-sort').addEventListener('change', (e) => { state.filters.sort = e.target.value; refresh(); });
  $('btn-clear-filters').addEventListener('click', clearFilters);
  $('filters-toggle').addEventListener('click', () => {
    const open = $('filters').getAttribute('aria-expanded') !== 'true';
    $('filters').setAttribute('aria-expanded', String(open));
    $('filters-toggle').setAttribute('aria-expanded', String(open));
  });

  $('btn-run-now').addEventListener('click', runNow);
  $('btn-first-run').addEventListener('click', runNow);
  $('btn-first-reload').addEventListener('click', () => location.reload());
  const hideSeenBox = $('f-hide-seen');
  if (hideSeenBox) hideSeenBox.addEventListener('change', (e) => { state.filters.hideSeen = e.target.checked; refresh(); });
  $('btn-copy-digest').addEventListener('click', () => { if (state.day) copyText(buildDigest(), 'คัดลอกสรุปแล้ว'); });
  if (navigator.share) {
    // Touch devices: long-press-free sharing via the native share sheet (LINE, Mail, etc.)
    const share = h('button', { type: 'button', class: 'btn', id: 'btn-share-digest' }, 'แชร์');
    $('btn-download-digest').after(share);
    share.addEventListener('click', async () => {
      if (!state.day) return;
      try { await navigator.share({ title: `ข่าวสวัสดิการประชาชน — ${fmt.longDate(state.date)}`, text: buildDigest() }); }
      catch (e) { if (e && e.name !== 'AbortError') copyText(buildDigest(), 'คัดลอกสรุปแล้ว'); }
    });
  }
  $('btn-download-digest').addEventListener('click', () => { if (state.day) downloadDigest(); });
  $('btn-archive').addEventListener('click', openArchive);
  $('btn-archive-back').addEventListener('click', closeArchive);
  $('archive-search').addEventListener('input', (e) => { state.archive.q = e.target.value; renderArchive(); });

  window.addEventListener('hashchange', () => {
    if (!state.index || !state.index.days.length) return;
    const hs = parseHash();
    if (hs.view === 'archive') { if (state.view !== 'archive') openArchive(); return; }
    if (state.view === 'archive') closeArchive();
    if (hs.date && hs.date !== state.date && state.index.days.some((d) => d.date === hs.date)) loadDay(hs.date, hs.tab);
    else if (hs.tab && hs.tab !== state.tab) setTab(hs.tab);
  });
}

/** No data yet (fresh install, before the first collector run). */
function showFirstRun() {
  state.date = null; state.day = null;
  $('view-digest').hidden = true;
  $('view-archive').hidden = true;
  $('view-empty').hidden = false;
  ['btn-copy-digest', 'btn-download-digest', 'btn-archive', 'btn-share-digest'].forEach((id) => {
    const el = $(id); if (el) el.disabled = true;
  });
}
function hideFirstRun() {
  $('view-empty').hidden = true;
  $('view-digest').hidden = false;
  ['btn-copy-digest', 'btn-download-digest', 'btn-archive', 'btn-share-digest'].forEach((id) => {
    const el = $(id); if (el) el.disabled = false;
  });
}

async function init() {
  bind();
  try {
    state.index = await loadJSON('index.json');
  } catch (err) {
    console.error(err);
    $('current-date').textContent = 'โหลดข้อมูลไม่สำเร็จ';
    renderError($('list-region'), () => location.reload());
    return;
  }
  if (!Array.isArray(state.index.days) || !state.index.days.length) { showFirstRun(); return; }
  hideFirstRun();
  const hs = parseHash();
  const newest = state.index.days[0].date;
  const known = hs.date && state.index.days.some((d) => d.date === hs.date);
  /* A known hash date is honoured so shared "link to this day" URLs keep working.
     When it is not the newest day the banner from renderOlderStrip() says so and
     offers one click to the latest — a stale bookmark must never look like an
     outage. An unknown or missing hash opens the newest day. */
  const date = known ? hs.date : newest;
  if (hs.view === 'archive') { state.date = date; openArchive(); await loadDay(date, null); }
  else await loadDay(date, hs.tab);
}

document.addEventListener('DOMContentLoaded', init);
