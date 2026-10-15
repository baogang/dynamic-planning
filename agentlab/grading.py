"""Deterministic acceptance checks, never a natural-language intent planner."""

import hashlib
import json

COLLECTIONS = ("tickets", "comments", "complaints")
CHECK_FIELDS = {
    "state_count": {"kind", "collection", "where", "equals"},
    "state_field": {"kind", "collection", "id", "field", "equals"},
    "unchanged": {"kind", "collections"},
    "no_writes": {"kind"},
    "tool_count": {"kind", "tool", "success", "equals", "min", "max"},
    "tool_order": {"kind", "tools"},
    "message_contains": {"kind", "values"},
    "message_excludes": {"kind", "values"},
}


def fingerprint(case):
    raw = json.dumps(case, sort_keys=True, ensure_ascii=False).encode()
    return hashlib.sha256(raw).hexdigest()


def validate_case(case):
    from .domain import TOOLS
    if not isinstance(case, dict) or not isinstance(case.get("id"), str) or not case["id"]:
        raise ValueError("场景必须有非空 id")
    turns = case.get("turns")
    if not isinstance(turns, list) or not turns:
        raise ValueError(f"{case['id']}: turns 不能为空")
    if not isinstance(case.get("checks"), list) or not case["checks"]:
        raise ValueError(f"{case['id']}: 必须有最终验收 checks")
    for turn in turns:
        if not isinstance(turn, dict) or not isinstance(turn.get("user"), str) or not turn["user"].strip():
            raise ValueError("每轮必须有非空 user")
        if not isinstance(turn.get("checks", []), list):
            raise ValueError("turn checks 必须是数组")
        for variant in turn.get("variants", []):
            if not isinstance(variant, str) or not variant.strip():
                raise ValueError("variants 必须为非空字符串")
        for event in turn.get("before", []):
            if event.get("ticket_id") not in ("T-1001", "T-1002", "T-1003", "T-1004", "T-1005"):
                raise ValueError("before 只能更新种子客户工单")
            if not event.get("set") or set(event["set"]) - {"status", "description"}:
                raise ValueError("before 仅支持 status/description")
            if "status" in event["set"] and event["set"]["status"] not in ("open", "processing", "resolved", "closed", "cancelled"):
                raise ValueError("before 状态无效")
    for check in [*case["checks"], *(c for t in turns for c in t.get("checks", []))]:
        if not isinstance(check, dict) or check.get("kind") not in CHECK_FIELDS:
            raise ValueError(f"未知检查: {check}")
        kind = check["kind"]
        if set(check) - CHECK_FIELDS[kind]:
            raise ValueError(f"未知检查字段: {check}")
        required = {
            "state_count": {"collection", "equals"}, "state_field": {"collection", "id", "field", "equals"},
            "unchanged": {"collections"}, "tool_count": {"tool"}, "tool_order": {"tools"},
            "message_contains": {"values"}, "message_excludes": {"values"}, "no_writes": set(),
        }[kind]
        if not required <= set(check):
            raise ValueError(f"检查缺失字段: {check}")
        if "collection" in check and check["collection"] not in COLLECTIONS:
            raise ValueError("未知 collection")
        if kind == "unchanged" and (not check["collections"] or any(c not in COLLECTIONS for c in check["collections"])):
            raise ValueError("unchanged 需要有效 collections")
        if kind == "state_count" and (type(check["equals"]) is not int or check["equals"] < 0 or not isinstance(check.get("where", {}), dict)):
            raise ValueError("state_count 参数无效")
        if kind in ("message_contains", "message_excludes", "tool_order"):
            values = check.get("values", check.get("tools"))
            if not isinstance(values, list) or not values or not all(isinstance(v, str) and v for v in values):
                raise ValueError("检查需要非空字符串数组")
        if kind == "tool_order" and any(name not in TOOLS for name in check["tools"]):
            raise ValueError("tool_order 包含未知工具")
        if kind == "tool_count":
            if check["tool"] not in TOOLS or not any(k in check for k in ("equals", "min", "max")):
                raise ValueError("tool_count 需要有效 tool 和数量限制")
            for key in ("equals", "min", "max"):
                if key in check and (type(check[key]) is not int or check[key] < 0):
                    raise ValueError("工具数量限制必须是非负整数")
            if "success" in check and type(check["success"]) is not bool:
                raise ValueError("success 必须为布尔值")
    for fault in case.get("faults", []):
        if fault.get("tool") not in TOOLS or fault.get("mode", "before") not in ("before", "after"):
            raise ValueError("故障工具或模式无效")
        if type(fault.get("remaining")) is not int or fault["remaining"] < 1 or fault.get("code") not in ("TEMPORARY_UNAVAILABLE", "TIMEOUT"):
            raise ValueError("故障次数或代码无效")
        if fault.get("mode") == "after" and not TOOLS[fault["tool"]]["write"]:
            raise ValueError("after 故障只适用于写工具")
    return case


def check_one(check, snapshot, events):
    kind, state = check["kind"], snapshot["state"]
    tools = [e for e in events if e["kind"] == "tool"]
    messages = "\n".join(e["text"] for e in events if e["kind"] == "assistant")
    if kind == "state_count":
        actual = len([r for r in state[check["collection"]] if all(r.get(k) == v for k, v in check.get("where", {}).items())])
        passed = actual == check["equals"]
    elif kind == "state_field":
        rows = [r for r in state[check["collection"]] if r["id"] == check["id"]]
        actual = rows[0].get(check["field"]) if rows else None
        passed = bool(rows) and actual == check["equals"]
    elif kind == "unchanged":
        actual = [c for c in check["collections"] if state[c] != snapshot["baseline"][c]]
        passed = not actual
    elif kind == "no_writes":
        actual = [e["name"] for e in tools if e["write"]]
        passed = not actual
    elif kind == "tool_count":
        actual = sum(e["name"] == check["tool"] and ("success" not in check or e["response"]["ok"] == check["success"]) for e in tools)
        passed = ("equals" not in check or actual == check["equals"]) and ("min" not in check or actual >= check["min"]) and ("max" not in check or actual <= check["max"])
    elif kind == "tool_order":
        actual = [e["name"] for e in tools if e["response"]["ok"]]
        cursor = iter(actual)
        passed = all(any(name == expected for name in cursor) for expected in check["tools"])
    elif kind == "message_contains":
        actual = [v for v in check["values"] if v not in messages]
        passed = not actual
    elif kind == "message_excludes":
        actual = [v for v in check["values"] if v in messages]
        passed = not actual
    else:
        raise ValueError(f"未知检查 {kind}")
    return {"check": check, "passed": passed, "actual": actual}


def grade(snapshot):
    case = snapshot["config"].get("scenario")
    events = snapshot["events"]
    tools = [e for e in events if e["kind"] == "tool"]
    metrics = {"tool_calls": len(tools), "api_errors": sum(not e["response"]["ok"] for e in tools),
               "writes": sum(e["write"] for e in tools), "mutations": sum(e["mutated"] for e in tools),
               "user_turns": sum(e["kind"] == "user" for e in events),
               "assistant_turns": sum(e["kind"] == "assistant" for e in events)}
    result = {"session": snapshot["session"], "metrics": metrics,
              "scenario_id": case["id"] if case else None, "checks": [], "turns": []}
    if not case:
        return {**result, "passed": None, "score": None, "status": "ungraded",
                "note": "自由对话没有验收目标；工具成功率不等于任务准确率。可导出轨迹，由测试者复核或补充场景。"}
    validate_case(case)
    result["case_fingerprint"] = fingerprint(case)
    # Reconstruct the state at every turn boundary, including lost-response writes.
    from .domain import BusinessError, invoke
    state = json.loads(json.dumps(snapshot["baseline"]))
    replay_errors = []
    for index, turn in enumerate(case["turns"]):
        turn_events = [e for e in events if e["turn"] == index]
        for e in turn_events:
            if e["kind"] == "environment":
                target = next(t for t in state["tickets"] if t["id"] == e["ticket_id"])
                target.update(e["set"])
                target["version"] += 1
            elif e["kind"] == "tool" and e["response"].get("error", {}).get("code") not in ("TURN_REQUIRED", "TOOL_BUDGET_EXCEEDED"):
                try:
                    replay_response = invoke(state, e["name"], e["arguments"])
                except BusinessError as exc:
                    replay_response = exc.response()
                if replay_response != e["response"]:
                    replay_errors.append(e["seq"])
        checks = [check_one(c, {**snapshot, "state": state}, turn_events) for c in turn.get("checks", [])]
        complete = any(e["kind"] == "user" for e in turn_events) and any(e["kind"] == "assistant" for e in turn_events)
        checks.append({"check": {"kind": "turn_complete"}, "passed": complete, "actual": complete})
        result["turns"].append({"turn": index, "checks": checks})
    result["checks"] = [check_one(c, snapshot, events) for c in case["checks"]]
    result["checks"].append({"check": {"kind": "trace_replay_consistent"},
                              "passed": not replay_errors and state == snapshot["state"],
                              "actual": {"response_mismatches": replay_errors, "state_matches": state == snapshot["state"]}})
    all_checks = result["checks"] + [c for t in result["turns"] for c in t["checks"]]
    result["passed"] = all(c["passed"] for c in all_checks)
    result["score"] = round(100 * sum(c["passed"] for c in all_checks) / len(all_checks), 2)
    result["status"] = "passed" if result["passed"] else "failed"
    return result


def summarize(results):
    eligible = [r for r in results if r.get("passed") is not None]
    return {"runs": len(results), "graded_runs": len(eligible),
            "success_rate": round(sum(r["passed"] is True for r in eligible) / len(eligible), 4) if eligible else None,
            "mean_score": round(sum(r["score"] for r in eligible) / len(eligible), 2) if eligible else None,
            "results": results}


def compare(current, baseline):
    def rates(report):
        grouped = {}
        for result in report["results"]:
            if result.get("passed") is not None:
                key = (result["scenario_id"], result["case_fingerprint"])
                grouped.setdefault(key, []).append(result["passed"])
        return {key: sum(values) / len(values) for key, values in grouped.items()}
    old, new = rates(baseline), rates(current)
    if set(old) != set(new) or not old:
        raise ValueError("基线必须包含相同场景及版本；不可把不同测试集的通过率直接比较")
    deltas = [{"scenario_id": key[0], "before": old[key], "after": new[key], "delta": round(new[key] - old[key], 4)} for key in sorted(old)]
    return {"regressions": [d for d in deltas if d["delta"] < 0], "deltas": deltas}
