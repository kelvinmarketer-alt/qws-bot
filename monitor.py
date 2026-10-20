#!/usr/bin/env python3
"""
QWS monitor — canh bot giao dịch trên máy bàn (bảng Supabase qws_live_bot), chạy 10 phút/lần qua GitHub Actions:
  1) Mất liên lạc: status.at cũ hơn 8 phút → push cảnh báo (1 lần / sự cố); có tín hiệu lại → push "hoạt động lại".
  2) Cảnh báo giá: command.data.priceAlerts chưa bắn → lấy giá Binance → chạm ngưỡng thì push + ghi firedAt.
  3) Tổng kết ngày từ 22:00 giờ VN (lượt chạy đầu tiên trong 15:00–16:59 UTC — cron GitHub hay trễ; mỗi ngày 1 lần nhờ dailySummaryDate) từ history.trades của chế độ đang chạy.
Ghi dòng 'command': đọc lại bản MỚI NHẤT ngay trước khi ghi, chỉ sửa trường của monitor rồi upsert
(không làm mất keys/config/stop/cảnh báo giá mà app vừa thêm).
Repo PUBLIC → log Actions ai cũng xem được: chỉ in số đếm/trạng thái, KHÔNG in giá, vốn, lãi/lỗ, ghi chú.
Chỉ thư viện chuẩn Python. `python monitor.py --selftest` kiểm tra logic bằng dữ liệu giả, không gọi mạng.
"""
import os, sys, json, re, time, datetime, urllib.request, urllib.error, urllib.parse

# Giống bot.py (URL + anon key là khoá công khai)
SUPABASE_URL = os.environ.get("SUPABASE_URL") or "https://dbfffwtnxhytcoczhxhf.supabase.co"
ANON = os.environ.get("SUPABASE_ANON_KEY") or "sb_publishable_TaKPhmv9_ig8Z7rl-PZupw_AnzYwFQo"
TABLE = "qws_live_bot"

OFFLINE_MS = 8 * 60 * 1000          # status.at cũ hơn 8 phút = mất liên lạc
DAILY_UTC = (15, 0)                  # 15:00 UTC = 22:00 giờ VN
DAILY_WINDOW_MIN = 120               # gửi ở lượt chạy đầu tiên trong 15:00–16:59 UTC (cron GitHub có thể trễ hàng chục phút)
VN = datetime.timezone(datetime.timedelta(hours=7))
URL_LIVE, URL_MARKET = "/coin?tab=live", "/coin?tab=market"
UA = {"User-Agent": "qws-monitor/1.0"}

SPOT_HOSTS = ["https://api.binance.com", "https://data-api.binance.vision"]  # binance.vision: không chặn IP Mỹ (runner GitHub)
FUT_HOST = "https://fapi.binance.com"


# ---------- tiện ích ----------
def log(*a):
    print(*a, flush=True)


def short_err(e):
    if isinstance(e, urllib.error.HTTPError):
        try:
            txt = e.read().decode(errors="replace")[:160]
        except Exception:
            txt = ""
        return f"HTTP {e.code} {txt}".strip()
    return repr(e)[:200]


def http(url, headers=None, data=None, method=None, timeout=30):
    """Như http() của bot.py, thêm method + chấp nhận body rỗng (upsert/logout trả 201/204)."""
    h = {"Content-Type": "application/json", **UA}
    if headers:
        h.update(headers)
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, headers=h, method=method or ("POST" if data is not None else "GET"))
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read().decode()
    return json.loads(raw) if raw.strip() else None


def get_json(url, timeout=15):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
        return json.loads(r.read().decode())


def sb(token):
    return {"apikey": ANON, "Authorization": f"Bearer {token}"}


def to_ms(v):
    """Mốc thời gian → ms (nhận số ms/giây hoặc chuỗi ISO). Không hợp lệ → None."""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, str):
        try:
            v = float(v)
        except ValueError:
            try:
                return datetime.datetime.fromisoformat(v.replace("Z", "+00:00")).timestamp() * 1000
            except ValueError:
                return None
    if not isinstance(v, (int, float)) or v <= 0:
        return None
    return float(v) * 1000 if v < 1e11 else float(v)  # lỡ ghi giây


def vn_dt(ms):
    return datetime.datetime.fromtimestamp(ms / 1000, VN)


def hhmm(ms, now_ms):
    d = vn_dt(ms)
    return d.strftime("%H:%M") + ("" if d.date() == vn_dt(now_ms).date() else d.strftime(" %d/%m"))


def vi_fixed(x, dec):
    """Số kiểu vi-VN: 1234.5 → '1.234,50' (dec=2)."""
    v = round(float(x), dec)
    if v == 0:
        v = 0.0
    s = f"{abs(v):,.{dec}f}".replace(",", "_").replace(".", ",").replace("_", ".")
    return ("-" if v < 0 else "") + s


def fmt_price(p):
    """Giá kiểu vi-VN, bỏ số 0 thừa: 85000 → '85.000', 0.00001234 → '0,00001234'."""
    a = abs(float(p))
    dec = 2 if a >= 1000 else 4 if a >= 1 else 6 if a >= 0.01 else 10
    s = vi_fixed(p, dec)
    if "," in s:
        s = s.rstrip("0").rstrip(",")
    return s


def signed(x, dec):
    s = vi_fixed(x, dec)
    return s if s.startswith("-") or round(float(x), dec) == 0 else "+" + s


def num(x):
    try:
        v = float(x)
        return v if v == v else None  # bỏ NaN
    except (TypeError, ValueError):
        return None


# ---------- 1) mất liên lạc ----------
def decide_offline(status, cmd, now_ms):
    at = to_ms((status or {}).get("at"))
    if not at:
        return None
    off, on = to_ms(cmd.get("offlineAlertAt")), to_ms(cmd.get("onlineAlertAt"))
    if now_ms - at > OFFLINE_MS:
        if not off or off < at:  # chưa báo cho lần mất liên lạc này
            return {"kind": "offline", "tag": "live-status", "url": URL_LIVE,
                    "title": "⚠️ Bot giao dịch mất liên lạc",
                    "body": f"Không nhận tín hiệu từ máy bàn từ {hhmm(at, now_ms)} (giờ VN) — kiểm tra điện/mạng/máy. "
                            "Lệnh đang mở vẫn có SL/TP trên sàn.",
                    "set": {"offlineAlertAt": now_ms, "offlineSince": at}}
        return None
    if off and at > off and (not on or on < off):  # đã báo mất, nay có tín hiệu lại, chưa báo "hoạt động lại"
        since = to_ms(cmd.get("offlineSince"))
        extra = f" sau khi mất liên lạc từ {hhmm(since, now_ms)}" if since else ""
        return {"kind": "online", "tag": "live-status", "url": URL_LIVE,
                "title": "✅ Bot hoạt động lại",
                "body": f"Máy bàn đã gửi tín hiệu lại lúc {hhmm(at, now_ms)} (giờ VN){extra}. Kiểm tra lệnh đang mở trong app.",
                "set": {"onlineAlertAt": now_ms}}
    return None


# ---------- 2) cảnh báo giá ----------
OPS = {">=": ">=", "≥": ">=", ">": ">=", "<=": "<=", "≤": "<=", "<": "<="}


def norm_symbol(s):
    s = re.sub(r"[^A-Z0-9]", "", str(s or "").upper())
    if s and not re.search(r"(USDT|USDC|FDUSD|BUSD|TUSD)$", s) and not (len(s) > 4 and re.search(r"(BTC|ETH|BNB)$", s)):
        s += "USDT"  # "BTC" → "BTCUSDT"
    return s


def alert_key(a):
    return str(a.get("id") or f"{a.get('symbol')}|{a.get('market')}|{a.get('op')}|{a.get('price')}|{a.get('createdAt')}")


def pending_alerts(alerts):
    out = []
    for a in alerts if isinstance(alerts, list) else []:
        if not isinstance(a, dict) or a.get("firedAt"):
            continue
        op, target, sym = OPS.get(str(a.get("op", "")).strip()), num(a.get("price")), norm_symbol(a.get("symbol"))
        if not op or target is None or target <= 0 or not sym:
            continue
        out.append({"key": alert_key(a), "a": a, "op": op, "target": target, "symbol": sym,
                    "market": "spot" if a.get("market") == "spot" else "futures"})
    return out


def decide_price_alerts(pend, prices, now_ms):
    """prices: {(market, symbol): (giá, xấp_xỉ_bool)} → các cảnh báo chạm ngưỡng."""
    hits = []
    for p in pend:
        got = prices.get((p["market"], p["symbol"]))
        if not got:
            continue
        price, approx = got
        if not (price >= p["target"] if p["op"] == ">=" else price <= p["target"]):
            continue
        mk = "Spot" if p["market"] == "spot" else "Futures"
        note = str(p["a"].get("note") or "").strip()
        cur = f"Giá {mk} hiện {fmt_price(price)}" + (" (tham chiếu giá spot)" if approx else "")
        hits.append({"key": p["key"], "tag": f"price-{p['key']}"[:64], "url": URL_MARKET,
                     "title": f"🔔 {p['symbol']} {'≥' if p['op'] == '>=' else '≤'} {fmt_price(p['target'])}",
                     "body": f"{note} · {cur}" if note else cur,
                     "set": {"firedAt": now_ms, "firedPrice": price}})
    return hits


def spot_prices(symbols):
    """{symbol: giá} — gom 1 lần gọi; nguồn chính bị chặn (451/403) thì sang data-api.binance.vision."""
    syms, out = sorted(set(symbols)), {}
    if not syms:
        return out
    q = urllib.parse.quote
    for host in SPOT_HOSTS:
        base = f"{host}/api/v3/ticker/price"
        try:
            if len(syms) == 1:
                j = [get_json(f"{base}?symbol={q(syms[0])}")]
            else:
                j = get_json(f"{base}?symbols=" + q(json.dumps(syms, separators=(",", ":"))))
            return {x["symbol"]: float(x["price"]) for x in j}
        except urllib.error.HTTPError as e:
            if e.code == 400:  # có mã không tồn tại → cả lô hỏng → hỏi từng mã
                for s in syms if len(syms) > 1 else []:
                    try:
                        out[s] = float(get_json(f"{base}?symbol={q(s)}")["price"])
                    except Exception:
                        pass
                bad = len(syms) - len(out)
                if bad:
                    log(f"Giá spot: {bad} mã không tồn tại trên sàn")
                return out
            log(f"Giá spot: {host.split('//')[1]} {short_err(e)[:40]} → thử nguồn khác")
        except Exception as e:
            log(f"Giá spot: {host.split('//')[1]} lỗi {type(e).__name__} → thử nguồn khác")
    return out


def spot_ref(fsym):
    """Mã futures → (mã spot, hệ số): 1000PEPEUSDT → ('PEPEUSDT', 1000)."""
    m = re.match(r"^(1000000|1000)([A-Z].*)$", fsym)
    return (m.group(2), float(m.group(1))) if m else (fsym, 1.0)


def futures_prices(symbols):
    """{symbol: (giá, xấp_xỉ)} từ fapi; fapi chặn IP (runner GitHub ở Mỹ) → dùng giá spot cùng mã làm tham chiếu."""
    want = sorted(set(symbols))
    if not want:
        return {}
    base = f"{FUT_HOST}/fapi/v1/ticker/price"
    try:
        j = [get_json(f"{base}?symbol={urllib.parse.quote(want[0])}")] if len(want) == 1 else get_json(base)  # cả sàn = weight 2
        return {x["symbol"]: (float(x["price"]), False) for x in j if x.get("symbol") in want}
    except urllib.error.HTTPError as e:
        if e.code == 400 and len(want) == 1:
            log("Giá futures: mã không tồn tại trên sàn")
            return {}
        log(f"Giá futures: fapi {short_err(e)[:40]} → dùng giá spot tham chiếu")
    except Exception as e:
        log(f"Giá futures: fapi lỗi {type(e).__name__} → dùng giá spot tham chiếu")
    refs = {s: spot_ref(s) for s in want}
    sp = spot_prices([r[0] for r in refs.values()])
    return {s: (sp[r] * k, True) for s, (r, k) in refs.items() if r in sp}


def fetch_prices(pend):
    out = {}
    fut = [p["symbol"] for p in pend if p["market"] == "futures"]
    spo = [p["symbol"] for p in pend if p["market"] == "spot"]
    for s, v in futures_prices(fut).items():
        out[("futures", s)] = v
    for s, v in spot_prices(spo).items():
        out[("spot", s)] = (v, False)
    return out


# ---------- 3) tổng kết ngày ----------
def in_daily_window(now_utc):
    start = now_utc.replace(hour=DAILY_UTC[0], minute=DAILY_UTC[1], second=0, microsecond=0)
    return start <= now_utc < start + datetime.timedelta(minutes=DAILY_WINDOW_MIN)


def decide_daily(history, status, cmd, now_utc):
    if not in_daily_window(now_utc):
        return None
    today = now_utc.astimezone(VN).date().isoformat()
    if cmd.get("dailySummaryDate") == today:
        return None
    status = status or {}
    mode = status.get("mode") or (cmd.get("config") or {}).get("mode") or "testnet"
    trades = []
    for t in (history or {}).get("trades") or []:
        ex = to_ms(t.get("exitAt")) if isinstance(t, dict) else None
        if ex and vn_dt(ex).date().isoformat() == today and (t.get("mode") or mode) == mode:
            trades.append(t)
    label = "Tiền thật" if mode == "live" else "Testnet"
    eq = num((status.get("lastScan") or {}).get("eq"))
    tail = f" · Vốn {vi_fixed(eq, 2)}$" if eq else ""
    if not trades:
        return {"tag": "live-daily", "url": URL_LIVE, "title": "📊 Hôm nay bot chưa có lệnh",
                "body": label + tail, "set": {"dailySummaryDate": today}, "n": 0}
    pnls = [num(t.get("pnl")) or 0.0 for t in trades]
    rs = [r for r in (num(t.get("R")) for t in trades) if r is not None]
    title = f"📊 Bot hôm nay: {len(trades)} lệnh · {sum(1 for p in pnls if p > 0)} thắng · {signed(sum(pnls), 2)}$"
    if rs:
        title += f" ({signed(sum(rs), 1)}R)"
    items = [f"{str(t.get('coin') or '?').replace('USDT', '')} {signed(p, 2)}$" for t, p in zip(trades, pnls)]
    more = f" · +{len(items) - 5} lệnh khác" if len(items) > 5 else ""
    return {"tag": "live-daily", "url": URL_LIVE, "title": title,
            "body": f"{label} · " + " · ".join(items[:5]) + more + tail,
            "set": {"dailySummaryDate": today}, "n": len(trades)}


# ---------- Supabase ----------
def login():
    email, pw = os.environ.get("QWS_EMAIL"), os.environ.get("QWS_PASSWORD")
    if not email or not pw:
        raise RuntimeError("thiếu biến môi trường QWS_EMAIL / QWS_PASSWORD")
    auth = http(f"{SUPABASE_URL}/auth/v1/token?grant_type=password", {"apikey": ANON}, {"email": email, "password": pw})
    return auth["access_token"], auth["user"]["id"]


def logout(token):
    """Đóng phiên vừa mở (scope=local: CHỈ phiên này — app trên điện thoại/máy tính vẫn đăng nhập)."""
    try:
        http(f"{SUPABASE_URL}/auth/v1/logout?scope=local", sb(token), {}, method="POST")
    except Exception:
        pass


def read_rows(token):
    rows = http(f"{SUPABASE_URL}/rest/v1/{TABLE}?select=id,data", sb(token))
    return {r.get("id"): (r.get("data") or {}) for r in rows or []}


def read_command(token):
    rows = http(f"{SUPABASE_URL}/rest/v1/{TABLE}?id=eq.command&select=data", sb(token))
    return (rows[0].get("data") or {}) if rows else None


def write_command(token, data):
    http(f"{SUPABASE_URL}/rest/v1/{TABLE}",
         {**sb(token), "Prefer": "resolution=merge-duplicates,return=minimal"},
         [{"id": "command", "data": data, "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat()}],
         method="POST")


def apply_patch(latest, sets, fired):
    """Gộp vào bản MỚI NHẤT: chỉ đặt trường của monitor + firedAt của đúng cảnh báo còn chưa bắn."""
    merged = json.loads(json.dumps(latest))
    merged.update(sets)
    n = 0
    if fired and isinstance(merged.get("priceAlerts"), list):
        out = []
        for a in merged["priceAlerts"]:
            if isinstance(a, dict) and not a.get("firedAt") and alert_key(a) in fired:
                a = {**a, **fired[alert_key(a)]}
                n += 1
            out.append(a)
        merged["priceAlerts"] = out
    return merged, n


def push(token, uid, msg):
    """Như send_push() của bot.py nhưng có url/tag riêng; trả True nếu edge function nhận."""
    try:
        r = http(f"{SUPABASE_URL}/functions/v1/qws-send-push", sb(token),
                 {"user_id": uid, "title": msg["title"], "body": msg["body"], "url": msg["url"], "tag": msg["tag"]})
        log(f"  push {msg['tag'].split('-')[0]}: {r}")
        return True
    except Exception as e:
        log(f"  push lỗi: {short_err(e)}")
        return False


def is_missing_table(e):
    if not isinstance(e, urllib.error.HTTPError):
        return False
    if e.code == 404:
        return True
    try:
        return bool(re.search(r"does not exist|schema cache|42P01|PGRST205", e.read().decode(errors="replace")))
    except Exception:
        return False


def run(token, uid, now_ms, now_utc):
    try:
        rows = read_rows(token)
    except Exception as e:
        log("Chưa có bảng qws_live_bot — bỏ qua." if is_missing_table(e) else f"Lỗi đọc qws_live_bot: {short_err(e)}")
        return
    cmd = rows.get("command")
    if cmd is None:
        log("Chưa có dòng 'command' — bỏ qua.")
        return
    status, history = rows.get("status"), rows.get("history")
    sets, fired = {}, {}

    # 1) mất liên lạc / hoạt động lại
    try:
        if status is None:
            log("Mất liên lạc: chưa có dòng 'status' — bỏ qua.")
        else:
            at = to_ms(status.get("at"))
            age = f"{int((now_ms - at) / 1000)}s" if at else "?"
            d = decide_offline(status, cmd, now_ms)
            log(f"Trạng thái: tín hiệu cách {age} → {d['kind'] if d else 'không cần báo'}")
            if d and push(token, uid, d):
                sets.update(d["set"])
    except Exception as e:
        log(f"Mất liên lạc: lỗi {short_err(e)}")

    # 2) cảnh báo giá
    try:
        pend = pending_alerts(cmd.get("priceAlerts"))
        if pend:
            prices = fetch_prices(pend)
            hits = decide_price_alerts(pend, prices, now_ms)
            log(f"Cảnh báo giá: {len(pend)} đang chờ, có giá {len({(p['market'], p['symbol']) for p in pend} & set(prices))} mã, chạm {len(hits)}")
            for h in hits:
                if push(token, uid, h):
                    fired[h["key"]] = h["set"]
        else:
            log("Cảnh báo giá: không có cảnh báo đang chờ")
    except Exception as e:
        log(f"Cảnh báo giá: lỗi {short_err(e)}")

    # 3) tổng kết ngày
    try:
        if in_daily_window(now_utc):
            if history is None:
                log("Tổng kết ngày: chưa có dòng 'history' — bỏ qua.")
            else:
                d = decide_daily(history, status, cmd, now_utc)
                log(f"Tổng kết ngày: {'gửi (' + str(d['n']) + ' lệnh)' if d else 'đã gửi hôm nay'}")
                if d and push(token, uid, d):
                    sets.update(d["set"])
    except Exception as e:
        log(f"Tổng kết ngày: lỗi {short_err(e)}")

    # 4) ghi command: đọc lại bản mới nhất NGAY trước khi ghi, gộp trường của monitor
    if not sets and not fired:
        return
    try:
        latest = read_command(token)
        if latest is None:
            log("Dòng 'command' vừa bị xoá — không ghi.")
            return
        merged, n = apply_patch(latest, sets, fired)
        if not sets and not n:
            log("Không còn gì để ghi (cảnh báo đã bị xoá/bắn ở nơi khác).")
            return
        write_command(token, merged)
        log(f"Đã ghi command: {sorted(sets)} + {n} cảnh báo giá")
    except Exception as e:
        log(f"Ghi command lỗi: {short_err(e)}")


def main():
    now_ms = int(time.time() * 1000)
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    try:
        token, uid = login()
    except Exception as e:
        log("LỖI đăng nhập:", short_err(e))
        sys.exit(1)
    try:
        run(token, uid, now_ms, now_utc)
    except Exception as e:  # lưới an toàn cuối — vẫn exit 0
        log(f"Lỗi ngoài dự kiến: {short_err(e)}")
    finally:
        logout(token)


# ---------- --selftest: dữ liệu giả, không gọi mạng ----------
def selftest():
    MIN = 60_000
    # 20/10/2026 10:00 giờ VN = 03:00 UTC
    now_utc = datetime.datetime(2026, 10, 20, 3, 0, tzinfo=datetime.timezone.utc)
    now = int(now_utc.timestamp() * 1000)

    # định dạng số vi-VN
    assert fmt_price(85000) == "85.000", fmt_price(85000)
    assert fmt_price(85000.5) == "85.000,5"
    assert fmt_price(0.00001234) == "0,00001234"
    assert fmt_price(1.5) == "1,5"
    assert signed(24.5, 2) == "+24,50" and signed(-3.1, 2) == "-3,10" and signed(0, 2) == "0,00"
    assert signed(1.44, 1) == "+1,4" and signed(-0.001, 2) == "0,00"
    assert vi_fixed(1234.5, 2) == "1.234,50"
    assert norm_symbol("btc") == "BTCUSDT" and norm_symbol("eth/usdt") == "ETHUSDT" and norm_symbol("ETHBTC") == "ETHBTC"
    assert spot_ref("1000PEPEUSDT") == ("PEPEUSDT", 1000.0) and spot_ref("BTCUSDT") == ("BTCUSDT", 1.0)
    assert to_ms("2026-10-20T03:00:00Z") == now and to_ms(now / 1000) == now and to_ms(None) is None

    # 1) mất liên lạc
    st_old = {"at": now - 9 * MIN, "mode": "testnet"}
    d = decide_offline(st_old, {}, now)
    assert d and d["kind"] == "offline" and d["set"] == {"offlineAlertAt": now, "offlineSince": now - 9 * MIN}
    assert "từ 09:51 (giờ VN)" in d["body"] and d["url"] == "/coin?tab=live", d["body"]
    assert decide_offline(st_old, {"offlineAlertAt": now - 1 * MIN}, now) is None          # đã báo rồi
    assert decide_offline({"at": now - 7 * MIN}, {}, now) is None                            # chưa quá 8 phút
    assert decide_offline({"at": now - 30_000}, {}, now) is None                             # bình thường
    back = decide_offline({"at": now - 30_000}, {"offlineAlertAt": now - 20 * MIN, "offlineSince": now - 30 * MIN}, now)
    assert back and back["kind"] == "online" and back["set"] == {"onlineAlertAt": now} and "từ 09:30" in back["body"]
    assert decide_offline({"at": now - 30_000}, {"offlineAlertAt": now - 20 * MIN, "onlineAlertAt": now - 10 * MIN}, now) is None
    again = decide_offline(st_old, {"offlineAlertAt": now - 60 * MIN, "onlineAlertAt": now - 50 * MIN}, now)
    assert again and again["kind"] == "offline"                                              # sự cố mới
    assert decide_offline({}, {}, now) is None
    d2 = decide_offline({"at": now - 20 * 60 * MIN}, {}, now)                                 # mất từ hôm qua → kèm ngày
    assert "14:00 19/10" in d2["body"], d2["body"]

    # 2) cảnh báo giá
    alerts = [
        {"id": "a1", "symbol": "BTCUSDT", "market": "futures", "op": ">=", "price": 85000, "note": "chốt lời", "firedAt": None},
        {"id": "a2", "symbol": "ETHUSDT", "market": "spot", "op": "<=", "price": 3000, "note": "", "firedAt": None},
        {"id": "a3", "symbol": "SOLUSDT", "market": "futures", "op": "<=", "price": 100, "firedAt": now - MIN},  # đã bắn
        {"id": "a4", "symbol": "XRPUSDT", "op": "!=", "price": 1},                                               # op sai
        {"id": "a5", "symbol": "DOGEUSDT", "market": "futures", "op": "<=", "price": 0.1},                      # chưa chạm
        {"id": "a6", "symbol": "BNBUSDT", "market": "spot", "op": "<=", "price": 500},                          # thiếu giá
    ]
    pend = pending_alerts(alerts)
    assert [p["key"] for p in pend] == ["a1", "a2", "a5", "a6"]
    prices = {("futures", "BTCUSDT"): (85123.4, False), ("spot", "ETHUSDT"): (2999.5, False), ("futures", "DOGEUSDT"): (0.12, True)}
    hits = decide_price_alerts(pend, prices, now)
    assert [h["key"] for h in hits] == ["a1", "a2"]
    assert hits[0]["title"] == "🔔 BTCUSDT ≥ 85.000" and hits[0]["body"] == "chốt lời · Giá Futures hiện 85.123,4", hits[0]
    assert hits[1]["title"] == "🔔 ETHUSDT ≤ 3.000" and hits[1]["url"] == "/coin?tab=market"
    assert hits[0]["set"] == {"firedAt": now, "firedPrice": 85123.4}
    approx = decide_price_alerts(pending_alerts([{"id": "x", "symbol": "BTC", "op": ">=", "price": 1}]),
                                 {("futures", "BTCUSDT"): (2.0, True)}, now)
    assert approx and "tham chiếu giá spot" in approx[0]["body"]

    # 3) tổng kết ngày (22:05 VN 20/10 = 15:05 UTC)
    t22 = datetime.datetime(2026, 10, 20, 15, 5, tzinfo=datetime.timezone.utc)
    assert in_daily_window(t22) and in_daily_window(t22.replace(minute=40)) and not in_daily_window(t22.replace(hour=17, minute=0)) and not in_daily_window(t22.replace(hour=14, minute=59))
    vn = lambda h, m, day=20: int(datetime.datetime(2026, 10, day, h, m, tzinfo=VN).timestamp() * 1000)
    hist = {"trades": [
        {"coin": "BTCUSDT", "pnl": 20.0, "R": 1.0, "exitAt": vn(0, 30), "mode": "testnet"},
        {"coin": "ETH", "pnl": -5.5, "R": -0.5, "exitAt": vn(9, 0), "mode": "testnet"},
        {"coin": "SOL", "pnl": 10.0, "R": 0.9, "exitAt": vn(21, 0), "mode": "testnet"},
        {"coin": "XRP", "pnl": 99.0, "R": 3.0, "exitAt": vn(23, 30, 19), "mode": "testnet"},  # hôm qua
        {"coin": "ADA", "pnl": 50.0, "R": 2.0, "exitAt": vn(10, 0), "mode": "live"},          # chế độ khác
    ]}
    st = {"at": now, "mode": "testnet", "lastScan": {"eq": 1234.5}}
    dd = decide_daily(hist, st, {}, t22)
    assert dd["title"] == "📊 Bot hôm nay: 3 lệnh · 2 thắng · +24,50$ (+1,4R)", dd["title"]
    assert dd["body"] == "Testnet · BTC +20,00$ · ETH -5,50$ · SOL +10,00$ · Vốn 1.234,50$", dd["body"]
    assert dd["set"] == {"dailySummaryDate": "2026-10-20"}
    assert decide_daily(hist, st, {"dailySummaryDate": "2026-10-20"}, t22) is None
    assert decide_daily(hist, st, {}, now_utc) is None                                        # ngoài khung giờ
    assert decide_daily({"trades": []}, {"mode": "live"}, {}, t22)["title"] == "📊 Hôm nay bot chưa có lệnh"
    assert decide_daily(hist, {"mode": "live"}, {}, t22)["title"].startswith("📊 Bot hôm nay: 1 lệnh · 1 thắng · +50,00$ (+2,0R)")

    # 4) gộp vào bản MỚI NHẤT (app vừa sửa config/keys/thêm cảnh báo trong lúc monitor chạy)
    latest = {"stop": False, "config": {"mode": "live"}, "keys": {"live": {"key": "enc"}}, "at": 1,
              "priceAlerts": [dict(alerts[0]), {"id": "new", "symbol": "SOLUSDT", "op": ">=", "price": 300, "firedAt": None},
                              {**alerts[1], "firedAt": 5}]}  # a2 đã bị bắn ở nơi khác
    merged, n = apply_patch(latest, {"offlineAlertAt": now}, {"a1": hits[0]["set"], "a2": hits[1]["set"], "gone": {"firedAt": now}})
    assert n == 1 and merged["offlineAlertAt"] == now
    assert merged["config"] == {"mode": "live"} and merged["keys"] == latest["keys"] and merged["stop"] is False and merged["at"] == 1
    assert [a["id"] for a in merged["priceAlerts"]] == ["a1", "new", "a2"]
    assert merged["priceAlerts"][0]["firedAt"] == now and merged["priceAlerts"][1]["firedAt"] is None
    assert merged["priceAlerts"][2]["firedAt"] == 5
    assert latest["priceAlerts"][0]["firedAt"] is None  # không sửa bản gốc

    # 5) chạy trọn run() với Supabase/Binance/push giả — kiểm tra đọc lại trước khi ghi
    g = globals()
    saved = {k: g[k] for k in ("http", "fetch_prices", "push")}
    db = {"status": {"at": now - 9 * MIN, "mode": "testnet"}, "history": {"trades": []},
          "command": {"stop": False, "config": {"mode": "testnet"},
                      "priceAlerts": [{"id": "a1", "symbol": "BTCUSDT", "op": ">=", "price": 85000, "firedAt": None}]}}
    pushed, writes, reads = [], [], {"n": 0}

    def fake_http(url, headers=None, data=None, method=None, timeout=30):
        if "select=id,data" in url:
            return [{"id": k, "data": json.loads(json.dumps(v))} for k, v in db.items()]
        if "id=eq.command" in url:
            reads["n"] += 1  # app ghi chen giữa 2 lần đọc: thêm cảnh báo + đổi cấu hình
            db["command"]["priceAlerts"].append({"id": "b2", "symbol": "ETHUSDT", "op": "<=", "price": 1, "firedAt": None})
            db["command"]["config"] = {"mode": "live"}
            return [{"data": json.loads(json.dumps(db["command"]))}]
        if method == "POST" and url.endswith("/" + TABLE):
            writes.append(data)
            db["command"] = data[0]["data"]
            return None
        raise AssertionError("gọi mạng ngoài dự kiến: " + url)

    g["http"] = fake_http
    g["fetch_prices"] = lambda pend: {("futures", "BTCUSDT"): (90000.0, False)}
    g["push"] = lambda token, uid, msg: pushed.append(msg["title"]) or True
    try:
        run("tok", "uid", now, now_utc)
        assert pushed == ["⚠️ Bot giao dịch mất liên lạc", "🔔 BTCUSDT ≥ 85.000"], pushed
        assert reads["n"] == 1 and len(writes) == 1 and writes[0][0]["id"] == "command"
        c = db["command"]
        assert c["offlineAlertAt"] == now and c["config"] == {"mode": "live"}
        assert [(a["id"], a["firedAt"]) for a in c["priceAlerts"]] == [("a1", now), ("b2", None)]
        pushed.clear(); writes.clear()
        run("tok", "uid", now + 10 * MIN, now_utc)                     # lượt sau: không báo lặp
        assert pushed == [] and writes == [], (pushed, writes)
        db["status"]["at"] = now + 19 * MIN + 30_000                   # máy bàn có lại
        run("tok", "uid", now + 20 * MIN, now_utc)
        assert pushed == ["✅ Bot hoạt động lại"] and db["command"]["onlineAlertAt"] == now + 20 * MIN
        del db["command"]; pushed.clear()
        run("tok", "uid", now, now_utc)                                # thiếu dòng command → im lặng
        assert pushed == []
    finally:
        g.update(saved)
    print("selftest OK")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    else:
        main()
