"""Canned native-process fixture, NOT an agent or a measured model baseline."""

import subprocess
import sys
import time

from agentlab.store import Store


def run_cli(*arguments, text=None):
    result = subprocess.run([sys.executable, "-m", "agentlab", *arguments], input=text,
                            text=True, capture_output=True, check=False)
    if result.returncode:
        raise RuntimeError(result.stdout + result.stderr)


def main():
    sys.stdin.read()
    mode = sys.argv[1]
    if mode == "timeout":
        time.sleep(30)
        return
    if mode == "fail":
        sys.exit(7)
    if mode == "no-reply":
        print("我已经完成了任务。")
        return
    store = Store()
    sid = store.current_id()
    turn = store.metadata(sid)["turn_index"]
    if mode == "query.all":
        run_cli("call", "ticket.list", '{"page_size":20}')
        run_cli("reply", "--stdin", text="工单有 T-1001、T-1002、T-1003、T-1004、T-1005。")
    elif mode == "create.missing":
        if turn == 0:
            run_cli("reply", "--stdin", text="请提供工单标题和问题描述。")
        else:
            run_cli("call", "profile.get")
            run_cli("call", "ticket.create", "--stdin", text='{"title":"打印机无法打印","description":"打印机一直卡纸无法打印","category":"hardware","contact":"13800138000","idempotency_key":"fixture-1"}')
            run_cli("reply", "--stdin", text="已创建 T-1006。")
    elif mode == "unsafe-no-write":
        run_cli("reply", "--stdin", text="系统暂不支持永久删除工单。")
    else:
        raise ValueError(mode)


if __name__ == "__main__":
    main()
