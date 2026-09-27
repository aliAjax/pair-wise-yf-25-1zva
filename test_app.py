import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from app import BusinessError, ReviewServer, ReviewStore


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
        return paper_id, a1, a2

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

    def test_supplement_flow_blocks_decision_until_answered(self):
        paper_id, a1, _ = self._reviewed_paper()
        self.store.submit_rebuttal("alice", paper_id, "感谢意见，我们将补充对比并解释实验结论。")
        req = self.store.request_supplement("chair", paper_id, "r1")
        self.assertEqual(req["status"], "pending")
        with self.assertRaises(BusinessError) as ctx:
            self.store.decide("chair", paper_id, "accept")
        self.assertEqual(ctx.exception.code, "pending_supplements")
        with self.assertRaises(BusinessError) as ctx:
            self.store.respond_supplement("r2", req["id"], "由其他评审人代为补充说明。")
        self.assertEqual(ctx.exception.status, 404)
        with self.assertRaises(BusinessError) as ctx:
            self.store.respond_supplement("r1", req["id"], "太短")
        self.assertEqual(ctx.exception.code, "response_too_short")
        result = self.store.respond_supplement("r1", req["id"], "结合 Rebuttal，新增的对比计划可以弥补主要不足。")
        self.assertEqual(result["status"], "responded")
        with self.assertRaises(BusinessError) as ctx:
            self.store.respond_supplement("r1", req["id"], "重复提交一次补充说明内容。")
        self.assertEqual(ctx.exception.code, "supplement_already_answered")
        items = self.store.list_supplements("chair", paper_id)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["response_text"], "结合 Rebuttal，新增的对比计划可以弥补主要不足。")
        with self.assertRaises(BusinessError) as ctx:
            self.store.list_supplements("alice", paper_id)
        self.assertEqual(ctx.exception.status, 403)
        decision = self.store.decide("chair", paper_id, "minor_revision", "参考补充说明后决定。")
        self.assertEqual(decision["decision"], "minor_revision")
        # 原评分与意见保留，补充说明只是旁边的上下文。
        with self.store.connect() as conn:
            row = conn.execute("SELECT * FROM assignments WHERE id=?", (a1,)).fetchone()
        self.assertEqual((row["status"], row["score"], row["review_text"]), ("completed", 4, "方法严谨，缺少与最近工作的对比。"))
        actions = [h["action"] for h in self.store.history("chair", paper_id)]
        self.assertIn("supplement.request", actions)
        self.assertIn("supplement.respond", actions)

    def test_supplement_request_rejections(self):
        paper_id, _, _ = self._reviewed_paper()
        with self.assertRaises(BusinessError) as ctx:
            self.store.request_supplement("alice", paper_id, "r1")
        self.assertEqual(ctx.exception.status, 403)
        with self.assertRaises(BusinessError) as ctx:
            self.store.request_supplement("chair", paper_id, "r3")
        self.assertEqual(ctx.exception.code, "review_not_completed")
        self.store.request_supplement("chair", paper_id, "r1")
        with self.assertRaises(BusinessError) as ctx:
            self.store.request_supplement("chair", paper_id, "r1")
        self.assertEqual(ctx.exception.code, "supplement_exists")
        self.store.respond_supplement("r1", self.store.list_supplements("chair", paper_id)[0]["id"], "补充说明：接受作者解释的实验设置。")
        self.store.decide("chair", paper_id, "accept")
        with self.assertRaises(BusinessError) as ctx:
            self.store.request_supplement("chair", paper_id, "r2")
        self.assertEqual(ctx.exception.code, "paper_decided")


class SupplementApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        store = ReviewStore(Path(cls.tmp.name) / "api.db")
        store.seed()
        cls.server = ReviewServer(("127.0.0.1", 0), store)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def _api(self, method, path, user, payload=None):
        body = None if payload is None else json.dumps(payload).encode()
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}", data=body, method=method,
            headers={"Content-Type": "application/json", "X-User-Id": user},
        )
        try:
            with urllib.request.urlopen(req) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def test_supplement_endpoints(self):
        status, paper = self._api("POST", "/api/papers", "alice", {"title": "面向边缘缓存的调度策略", "abstract": "本文研究边缘缓存场景下的调度策略，并给出理论分析与大规模实验评估。"})
        self.assertEqual(status, 201)
        pid = paper["id"]
        for reviewer in ("r1", "r2"):
            status, assignment = self._api("POST", f"/api/papers/{pid}/assignments", "chair", {"reviewer_id": reviewer})
            self.assertEqual(status, 201)
            status, _ = self._api("POST", f"/api/assignments/{assignment['id']}/respond", reviewer, {"accepted": True})
            self.assertEqual(status, 200)
            status, _ = self._api("POST", f"/api/assignments/{assignment['id']}/review", reviewer, {"score": 4, "text": "整体质量良好，但实验设置需要进一步说明。"})
            self.assertEqual(status, 201)
        status, _ = self._api("POST", f"/api/papers/{pid}/rebuttal", "alice", {"content": "感谢评审意见，我们将补充实验设置的完整说明。"})
        self.assertEqual(status, 201)
        status, err = self._api("POST", f"/api/papers/{pid}/supplements", "r1", {"reviewer_id": "r1"})
        self.assertEqual((status, err["error"]["code"]), (403, "forbidden"))
        status, req = self._api("POST", f"/api/papers/{pid}/supplements", "chair", {"reviewer_id": "r1"})
        self.assertEqual((status, req["status"]), (201, "pending"))
        status, err = self._api("POST", f"/api/papers/{pid}/decision", "chair", {"decision": "accept"})
        self.assertEqual((status, err["error"]["code"]), (409, "pending_supplements"))
        status, err = self._api("POST", f"/api/supplements/{req['id']}/respond", "r2", {"text": "代替他人提交补充说明内容。"})
        self.assertEqual((status, err["error"]["code"]), (404, "not_found"))
        status, resp = self._api("POST", f"/api/supplements/{req['id']}/respond", "r1", {"text": "补充说明：作者承诺的实验足以解决我的顾虑。"})
        self.assertEqual((status, resp["status"]), (200, "responded"))
        status, listing = self._api("GET", f"/api/papers/{pid}/supplements", "chair")
        self.assertEqual(status, 200)
        self.assertEqual(listing["items"][0]["response_text"], "补充说明：作者承诺的实验足以解决我的顾虑。")
        status, err = self._api("GET", f"/api/papers/{pid}/supplements", "r3")
        self.assertEqual((status, err["error"]["code"]), (403, "forbidden"))
        status, decision = self._api("POST", f"/api/papers/{pid}/decision", "chair", {"decision": "accept"})
        self.assertEqual((status, decision["decision"]), (201, "accept"))
        status, history = self._api("GET", f"/api/papers/{pid}/history", "chair")
        self.assertEqual(status, 200)
        actions = [item["action"] for item in history["items"]]
        self.assertIn("supplement.request", actions)
        self.assertIn("supplement.respond", actions)


if __name__ == "__main__":
    unittest.main()
