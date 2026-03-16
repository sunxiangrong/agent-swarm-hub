# agent-swarm-hub

项目级 agent 中枢。它把 Telegram、飞书、本地 CLI、共享项目会话库、Claude/Codex 分工，以及 `ccb/askd` 执行底座接在一起。

它现在有两种使用形态：

- 远程聊天模式  
  Telegram / 飞书进入统一项目命令层，适合项目管理、监控和汇报。
- 本地原生模式  
  先选项目，再直接进入原生 `claude` / `codex` CLI，适合本地编码和深度对话。

## 核心目标

- 每个正式对话都归属于具体项目
- Claude 与 Codex 在项目内协作
- `ccb` 负责底层会话载体
- 远程聊天负责项目管理和汇报
- 本地 CLI 负责进入原生 agent 工作流

## 当前能力

- Telegram 长轮询，支持断线重试
- 飞书 WebSocket，支持断线重连
- 本地全局启动入口：`ash-chat`
- 远程 chat -> workspace 绑定
- workspace 项目配置：
  - `path`
  - `backend`
  - `transport`
- 共享项目会话库：
  - 项目 `profile`
  - 项目 `summary`
  - Claude/Codex 原始会话归类
  - `active / archived` 生命周期
- 项目级 task 持久化
- 项目级 phase 状态机：
  - `discussion`
  - `planning`
  - `ready_for_execution`
  - `executing`
  - `reviewing`
  - `reported`
- Claude/Codex 双 agent 分工：
  - Claude：讨论、拆解、规划、校验、汇报
  - Codex：实现、执行、验证
- sub-agent 派发：
  - 大任务先规划
  - 根据 `execution_plan` 自动建议并派发 sub-agent
- 双 agent 独立 session id：
  - `claude_session_id`
  - `codex_session_id`
- 双 agent 独立最近记忆流
- 临时模式与正式项目模式分离：
  - 短对话 -> `ephemeral`
  - 未绑定项目的长对话 -> `temporary swarm`
  - 绑定项目后 -> 正式项目 worker 流
- 结构化交接对象：
  - `discussion_brief`
  - `execution_plan`
  - `execution_packet`
  - `subagent_packet`
  - `subagent_result`
  - `review_verdict`
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

远程项目模式典型流转：

1. `/write <task>` 进入 `discussion`
2. 普通文本继续和 Claude 讨论
3. 大任务自动进入 `planning`
4. `/execute [notes]` 生成结构化执行包
5. 必要时派发 sub-agent
6. Codex 执行
7. Claude review 并向用户汇报

本地模式典型流转：

1. `ash-chat codex` 或 `ash-chat claude`
2. 启动前先选项目
3. 绑定项目目录与 `ccb` 环境
4. 直接进入原生 `codex` / `claude` CLI

## 使用模式

### 1. 远程聊天模式

适合：

- 项目管理
- 监控任务状态
- 让 Claude 汇报
- 让 Codex 执行

入口：

- Telegram
- 飞书
- `python -m agent_swarm_hub.cli local-chat`

### 2. 本地原生模式

适合：

- 直接进入 `claude` / `codex` 原生 CLI
- 在开始对话前绑定项目
- 保持本地使用习惯

入口：

- `ash-chat`
- `./scripts/start-chat.sh`

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
- `/worker`
- `/tasks`
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

本地统一聊天入口:

```bash
cd /Users/sunxiangrong/Desktop/CLI/git/agent-swarm-hub
./scripts/start-chat.sh
```

本地全局入口:

```bash
ash-chat codex
ash-chat claude
```

指定 provider:

```bash
./scripts/start-chat.sh claude
./scripts/start-chat.sh codex
```

直接绑定项目启动:

```bash
./scripts/start-chat.sh claude agent-swarm-hub
```

说明：

- `start-chat.sh` / `ash-chat` 现在默认进入本地原生模式
- 启动前会从共享项目库里选择项目
- 选定项目后，直接进入原生 `claude` / `codex` CLI
- 不再先进入 `/write` 风格的本地聊天壳

如果你要保留统一命令层的本地测试入口，仍可手动运行：

```bash
cd /Users/sunxiangrong/Desktop/CLI/git/agent-swarm-hub
PYTHONPATH=src conda run -n cli python -m agent_swarm_hub.cli local-chat --provider codex
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

## 数据层

这套系统现在有两套数据库：

- 共享项目库  
  [`/Users/sunxiangrong/Desktop/CLI/local-skills/project-session-manager/data/sessions.sqlite3`](/Users/sunxiangrong/Desktop/CLI/local-skills/project-session-manager/data/sessions.sqlite3)  
  负责项目总账、项目画像、项目摘要、Claude/Codex 历史归类。

- 本地运行时库  
  [`/Users/sunxiangrong/Desktop/CLI/git/agent-swarm-hub/.agent-swarm-hub.sqlite3`](/Users/sunxiangrong/Desktop/CLI/git/agent-swarm-hub/.agent-swarm-hub.sqlite3)  
  负责 chat 绑定、task、phase、handoff、ephemeral、agent message 流。

## 文档

- [操作手册](/Users/sunxiangrong/Desktop/CLI/git/agent-swarm-hub/docs/操作手册.md)
- [实现手册](/Users/sunxiangrong/Desktop/CLI/git/agent-swarm-hub/docs/实现手册.md)
- [开发日志](/Users/sunxiangrong/Desktop/CLI/git/agent-swarm-hub/docs/开发日志.md)
- [架构说明](/Users/sunxiangrong/Desktop/CLI/git/agent-swarm-hub/docs/swarm-architecture.md)
