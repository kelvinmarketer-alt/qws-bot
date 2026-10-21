// QWS monitor trên Cloudflare Worker (cron 5 phút/lần, chạy đúng giờ — GitHub Actions hay trễ hàng giờ).
// Cùng logic monitor.py: 1) mất liên lạc / hoạt động lại · 2) cảnh báo giá · 3) tổng kết ngày từ 22:00 giờ VN.
// Ghi 'command': đọc lại bản MỚI NHẤT ngay trước khi ghi, chỉ sửa trường của monitor (không mất keys/config/cảnh báo app vừa thêm).
// Nhịp tim: ghi command.cfMonitorAt ≥ 30 phút/lần → monitor.py trên GitHub thấy Worker còn sống thì nhường (không báo trùng).
// Secret (đặt bằng `wrangler secret put`): QWS_EMAIL, QWS_PASSWORD. URL + anon key là khoá công khai.

export const SUPABASE_URL = "https://dbfffwtnxhytcoczhxhf.supabase.co";
export const ANON = "sb_publishable_TaKPhmv9_ig8Z7rl-PZupw_AnzYwFQo";
const TABLE = "qws_live_bot";
const MIN = 60000;
export const OFFLINE_MS = 8 * MIN;
export const HEARTBEAT_MS = 30 * MIN;
const DAILY_UTC_H = 15, DAILY_WINDOW_MIN = 120; // 15:00 UTC = 22:00 giờ VN; gửi ở lượt đầu tiên trong 15:00–16:59 UTC
const VN = 7 * 3600000;
const URL_LIVE = "/coin?tab=live", URL_MARKET = "/coin?tab=market";
const SPOT_HOSTS = ["https://api.binance.com", "https://data-api.binance.vision"];
const FUT_HOST = "https://fapi.binance.com";

// ---------- tiện ích ----------
export function toMs(v) {
  if (v == null || typeof v === "boolean") return null;
  if (typeof v === "string") { const n = Number(v); if (v.trim() !== "" && isFinite(n)) v = n; else { const t = Date.parse(v); return isFinite(t) ? t : null; } }
  if (typeof v !== "number" || !(v > 0)) return null;
  return v < 1e11 ? v * 1000 : v; // lỡ ghi giây
}
const vnParts = (ms) => { const d = new Date(ms + VN); return { day: d.toISOString().slice(0, 10), hm: d.toISOString().slice(11, 16), dm: `${d.toISOString().slice(8, 10)}/${d.toISOString().slice(5, 7)}` }; };
export const hhmm = (ms, now) => { const a = vnParts(ms); return a.hm + (a.day === vnParts(now).day ? "" : ` ${a.dm}`); };
export function viFixed(x, dec) {
  let v = Number(Number(x).toFixed(dec)); if (Object.is(v, -0) || v === 0) v = 0;
  const [i, f] = Math.abs(v).toFixed(dec).split(".");
  return (v < 0 ? "-" : "") + i.replace(/\B(?=(\d{3})+(?!\d))/g, ".") + (f ? `,${f}` : "");
}
export function fmtPrice(p) {
  const a = Math.abs(Number(p)), dec = a >= 1000 ? 2 : a >= 1 ? 4 : a >= 0.01 ? 6 : 10;
  let s = viFixed(p, dec); if (s.includes(",")) s = s.replace(/0+$/, "").replace(/,$/, "");
  return s;
}
export const signed = (x, dec) => { const s = viFixed(x, dec); return s.startsWith("-") || Number(Number(x).toFixed(dec)) === 0 ? s : `+${s}`; };
const num = (x) => { const v = Number(x); return x == null || x === "" || !isFinite(v) ? null : v; };

// ---------- 1) mất liên lạc ----------
export function decideOffline(status, cmd, now) {
  const at = toMs(status?.at); if (!at) return null;
  const off = toMs(cmd.offlineAlertAt), on = toMs(cmd.onlineAlertAt);
  if (now - at > OFFLINE_MS) {
    if (!off || off < at) return { kind: "offline", tag: "live-status", url: URL_LIVE, title: "⚠️ Bot giao dịch mất liên lạc",
      body: `Không nhận tín hiệu từ máy bàn từ ${hhmm(at, now)} (giờ VN) — kiểm tra điện/mạng/máy. Lệnh đang mở vẫn có SL/TP trên sàn.`,
      set: { offlineAlertAt: now, offlineSince: at } };
    return null;
  }
  if (off && at > off && (!on || on < off)) {
    const since = toMs(cmd.offlineSince);
    return { kind: "online", tag: "live-status", url: URL_LIVE, title: "✅ Bot hoạt động lại",
      body: `Máy bàn đã gửi tín hiệu lại lúc ${hhmm(at, now)} (giờ VN)${since ? ` sau khi mất liên lạc từ ${hhmm(since, now)}` : ""}. Kiểm tra lệnh đang mở trong app.`,
      set: { onlineAlertAt: now } };
  }
  return null;
}

// ---------- 2) cảnh báo giá ----------
const OPS = { ">=": ">=", "≥": ">=", ">": ">=", "<=": "<=", "≤": "<=", "<": "<=" };
export function normSymbol(s) {
  s = String(s || "").toUpperCase().replace(/[^A-Z0-9]/g, "");
  if (s && !/(USDT|USDC|FDUSD|BUSD|TUSD)$/.test(s) && !(s.length > 4 && /(BTC|ETH|BNB)$/.test(s))) s += "USDT";
  return s;
}
export const alertKey = (a) => String(a.id || `${a.symbol}|${a.market}|${a.op}|${a.price}|${a.createdAt}`);
export function pendingAlerts(alerts) {
  const out = [];
  for (const a of Array.isArray(alerts) ? alerts : []) {
    if (!a || typeof a !== "object" || a.firedAt) continue;
    const op = OPS[String(a.op ?? "").trim()], target = num(a.price), symbol = normSymbol(a.symbol);
    if (!op || target == null || target <= 0 || !symbol) continue;
    out.push({ key: alertKey(a), a, op, target, symbol, market: a.market === "spot" ? "spot" : "futures" });
  }
  return out;
}
export function decidePriceAlerts(pend, prices, now) {
  const hits = [];
  for (const p of pend) {
    const got = prices[`${p.market}|${p.symbol}`]; if (!got) continue;
    const [price, approx] = got;
    if (!(p.op === ">=" ? price >= p.target : price <= p.target)) continue;
    const note = String(p.a.note || "").trim(), cur = `Giá ${p.market === "spot" ? "Spot" : "Futures"} hiện ${fmtPrice(price)}${approx ? " (tham chiếu giá spot)" : ""}`;
    hits.push({ key: p.key, tag: `price-${p.key}`.slice(0, 64), url: URL_MARKET, title: `🔔 ${p.symbol} ${p.op === ">=" ? "≥" : "≤"} ${fmtPrice(p.target)}`, body: note ? `${note} · ${cur}` : cur, set: { firedAt: now, firedPrice: price } });
  }
  return hits;
}
const getJson = async (url) => { const r = await fetch(url, { signal: AbortSignal.timeout(15000) }); if (!r.ok) { const e = new Error(`HTTP ${r.status}`); e.status = r.status; throw e; } return r.json(); };
async function spotPrices(symbols) {
  const syms = [...new Set(symbols)].sort(), out = {}; if (!syms.length) return out;
  for (const host of SPOT_HOSTS) {
    const base = `${host}/api/v3/ticker/price`;
    try {
      const j = syms.length === 1 ? [await getJson(`${base}?symbol=${encodeURIComponent(syms[0])}`)] : await getJson(`${base}?symbols=${encodeURIComponent(JSON.stringify(syms))}`);
      for (const x of j) out[x.symbol] = Number(x.price); return out;
    } catch (e) {
      if (e.status === 400) { if (syms.length > 1) for (const s of syms) { try { out[s] = Number((await getJson(`${base}?symbol=${encodeURIComponent(s)}`)).price); } catch { /* mã không tồn tại */ } } return out; }
    }
  }
  return out;
}
const spotRef = (f) => { const m = /^(1000000|1000)([A-Z].*)$/.exec(f); return m ? [m[2], Number(m[1])] : [f, 1]; };
async function futuresPrices(symbols) {
  const want = [...new Set(symbols)].sort(), out = {}; if (!want.length) return out;
  try {
    const base = `${FUT_HOST}/fapi/v1/ticker/price`;
    const j = want.length === 1 ? [await getJson(`${base}?symbol=${encodeURIComponent(want[0])}`)] : await getJson(base);
    for (const x of j) if (want.includes(x.symbol)) out[x.symbol] = [Number(x.price), false];
    return out;
  } catch (e) { if (e.status === 400 && want.length === 1) return out; } // fapi chặn IP → dùng giá spot tham chiếu
  const refs = Object.fromEntries(want.map((s) => [s, spotRef(s)])), sp = await spotPrices(Object.values(refs).map((r) => r[0]));
  for (const [s, [r, k]] of Object.entries(refs)) if (sp[r] != null) out[s] = [sp[r] * k, true];
  return out;
}
async function fetchPrices(pend) {
  const out = {};
  for (const [s, v] of Object.entries(await futuresPrices(pend.filter((p) => p.market === "futures").map((p) => p.symbol)))) out[`futures|${s}`] = v;
  for (const [s, v] of Object.entries(await spotPrices(pend.filter((p) => p.market === "spot").map((p) => p.symbol)))) out[`spot|${s}`] = [v, false];
  return out;
}

// ---------- 3) tổng kết ngày ----------
export function inDailyWindow(now) { const d = new Date(now), start = Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate(), DAILY_UTC_H); return now >= start && now < start + DAILY_WINDOW_MIN * MIN; }
export function decideDaily(history, status, cmd, now) {
  if (!inDailyWindow(now)) return null;
  const today = vnParts(now).day; if (cmd.dailySummaryDate === today) return null;
  status = status || {};
  const mode = status.mode || cmd.config?.mode || "testnet";
  const trades = (history?.trades || []).filter((t) => { const ex = t && typeof t === "object" ? toMs(t.exitAt) : null; return ex && vnParts(ex).day === today && (t.mode || mode) === mode && t.setup !== "TEST"; });
  const label = mode === "live" ? "Tiền thật" : "Testnet", eq = num(status.lastScan?.eq), tail = eq ? ` · Vốn ${viFixed(eq, 2)}$` : "";
  if (!trades.length) return { tag: "live-daily", url: URL_LIVE, title: "📊 Hôm nay bot chưa có lệnh", body: label + tail, set: { dailySummaryDate: today }, n: 0 };
  const pnls = trades.map((t) => num(t.pnl) || 0), rs = trades.map((t) => num(t.R)).filter((r) => r != null);
  let title = `📊 Bot hôm nay: ${trades.length} lệnh · ${pnls.filter((p) => p > 0).length} thắng · ${signed(pnls.reduce((s, p) => s + p, 0), 2)}$`;
  if (rs.length) title += ` (${signed(rs.reduce((s, r) => s + r, 0), 1)}R)`;
  const items = trades.map((t, i) => `${String(t.coin || "?").replace("USDT", "")} ${signed(pnls[i], 2)}$`);
  return { tag: "live-daily", url: URL_LIVE, title, body: `${label} · ${items.slice(0, 5).join(" · ")}${items.length > 5 ? ` · +${items.length - 5} lệnh khác` : ""}${tail}`, set: { dailySummaryDate: today }, n: trades.length };
}

// ---------- gộp vào bản MỚI NHẤT ----------
export function applyPatch(latest, sets, fired) {
  const merged = { ...structuredClone(latest), ...sets }; let n = 0;
  if (Object.keys(fired).length && Array.isArray(merged.priceAlerts)) merged.priceAlerts = merged.priceAlerts.map((a) => { if (a && typeof a === "object" && !a.firedAt && fired[alertKey(a)]) { n++; return { ...a, ...fired[alertKey(a)] }; } return a; });
  return [merged, n];
}

// ---------- Supabase ----------
export function makeApi(env, http = fetch) {
  const call = async (path, { token, method = "GET", body, prefer } = {}) => {
    const r = await http(`${SUPABASE_URL}${path}`, { method, headers: { apikey: ANON, Authorization: `Bearer ${token || ANON}`, "Content-Type": "application/json", ...(prefer ? { Prefer: prefer } : {}) }, body: body === undefined ? undefined : JSON.stringify(body), signal: AbortSignal.timeout(30000) });
    const txt = await r.text();
    if (!r.ok) { const e = new Error(`HTTP ${r.status} ${txt.slice(0, 160)}`); e.status = r.status; e.text = txt; throw e; }
    return txt.trim() ? JSON.parse(txt) : null;
  };
  return {
    login: async () => { if (!env.QWS_EMAIL || !env.QWS_PASSWORD) throw new Error("thiếu secret QWS_EMAIL / QWS_PASSWORD"); const a = await call("/auth/v1/token?grant_type=password", { method: "POST", body: { email: env.QWS_EMAIL, password: env.QWS_PASSWORD } }); return [a.access_token, a.user.id]; },
    logout: (token) => call("/auth/v1/logout?scope=local", { token, method: "POST", body: {} }).catch(() => {}), // chỉ phiên này, app vẫn đăng nhập
    readRows: async (token) => Object.fromEntries(((await call(`/rest/v1/${TABLE}?select=id,data`, { token })) || []).map((r) => [r.id, r.data || {}])),
    readCommand: async (token) => { const rows = await call(`/rest/v1/${TABLE}?id=eq.command&select=data`, { token }); return rows?.length ? rows[0].data || {} : null; },
    writeCommand: (token, data) => call(`/rest/v1/${TABLE}`, { token, method: "POST", prefer: "resolution=merge-duplicates,return=minimal", body: [{ id: "command", data, updated_at: new Date().toISOString() }] }),
    push: async (token, uid, m) => { try { await call("/functions/v1/qws-send-push", { token, method: "POST", body: { user_id: uid, title: m.title, body: m.body, url: m.url, tag: m.tag } }); return true; } catch (e) { console.log(`push lỗi: ${e.message}`); return false; } },
  };
}

export async function run(api, token, uid, now, deps = { fetchPrices }) {
  let rows;
  try { rows = await api.readRows(token); } catch (e) { console.log(`Lỗi đọc ${TABLE}: ${e.message}`); return; }
  const cmd = rows.command; if (!cmd) { console.log("Chưa có dòng 'command' — bỏ qua."); return; }
  const { status, history } = rows, sets = {}, fired = {};
  try { if (status) { const d = decideOffline(status, cmd, now); console.log(`Trạng thái: tín hiệu cách ${Math.round((now - (toMs(status.at) || now)) / 1000)}s → ${d ? d.kind : "không cần báo"}`); if (d && (await api.push(token, uid, d))) Object.assign(sets, d.set); } } catch (e) { console.log(`Mất liên lạc: lỗi ${e.message}`); }
  try {
    const pend = pendingAlerts(cmd.priceAlerts);
    if (pend.length) { const hits = decidePriceAlerts(pend, await deps.fetchPrices(pend), now); console.log(`Cảnh báo giá: ${pend.length} đang chờ, chạm ${hits.length}`); for (const h of hits) if (await api.push(token, uid, h)) fired[h.key] = h.set; }
  } catch (e) { console.log(`Cảnh báo giá: lỗi ${e.message}`); }
  try { if (history && inDailyWindow(now)) { const d = decideDaily(history, status, cmd, now); if (d && (await api.push(token, uid, d))) Object.assign(sets, d.set); } } catch (e) { console.log(`Tổng kết ngày: lỗi ${e.message}`); }
  const beat = !(toMs(cmd.cfMonitorAt) > now - HEARTBEAT_MS);
  if (!Object.keys(sets).length && !Object.keys(fired).length && !beat) return;
  try {
    const latest = await api.readCommand(token); if (!latest) return;
    const [merged, n] = applyPatch(latest, { ...sets, cfMonitorAt: now }, fired);
    await api.writeCommand(token, merged);
    console.log(`Đã ghi command: ${Object.keys(sets).join(",") || "nhịp tim"} + ${n} cảnh báo giá`);
  } catch (e) { console.log(`Ghi command lỗi: ${e.message}`); }
}

export default {
  async scheduled(_ev, env, ctx) {
    const api = makeApi(env);
    ctx.waitUntil((async () => {
      let token, uid;
      try { [token, uid] = await api.login(); } catch (e) { console.log(`LỖI đăng nhập: ${e.message}`); return; }
      try { await run(api, token, uid, Date.now()); } finally { await api.logout(token); }
    })());
  },
  async fetch() { return new Response("qws-monitor đang chạy (cron 5 phút/lần)", { headers: { "content-type": "text/plain; charset=utf-8" } }); },
};
