from __future__ import annotations

import unittest

from memory_scene import analyze_memory_need_by_rules, parse_memory_need_decision


class MemorySceneTest(unittest.TestCase):
    def test_recall_question_needs_memory(self) -> None:
        decision = analyze_memory_need_by_rules("小昭你还记得我之前说喜欢什么语言吗")

        self.assertTrue(decision.need_memory)
        self.assertIn("喜欢什么语言", decision.query)
        self.assertTrue(decision.reason.startswith("rule:"))

    def test_parse_llm_memory_need_json(self) -> None:
        decision = parse_memory_need_decision(
            '{"need_memory": true, "query": "Python 插件", "reason": "问以前偏好"}',
        )

        self.assertTrue(decision.need_memory)
        self.assertEqual(decision.query, "Python 插件")
        self.assertEqual(decision.reason, "llm:问以前偏好")

    def test_bad_llm_memory_need_output_falls_back_to_skip(self) -> None:
        decision = parse_memory_need_decision("我觉得不用查")

        self.assertFalse(decision.need_memory)
        self.assertEqual(decision.reason, "llm_parse_failed")


if __name__ == "__main__":
    unittest.main()
