"""Kiểm thử phần "Ý hay hôm nay" của bot (không cần mạng). Chạy: python3 -m unittest test_bot_idea -v"""
import os, unittest
from unittest import mock
for k in ("QWS_EMAIL", "QWS_PASSWORD", "TELEGRAM_TOKEN", "TELEGRAM_CHAT_ID"):
    os.environ.setdefault(k, "x")
import bot


class IdeaTests(unittest.TestCase):
    def test_push_part_ngan_gon_cat_o_ranh_gioi_tu(self):
        p = bot.idea_push_part({"idea": "Kiểm kê kho " + "mỗi tuần và đối chiếu doanh thu bán ra " * 6})
        self.assertTrue(p.startswith("💡 ") and p.endswith("…"))
        self.assertLessEqual(len(p), 3 + 121)
        self.assertNotIn("  ", p)
        self.assertEqual(bot.idea_push_part({"idea": "Ngắn thôi."}), "💡 Ngắn thôi.")

    def test_telegram_phai_escape_html(self):
        out = "\n".join(bot.idea_telegram_lines({"idea": "Giữ tồn kho < 3 tuần & chốt giá <b>bằng</b> văn bản", "title": "Bài <script> & co"}))
        self.assertIn("&lt; 3 tuần &amp; chốt giá &lt;b&gt;bằng&lt;/b&gt;", out)
        self.assertIn("— Bài &lt;script&gt; &amp; co", out)
        self.assertNotIn("<script>", out)
        self.assertIn("<b>Ý hay hôm nay</b>", out)  # thẻ của chính bot thì giữ

    def test_telegram_khong_co_tieu_de(self):
        out = bot.idea_telegram_lines({"idea": "Một ý.", "title": ""})
        self.assertEqual(len(out), 3)

    def test_fetch_idea_goi_dung_ham_va_seed(self):
        with mock.patch.object(bot, "http", return_value={"idea": " Ý hay. ", "doc_title": " Tài liệu A "}) as h:
            r = bot.fetch_idea("TOKEN", "2026-11-10")
        self.assertEqual(r, {"idea": "Ý hay.", "title": "Tài liệu A"})
        url, headers, data = h.call_args[0]
        self.assertTrue(url.endswith("/functions/v1/qws-lib"))
        self.assertEqual(headers["Authorization"], "Bearer TOKEN")
        self.assertEqual(data, {"mode": "idea", "seed": "2026-11-10"})

    def test_fetch_idea_loi_hoac_trong_thi_none_khong_nem_loi(self):
        for resp in ({"idea": None, "reason": "empty"}, {}, None, {"idea": "   "}):
            with mock.patch.object(bot, "http", return_value=resp):
                self.assertIsNone(bot.fetch_idea("T", "d"))
        with mock.patch.object(bot, "http", side_effect=Exception("404 hàm chưa cài")):
            self.assertIsNone(bot.fetch_idea("T", "d"))


class MainFlowTests(unittest.TestCase):
    """Chạy cả main() với mạng giả: đảm bảo ý hay vào thông báo đẩy + Telegram và KHÔNG phá nhắc việc cũ."""

    def run_main(self, idea_resp, workspace):
        sent = {"push": None, "tg": None}

        def fake_http(url, headers=None, data=None):
            if "auth/v1/token" in url:
                return {"access_token": "T", "user": {"id": "u1"}}
            if "rest/v1/qws_workspaces" in url:
                return [{"data": workspace}]
            if url.endswith("/functions/v1/qws-lib"):
                if isinstance(idea_resp, Exception):
                    raise idea_resp
                return idea_resp
            if url.endswith("/functions/v1/qws-send-push"):
                sent["push"] = data
                return {"sent": 1}
            if "api.telegram.org" in url:
                sent["tg"] = data
                return {"ok": True}
            raise AssertionError("gọi lạ: " + url)

        with mock.patch.object(bot, "http", side_effect=fake_http):
            bot.main()
        return sent

    def ws(self, with_task=True):
        import datetime
        today = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=7)).date().isoformat()
        return {"tasks": [{"id": "1", "title": "Gửi báo cáo", "date": today, "status": "todo"}] if with_task else []}

    def test_co_viec_va_co_y_hay(self):
        s = self.run_main({"idea": "Kiểm kê kho mỗi tuần.", "doc_title": "Ghi chú kho"}, self.ws())
        self.assertIn("✅ 1 việc hôm nay", s["push"]["body"])
        self.assertIn("💡 Kiểm kê kho mỗi tuần.", s["push"]["body"])
        self.assertIn("Ý hay hôm nay", s["tg"]["text"])
        self.assertIn("Gửi báo cáo", s["tg"]["text"])

    def test_ham_loi_van_gui_nhac_viec(self):
        s = self.run_main(Exception("500"), self.ws())
        self.assertIn("✅ 1 việc hôm nay", s["push"]["body"])
        self.assertNotIn("💡", s["push"]["body"])
        self.assertNotIn("Ý hay", s["tg"]["text"])

    def test_chi_co_y_hay_thi_chi_day_thong_bao_khong_gui_telegram(self):
        s = self.run_main({"idea": "Chỉ có ý hay."}, self.ws(with_task=False))
        self.assertIn("💡 Chỉ có ý hay.", s["push"]["body"])
        self.assertIsNone(s["tg"])  # không spam Telegram khi không có nhắc nào


if __name__ == "__main__":
    unittest.main()
