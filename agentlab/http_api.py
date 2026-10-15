"""HTTP transport for the same business tools used by the CLI."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import secrets
from urllib.parse import urlsplit

from .domain import TOOLS


def openapi():
    document = {"openapi": "3.1.0", "info": {"title": "Agent Planning Lab Mock API", "version": "0.1.0"},
            "paths": {
                "/health": {"get": {"responses": {"200": {"description": "Healthy"}}}},
                "/tools": {"get": {"responses": {"200": {"description": "Tool schemas"}}}},
                "/tools/call": {"post": {
                    "parameters": [{"name": "X-Session-Id", "in": "header", "required": True,
                                    "schema": {"type": "string"}}],
                    "requestBody": {"required": True, "content": {"application/json": {"schema": {
                        "oneOf": [{"type": "object", "properties": {
                            "name": {"const": name}, "arguments": tool["inputSchema"]},
                            "required": ["name", "arguments"], "additionalProperties": False}
                                  for name, tool in TOOLS.items()]}}}},
                    "responses": {"200": {"description": "Business result envelope (inspect ok)"},
                                  "400": {"description": "Invalid transport request"},
                                  "404": {"description": "Session not found"}}}}}}
    document["components"] = {"securitySchemes": {"bearerAuth": {"type": "http", "scheme": "bearer"}}}
    for path, method, schema in (
        ("/sessions", "post", {"type": "object", "properties": {"label": {"type": "string", "maxLength": 100}}, "additionalProperties": False}),
        ("/messages", "post", {"type": "object", "properties": {"role": {"enum": ["user", "assistant", "note"]},
                               "text": {"type": "string", "minLength": 1}}, "required": ["role", "text"], "additionalProperties": False}),
        ("/sessions/current", "get", None), ("/sessions/history", "get", None),
    ):
        operation = {"security": [{"bearerAuth": []}],
                     "responses": {"200": {"description": "Session or conversation result"},
                                   "400": {"description": "Invalid request or turn"},
                                   "401": {"description": "Missing or invalid API token"},
                                   "404": {"description": "Session not found"}}}
        if path != "/sessions":
            operation["parameters"] = [{"name": "X-Session-Id", "in": "header", "required": True, "schema": {"type": "string"}}]
        if schema:
            operation["requestBody"] = {"required": True, "content": {"application/json": {"schema": schema}}}
        document["paths"][path] = {method: operation}
    document["paths"]["/tools/call"]["post"]["security"] = [{"bearerAuth": []}]
    document["paths"]["/tools/call"]["post"]["responses"]["401"] = {"description": "Missing or invalid API token"}
    return document


def handler_class(store, api_token=None):
    class Handler(BaseHTTPRequestHandler):
        def send(self, status, value, content_type="application/json; charset=utf-8"):
            raw = value.encode() if isinstance(value, str) else json.dumps(value, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(raw)

        def authorized(self):
            supplied = self.headers.get("Authorization", "").encode()
            expected = f"Bearer {api_token}".encode()
            if api_token and not secrets.compare_digest(supplied, expected):
                self.send(401, {"error": "unauthorized"})
                return False
            return True

        def session_id(self):
            sid = self.headers.get("X-Session-Id")
            if not sid:
                raise ValueError("缺少 X-Session-Id")
            return sid

        def do_GET(self):
            route = urlsplit(self.path).path
            if route == "/":
                self.send(200, """<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Agent Planning Lab</title>
<style>body{max-width:820px;margin:64px auto;padding:24px;font:17px/1.8 system-ui;color:#172033}
code{background:#eef2f6;padding:3px 7px;border-radius:4px}a{color:#2255bf}</style>
<h1>Agent Planning Lab</h1><p>原生 Agent 动态规划测试环境 · Mock 工单与投诉 API</p>
<p>使用当前 Codex、Claude Code、Pi 或其他 harness 自主规划并调用这里的业务工具。</p>
<p><a href="/health">服务健康</a> · <a href="/tools">11 个工具的 Schema</a> · <a href="/openapi.json">OpenAPI</a></p>
<p>远程客户端设置 <code>AGENTLAB_URL</code> 和 <code>AGENTLAB_API_TOKEN</code> 后，运行
<code>python3 -m agentlab init</code> 创建测试会话，再直接向 Agent 提需求。</p>
<p>HTTP 调用：<code>POST /sessions</code> 创建会话，<code>POST /messages</code> 记录对话，
<code>POST /tools/call</code> 调用工具。会话请求使用 Bearer token 和 X-Session-Id。</p>
<p>这里托管模拟业务环境；自然语言任务由你的原生 Agent 执行。测试数据可能在服务重启或重新部署后重置。</p>
</html>""", "text/html; charset=utf-8")
            elif route == "/health":
                self.send(200, {"ok": True, "service": "agentlab"})
            elif route == "/tools":
                self.send(200, {"tools": list(TOOLS.values())})
            elif route == "/openapi.json":
                self.send(200, openapi())
            elif route in ("/sessions/current", "/sessions/history"):
                if not self.authorized():
                    return
                try:
                    sid = self.session_id()
                    self.send(200, store.metadata(sid) if route.endswith("current") else store.conversation(sid))
                except ValueError as exc:
                    self.send(404, {"error": str(exc)})
            else:
                self.send(404, {"error": "route_not_found"})

        def do_POST(self):
            route = urlsplit(self.path).path
            if route not in ("/tools/call", "/sessions", "/messages"):
                self.send(404, {"error": "route_not_found"})
                return
            if not self.authorized():
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length < 1 or length > 65536:
                    raise ValueError("请求需包含不超过 64 KiB 的 JSON body")
                body = json.loads(self.rfile.read(length))
                if not isinstance(body, dict):
                    raise ValueError("body 必须是 JSON 对象")
                if route == "/sessions":
                    label = body.get("label", "remote")
                    if set(body) - {"label"} or not isinstance(label, str) or not 1 <= len(label.strip()) <= 100:
                        raise ValueError("只接受非空 label，最多 100 字符")
                    self.send(200, store.create(label.strip(), activate=False))
                    return
                if route == "/messages":
                    if set(body) != {"role", "text"} or body["role"] not in ("user", "assistant", "note"):
                        raise ValueError("消息需要 role(user/assistant/note) 和 text")
                    sid = self.session_id()
                    result = store.user(sid, body["text"]) if body["role"] == "user" else store.say(sid, body["role"], body["text"])
                    self.send(200, result)
                    return
                if set(body) != {"name", "arguments"} or not isinstance(body["name"], str):
                    raise ValueError("body 必须只包含 name 字符串及 arguments")
                sid = self.session_id()
            except (ValueError, UnicodeError) as exc:
                self.send(400, {"error": str(exc)})
                return
            try:
                self.send(200, store.call(sid, body["name"], body["arguments"]))
            except ValueError as exc:
                self.send(404, {"error": str(exc)})

        def log_message(self, *_):
            pass

    return Handler


def serve(store, port=8765, host="127.0.0.1"):
    api_token = os.environ.get("AGENTLAB_API_TOKEN")
    if host not in ("127.0.0.1", "localhost", "::1") and (not api_token or len(api_token) < 32):
        raise ValueError("公网监听需设置至少 32 字符的 AGENTLAB_API_TOKEN")
    server = ThreadingHTTPServer((host, port), handler_class(store, api_token))
    print(f"Mock API: http://{host}:{server.server_port}", flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()
