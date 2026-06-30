#!/usr/bin/env python3
"""
Quang Workspace — bot nhắc Telegram.
Đọc workspace trên Supabase (đăng nhập bằng email/mật khẩu của app, RLS chỉ thấy
dòng của chính mình) rồi gửi Telegram:
  - Lịch chuyển quỹ ĐẾN HẠN (chế độ nhắc + xác nhận: bot chỉ nhắc, KHÔNG tự chuyển)
  - Việc hôm nay / việc quá hạn
Chạy hằng ngày qua GitHub Actions (07:00 giờ VN). Không gửi gì nếu không có gì đến hạn.
"""
import os, sys, json, calendar, datetime, urllib.request

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
