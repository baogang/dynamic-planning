# 部署到 Render

部署目标是 Mock 业务 API。原生 Agent 仍由本机 Codex、Claude Code、Pi 或其他 harness 提供。

`render.yaml` 提供 Python Web Service、Singapore 区域、free 实例、`/health` 健康检查。构建时运行完整环境回归，不调用模型；启动时监听 `0.0.0.0:$PORT`。端口遵循 [Render Web Service 规则](https://render.com/docs/web-services#port-binding)。

## 代码准备与创建服务

将代码推送到你选择的 GitHub / GitLab / Bitbucket 仓库。Render 需要能够读取该仓库，私有仓库需在 Render 中给对应 Git 集成授权。

可以从 Blueprint 导入 `render.yaml`，也可以创建单个 Python Web Service：

```text
Build:  python -m unittest discover -s tests -v
Start:  python -m agentlab serve --host 0.0.0.0
Health: /health
Plan:   free
Region: singapore
```

环境变量：`PYTHON_VERSION=3.12.12`、`PYTHONUNBUFFERED=1`、`AGENTLAB_API_TOKEN`。Blueprint 自动生成 token；手工/MCP 创建时提供随机的至少 32 字符 token。令牌只保存在 Render 环境变量或本机忽略的配置中，不提交到 Git。公网绑定没有足够长的 token 时，服务会拒绝启动。

根路径提供简短接入说明；`/health`、`/tools` 和 `/openapi.json` 可公开读取。创建会话、对话消息、业务调用及会话历史需要 `Authorization: Bearer <token>`。

## 使用线上环境

在本机设置线上地址和同一个令牌，例如通过本机忽略的配置文件加载，或在 shell 中安全设置：

```bash
export AGENTLAB_URL=https://YOUR-SERVICE.onrender.com
read -rs 'AGENTLAB_API_TOKEN?API token: '
export AGENTLAB_API_TOKEN
python3 -m agentlab init
```

上面的 `read` 示例适用于 zsh。不要把 token 放进提交、可分享的报告或 URL。

随后继续直接与原生 Agent 聊天。它仍使用相同的 `user`、`tools`、`call`、`reply` 和 `session history` 命令，CLI 会把操作转发到线上服务。活动远程会话只在本机保存地址和 ID，令牌不写入会话绑定文件。

```bash
python3 -m agentlab session start --label codex-render
python3 -m agentlab user '查一下我的全部工单'
python3 -m agentlab call ticket.list '{"page_size":20}'
python3 -m agentlab reply '已查询到你的工单。'
python3 -m agentlab session history
```

远程模式暂不开放 `bench`、`grade`、`export` 管理操作；22 个场景的批量回归继续使用本地隔离数据库。回到本地模式运行 `unset AGENTLAB_URL`。业务接口和规则与本地共用同一实现。

直接 HTTP 接入：先 `POST /sessions`，保存返回的 `session_id`；用 `X-Session-Id` 和 Bearer token 请求 `POST /messages` 开始用户轮次，再请求 `POST /tools/call`，最后发送 assistant 消息结束本轮。完整 schema 见 `/openapi.json`。

## 数据与免费实例

SQLite 默认保存在实例本地，因此该部署是可重置的测试环境。Render 免费实例闲置后会休眠，恢复有冷启动时间；本地文件在休眠、重启或重新部署后可能丢失。此时重新运行 `session start` 新建会话。客户端遇到旧 ID 不会自动重建，以免让用户误以为原来的记录还存在。需要持续保存时，应另行选择持久化存储方案。[Render 免费实例说明](https://render.com/docs/free)

不要在 Mock 工单中输入真实客户资料。API token 的持有者可以创建和操作测试会话；这是单个测试团队共用的环境，不提供生产多租户认证或隔离。
