"""Transactional, session-isolated persistence and observable execution traces."""

import copy
import json
import os
from pathlib import Path
import sqlite3
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone

from .domain import BusinessError, TOOLS, invoke, seed_state


def dumps(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def utc_now():
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, path=None):
        self.path = Path(path or os.environ.get("AGENTLAB_DB", ".agentlab/lab.sqlite3")).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.active_path = self.path.with_suffix(".active")
        with self.connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY, label TEXT NOT NULL, created_at TEXT NOT NULL,
                    turn_index INTEGER NOT NULL DEFAULT -1, state TEXT NOT NULL,
                    baseline TEXT NOT NULL, config TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL,
                    turn_index INTEGER NOT NULL, timestamp TEXT NOT NULL,
                    kind TEXT NOT NULL, payload TEXT NOT NULL,
                    FOREIGN KEY(session_id) REFERENCES sessions(id));
                CREATE INDEX IF NOT EXISTS events_session ON events(session_id, id);
            """)

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def current_id(self, explicit=None):
        session_id = explicit or os.environ.get("AGENTLAB_SESSION_ID")
        if not session_id and self.active_path.exists():
            session_id = self.active_path.read_text().strip()
        if not session_id:
            raise ValueError("没有活动会话，请先执行 python3 -m agentlab init")
        return session_id

    def create(self, label="manual", *, config=None, state=None, activate=True):
        state = copy.deepcopy(state if state is not None else seed_state())
        session_id = uuid.uuid4().hex[:16]
        with self.connection() as db:
            db.execute("INSERT INTO sessions(id,label,created_at,state,baseline,config) VALUES(?,?,?,?,?,?)",
                       (session_id, label, utc_now(), dumps(state), dumps(state), dumps(config or {})))
        if activate:
            self.active_path.write_text(session_id + "\n")
        return self.metadata(session_id)

    def _row(self, db, session_id):
        row = db.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
        if row is None:
            raise ValueError(f"会话不存在: {session_id}")
        return row

    def metadata(self, session_id):
        with self.connection() as db:
            row = self._row(db, session_id)
        config = json.loads(row["config"])
        return {"session_id": row["id"], "label": row["label"], "created_at": row["created_at"],
                "turn_index": row["turn_index"], "scenario_id": config.get("scenario", {}).get("id")}

    def _event(self, db, session_id, turn_index, kind, payload):
        db.execute("INSERT INTO events(session_id,turn_index,timestamp,kind,payload) VALUES(?,?,?,?,?)",
                   (session_id, turn_index, utc_now(), kind, dumps(payload)))

    def _events(self, db, session_id):
        return [{"seq": row["id"], "turn": row["turn_index"], "timestamp": row["timestamp"],
                 "kind": row["kind"], **json.loads(row["payload"])}
                for row in db.execute("SELECT * FROM events WHERE session_id=? ORDER BY id", (session_id,))]

    def export(self, session_id):
        with self.connection() as db:
            row = self._row(db, session_id)
            events = self._events(db, session_id)
        return {"format_version": 1, "session": self.metadata(session_id), "events": events,
                "state": json.loads(row["state"]), "baseline": json.loads(row["baseline"]),
                "config": json.loads(row["config"])}

    def conversation(self, session_id):
        with self.connection() as db:
            self._row(db, session_id)
            return [event for event in self._events(db, session_id)
                    if event["kind"] in ("user", "assistant", "note", "tool")]

    def user(self, session_id, text):
        if not isinstance(text, str) or not text.strip():
            raise ValueError("用户消息不能为空")
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._row(db, session_id)
            events = self._events(db, session_id)
            current = [e for e in events if e["turn"] == row["turn_index"]]
            # A benchmark operator may have already recorded the supplied prompt.
            if current and current[0]["kind"] == "user" and current[0]["text"] == text and not any(
                    e["kind"] == "assistant" for e in current):
                return {"turn": row["turn_index"], "already_recorded": True}
            if row["turn_index"] >= 0 and current and not any(e["kind"] == "assistant" for e in current):
                raise ValueError("上一轮尚未回复；先记录 reply，再开启下一轮")
            turn = row["turn_index"] + 1
            config = json.loads(row["config"])
            scenario = config.get("scenario")
            state = json.loads(row["state"])
            if scenario:
                if turn >= len(scenario["turns"]):
                    raise ValueError("此场景已结束；自由测试请新建 manual 会话")
                expected = config.get("prompts", [t["user"] for t in scenario["turns"]])[turn]
                if text != expected:
                    raise ValueError("场景输入与当前轮次不符；自由输入请新建 manual 会话")
                for event in scenario["turns"][turn].get("before", []):
                    target = next(t for t in state["tickets"] if t["id"] == event["ticket_id"])
                    target.update(event["set"])
                    target["version"] += 1
            db.execute("UPDATE sessions SET turn_index=?, state=? WHERE id=?", (turn, dumps(state), session_id))
            self._event(db, session_id, turn, "user", {"text": text})
            if scenario:
                for event in scenario["turns"][turn].get("before", []):
                    self._event(db, session_id, turn, "environment", event)
            return {"turn": turn}

    def say(self, session_id, kind, text):
        if kind not in ("assistant", "note") or not isinstance(text, str) or not text.strip():
            raise ValueError("需要非空的 reply 或 note")
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._row(db, session_id)
            current = [e for e in self._events(db, session_id) if e["turn"] == row["turn_index"]]
            if row["turn_index"] < 0 or any(e["kind"] == "assistant" for e in current):
                raise ValueError("请先记录本轮 user；每轮只能有一个最终 reply")
            self._event(db, session_id, row["turn_index"], kind, {"text": text})
        return {"recorded": kind, "turn": row["turn_index"]}

    def call(self, session_id, name, arguments):
        started = time.monotonic()
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._row(db, session_id)
            state, config = json.loads(row["state"]), json.loads(row["config"])
            events = self._events(db, session_id)
            baseline_business = {k: copy.deepcopy(state[k]) for k in ("tickets", "comments", "complaints")}
            try:
                if row["turn_index"] < 0 or any(e["kind"] == "assistant" and e["turn"] == row["turn_index"] for e in events):
                    raise BusinessError("TURN_REQUIRED", "请先记录本轮 user，再调用业务工具")
                if len([e for e in events if e["kind"] == "tool"]) >= config.get("max_tools", 50):
                    raise BusinessError("TOOL_BUDGET_EXCEEDED", "本会话工具调用次数已达上限")
                response = invoke(state, name, arguments)
            except BusinessError as exc:
                response = exc.response()
            mutated = any(state[k] != baseline_business[k] for k in baseline_business)
            self._event(db, session_id, row["turn_index"], "tool",
                        {"name": name, "arguments": arguments, "response": response,
                         "write": TOOLS.get(name, {}).get("write", False), "mutated": mutated,
                         "elapsed_ms": round((time.monotonic() - started) * 1000, 3)})
            db.execute("UPDATE sessions SET state=? WHERE id=?", (dumps(state), session_id))
        return response
