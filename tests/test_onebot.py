import unittest
from pathlib import Path
from unittest.mock import AsyncMock

from qq_codex_companion.config import OneBotSettings
from qq_codex_companion.onebot import (
    OneBotReverseServer,
    _extract_content,
    _private_poke_sender,
)


class PrivatePokeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = OneBotSettings(
            host="127.0.0.1",
            port=8643,
            path="/ws",
            access_token="test-token",
            bot_qq=20002,
            allowed_user_ids=frozenset({10001}),
        )

    def test_accepts_allowlisted_private_poke_targeting_bot(self) -> None:
        event = {
            "post_type": "notice",
            "notice_type": "notify",
            "sub_type": "poke",
            "user_id": 10001,
            "sender_id": 10001,
            "target_id": 20002,
        }
        self.assertEqual(_private_poke_sender(event, self.settings), 10001)

    def test_ignores_bot_originated_poke_notice(self) -> None:
        event = {
            "post_type": "notice",
            "notice_type": "notify",
            "sub_type": "poke",
            "user_id": 10001,
            "sender_id": 20002,
            "target_id": 10001,
        }
        self.assertIsNone(_private_poke_sender(event, self.settings))

    def test_ignores_non_allowlisted_poke(self) -> None:
        event = {
            "post_type": "notice",
            "notice_type": "notify",
            "sub_type": "poke",
            "sender_id": 30003,
            "target_id": 20002,
        }
        self.assertIsNone(_private_poke_sender(event, self.settings))


class IncomingImageTests(unittest.TestCase):
    def test_extracts_text_and_real_image_reference(self) -> None:
        event = {
            "message": [
                {"type": "text", "data": {"text": "看看这个"}},
                {
                    "type": "image",
                    "data": {
                        "file": "ABC123.jpg",
                        "url": "https://multimedia.nt.qq.com.cn/example",
                        "summary": "",
                    },
                },
            ]
        }
        text, images = _extract_content(event)
        self.assertEqual(text, "看看这个")
        self.assertEqual(len(images), 1)
        self.assertEqual(images[0].file, "ABC123.jpg")

    def test_image_only_message_is_not_discarded(self) -> None:
        event = {
            "message": [
                {"type": "image", "data": {"file": "ONLY.jpg", "url": ""}}
            ]
        }
        text, images = _extract_content(event)
        self.assertEqual(text, "")
        self.assertEqual([image.file for image in images], ["ONLY.jpg"])


class OutgoingImageTests(unittest.IsolatedAsyncioTestCase):
    async def test_sends_local_image_as_onebot_segment(self) -> None:
        settings = OneBotSettings(
            host="127.0.0.1",
            port=8643,
            path="/ws",
            access_token="test-token",
            bot_qq=20002,
            allowed_user_ids=frozenset({10001}),
        )
        server = OneBotReverseServer(settings, AsyncMock())
        server._call_action = AsyncMock(return_value={"status": "ok"})  # type: ignore[method-assign]
        path = Path(__file__).resolve()

        await server.send_private_image(10001, path)

        server._call_action.assert_awaited_once_with(
            "send_private_msg",
            {
                "user_id": 10001,
                "message": [
                    {
                        "type": "image",
                        "data": {"file": path.as_uri()},
                    }
                ],
            },
        )

if __name__ == "__main__":
    unittest.main()
