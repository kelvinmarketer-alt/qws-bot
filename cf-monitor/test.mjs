// Kiểm tra logic Worker bằng dữ liệu giả, không gọi mạng: node cf-monitor/test.mjs
import assert from "node:assert/strict";
import { fmtPrice, signed, viFixed, normSymbol, toMs, decideOffline, pendingAlerts, decidePriceAlerts, inDailyWindow, decideDaily, applyPatch, mt5Prices, run } from "./worker.js";

const MIN = 60000, now = Date.UTC(2026, 9, 20, 3, 0); // 10:00 giờ VN 20/10
assert.equal(fmtPrice(85000), "85.000"); assert.equal(fmtPrice(85000.5), "85.000,5"); assert.equal(fmtPrice(0.00001234), "0,00001234"); assert.equal(fmtPrice(1.5), "1,5");
assert.equal(signed(24.5, 2), "+24,50"); assert.equal(signed(-3.1, 2), "-3,10"); assert.equal(signed(0, 2), "0,00"); assert.equal(signed(-0.001, 2), "0,00"); assert.equal(viFixed(1234.5, 2), "1.234,50");
assert.equal(normSymbol("btc"), "BTCUSDT"); assert.equal(normSymbol("eth/usdt"), "ETHUSDT"); assert.equal(normSymbol("ETHBTC"), "ETHBTC");
assert.equal(toMs("2026-10-20T03:00:00Z"), now); assert.equal(toMs(now / 1000), now); assert.equal(toMs(null), null);

// 1) mất liên lạc
const d = decideOffline({ at: now - 9 * MIN }, {}, now);
assert.equal(d.kind, "offline"); assert.deepEqual(d.set, { offlineAlertAt: now, offlineSince: now - 9 * MIN }); assert.ok(d.body.includes("từ 09:51 (giờ VN)"), d.body);
assert.equal(decideOffline({ at: now - 9 * MIN }, { offlineAlertAt: now - MIN }, now), null);
assert.equal(decideOffline({ at: now - 7 * MIN }, {}, now), null);
const back = decideOffline({ at: now - 30000 }, { offlineAlertAt: now - 20 * MIN, offlineSince: now - 30 * MIN }, now);
assert.equal(back.kind, "online"); assert.ok(back.body.includes("từ 09:30"));
assert.equal(decideOffline({ at: now - 30000 }, { offlineAlertAt: now - 20 * MIN, onlineAlertAt: now - 10 * MIN }, now), null);
assert.ok(decideOffline({ at: now - 20 * 60 * MIN }, {}, now).body.includes("14:00 19/10"));

// 2) cảnh báo giá
const alerts = [
  { id: "a1", symbol: "BTCUSDT", market: "futures", op: ">=", price: 85000, note: "chốt lời" },
  { id: "a2", symbol: "ETHUSDT", market: "spot", op: "<=", price: 3000 },
  { id: "a3", symbol: "SOLUSDT", op: "<=", price: 100, firedAt: 1 }, { id: "a4", symbol: "XRP", op: "!=", price: 1 },
];
const pend = pendingAlerts(alerts); assert.deepEqual(pend.map((p) => p.key), ["a1", "a2"]);
const hits = decidePriceAlerts(pend, { "futures|BTCUSDT": [85123.4, false], "spot|ETHUSDT": [2999.5, false] }, now);
assert.equal(hits[0].title, "🔔 BTCUSDT ≥ 85.000"); assert.equal(hits[0].body, "chốt lời · Giá Futures hiện 85.123,4"); assert.equal(hits[1].url, "/coin?tab=market");

// 3) tổng kết ngày (22:05 VN)
const t22 = Date.UTC(2026, 9, 20, 15, 5);
assert.ok(inDailyWindow(t22)); assert.ok(!inDailyWindow(Date.UTC(2026, 9, 20, 17, 0))); assert.ok(!inDailyWindow(Date.UTC(2026, 9, 20, 14, 59)));
const vn = (h, m, day = 20) => Date.UTC(2026, 9, day, h, m) - 7 * 3600000;
const hist = { trades: [
  { coin: "BTCUSDT", pnl: 20, R: 1, exitAt: vn(0, 30), mode: "testnet" }, { coin: "ETH", pnl: -5.5, R: -0.5, exitAt: vn(9, 0), mode: "testnet" },
  { coin: "SOL", pnl: 10, R: 0.9, exitAt: vn(21, 0), mode: "testnet" }, { coin: "XRP", pnl: 99, R: 3, exitAt: vn(23, 30, 19), mode: "testnet" },
  { coin: "ADA", pnl: 50, R: 2, exitAt: vn(10, 0), mode: "live" }, { coin: "BTC", pnl: 1, R: 1, exitAt: vn(11, 0), mode: "testnet", setup: "TEST" },
] };
const dd = decideDaily(hist, { mode: "testnet", lastScan: { eq: 1234.5 } }, {}, t22);
assert.equal(dd.title, "📊 Bot hôm nay: 3 lệnh · 2 thắng · +24,50$ (+1,4R)"); assert.equal(dd.body, "Testnet · BTC +20,00$ · ETH -5,50$ · SOL +10,00$ · Vốn 1.234,50$");
assert.equal(decideDaily(hist, {}, { dailySummaryDate: "2026-10-20" }, t22), null);

// 4) gộp vào bản mới nhất
const latest = { config: { mode: "live" }, keys: { live: { key: "enc" } }, priceAlerts: [{ ...alerts[0] }, { id: "new", symbol: "SOL", op: ">=", price: 1 }] };
const [m, n] = applyPatch(latest, { offlineAlertAt: now }, { a1: hits[0].set });
assert.equal(n, 1); assert.deepEqual(m.keys, latest.keys); assert.equal(m.priceAlerts[0].firedAt, now); assert.equal(latest.priceAlerts[0].firedAt, undefined);

// 5) run() trọn vòng với API giả: báo mất liên lạc + cảnh báo giá, đọc lại trước khi ghi, nhịp tim
const db = { status: { at: now - 9 * MIN }, history: { trades: [] }, command: { config: { mode: "testnet" }, priceAlerts: [{ id: "a1", symbol: "BTCUSDT", op: ">=", price: 85000 }] } };
const pushed = [], writes = [];
const api = {
  readRows: async () => structuredClone(db),
  readCommand: async () => { db.command.priceAlerts.push({ id: "b2", symbol: "ETH", op: "<=", price: 1 }); return structuredClone(db.command); },
  writeCommand: async (_t, data) => { writes.push(data); db.command = data; },
  push: async (_t, _u, msg) => { pushed.push(msg.title); return true; },
};
const deps = { fetchPrices: async () => ({ "futures|BTCUSDT": [90000, false] }) };
await run(api, "tok", "uid", now, deps);
assert.deepEqual(pushed, ["⚠️ Bot giao dịch mất liên lạc", "🔔 BTCUSDT ≥ 85.000"]);
assert.equal(writes.length, 1); assert.equal(db.command.cfMonitorAt, now); assert.deepEqual(db.command.priceAlerts.map((a) => [a.id, a.firedAt ?? null]), [["a1", now], ["b2", null]]);
pushed.length = 0; writes.length = 0;
await run(api, "tok", "uid", now + 5 * MIN, deps); // không báo lặp, nhịp tim còn mới → không ghi
assert.deepEqual(pushed, []); assert.equal(writes.length, 0);
await run(api, "tok", "uid", now + 31 * MIN, deps); // quá 30 phút → ghi nhịp tim
assert.equal(writes.length, 1); assert.equal(db.command.cfMonitorAt, now + 31 * MIN);
// 6) cảnh báo Forex (MT5): mã giữ nguyên, giá từ mkq, nến M5 bắt cú chạm, không báo khi giá cũ / chợ đóng / alert tạo sau nến
{
  const T = 1_800_000_000_000, bar = T - 2 * MIN;
  const al = [
    { id: "f1", symbol: "xauusd", market: "mt5", op: ">=", price: 4125, createdAt: bar - 1000 },   // nến M5 chạm 4126 sau khi tạo → báo dù giá hiện 4122
    { id: "f2", symbol: "EURUSD", market: "mt5", op: "<=", price: 1.0800, createdAt: bar + 1000 }, // tạo SAU khi nến mở → chỉ xét giá hiện tại (1.0850) → chưa báo
    { id: "f3", symbol: "BTCUSD", market: "mt5", op: ">=", price: 1, createdAt: bar },            // mã không có trong mkq → bỏ qua
    { id: "c1", symbol: "BTC", market: "futures", op: ">=", price: 1 },
  ];
  const pf = pendingAlerts(al); assert.deepEqual(pf.map((p) => [p.symbol, p.market]), [["XAUUSD", "mt5"], ["EURUSD", "mt5"], ["BTCUSD", "mt5"], ["BTCUSDT", "futures"]]);
  const mk = { ts: T - 30000, live: true, q: { XAUUSD: { bid: 4122, f: { "5m": [bar / 1000, 4120, 4126, 4119, 4122, 10] } }, EURUSD: { bid: 1.085, f: { "5m": [bar / 1000, 1.0855, 1.0860, 1.0845, 1.085, 10] } } } };
  const pr = mt5Prices(pf, mk, T); assert.deepEqual(Object.keys(pr).sort(), ["mt5|EURUSD", "mt5|XAUUSD"]);
  const h = decidePriceAlerts(pf, pr, T); assert.deepEqual(h.map((x) => x.key), ["f1"]);
  assert.match(h[0].title, /XAUUSD ≥ 4\.125/); assert.equal(h[0].url, "/coin?tab=fxmarket"); assert.match(h[0].body, /Giá MT5 hiện 4\.122.*đã chạm 4\.126/); assert.equal(h[0].set.firedPrice, 4126);
  assert.deepEqual(mt5Prices(pf, { ...mk, ts: T - 11 * MIN }, T), {}, "cầu nối ngừng gửi → không có giá");
  assert.deepEqual(mt5Prices(pf, { ...mk, live: false }, T), {}, "chợ đóng cửa → không báo bằng giá cũ");
  assert.deepEqual(mt5Prices(pf, null, T), {});
}
console.log("test OK");
