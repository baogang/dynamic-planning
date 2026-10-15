# 接入不同原生 Agent

这里不提供模型客户端或自建 Agent。被测对象始终是已有 Agent/harness，环境仅要求它能运行 Python 命令或调用本机 HTTP。

## 直接自然语言测试

在本目录打开 Codex、Claude Code、Pi 或你自己的 DeepSeek harness，要求它读取 `AGENTS.md`，然后直接开始聊天。`CLAUDE.md` 也指向同一个入口。即使 harness 没有自动加载项目说明，显式读取也足够。

Python 工具使用同一个数据库；不需要 API key，也不需要为 Mock 环境安装服务。各 Agent 调用真实模型所需的登录和配置由对应工具管理。

不同 Agent 依次测试时，新建独立会话；复现同一真实失败时，可以保留并导出原会话。并行运行时每个进程显式设置自己的 `AGENTLAB_SESSION_ID`；`AGENTLAB_DB` 是可选数据库绝对路径。

## 原生 CLI 自动回归

内置 Codex 启动参数：

```text
codex exec --ephemeral --skip-git-repo-check --sandbox workspace-write -
```

参数已通过本机帮助核实。默认使用你的 CLI 模型配置。若需指定配置，使用 JSON argv 替换默认命令；调度器把本轮提示直接写到原生 CLI stdin，不使用 shell 拼接或自定义模型协议。

Claude Code / Pi 的示例参数基于本机 `--help`；尚未完成真实模型端到端验证，应先运行单个场景确认本机登录、stdin 和工具授权工作正常。

```bash
python3 -m agentlab bench run \
  --command-json '["claude","-p","--tools","Bash,Read","--allowedTools","Bash(python3 -m agentlab *)","Read"]' \
  --label claude-local-config --case create.missing \
  --output reports/claude-smoke.json

python3 -m agentlab bench run \
  --command-json '["pi","--print","--no-session","--tools","read,bash"]' \
  --label pi-local-config --case create.missing \
  --output reports/pi-smoke.json
```

示例没有替你改变默认模型，也没有关闭整个 harness 的权限保护。Claude 的示例只为业务 CLI 和读取开放工具权限；如本机策略阻止其他操作，检查原生 CLI 的 stderr 和策略设置。

DeepSeek harness 无统一 CLI 命令：提供自己的 argv 数组即可。要求：从 stdin 读取完整本轮提示、能调用 shell 中的业务 CLI、退出前用 `reply --stdin` 写最终回复。工具和模型的执行循环由你的 harness 提供，环境不会把它替换成另一种 Agent。

```bash
python3 -m agentlab bench run \
  --command-json '["your-harness","--non-interactive"]' \
  --label deepseek-your-harness --case complaint.choose \
  --output reports/deepseek-smoke.json
```

报告会保存原始 stdout/stderr，但不尝试从任意厂商的日志格式猜测最终回复；以统一的 `reply` 记录作为最终用户可见文本。提示中会要求原生 Agent 记录该文本。没有记录时会明确判为执行错误。

自动回归按轮启动新进程并注入完整可见历史，因此比较的是这个共同协议下的任务表现。若要测原生会话记忆、压缩、长时间交互或具体厂商的会话恢复，应使用直接聊天方式并导出业务轨迹，或另行扩展测试调度器。

## HTTP 工具 harness

本机启动 `python3 -m agentlab serve` 后，读取 `/tools` 或 `/openapi.json`，将工具 schema 注册到现有 harness。每次业务调用：

```http
POST /tools/call
Content-Type: application/json
X-Session-Id: 当前测试会话

{"name":"ticket.get","arguments":{"ticket_id":"T-1001"}}
```

每个用户轮次由 CLI `user` 开启，最终回复由 `reply` 关闭。HTTP 业务错误也返回 200，必须检查 JSON 的 `ok`；参数体或会话不合法才使用 400/404。HTTP 与 CLI 调用经过完全相同的事务和审计路径。

目前没有实现 MCP 传输；有 shell 工具的原生 Agent 可以直接使用 CLI，需要 HTTP 的 harness 可以使用上述接口。
