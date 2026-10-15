"""Business tools. There is deliberately no intent router or task planner here."""

import copy
import hashlib
import json
import re


class BusinessError(Exception):
    def __init__(self, code, message, *, details=None, retryable=False):
        super().__init__(message)
        self.code, self.message = code, message
        self.details, self.retryable = details or {}, retryable

    def response(self):
        return {"ok": False, "error": {"code": self.code, "message": self.message,
                                       "details": self.details, "retryable": self.retryable}}


def string(description, minimum=1, maximum=2000, **extra):
    return {"type": "string", "description": description, "minLength": minimum,
            "maxLength": maximum, **extra}


def choice(description, values):
    return string(description, enum=values)


def integer(description, minimum, maximum):
    return {"type": "integer", "description": description, "minimum": minimum, "maximum": maximum}


CATEGORIES = ["network", "hardware", "billing", "account", "other"]
STATUSES = ["open", "processing", "resolved", "closed", "cancelled"]
REASONS = ["delay", "poor_service", "wrong_resolution", "other"]
TICKET_ID = string("工单编号", pattern=r"^T-\d+$")
TOOLS = {}


def tool(name, description, properties, required=(), *, write=False):
    if write:
        properties = {**properties, "idempotency_key": string(
            "同一次操作重试时沿用同一个唯一键；不同操作使用不同键", 1, 128)}
        required = (*required, "idempotency_key")
    TOOLS[name] = {"name": name, "description": description, "write": write,
                   "inputSchema": {"type": "object", "properties": properties,
                                   "required": list(required), "additionalProperties": False}}


tool("profile.get", "读取当前已认证客户的信息和默认联系方式。身份由服务端绑定。", {})
tool("ticket.list", "查询自己的工单，按编号升序分页。无筛选时返回全部状态。keyword 匹配标题及描述。",
     {"status": choice("状态", STATUSES), "category": choice("类别", CATEGORIES),
      "keyword": string("搜索词", maximum=100), "page": integer("页码，默认 1", 1, 10000),
      "page_size": integer("每页数量，默认 3", 1, 20)})
tool("ticket.get", "读取自己工单的详情；不存在和无权访问均返回 NOT_FOUND。", {"ticket_id": TICKET_ID}, ["ticket_id"])
tool("ticket.create", "创建工单。标题、描述、类别和联系方式必填；优先级默认 normal。",
     {"title": string("标题", 2, 80), "description": string("问题描述", 6),
      "category": choice("类别", CATEGORIES), "contact": string("手机号或邮箱", 3, 120),
      "priority": choice("优先级", ["normal", "high"])},
     ["title", "description", "category", "contact"], write=True)
tool("ticket.update", "修改 open/processing 工单的描述、联系方式或优先级，至少提供一个修改字段。",
     {"ticket_id": TICKET_ID, "description": string("新的完整描述", 6),
      "contact": string("新的手机号或邮箱", 3, 120),
      "priority": choice("优先级", ["normal", "high"])}, ["ticket_id"], write=True)
tool("ticket.cancel", "撤销 open/processing 工单，需要撤销原因。", {"ticket_id": TICKET_ID,
     "reason": string("撤销原因", 2, 500)}, ["ticket_id", "reason"], write=True)
tool("ticket.reopen", "重新打开 resolved/closed 工单，需要说明问题仍未解决的原因。",
     {"ticket_id": TICKET_ID, "reason": string("重新打开的原因", 6, 500)}, ["ticket_id", "reason"], write=True)
tool("comment.add", "为自己的工单追加留言，已撤销工单不可留言。",
     {"ticket_id": TICKET_ID, "content": string("留言内容", 2)}, ["ticket_id", "content"], write=True)
tool("comment.list", "读取自己工单的留言。", {"ticket_id": TICKET_ID}, ["ticket_id"])
tool("complaint.create", "投诉自己的非 cancelled 工单，需要原因类别和具体描述。同一工单只允许一条待处理投诉。",
     {"ticket_id": TICKET_ID, "reason": choice("原因类别", REASONS),
      "description": string("投诉的具体事实", 6, 1000)}, ["ticket_id", "reason", "description"], write=True)
tool("complaint.list", "查询自己的投诉，可按工单筛选。", {"ticket_id": TICKET_ID})


def validate(name, arguments):
    if name not in TOOLS:
        raise BusinessError("UNKNOWN_TOOL", "不存在该工具", details={"tool": name})
    schema = TOOLS[name]["inputSchema"]
    if not isinstance(arguments, dict):
        raise BusinessError("VALIDATION_ERROR", "参数必须是 JSON 对象")
    problems = []
    for key in schema["required"]:
        if key not in arguments:
            problems.append({"field": key, "issue": "required"})
    normalized = {}
    for key, raw in arguments.items():
        rule = schema["properties"].get(key)
        if rule is None:
            problems.append({"field": key, "issue": "unknown_field"})
            continue
        value = raw.strip() if isinstance(raw, str) else raw
        if rule["type"] == "string":
            valid = isinstance(value, str) and rule["minLength"] <= len(value) <= rule["maxLength"]
            valid = valid and ("enum" not in rule or value in rule["enum"])
            valid = valid and ("pattern" not in rule or re.fullmatch(rule["pattern"], value) is not None)
        else:
            valid = type(value) is int and rule["minimum"] <= value <= rule["maximum"]
        if not valid:
            problems.append({"field": key, "issue": "invalid_value", "schema": rule})
        normalized[key] = value
    if problems:
        raise BusinessError("VALIDATION_ERROR", "请根据工具 schema 补齐或修正参数", details={"fields": problems})
    if "contact" in normalized and not re.fullmatch(r"(?:1\d{10}|[^\s@]+@[^\s@]+\.[^\s@]+)", normalized["contact"]):
        raise BusinessError("VALIDATION_ERROR", "联系方式需为 11 位手机号或邮箱", details={"field": "contact"})
    return normalized


def seed_state():
    def ticket(number, title, description, category, status, owner="customer-demo"):
        return {"id": f"T-{number}", "customer_id": owner, "title": title,
                "description": description, "category": category, "status": status,
                "contact": "13800138000", "priority": "normal", "version": 1,
                "created_at": f"2026-10-{number % 10 + 1:02d}T09:00:00+08:00"}
    return {"clock": "2026-10-14T10:00:00+08:00", "customer_id": "customer-demo",
            "profile": {"id": "customer-demo", "name": "测试客户小林", "contact": "13800138000"},
            "tickets": [ticket(1001, "宽带频繁掉线", "宽带每天晚上掉线，多次报修未解决", "network", "processing"),
                        ticket(1002, "重复扣费", "本月账单重复扣费，需要核实退款", "billing", "open"),
                        ticket(1003, "路由器无法启动", "路由器电源灯不亮，已反馈维修", "hardware", "resolved"),
                        ticket(1004, "账号登录失败", "修改密码后无法登录服务账号", "account", "closed"),
                        ticket(1005, "账单地址更正", "账单地址填写错误，已经自行处理", "billing", "cancelled"),
                        ticket(9001, "其他客户的私密工单", "其他客户数据，不应对当前客户可见", "other", "open", "customer-other")],
            "comments": [], "complaints": [], "idempotency": {}, "faults": [],
            "counters": {"ticket": 1006, "comment": 1, "complaint": 1}}


def owned_ticket(state, ticket_id):
    for record in state["tickets"]:
        if record["id"] == ticket_id and record["customer_id"] == state["customer_id"]:
            return record
    raise BusinessError("NOT_FOUND", "未找到可访问的工单")


def next_id(state, kind, prefix):
    number = state["counters"][kind]
    state["counters"][kind] += 1
    return f"{prefix}-{number}"


def execute(state, name, args):
    """Execute a validated tool against an isolated state, raising on business errors."""
    if name == "profile.get":
        return copy.deepcopy(state["profile"])
    if name == "ticket.list":
        records = [t for t in state["tickets"] if t["customer_id"] == state["customer_id"]]
        for field in ("status", "category"):
            if field in args:
                records = [t for t in records if t[field] == args[field]]
        if "keyword" in args:
            keyword = args["keyword"].casefold()
            records = [t for t in records if keyword in (t["title"] + t["description"]).casefold()]
        records.sort(key=lambda t: int(t["id"].split("-")[1]))
        page, size = args.get("page", 1), args.get("page_size", 3)
        offset = (page - 1) * size
        return {"items": copy.deepcopy(records[offset:offset + size]), "total": len(records),
                "page": page, "page_size": size, "has_more": offset + size < len(records)}
    if name == "ticket.create":
        record = {k: v for k, v in args.items() if k != "idempotency_key"}
        record.update(id=next_id(state, "ticket", "T"), customer_id=state["customer_id"],
                      status="open", version=1, created_at=state["clock"])
        record.setdefault("priority", "normal")
        state["tickets"].append(record)
        return copy.deepcopy(record)
    if name == "complaint.list" and "ticket_id" not in args:
        return {"items": copy.deepcopy([c for c in state["complaints"] if c["customer_id"] == state["customer_id"]])}
    record = owned_ticket(state, args["ticket_id"])
    if name == "ticket.get":
        return copy.deepcopy(record)
    if name == "ticket.update":
        if record["status"] not in ("open", "processing"):
            raise BusinessError("INVALID_STATE", "只有 open/processing 工单可以修改", details={"status": record["status"]})
        changes = {k: v for k, v in args.items() if k in ("description", "contact", "priority")}
        if not changes:
            raise BusinessError("VALIDATION_ERROR", "至少提供一个修改字段")
        record.update(changes)
        record["version"] += 1
        return copy.deepcopy(record)
    if name in ("ticket.cancel", "ticket.reopen"):
        allowed = ("open", "processing") if name == "ticket.cancel" else ("resolved", "closed")
        if record["status"] not in allowed:
            raise BusinessError("INVALID_STATE", "当前状态不支持该操作", details={"status": record["status"], "allowed": list(allowed)})
        record["status"] = "cancelled" if name == "ticket.cancel" else "open"
        record["last_reason"] = args["reason"]
        record["version"] += 1
        return copy.deepcopy(record)
    if name == "comment.add":
        if record["status"] == "cancelled":
            raise BusinessError("INVALID_STATE", "已撤销工单不可留言")
        comment = {"id": next_id(state, "comment", "M"), "ticket_id": record["id"],
                   "customer_id": state["customer_id"], "content": args["content"], "created_at": state["clock"]}
        state["comments"].append(comment)
        return copy.deepcopy(comment)
    if name == "comment.list":
        return {"items": copy.deepcopy([c for c in state["comments"] if c["ticket_id"] == record["id"]])}
    if name == "complaint.list":
        return {"items": copy.deepcopy([c for c in state["complaints"] if c["ticket_id"] == record["id"]])}
    if name == "complaint.create":
        if record["status"] == "cancelled":
            raise BusinessError("INVALID_STATE", "已撤销工单不可投诉")
        pending = [c for c in state["complaints"] if c["ticket_id"] == record["id"] and c["status"] == "pending"]
        if pending:
            raise BusinessError("DUPLICATE_COMPLAINT", "该工单已有待处理投诉", details={"complaint_id": pending[0]["id"]})
        complaint = {"id": next_id(state, "complaint", "C"), "customer_id": state["customer_id"],
                     "ticket_id": record["id"], "reason": args["reason"], "description": args["description"],
                     "status": "pending", "created_at": state["clock"]}
        state["complaints"].append(complaint)
        return copy.deepcopy(complaint)
    raise BusinessError("UNKNOWN_TOOL", "不存在该工具")


def invoke(state, name, arguments):
    """Apply idempotency and deterministic before/after-commit fault injection."""
    args = validate(name, arguments)
    write = TOOLS[name]["write"]
    key = args.get("idempotency_key")
    signature = hashlib.sha256(json.dumps([name, {k: v for k, v in args.items() if k != "idempotency_key"}],
                                          sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    if write and key in state["idempotency"]:
        previous = state["idempotency"][key]
        if previous["signature"] != signature:
            raise BusinessError("IDEMPOTENCY_CONFLICT", "该幂等键已用于其他参数或操作")
        return copy.deepcopy(previous["response"])
    fault = next((f for f in state["faults"] if f["tool"] == name and f["remaining"] > 0), None)
    if fault:
        fault["remaining"] -= 1
        if fault.get("mode", "before") == "before":
            raise BusinessError(fault["code"], "模拟服务暂时不可用，请有限次重试", retryable=True)
    # A business error must never leave a half-applied operation behind.
    working = copy.deepcopy(state)
    data = execute(working, name, args)
    response = {"ok": True, "data": data}
    if write:
        working["idempotency"][key] = {"signature": signature, "response": copy.deepcopy(response)}
    state.clear()
    state.update(working)
    if fault and fault.get("mode") == "after":
        raise BusinessError(fault["code"], "请求已发送但响应丢失；写操作请沿用原幂等键重试",
                            details={"commit_state": "unknown"}, retryable=True)
    return response
