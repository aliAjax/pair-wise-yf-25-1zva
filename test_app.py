import tempfile
import unittest
from pathlib import Path

from app import BusinessError, ReviewStore


class ReviewFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = ReviewStore(Path(self.tmp.name) / "test.db")
        self.store.seed()

    def tearDown(self):
        self.tmp.cleanup()

    def _paper(self):
        return self.store.submit_paper("alice", "可靠分布式提交协议", "本文提出一种用于弱网环境的可靠提交协议，并通过模拟实验验证其安全性和性能。")["id"]

    def _reviewed_paper(self):
        paper_id = self._paper()
        a1 = self.store.assign("chair", paper_id, "r1")["id"]
        a2 = self.store.assign("chair", paper_id, "r2")["id"]
        self.store.respond_assignment("r1", a1, True)
        self.store.respond_assignment("r2", a2, True)
        self.store.submit_review("r1", a1, 4, "方法严谨，缺少与最近工作的对比。")
        self.store.submit_review("r2", a2, 3, "实验充分，但部分结论需要进一步解释。")
        return paper_id

    def test_complete_flow_and_double_blind_view(self):
        paper_id = self._paper()
        a1 = self.store.assign("chair", paper_id, "r1")["id"]
        a2 = self.store.assign("chair", paper_id, "r2")["id"]
        self.store.respond_assignment("r1", a1, True)
        self.store.respond_assignment("r2", a2, True)
        self.store.submit_review("r1", a1, 4, "方法严谨，缺少与最近工作的对比。")
        self.store.submit_review("r2", a2, 3, "实验充分，但部分结论需要进一步解释。")
        self.store.submit_rebuttal("alice", paper_id, "感谢意见，我们将补充对比并解释实验结论。")
        result = self.store.decide("chair", paper_id, "minor_revision", "补充实验后接收。")
        self.assertEqual(result["decision"], "minor_revision")
        self.assertIsNone(self.store.get_paper("r1", paper_id)["author_id"])
        self.assertIsNotNone(self.store.get_paper("chair", paper_id)["author_id"])
        history = self.store.history("chair", paper_id)
        self.assertEqual(history[-1]["action"], "decision.record")
        self.assertGreaterEqual(len(history), 8)

    def test_conflict_blocks_assignment_and_role_is_enforced(self):
        paper_id = self._paper()
        self.store.add_conflict("chair", paper_id, "r1", "同一导师团队成员")
        with self.assertRaises(BusinessError) as ctx:
            self.store.assign("chair", paper_id, "r1")
        self.assertEqual(ctx.exception.code, "conflict_of_interest")
        with self.assertRaises(BusinessError) as ctx:
            self.store.assign("alice", paper_id, "r2")
        self.assertEqual(ctx.exception.status, 403)
        with self.assertRaises(BusinessError) as ctx:
            self.store.get_paper("r2", paper_id)
        self.assertEqual(ctx.exception.status, 403)

    def test_rebuttal_response_blocks_decision_until_answered(self):
        paper_id = self._reviewed_paper()
        self.store.submit_rebuttal("alice", paper_id, "感谢意见，我们将补充对比并解释实验结论。")
        req = self.store.request_rebuttal_response("chair", paper_id, "r1")
        self.assertEqual(req["status"], "pending")
        # 未回应的请求未清空，不能作决定。
        with self.assertRaises(BusinessError) as ctx:
            self.store.decide("chair", paper_id, "accept")
        self.assertEqual(ctx.exception.code, "pending_rebuttal_responses")
        # 他人不能代替提交回应。
        with self.assertRaises(BusinessError) as ctx:
            self.store.respond_rebuttal_request("r2", req["id"], "我代替别人提交回应内容。")
        self.assertEqual(ctx.exception.status, 404)
        done = self.store.respond_rebuttal_request("r1", req["id"], "作者的补充解释合理，维持原评分。")
        self.assertEqual(done["status"], "answered")
        with self.assertRaises(BusinessError) as ctx:
            self.store.respond_rebuttal_request("r1", req["id"], "再次提交补充说明内容。")
        self.assertEqual(ctx.exception.code, "already_answered")
        # 回应清空后可以决定；原评分保留并仍计入最低要求。
        result = self.store.decide("chair", paper_id, "minor_revision", "参考补充说明后决定。")
        self.assertEqual(result["decision"], "minor_revision")
        items = self.store.list_rebuttal_requests("chair", paper_id)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["score"], 4)
        self.assertEqual(items[0]["response"], "作者的补充解释合理，维持原评分。")
        actions = [h["action"] for h in self.store.history("chair", paper_id)]
        self.assertIn("rebuttal_response.request", actions)
        self.assertIn("rebuttal_response.submit", actions)

    def test_rebuttal_request_rejections(self):
        paper_id = self._reviewed_paper()
        self.store.submit_rebuttal("alice", paper_id, "感谢意见，我们将补充对比并解释实验结论。")
        req = self.store.request_rebuttal_response("chair", paper_id, "r1")
        # 重复请求被拒。
        with self.assertRaises(BusinessError) as ctx:
            self.store.request_rebuttal_response("chair", paper_id, "r1")
        self.assertEqual(ctx.exception.code, "request_exists")
        # 未评完的评审人不能被请求。
        with self.assertRaises(BusinessError) as ctx:
            self.store.request_rebuttal_response("chair", paper_id, "r3")
        self.assertEqual(ctx.exception.code, "review_not_completed")
        # 非主席不能发请求，作者不能查看请求列表。
        with self.assertRaises(BusinessError) as ctx:
            self.store.request_rebuttal_response("alice", paper_id, "r2")
        self.assertEqual(ctx.exception.status, 403)
        with self.assertRaises(BusinessError) as ctx:
            self.store.list_rebuttal_requests("alice", paper_id)
        self.assertEqual(ctx.exception.status, 403)
        mine = self.store.my_rebuttal_requests("r1")
        self.assertEqual([m["id"] for m in mine], [req["id"]])
        # 已有决定后不能再发请求。
        self.store.respond_rebuttal_request("r1", req["id"], "补充说明：作者解释合理。")
        self.store.decide("chair", paper_id, "accept")
        with self.assertRaises(BusinessError) as ctx:
            self.store.request_rebuttal_response("chair", paper_id, "r2")
        self.assertEqual(ctx.exception.code, "paper_decided")


if __name__ == "__main__":
    unittest.main()
