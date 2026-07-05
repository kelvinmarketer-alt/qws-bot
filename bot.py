#!/usr/bin/env python3
"""
Quang Workspace — bot nhắc Telegram.
Đọc workspace trên Supabase (đăng nhập bằng email/mật khẩu của app, RLS chỉ thấy
dòng của chính mình) rồi gửi Telegram:
  - Lịch chuyển quỹ ĐẾN HẠN (chế độ nhắc + xác nhận: bot chỉ nhắc, KHÔNG tự chuyển)
  - Việc hôm nay / việc quá hạn
  - Sự kiện LỊCH ÂM (giỗ/sinh nhật/kỷ niệm/nhắc việc) thêm trong app — nhắc trước N ngày
Chạy hằng ngày qua GitHub Actions (07:00 giờ VN). Không gửi gì nếu không có gì đến hạn.
"""
import os, sys, json, calendar, datetime, urllib.request
from lunar import solar2lunar, lunar2solar

SUPABASE_URL = os.environ.get("SUPABASE_URL") or "https://dbfffwtnxhytcoczhxhf.supabase.co"
ANON = os.environ.get("SUPABASE_ANON_KEY") or "sb_publishable_TaKPhmv9_ig8Z7rl-PZupw_AnzYwFQo"
EMAIL = os.environ["QWS_EMAIL"]
PASSWORD = os.environ["QWS_PASSWORD"]
TG_TOKEN = os.environ["TELEGRAM_TOKEN"]
TG_CHAT = os.environ["TELEGRAM_CHAT_ID"]
APP_URL = os.environ.get("APP_URL") or "https://quang-workspace.pages.dev"


def http(url, headers=None, data=None):
    h = {"Content-Type": "application/json"}
    if headers:
        h.update(headers)
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, headers=h, method="POST" if data is not None else "GET")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def vnd(n):
    return f"{int(n or 0):,}".replace(",", ".") + "đ"


def add_every(d, every):
    if every == "week":
        return d + datetime.timedelta(days=7)
    if every == "month":
        m = d.month + 1
        y = d.year + (m - 1) // 12
        m = (m - 1) % 12 + 1
        return datetime.date(y, m, min(d.day, calendar.monthrange(y, m)[1]))
    return d + datetime.timedelta(days=14)  # 2week (mặc định)


def pending_occs(sc, today_iso):
    """Các kỳ đã đến hạn (<= hôm nay) chưa xử lý (sau lastDone)."""
    if sc.get("active") is False or not sc.get("startDate"):
        return []
    last = sc.get("lastDone") or ""
    try:
        d = datetime.date.fromisoformat(str(sc["startDate"])[:10])
    except Exception:
        return []
    out, guard = [], 0
    while guard < 400:
        guard += 1
        iso = d.isoformat()
        if iso > today_iso:
            break
        if iso > last:
            out.append(iso)
        d = add_every(d, sc.get("every", "2week"))
    return out


# ===== SỰ KIỆN LỊCH ÂM (family) — khớp logic upcomingEvents trong app =====
CAT_ICON = {"giỗ": "🕯️", "sinh nhật": "🎂", "kỷ niệm": "💗", "lễ": "🎉", "khác": "🔔"}


def norm_ev(f):
    remind = f.get("remindBefore")
    if not isinstance(remind, list) or not remind:
        remind = [7, 3, 1, 0]
    base = f.get("baseYear")
    return {
        "title": (f.get("title") or "").strip(),
        "cal": "am" if f.get("calendar") == "am" else "duong",
        "day": int(f.get("day") or 0),
        "month": int(f.get("month") or 0),
        "repeat": f.get("repeat") or "year",
        "remind": [int(x) for x in remind if isinstance(x, (int, float))],
        "baseYear": int(base) if base else None,
        "done": f.get("done") is True,
        "category": f.get("category") or "giỗ",
    }


def _occ_solar(ev, year):
    if ev["cal"] == "am":
        d, m, y = lunar2solar(ev["day"], ev["month"], year)
        if not d:
            return None
        return datetime.date(y, m, d)
    try:
        return datetime.date(year, ev["month"], ev["day"])
    except ValueError:
        return None


def next_occ(ev, today):
    rp = ev["repeat"]
    if rp == "once":
        return _occ_solar(ev, ev["baseYear"] or today.year)
    if rp == "month":  # lặp mỗi tháng (âm hoặc dương)
        for i in range(0, 62):
            d = today + datetime.timedelta(days=i)
            if ev["cal"] == "am":
                if solar2lunar(d.day, d.month, d.year)[0] == ev["day"]:
                    return d
            elif d.day == ev["day"]:
                return d
        return None
    for y in (today.year, today.year + 1):  # hằng năm
        o = _occ_solar(ev, y)
        if o and o >= today:
            return o
    return None


def collect_events(family, today):
    out = []
    for f in family or []:
        ev = norm_ev(f)
        if not ev["title"] or ev["day"] <= 0:
            continue
        o = next_occ(ev, today)
        if not o:
            continue
        du = (o - today).days
        if ev["repeat"] == "once" and (ev["done"] or o < today):
            continue
        if du >= 0 and du in ev["remind"]:  # đúng ngày cần nhắc (giống willRemind trong app)
            out.append((ev, o, du))
    out.sort(key=lambda x: x[2])
    return out


def main():
    # ngày theo giờ VN (CI chạy UTC)
    today = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=7)).date()
    today_iso = today.isoformat()

    # 1) đăng nhập Supabase
    auth = http(f"{SUPABASE_URL}/auth/v1/token?grant_type=password",
                {"apikey": ANON}, {"email": EMAIL, "password": PASSWORD})
    token, uid = auth["access_token"], auth["user"]["id"]

    # 2) lấy workspace của user
    rows = http(f"{SUPABASE_URL}/rest/v1/qws_workspaces?user_id=eq.{uid}&select=data",
                {"apikey": ANON, "Authorization": f"Bearer {token}"})
    data = (rows[0]["data"] if rows else {}) or {}

    funds = data.get("funds", []) or []
    schedules = data.get("fundSchedules", []) or []
    tasks = data.get("tasks", []) or []
    family = data.get("family", []) or []
    name_of = {f.get("id"): f.get("name", "quỹ") for f in funds}

    # lịch chuyển đến hạn
    due = []
    for sc in schedules:
        occs = pending_occs(sc, today_iso)
        if occs:
            due.append((sc, occs))

    # việc hôm nay / quá hạn
    today_tasks = [t for t in tasks if t.get("date") == today_iso and t.get("status") != "done"]
    overdue = [t for t in tasks if t.get("date") and t.get("date") < today_iso and t.get("status") != "done"]

    # sự kiện lịch âm cần nhắc hôm nay
    events = collect_events(family, today)

    lines = []
    if due:
        lines.append("💸 <b>Lịch chuyển quỹ đến hạn</b>")
        for sc, occs in due:
            frm = name_of.get(sc.get("fromId"), "?")
            to = name_of.get(sc.get("toId"), "?")
            extra = f" · +{len(occs) - 1} kỳ quá hạn" if len(occs) > 1 else ""
            lines.append(f"• {frm} → {to}: {vnd(sc.get('amount'))}{extra}")
        lines.append("→ Mở app bấm <b>Chuyển ngay</b> để duyệt.")
    if today_tasks:
        lines.append("")
        lines.append("✅ <b>Việc hôm nay</b>")
        for t in today_tasks[:10]:
            lines.append(f"• {t.get('title', '')}" + (f" — {t['time']}" if t.get("time") else ""))
    if overdue:
        lines.append("")
        lines.append("⚠️ <b>Việc quá hạn</b>")
        for t in overdue[:10]:
            lines.append(f"• {t.get('title', '')} ({t.get('date')})")
    if events:
        lines.append("")
        lines.append("📅 <b>Lịch âm · Sự kiện</b>")
        for ev, o, du in events[:12]:
            ld = solar2lunar(o.day, o.month, o.year)
            when = "hôm nay" if du == 0 else f"còn {du} ngày"
            icon = CAT_ICON.get(ev["category"], "🔔")
            yrs = ""
            if ev["baseYear"] and ev["repeat"] != "once":
                nyr = o.year - ev["baseYear"]
                yrs = f" · {nyr} tuổi" if ev["category"] == "sinh nhật" else f" · {nyr} năm"
            lines.append(f"{icon} {ev['title']} — {o.day}/{o.month} (ÂL {ld[0]}/{ld[1]}) · {when}{yrs}")

    if not lines:
        print("Không có gì đến hạn hôm nay — không gửi.")
        return

    msg = f"📊 <b>Quang Workspace</b> · {today.strftime('%d/%m/%Y')}\n" + "\n".join(lines)
    msg += f"\n\n🔗 {APP_URL}"

    http(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage", None,
         {"chat_id": TG_CHAT, "text": msg, "parse_mode": "HTML", "disable_web_page_preview": True})
    print("Đã gửi Telegram.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("LỖI:", repr(e), file=sys.stderr)
        sys.exit(1)
