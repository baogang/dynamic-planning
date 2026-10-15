# Agent 动态规划测试环境

这个仓库提供 Mock 业务工具和评测设施。当前与你对话的 Codex / Claude Code / Pi / 其他 harness 就是被测 Agent。业务计划由你自主产生，仓库没有自然语言路由器或预设执行流程。

## 处理用户的工单业务请求

先读 `docs/agent-contract.md`，通过 `python3 -m agentlab tools` 获取真实工具 schema。当前聊天中的工单请求属于业务测试；用户是在测试你的理解、追问、工具调用和重新规划能力。

使用已绑定会话；首次自由测试可执行 `python3 -m agentlab init`。每轮先用 `user --stdin` 记录用户原话，通过 `call` 操作业务，将最终回复用 `reply --stdin` 记录，再向用户输出同一回复。后台会保存工具结果，命令返回的 JSON 是业务事实来源。业务 CLI 不需要常驻服务器。

测试时按需读取业务契约、工具 schema 和 `session history`。场景、参考轨迹、评分器、管理命令和数据库由测试管理员使用；被测角色通过业务工具读取和修改数据，不读取测试答案或直接修改存储。不启动其他 Agent 代做业务任务。

## 修改测试环境

用户明确要求实现或修复环境时，按开发任务处理，先读 `README.md` 和 `docs/evaluation.md`。此时可以维护场景、评分器和测试。完成后运行 `python3 -m unittest discover -s tests -v`。

新增业务能力时保持工具 schema、CLI/HTTP 行为和回归测试一致。对话测试与基础设施参考轨迹分开报告，参考轨迹通过不能计作真实 Agent 的准确率。
