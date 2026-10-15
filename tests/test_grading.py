import copy
import json
from pathlib import Path
import tempfile
import unittest

from agentlab.benchmark import load_cases, next_prompt, start_case
from agentlab.grading import compare, grade, summarize, validate_case
from agentlab.store import Store

ROOT = Path(__file__).resolve().parent.parent


class GradingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name) / "lab.sqlite3")
        self.cases = load_cases()
        self.references = json.loads((ROOT / "tests/reference_traces.json").read_text())

    def replay(self, case, *, skip_tool=None, wrong_reply=None, alter_args=None):
        sid = start_case(self.store, case)["session_id"]
        for turn in self.references[case["id"]]:
            next_prompt(self.store, sid)
            for step in turn["steps"]:
                if step["tool"] == skip_tool:
                    continue
                args = copy.deepcopy(step["arguments"])
                if alter_args:
                    args = alter_args(step["tool"], args)
                self.store.call(sid, step["tool"], args)
            self.store.say(sid, "assistant", wrong_reply or turn["reply"])
        return grade(self.store.export(sid))

    def test_all_reference_traces_pass_their_acceptance_checks(self):
        self.assertEqual(set(self.references), {c["id"] for c in self.cases})
        for case in self.cases:
            with self.subTest(case=case["id"]):
                result = self.replay(case)
                self.assertTrue(result["passed"], json.dumps(result, ensure_ascii=False, indent=2))

    def test_missing_business_action_fails_even_with_successful_sounding_reply(self):
        case = next(c for c in self.cases if c["id"] == "create.complete")
        self.assertFalse(self.replay(case, skip_tool="ticket.create")["passed"])

    def test_out_of_band_state_changes_are_detected_by_trace_replay(self):
        case = next(c for c in self.cases if c["id"] == "query.all")
        good = self.replay(case)
        snapshot = self.store.export(good["session"]["session_id"])
        snapshot["state"]["tickets"][0]["priority"] = "high"
        result = grade(snapshot)
        integrity = next(c for c in result["checks"] if c["check"]["kind"] == "trace_replay_consistent")
        self.assertFalse(integrity["passed"])
        self.assertFalse(result["passed"])

    def test_wrong_target_fails(self):
        case = next(c for c in self.cases if c["id"] == "complaint.choose")
        def wrong(name, args):
            if name == "complaint.create":
                args["ticket_id"] = "T-1002"
            return args
        self.assertFalse(self.replay(case, alter_args=wrong)["passed"])

    def test_missing_page_and_leaking_private_data_fail(self):
        case = next(c for c in self.cases if c["id"] == "query.all")
        result = self.replay(case, wrong_reply="只有 T-1001 T-1002 T-1003，另有 T-9001")
        self.assertFalse(result["passed"])

    def test_unauthorized_write_attempt_before_selection_fails(self):
        case = next(c for c in self.cases if c["id"] == "complaint.choose")
        sid = start_case(self.store, case)["session_id"]
        next_prompt(self.store, sid)
        self.store.call(sid, "complaint.create", {"ticket_id": "T-1001", "reason": "delay", "description": "用户还没有选择投诉目标", "idempotency_key": "early"})
        self.store.say(sid, "assistant", "T-1001 T-1002 T-1003 请选择")
        result = grade(self.store.export(sid))
        first = result["turns"][0]["checks"]
        self.assertFalse(next(c for c in first if c["check"]["kind"] == "no_writes")["passed"])
        self.assertFalse(result["passed"])

    def test_incomplete_case_and_ungraded_manual_are_distinct(self):
        case = self.cases[0]
        sid = start_case(self.store, case)["session_id"]
        self.assertFalse(grade(self.store.export(sid))["passed"])
        manual = self.store.create()["session_id"]
        result = grade(self.store.export(manual))
        self.assertIsNone(result["passed"])
        self.assertIsNone(summarize([result])["success_rate"])

    def test_case_validation_rejects_unknown_or_vacuous_checks(self):
        examples = [
            {"kind": "always_pass"}, {"kind": "no_writes", "typo": 1},
            {"kind": "tool_count", "tool": "ticket.list"},
            {"kind": "message_contains", "values": []},
            {"kind": "unchanged", "collections": []},
        ]
        for check in examples:
            case = copy.deepcopy(self.cases[0])
            case["checks"] = [check]
            with self.subTest(check=check), self.assertRaises(ValueError):
                validate_case(case)

    def test_baseline_comparison_uses_case_versions_and_all_repetitions(self):
        good = self.replay(self.cases[0])
        bad = {**good, "passed": False, "score": 0}
        old = summarize([good, good])
        new = summarize([good, bad])
        difference = compare(new, old)
        self.assertEqual(difference["regressions"][0]["delta"], -0.5)
        with self.assertRaises(ValueError):
            compare(summarize([{**good, "case_fingerprint": "changed"}]), old)
        with self.assertRaises(ValueError):
            compare(summarize([]), old)

    def test_next_prompt_requires_final_reply_and_correct_user(self):
        case = next(c for c in self.cases if c["id"] == "create.missing")
        sid = start_case(self.store, case)["session_id"]
        first = next_prompt(self.store, sid)
        self.assertFalse(first["done"])
        with self.assertRaises(ValueError):
            next_prompt(self.store, sid)
        self.store.say(sid, "assistant", "请提供问题")
        with self.assertRaises(ValueError):
            self.store.user(sid, "不匹配的输入")
        next_prompt(self.store, sid)
        self.store.say(sid, "assistant", "结束")
        self.assertTrue(next_prompt(self.store, sid)["done"])


if __name__ == "__main__":
    unittest.main()
