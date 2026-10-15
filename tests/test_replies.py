import unittest

from qq_codex_companion.replies import parse_reply, parse_reply_plan


class ReplyParsingTests(unittest.TestCase):
    def test_structured_messages_stay_as_bubbles(self) -> None:
        raw = '{"messages":["今天怎么样？","我在听。"]}'
        self.assertEqual(
            parse_reply(raw, max_parts=4, max_chars=160),
            ["今天怎么样？", "我在听。"],
        )

    def test_plain_text_never_splits_into_characters(self) -> None:
        reply = parse_reply("这是完整的一句话。还有下一句。", max_parts=4, max_chars=12)
        self.assertEqual(reply, ["这是完整的一句话。", "还有下一句。"])

    def test_overflow_folds_into_last_bubble(self) -> None:
        raw = '{"messages":["一。","二。","三。","四。","五。"]}'
        self.assertEqual(
            parse_reply(raw, max_parts=3, max_chars=160),
            ["一。", "二。", "三。四。五。"],
        )

    def test_reply_plan_preserves_poke_decision(self) -> None:
        plan = parse_reply_plan(
            '{"messages":["抓到你了"],"poke_user":true}',
            max_parts=4,
            max_chars=160,
        )
        self.assertEqual(plan.messages, ["抓到你了"])
        self.assertTrue(plan.poke_user)

    def test_reply_plan_preserves_private_memory_controls(self) -> None:
        plan = parse_reply_plan(
            '{"messages":["记住啦"],"poke_user":false,'
            '"memory_updates":[{"content":"用户喜欢黑咖啡","category":"preference",'
            '"importance":3,"ttl_days":0,"keywords":["咖啡"]}],'
            '"memory_forget":["拿铁"]}',
            max_parts=4,
            max_chars=160,
        )
        self.assertEqual(plan.memory_updates[0].content, "用户喜欢黑咖啡")
        self.assertEqual(plan.memory_updates[0].keywords, ("咖啡",))
        self.assertEqual(plan.memory_forget, ("拿铁",))

    def test_poke_event_can_choose_silent_response(self) -> None:
        plan = parse_reply_plan(
            '{"messages":[],"poke_user":false}',
            max_parts=2,
            max_chars=160,
            allow_empty=True,
        )
        self.assertEqual(plan.messages, [])
        self.assertFalse(plan.poke_user)


if __name__ == "__main__":
    unittest.main()
