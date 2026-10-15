import argparse
import json
import os
from pathlib import Path
import sys

from .benchmark import load_cases, next_prompt, run_suite, start_case
from .domain import TOOLS
from .grading import compare, grade, summarize
from .store import Store


def emit(value):
    print(json.dumps(value, ensure_ascii=False, indent=2))


def text_argument(parser):
    parser.add_argument("text", nargs="?")
    parser.add_argument("--stdin", action="store_true", help="从标准输入读取完整文本，避免 shell 转义")


def main(argv=None):
    parser = argparse.ArgumentParser(description="原生 Agent 动态任务规划测试场地；不含 Agent 实现")
    parser.add_argument("--db", help="SQLite 文件，默认 AGENTLAB_DB 或 .agentlab/lab.sqlite3")
    parser.add_argument("--session", help="会话 ID，默认 AGENTLAB_SESSION_ID 或活动会话")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init", help="保留已有活动会话；不存在时创建自由测试会话")
    sessions = sub.add_parser("session").add_subparsers(dest="action", required=True)
    start = sessions.add_parser("start", help="创建并切换到一个独立的新会话，旧记录保留")
    start.add_argument("--label", default="manual")
    sessions.add_parser("current")
    sessions.add_parser("history", help="当前会话的可见对话和工具结果")
    for name in ("user", "reply", "note"):
        text_argument(sub.add_parser(name))
    sub.add_parser("tools", help="列出工具 schema")
    call = sub.add_parser("call", help="调用一个业务工具")
    call.add_argument("name")
    call.add_argument("arguments", nargs="?", default="{}", help="JSON 对象")
    call.add_argument("--stdin", action="store_true")
    export = sub.add_parser("export", help="测试管理：导出完整轨迹")
    export.add_argument("--output", required=True)
    grading = sub.add_parser("grade", help="测试管理：按当前场景验收")
    grading.add_argument("--output")
    comparing = sub.add_parser("compare", help="比较相同场景版本的两份批量报告")
    comparing.add_argument("current")
    comparing.add_argument("baseline")
    serving = sub.add_parser("serve", help="启动 HTTP Mock API（默认仅监听本机）")
    serving.add_argument("--port", type=int, default=os.environ.get("PORT", "8765"))
    serving.add_argument("--host", default=os.environ.get("AGENTLAB_HOST", "127.0.0.1"))
    bench = sub.add_parser("bench", help="测试管理：场景和原生 CLI 批量评测").add_subparsers(dest="action", required=True)
    listing = bench.add_parser("list")
    listing.add_argument("--suite")
    starting = bench.add_parser("start")
    starting.add_argument("case_id")
    starting.add_argument("--suite")
    starting.add_argument("--label", default="manual")
    starting.add_argument("--seed", type=int, default=0)
    bench.add_parser("next", help="记录并显示下一轮测试用户输入")
    running = bench.add_parser("run", help="直接启动本机原生 Agent CLI；测试框架不作业务决策")
    running.add_argument("--suite")
    running.add_argument("--case", action="append", dest="cases")
    running.add_argument("--command-json", help="原生 Agent 的 argv JSON 数组，通过 stdin 接收提示")
    running.add_argument("--agent", choices=["codex"], help="使用经本机 --help 核实的 Codex CLI 参数")
    running.add_argument("--label", required=True, help="建议包含 harness、model、reasoning 配置")
    running.add_argument("--repeat", type=int, default=1)
    running.add_argument("--seed", type=int, default=0)
    running.add_argument("--timeout", type=float, default=180)
    running.add_argument("--output", required=True)
    running.add_argument("--baseline")
    args = parser.parse_args(argv)
    try:
        if os.environ.get("AGENTLAB_URL") and args.command != "serve":
            from .remote import dispatch
            return dispatch(args, emit)
        if args.command == "tools":
            emit({"tools": list(TOOLS.values())})
            return 0
        if args.command == "compare":
            comparison = compare(json.loads(Path(args.current).read_text()), json.loads(Path(args.baseline).read_text()))
            emit(comparison)
            return int(bool(comparison["regressions"]))
        store = Store(args.db)
        if args.command == "init":
            try:
                sid = store.current_id(args.session)
            except ValueError:
                session = store.create()
            else:
                session = store.metadata(sid)
            emit(session)
            return 0
        if args.command == "serve":
            from .http_api import serve
            serve(store, args.port, args.host)
            return 0
        if args.command == "session" and args.action == "start":
            emit(store.create(args.label))
            return 0
        if args.command == "bench" and args.action in ("list", "start", "run"):
            cases = load_cases(args.suite)
            if args.action == "list":
                emit([{k: case[k] for k in ("id", "description", "tags")} for case in cases])
                return 0
            if args.action == "start":
                matches = [c for c in cases if c["id"] == args.case_id]
                if not matches:
                    raise ValueError("未知场景 id")
                session = start_case(store, matches[0], args.label, args.seed)
                emit({**session, **next_prompt(store, session["session_id"])})
                return 0
            if bool(args.command_json) == bool(args.agent):
                raise ValueError("选择 --agent codex 或 --command-json 其中一种")
            selected = [c for c in cases if not args.cases or c["id"] in args.cases]
            if not selected or (args.cases and set(args.cases) - {c["id"] for c in cases}):
                raise ValueError("指定了未知场景或测试集为空")
            command = (json.loads(args.command_json) if args.command_json else
                       ["codex", "exec", "--ephemeral", "--skip-git-repo-check", "--sandbox", "workspace-write", "-"])
            report = run_suite(store, selected, command, label=args.label, output=args.output,
                               repeat=args.repeat, seed=args.seed, timeout=args.timeout, baseline=args.baseline)
            emit({k: v for k, v in report.items() if k != "results"})
            return int(any(r["passed"] is not True for r in report["results"]) or bool(report.get("comparison", {}).get("regressions")))
        sid = store.current_id(args.session)
        if args.command == "session":
            emit(store.metadata(sid) if args.action == "current" else store.conversation(sid))
        elif args.command == "bench" and args.action == "next":
            emit(next_prompt(store, sid))
        elif args.command in ("user", "reply", "note"):
            text = sys.stdin.read().removesuffix("\n") if args.stdin else args.text
            emit(store.user(sid, text) if args.command == "user" else store.say(sid, "assistant" if args.command == "reply" else "note", text))
        elif args.command == "call":
            arguments = json.loads(sys.stdin.read() if args.stdin else args.arguments)
            result = store.call(sid, args.name, arguments)
            emit(result)
            return 0 if result["ok"] else 2
        elif args.command == "export":
            path = Path(args.output)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(store.export(sid), ensure_ascii=False, indent=2))
            emit({"output": str(path.resolve())})
        elif args.command == "grade":
            result = grade(store.export(sid))
            if args.output:
                path = Path(args.output)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(summarize([result]), ensure_ascii=False, indent=2))
            emit(result)
            return int(result["passed"] is False)
        return 0
    except (ValueError, OSError, KeyError, TypeError) as exc:
        emit({"ok": False, "error": {"code": "LAB_ERROR", "message": str(exc)}})
        return 2


if __name__ == "__main__":
    sys.exit(main())
