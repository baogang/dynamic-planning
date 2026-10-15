# 原生 Agent 动态规划测试场地

**直接测试你正在使用的 Codex、Claude Code、Pi 或其他 Agent。** 这里是一个模拟工单投诉系统，包含业务 API、测试数据、操作轨迹和验收规则。没有另造 Agent，没有用固定工作流替代被测 Agent 的规划。

你用自然语言提出需求，当前 Agent 自己决定：需要哪些信息、是否追问、调用什么工具、如何根据结果调整计划。支持任意表达和任务组合，不局限于创建、查询、投诉三条流程。

## 现在开始

环境需要 Python 3.11+，只用标准库，无需安装依赖或启动服务器。

在这个目录打开 Agent，首次执行一次：

```bash
python3 -m agentlab init
```

然后直接在聊天里说：

> 帮我创建一个工单。

> 我要投诉之前那个一直没修好的宽带问题。

> 算了先不投诉，查一下进度，再把联系方式改成 help@example.com。

Agent 从 [AGENTS.md](AGENTS.md) 读取角色约定，按需发现工具并执行；信息不足时应该向你追问。你无需手工拼 API 参数。当前聊天在环境交付后可以直接开始测试。

新开一组自由测试，保留旧记录：

```bash
python3 -m agentlab session start --label codex-live
```

`init` 保留现有会话；`session start` 创建全新且独立的数据。活动会话保存在 `.agentlab/`，切换 Agent 时默认可以接续，也可以新建会话做公平比较。

## 模拟的业务

| 能力 | 工具 |
| --- | --- |
| 当前客户资料 | profile.get |
| 工单查询、筛选、分页、详情 | ticket.list / ticket.get |
| 创建、修改、撤销、重开 | ticket.create / ticket.update / ticket.cancel / ticket.reopen |
| 留言 | comment.add / comment.list |
| 提交投诉、查询投诉 | complaint.create / complaint.list |

初始客户有 5 张不同状态的工单；另外一张其他客户的记录用于验证权限隔离。服务端执行必填字段、身份、状态转换、重复投诉和幂等规则。支持临时故障、写入后响应丢失、多轮间状态变化。

模拟业务时间固定在 **2026-10-14**，每个新会话数据一致，方便复现；审计记录另存实际时间。所有号码和业务内容都是测试数据。

读取 [业务契约](docs/agent-contract.md) 查看规则，运行下列命令发现完整 schema：

```bash
python3 -m agentlab tools
```

## 两层回归

**环境回归，不调用模型、不消耗模型额度：**

```bash
python3 -m unittest discover -s tests -v
# 或 make test
```

验证业务规则、权限、幂等与并发、多轮记录、HTTP/CLI、场景评分、基线比较和进程异常。22 个场景的参考轨迹用于检查评分设施，**不代表任何真实 Agent 的准确率**。

**原生 Agent 回归，直接启动你已安装并登录的 CLI：**

```bash
python3 -m agentlab bench run \
  --agent codex \
  --label codex-local-default \
  --case complaint.choose \
  --repeat 3 \
  --output reports/codex-complaint.json
```

去掉 `--case` 跑全部 22 个场景。框架只提供测试用户消息、启动原生 CLI、收集证据及评分；**工具选择和业务计划由原生 Agent 自己完成**。这是可选的真实模型测试，会使用该 CLI 已配置的账户和额度。每个用户轮次启动一个独立 CLI 进程，并传入完整可见历史，保持不同 harness 的测试协议一致；不是原生会话恢复测试。

内置 Codex 参数以本机 `codex exec --help` 核实，并参照 [OpenAI 官方评测说明](https://developers.openai.com/blog/eval-skills)。外部 CLI 的权限、登录或网络问题单独记为 `execution_error`，不会当成成功。

查看所有场景：

```bash
python3 -m agentlab bench list
```

覆盖：信息齐全与缺失、多轮选择、投诉原因、重复请求、用户改口、模糊动作、先创建再留言、重开与修改、完整分页、空结果、越权、不支持的删除、正文中的指令注入、持续故障、响应丢失、状态改变。

## 查看一次真实测试

```bash
python3 -m agentlab session history
python3 -m agentlab export --output reports/live-trace.json
python3 -m agentlab grade
```

自由对话没有预设正确结果，`grade` 返回 `ungraded`，不会把“API 成功率”冒充“任务准确率”。有场景验收目标的会话才会给出通过率和得分。完整轨迹含用户输入、回复、计划摘要、工具参数/结果和最终状态，便于人工复核及将失败对话加入回归集。

详细用法见 [评测与回归](docs/evaluation.md)，其他 Agent 接入见 [Harness 接入](docs/harnesses.md)。

## 可选 HTTP 接口

需要 HTTP 工具的 harness 可以运行：

```bash
python3 -m agentlab serve --port 8765
```

默认只监听 `127.0.0.1`，与 CLI 共用同一业务实现和 SQLite 会话。提供健康检查、工具 schema、OpenAPI、远程会话和对话消息接口。业务调用需要 `X-Session-Id`，并先通过 CLI 或 HTTP 记录本轮用户输入。

```bash
curl http://127.0.0.1:8765/tools/call \
  -H 'Content-Type: application/json' \
  -H 'X-Session-Id: YOUR_SESSION_ID' \
  -d '{"name":"ticket.list","arguments":{"page_size":20}}'
```

项目包含 [Render 部署配置](render.yaml) 和 [云端接入说明](docs/render.md)。公网监听要求 `AGENTLAB_API_TOKEN`，远程 CLI 设置 `AGENTLAB_URL` 后使用相同业务命令。服务不提供生产多租户认证。业务 API 的客户隔离不等同于评测文件或管理面的安全隔离；严格盲测时，应由测试者将评分文件和数据库放到 Agent 无权读取的外部环境。

## 文件结构

```text
AGENTS.md / CLAUDE.md     原生 Agent 的入口说明
agentlab/domain.py       工具 schema 和业务规则，唯一业务实现
agentlab/store.py        会话、事务、审计、工具预算
agentlab/http_api.py     可选 HTTP 传输
agentlab/benchmark.py    原生 CLI 启动与多轮测试调度
agentlab/grading.py      结果和证据验收、基线比较
scenarios/core.json     测试者持有的 22 个场景
tests/                  环境测试及评分器参考轨迹
docs/                   业务契约、测试协议、接入说明
.github/workflows/      环境回归 CI（推送到 GitHub 后运行）
.agentlab/              本机运行数据，已忽略
reports/                报告和完整轨迹，已忽略
```
