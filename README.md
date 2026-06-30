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
