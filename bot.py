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


def _num(x):
    try:
        return float(x or 0)
    except Exception:
        return 0


def send_push(token, uid, title, body):
    """Gửi Web Push nền qua edge function qws-send-push tới các thiết bị của user."""
    try:
        r = http(f"{SUPABASE_URL}/functions/v1/qws-send-push",
                 {"apikey": ANON, "Authorization": f"Bearer {token}"},
                 {"user_id": uid, "title": title, "body": body, "url": APP_URL, "tag": "daily"})
        print("Push:", r)
    except Exception as e:
        print("Push lỗi:", repr(e), file=sys.stderr)


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


REN_DAYS = {14, 7, 3, 1, 0}  # nhắc trước 14/7/3/1 ngày + ngày đến hạn


def next_renewal(start_iso, recurring, today):
    """Ngày gia hạn KẾ TIẾP (>= hôm nay) của chi phí định kỳ."""
    try:
        d0 = datetime.date.fromisoformat(str(start_iso)[:10])
    except Exception:
        return None
    if recurring == "monthly":
        y, m = today.year, today.month
        for _ in range(3):
            cand = datetime.date(y, m, min(d0.day, calendar.monthrange(y, m)[1]))
            if cand >= today:
                return cand
            m += 1
            if m > 12:
                m = 1; y += 1
        return None
    if recurring == "yearly":
        for yy in (today.year, today.year + 1):
            cand = datetime.date(yy, d0.month, min(d0.day, calendar.monthrange(yy, d0.month)[1]))
            if cand >= today:
                return cand
        return None
    return None


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
    projects = data.get("projects", []) or []
    fund_tx = data.get("fundTx", []) or []
    expenses = data.get("expenses", []) or []
    name_of = {f.get("id"): f.get("name", "quỹ") for f in funds}

    # Gia hạn chi phí định kỳ (hằng tháng/năm) — nhắc trước 14/7/3/1 ngày + ngày đến hạn
    exp_due = []
    for e in expenses:
        rec = e.get("recurring")
        if rec not in ("monthly", "yearly") or e.get("active") is False:
            continue
        nr = next_renewal(e.get("date"), rec, today)
        if not nr:
            continue
        end = e.get("endDate")
        if end:
            try:
                if nr > datetime.date.fromisoformat(str(end)[:10]):
                    continue
            except Exception:
                pass
        du = (nr - today).days
        if du in REN_DAYS:
            exp_due.append((e, nr, du))
    exp_due.sort(key=lambda x: x[2])

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

    # thu / chi hôm nay (cho push cập nhật)
    thu_today = 0.0
    for p in projects:
        for i in (p.get("installments") or []):
            if str(i.get("date"))[:10] == today_iso:
                thu_today += _num(i.get("amount")) - _num(i.get("refund")) - _num(i.get("carry"))
    chi_today = sum(_num(t.get("amount")) for t in fund_tx
                    if t.get("type") == "out" and not t.get("xferId") and str(t.get("date"))[:10] == today_iso)

    # ===== WEB PUSH NỀN — gửi MỖI NGÀY (cập nhật sự kiện + thu/chi) =====
    pparts = []
    ev_today = [ev for ev, o, du in events if du == 0]
    if ev_today:
        pparts.append("📅 " + ", ".join(e["title"] for e in ev_today[:3]))
    if today_tasks:
        pparts.append(f"✅ {len(today_tasks)} việc hôm nay")
    if overdue:
        pparts.append(f"⚠️ {len(overdue)} việc quá hạn")
    if due:
        pparts.append(f"💸 {len(due)} chuyển quỹ đến hạn")
    if exp_due:
        pparts.append("🔁 " + ", ".join(f"{e.get('name','')} ({'hôm nay' if du == 0 else str(du) + 'n'})" for e, nr, du in exp_due[:3]))
    if thu_today or chi_today:
        pparts.append(f"💰 thu {vnd(thu_today)} · chi {vnd(chi_today)}")
    pbody = " · ".join(pparts) if pparts else "Chưa có nhắc nào — nhớ ghi thu/chi & sự kiện hôm nay 📝"
    send_push(token, uid, f"Quang Workspace · {today.strftime('%d/%m')}", pbody)

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
    if exp_due:
        lines.append("")
        lines.append("🔁 <b>Gia hạn sắp tới</b>")
        for e, nr, du in exp_due[:12]:
            when = "hôm nay" if du == 0 else f"còn {du} ngày"
            lines.append(f"• {e.get('name', '')} — {vnd(e.get('amount'))} · {nr.strftime('%d/%m/%Y')} ({when})")
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
