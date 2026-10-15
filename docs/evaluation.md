# 评测与回归

本文件供测试管理员和环境开发者使用。被测角色使用 `docs/agent-contract.md` 和业务工具，不读取场景或参考轨迹。

## 自由测试

在当前 Agent 聊天内直接说业务需求。Agent 记录 `user` / `reply`，工具调用自动记录。自由会话可接续任意多轮，直到新建会话；默认会话预算 50 次工具调用。

```bash
python3 -m agentlab session start --label codex-exploratory
# 在聊天中用自然语言测试
python3 -m agentlab export --output reports/exploratory.json
```

没有预先定义目标的自由对话只能人工验收，报告的 `passed` 和 `score` 为 null。需要准确率时，先固定测试输入和验收条件，再运行被测 Agent。将发现的真实失败转为新场景，持续扩展测试集。

可观察计划摘要通过 `note` 留存，工具依赖关系体现在调用顺序中。无需提供内部推理过程；评价执行证据和最终业务结果。

## 人工主持一个固定场景

```bash
python3 -m agentlab bench start complaint.choose --label codex-manual
```

命令输出并记录本轮 `user`。将该文本原样交给被测 Agent，等它执行或追问并记录最终 `reply` 后，测试者推进下一轮：

```bash
python3 -m agentlab bench next
# 将下一条 user 原样交给 Agent
python3 -m agentlab grade --output reports/manual-case.json
```

`bench next` 是测试者动作，不应由被测 Agent 读取未来输入。最后一轮后返回 `done: true`。缺少回复或未完成的后续轮次会判定失败。场景按预定义的测试用户输入推进，不会根据 Agent 的具体问题智能生成用户回答；问题措辞与追问质量需人工复核。

不同场景分别拥有种子数据，不共享创建或投诉记录。批量测试通过环境变量绑定会话，不改变日常自由测试的活动会话。

## 批量测试原生 CLI

```bash
python3 -m agentlab bench run --agent codex \
  --label codex-model-and-effort-in-your-config \
  --repeat 3 --seed 42 --timeout 180 \
  --output reports/codex-baseline.json
```

这里没有自建的自然语言 Agent 循环。调度器按场景提供用户轮次，原生 CLI 在各自进程内自主理解用户、规划和调用 shell 业务工具。下一轮提示包含之前所有用户消息、工具观察、操作摘要及最终回复。既不会传入验收规则，也不会提供参考执行步骤。

每轮超时会终止整个进程组；退出非零、无法启动、没有记录最终回复均标为 `execution_error`。单场景最多 40 次业务工具调用。CLI 自身的模型 token 上限、认证、外部工具和访问权限由原生 harness 配置决定；Mock 工具预算不能限制它在业务工具之外的计算。

每条案例每次重复使用独立会话。`--seed` 控制预先编写的等义表达选择，`--repeat` 在 seed、seed+1 等输入变体上重复；它不控制模型随机性，也不会自动生成无限测试输入。建议至少重复 3 次，并继续补充真实用户表达。跨 Agent 比较时固定相同场景版本、seed、重复次数和权限，报告标签注明模型、推理设置、CLI 版本及用户配置。

输出包括：总报告 JSON、每轮 prompt/stdout/stderr 和每次运行的完整轨迹 JSON。中断前已完成的结果会保存为 `complete: false` 的部分报告。日志可能包含全部测试输入和工具参数，分享前按实际内容处理。

## 评分语义

每个场景由多轮用户输入、逐轮检查、最终检查组成。`passed` 要求每一轮完成且所有验收检查通过。`score` 为通过检查数占全部检查数的百分比，**不是任务成功概率**。总 `success_rate` 为全部已评分运行中完全通过的比例；自由会话不进入分母。

| 检查 | 判断什么 |
| --- | --- |
| state_count | 集合中符合字段条件的记录数量 |
| state_field | 指定记录的字段值 |
| unchanged | 指定业务集合与初始数据完全相同 |
| no_writes | 当前检查范围内没有尝试写工具，包括被拒绝的写请求 |
| tool_count | 工具调用次数，可只统计成功或失败调用 |
| tool_order | 必要依赖的成功调用先后关系，不要求完全匹配参考轨迹 |
| message_contains / message_excludes | 最终回复出现或不出现稳定文本，例如真实工单编号 |
| turn_complete（自动） | 本轮有用户输入和最终回复 |
| trace_replay_consistent（自动） | 从种子数据和调用轨迹能重建最终状态与 API 返回 |

逐轮状态通过重放重建，能识别“后来补做了正确操作，但上一轮擅自写入”的错误。重放是评分器的内部校验，不是被测 Agent 的计划。临时错误本身不必导致任务失败，业务恢复后仍按最终结果和约束验收。

自动检查能够验证数据、调用及部分回复证据，不能完整评价自然语言语义、礼貌、追问是否恰到好处，也不能发现所有可能的虚假表述。例如没有编号且仅说“操作完成”的错误回复，需要更具体的场景断言或人工复核。不要把 22 个预置场景的通过率外推为任意随机输入的准确率。

仓库文件的隔离依赖被测 Agent 遵守角色约定。Agent 拥有 shell 和同一用户权限时，能够读取测试答案或伪造轨迹；重放能发现部分意外的绕过写入，不是抗作弊的安全边界。当前创建环境的聊天已看到场景，适合交互演练；严格比较用全新聊天/进程，并将测试管理面隔离到外部环境。

## 检测回归

```bash
python3 -m agentlab bench run --agent codex \
  --label codex-candidate --repeat 3 --seed 42 \
  --baseline reports/codex-baseline.json \
  --output reports/codex-candidate.json

python3 -m agentlab compare reports/codex-candidate.json reports/codex-baseline.json
```

按每个场景的全部重复运行比较通过率，任一场景下降就报告回归。场景 ID 和内容指纹必须一致，测试集变化会拒绝直接比较，防止把增加简单场景导致的总分上涨误认为进步。即使与基线持平，当前运行中有失败案例时 `bench run` 仍退出 1；单独 `compare` 只判断下降。

退出码：0 表示命令正常完成且要求的验收通过；1 表示验收失败或发现回归；2 表示参数、配置或业务工具错误。自由会话 `grade` 正常返回 0，但 `passed: null`。

## 添加一个场景

在测试集的 `cases` 数组中增加唯一 ID，保留验收重点，不限定所有合理计划必须执行相同调用序列：

```json
{
  "id": "cancel.new-expression",
  "description": "目标和原因分两轮提供",
  "tags": ["cancel", "clarification"],
  "turns": [
    {"user": "T-1002 不用处理了。", "checks": [{"kind": "no_writes"}]},
    {"user": "撤销原因是我已经自己解决了。", "checks": []}
  ],
  "checks": [
    {"kind": "state_field", "collection": "tickets", "id": "T-1002", "field": "status", "equals": "cancelled"}
  ]
}
```

可用 `variants` 添加保持信息完全一致的用户表达；可用 `faults` 注入指定工具的 before/after 故障；可用每轮 `before` 模拟后台状态变化，参考现有测试集的数据结构。新增场景后为 `tests/reference_traces.json` 添加一种可行执行轨迹，以验证规则本身可满足，并加入“错目标、漏操作或越权动作会失败”的反例测试。

参考轨迹与进程测试中的 `tests.native_fixture` 都是固定测试数据，绝不能作为 Agent 基准成绩。CI 只运行环境回归，不自动调用收费模型。
