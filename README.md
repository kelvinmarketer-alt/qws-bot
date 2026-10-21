# QWS Bot — nhắc Quang Workspace qua Telegram

Bot đọc dữ liệu **Quỹ / Công việc** của app [Quang Workspace](https://quang-workspace.pages.dev)
trên Supabase rồi gửi Telegram hằng ngày (07:00 giờ VN).

## Gửi gì
- 💸 **Lịch chuyển quỹ đến hạn** — chế độ *nhắc + xác nhận*: bot chỉ nhắc, **không tự chuyển tiền**. Bạn mở app bấm "Chuyển ngay".
- ✅ **Việc hôm nay** / ⚠️ **việc quá hạn**.
- Không có gì đến hạn → **không gửi** (đỡ spam).

## Cách hoạt động
`bot.py` đăng nhập Supabase bằng email/mật khẩu của app (RLS chỉ thấy dòng của chính bạn),
lấy `qws_workspaces.data`, tính các kỳ đến hạn (logic giống trong app: 1 tuần / 2 tuần / 1 tháng,
chạy bù kỳ quá hạn), rồi gọi Telegram Bot API. Không có dependency ngoài (chỉ thư viện chuẩn Python).

## Secrets cần đặt (Settings → Secrets and variables → Actions)
| Secret | Ý nghĩa |
|---|---|
| `QWS_EMAIL` | email đăng nhập app |
| `QWS_PASSWORD` | mật khẩu đăng nhập app |
| `TELEGRAM_TOKEN` | token bot Telegram (tạo qua @BotFather) |
| `TELEGRAM_CHAT_ID` | chat_id người/nhóm nhận |

URL + anon key Supabase đã đặt mặc định trong `bot.py` (đều là khoá công khai).

## Chạy thử
Tab **Actions → QWS reminders → Run workflow**.

## Canh bot giao dịch (`monitor.py`, workflow `QWS live-bot monitor`, 10 phút/lần)
Đọc bảng Supabase `qws_live_bot` (bot trên máy bàn ghi `status`/`history`; app ghi `command`) rồi gửi
**Web Push** tới mọi thiết bị của chủ (dùng chung secret `QWS_EMAIL`/`QWS_PASSWORD`, không cần thêm gì):
- ⚠️ **Mất liên lạc**: `status.at` cũ hơn 8 phút → báo 1 lần/sự cố; có tín hiệu lại → ✅ "Bot hoạt động lại".
- 🔔 **Cảnh báo giá**: `command.data.priceAlerts` chưa bắn → giá Binance (futures `fapi`, spot `api`) chạm ngưỡng
  → push + ghi `firedAt`. Runner GitHub ở Mỹ có thể bị Binance chặn (451) → tự dùng `data-api.binance.vision`
  (futures lấy giá spot cùng mã làm tham chiếu, có ghi chú trong thông báo).
- 📊 **Tổng kết ngày** ~22:00 giờ VN (lượt chạy rơi vào 15:00–15:12 UTC): số lệnh, thắng, lãi/lỗ $, tổng R hôm nay.

Trạng thái chống báo trùng lưu trong `command.data`: `offlineAlertAt`, `offlineSince`, `onlineAlertAt`,
`dailySummaryDate`, `priceAlerts[].firedAt/firedPrice`. Trước khi ghi luôn đọc lại bản mới nhất và chỉ sửa các
trường này (giữ nguyên `keys`/`config`/`stop`/cảnh báo mới). Chưa có bảng/dòng → bỏ qua, không báo lỗi.
Repo public → log chỉ in số đếm, không in giá/vốn/lãi lỗ/ghi chú.

Kiểm tra logic không cần mạng: `python3 monitor.py --selftest`.

## Canh bot đúng giờ trên Cloudflare (`cf-monitor/`, 5 phút/lần)
Cron của GitHub Actions hay trễ hàng giờ. Vì vậy **Worker Cloudflare `qws-monitor`** chạy chính, cùng logic với `monitor.py` nhưng viết lại bằng JS.
- Worker ghi nhịp tim vào `command.cfMonitorAt` (≥ 30 phút/lần).
- `monitor.py` trên GitHub thấy nhịp tim mới hơn 45 phút thì **nhường**, để không báo trùng. Worker chết thì GitHub tự làm thay.
- Cài 1 lần:
  1. `cd cf-monitor && npx wrangler deploy`
  2. **Tự gõ** 2 secret: `npx wrangler secret put QWS_EMAIL` và `npx wrangler secret put QWS_PASSWORD`
- Kiểm tra logic không cần mạng: `node cf-monitor/test.mjs`
