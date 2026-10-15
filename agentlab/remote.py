"""CLI transport to a hosted Mock environment. No model or planning logic."""

import json
import os
from pathlib import Path
import sys
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


class Remote:
    def __init__(self, base_url=None, token=None, binding=None):
        self.base_url = (base_url or os.environ["AGENTLAB_URL"]).rstrip("/")
        parsed = urlsplit(self.base_url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("AGENTLAB_URL 必须为不含凭据、query 或 fragment 的 HTTP(S) URL")
        if parsed.scheme != "https" and parsed.hostname not in ("127.0.0.1", "localhost", "::1"):
            raise ValueError("远程服务必须使用 HTTPS")
        self.token = token if token is not None else os.environ.get("AGENTLAB_API_TOKEN", "")
        self.binding = Path(binding or os.environ.get("AGENTLAB_REMOTE_SESSION_FILE", ".agentlab/remote-session.json"))

    def session_id(self, explicit=None):
        if explicit or os.environ.get("AGENTLAB_SESSION_ID"):
            return explicit or os.environ["AGENTLAB_SESSION_ID"]
        if self.binding.exists():
            saved = json.loads(self.binding.read_text())
            if saved["base_url"] == self.base_url:
                return saved["session_id"]
        raise ValueError("远程服务尚无活动会话，请先运行 python3 -m agentlab init")

    def request(self, route, body=None, session_id=None):
        headers = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        if session_id:
            headers["X-Session-Id"] = session_id
        raw = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
        if raw is not None:
            headers["Content-Type"] = "application/json"
        request = Request(self.base_url + route, data=raw, headers=headers, method="POST" if raw is not None else "GET")
        try:
            with build_opener(NoRedirect()).open(request, timeout=120) as response:
                return json.load(response)
        except HTTPError as exc:
            detail = exc.read().decode(errors="replace")
            raise ValueError(f"远程服务返回 HTTP {exc.code}: {detail}") from None

    def create(self, label="remote"):
        session = self.request("/sessions", {"label": label})
        self.binding.parent.mkdir(parents=True, exist_ok=True)
        self.binding.write_text(json.dumps({"base_url": self.base_url, "session_id": session["session_id"]}) + "\n")
        return session


def dispatch(args, emit):
    remote = Remote()
    command = args.command
    if command == "tools":
        result = remote.request("/tools")
    elif command == "init":
        try:
            sid = remote.session_id(args.session)
        except ValueError:
            result = remote.create()
        else:
            result = remote.request("/sessions/current", session_id=sid)
    elif command == "session" and args.action == "start":
        result = remote.create(args.label)
    elif command == "session":
        result = remote.request("/sessions/current" if args.action == "current" else "/sessions/history", session_id=remote.session_id(args.session))
    elif command in ("user", "reply", "note"):
        text = sys.stdin.read().removesuffix("\n") if args.stdin else args.text
        result = remote.request("/messages", {"role": "assistant" if command == "reply" else command, "text": text}, remote.session_id(args.session))
    elif command == "call":
        arguments = json.loads(sys.stdin.read() if args.stdin else args.arguments)
        result = remote.request("/tools/call", {"name": args.name, "arguments": arguments}, remote.session_id(args.session))
        emit(result)
        return 0 if result["ok"] else 2
    else:
        raise ValueError("远程模式支持 init/session/user/reply/note/tools/call；场景管理和评分仍在本地运行，请取消 AGENTLAB_URL 后执行")
    emit(result)
    return 0
