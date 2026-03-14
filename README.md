# agent-swarm-hub

项目级远程 agent 中枢。它把 Telegram、飞书、项目上下文、Claude/Codex 分工、以及 `ccb/askd` 执行底座接在一起。

## 当前能力

- Telegram 长轮询，支持断线重试
- 飞书 WebSocket，支持断线重连
- chat -> workspace 绑定
- workspace 项目配置：
  - `path`
  - `backend`
  - `transport`
- 项目级 task 持久化
- 项目级 phase 状态机：
  - `discussion`
  - `ready_for_execution`
  - `executing`
  - `reviewing`
  - `reported`
- Claude/Codex 双 agent 分工：
  - Claude：讨论、拆解、校验、汇报
  - Codex：实现、执行、验证
- 双 agent 独立 session id：
  - `claude_session_id`
  - `codex_session_id`
- 双 agent 独立最近记忆流
- 结构化交接对象：
  - `discussion_brief`
  - `execution_packet`
  - `review_verdict`
- 共享项目会话库接入
- `ccb` 环境注入：
  - `CCB_SESSION_ID`
  - `CCB_WORK_DIR`
  - `CCB_RUN_DIR`

## 核心流程

```text
Telegram / Lark
      |
      v
project worker
      |
      +--> Claude
      |     讨论 / 拆解 / 校验 / 汇报
      |
      +--> Codex
            实现 / 执行 / 验证
```

典型流转：

1. `/write <task>` 进入 `discussion`
2. 普通文本继续和 Claude 讨论
3. `/execute [notes]` 生成结构化执行包
4. Codex 执行
5. Claude review 并向用户汇报

## 常用命令

- `/projects`
- `/use <workspace>`
- `/where`
- `/project set-path <path>`
- `/project set-backend <backend>`
- `/project set-transport <transport>`
- `/write <task>`
- `/execute [notes]`
- `/status`
- `/new`
- `/escalations`

## 启动

Telegram:

```bash
cd /Users/sunxiangrong/Desktop/CLI/git/agent-swarm-hub
./scripts/start-telegram.sh
```

飞书:

```bash
cd /Users/sunxiangrong/Desktop/CLI/git/agent-swarm-hub
./scripts/start-lark.sh
```

一起启动:

```bash
cd /Users/sunxiangrong/Desktop/CLI/git/agent-swarm-hub
./scripts/start-local.sh
```

## 推荐配置

`.env.local` 最少建议包含：

```bash
ASH_EXECUTOR=claude
ASH_EXECUTOR_TRANSPORT=ccb
ASH_PROXY_URL=http://127.0.0.1:6789
```

说明：

- `ASH_EXECUTOR` 是默认 workspace 后端
- workspace 级配置会覆盖全局默认值
- `transport=ccb` 时会优先走 `ask/askd + ccb` 会话路由

## 文档

- [操作手册](/Users/sunxiangrong/Desktop/CLI/git/agent-swarm-hub/docs/操作手册.md)
- [实现手册](/Users/sunxiangrong/Desktop/CLI/git/agent-swarm-hub/docs/实现手册.md)
- [架构说明](/Users/sunxiangrong/Desktop/CLI/git/agent-swarm-hub/docs/swarm-architecture.md)
