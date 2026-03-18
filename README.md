# agent-swarm-hub

项目级 agent 中枢。它把 Telegram、飞书、本地原生 CLI、本地 swarm shell、共享项目会话库、Claude/Codex 分工，以及 `ccb/askd` 执行底座接在一起。

它现在有三种使用形态：

- 本地原生 CLI 模式  
  先选项目，优先恢复该项目最近的 Claude/Codex 原生会话，再进入原生 CLI。
- 远程聊天模式  
  Telegram / 飞书进入统一项目命令层，适合项目管理、监控和汇报。
- 本地 swarm 模式  
  先选项目，再直接进入本地 swarm shell，适合项目级协同、任务监控与跨 agent 调度。

## 核心目标

- 每个正式对话都归属于具体项目
- Claude 与 Codex 在项目内协作
- `ccb` 负责底层会话载体
- 远程聊天负责项目管理和汇报
- 本地 `ash-chat` 负责进入项目并直接进入原生 Claude/Codex CLI
- 本地 `ash-swarm` 负责进入统一 swarm shell

## 当前能力

- Telegram 长轮询，支持断线重试
- 飞书 WebSocket，支持断线重连
- 本地全局启动入口：
  - `ash-chat`
  - `ash-swarm`
- 远程 chat -> workspace 绑定
- workspace 项目配置：
  - `path`
  - `backend`
  - `transport`
- 共享项目会话库：
  - 项目 `profile`
  - 项目 `summary`
  - 项目级 `project_memory`
  - 项目级 `provider_bindings`
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
  - Codex：实现、执行、自校验
- sub-agent 派发：
  - 大任务先规划
  - 根据 `execution_plan` 自动建议并派发 sub-agent
- sub-agent 用于上下文隔离，而不是模拟组织层级
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
  - `verification_packet`
  - `verification_result`
  - `review_verdict`
- 交互确认上浮：
  - Claude `go ahead` / proceed 页会回到聊天壳
  - 用户通过 `/confirm` 继续
  - 启动级认证问题会返回 `Authentication Required`
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

本地原生 CLI 模式典型流转：

1. `ash-chat codex` 或 `ash-chat claude`
2. 启动前先选项目
3. 先显示该项目的稳定 `summary` 视图，并等待回车确认
4. 进入对应项目路径
5. 根据共享项目会话库优先恢复当前 provider 绑定的原生 session
6. 如果绑定不可用，再回退到该项目最近可用的原生 session
7. 进入原生 `codex` / `claude` CLI

本地 swarm 模式典型流转：

1. `ash-swarm codex` 或 `ash-swarm claude`
2. 启动前先选项目
3. 进入项目后直接进入 swarm shell
4. 使用与远程聊天一致的命令：
   - `/projects`
   - `/use`
   - `/where`
   - `/write`
   - `/execute`
   - `/worker`
   - `/tasks`
   - `/sessions`

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

### 2. 本地原生 CLI 模式

适合：

- 直接进入 `codex resume` / `claude --resume` 语义
- 在项目目录下继续历史原生会话
- 把“项目选择”放在原生 CLI 之前
- 让“选项目”本身就等价于恢复该项目的本地 CLI 上下文
- 把长期上下文沉淀到项目记忆，而不是依赖堆积大量 provider session

入口：

- `ash-chat`
- `./scripts/start-chat.sh`

### 3. 本地 swarm 模式

适合：

- 本地跨 agent 协作
- 项目级 worker 调度
- 监控 planning / sub-agent / handoff / phase
- 与远程聊天保持同一套命令逻辑

入口：

- `ash-swarm`
- `./scripts/start-swarm.sh`

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
cd /Users/sunxiangrong/dev/cli/git/agent-swarm-hub
./scripts/start-telegram.sh
```

飞书:

```bash
cd /Users/sunxiangrong/dev/cli/git/agent-swarm-hub
./scripts/start-lark.sh
```

一起启动:

```bash
cd /Users/sunxiangrong/dev/cli/git/agent-swarm-hub
./scripts/start-local.sh
```

本地原生 CLI 入口:

```bash
cd /Users/sunxiangrong/dev/cli/git/agent-swarm-hub
./scripts/start-chat.sh
```

本地 swarm shell 入口:

```bash
cd /Users/sunxiangrong/dev/cli/git/agent-swarm-hub
./scripts/start-swarm.sh
```

本地全局原生入口:

```bash
ash-chat codex
ash-chat claude
```

本地全局 swarm 入口:

```bash
ash-swarm codex
ash-swarm claude
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

直接绑定项目启动 swarm shell:

```bash
./scripts/start-swarm.sh claude agent-swarm-hub
```

说明：

- `start-chat.sh` / `ash-chat` 现在默认进入原生 `claude` / `codex` CLI
- 启动前会从共享项目库里选择项目
- 选定项目后，会先显示该项目的稳定 `summary`，再回车进入原生 CLI
- `summary` 与 `project_memory` 分层：
  - `summary` 用于进入前的稳定项目摘要
  - `project_memory` 用于动态工作记忆与后续回写
- 选定项目后，会优先恢复这个项目当前绑定的 provider 原生会话
- 找不到绑定或绑定不可恢复时，才回退到该项目最近可用的原生会话
- 共享项目库会保留原生会话的 `active / archived` 生命周期，但真正默认恢复的是 `provider_bindings`
- 原生 CLI 环境会注入：
  - `ASH_ACTIVE_WORKSPACE`
  - `ASH_PROJECT_PATH`
  - `ASH_PROJECT_PROVIDER`
  - `ASH_PROJECT_SESSION_MODE`
  - `ASH_PROJECT_IDENTITY_TEXT`
- 原生 CLI 里可以直接运行：
  - `ash-where`
  - `ash-where --json`
  查询当前项目、路径、provider 和 session 状态
- `start-swarm.sh` / `ash-swarm` 进入统一 swarm shell
- `local-chat` 仍然保留，用于统一命令层

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

## 当前真实联调状态

- Claude bridge 已经基本修通：
  - trust prompt 自动通过
  - `go ahead` / proceed 这类执行确认会上浮
  - planning 即使未显式输出 `CCB_DONE`，也能靠 idle 收口继续
- Codex bridge 也已能稳定 mounted / ping
- 当前真实 end-to-end 多 agent 闭环剩下的主要阻塞点是：
  - fresh Codex pane 在某些环境下仍会落到登录页
  - 这类问题现在会明确上浮为 `Authentication Required`
  - 不会再静默卡死

## 数据层

这套系统现在有两套数据库：

- 共享项目库  
  [`/Users/sunxiangrong/dev/cli/local-skills/project-session-manager/data/sessions.sqlite3`](/Users/sunxiangrong/dev/cli/local-skills/project-session-manager/data/sessions.sqlite3)  
  负责项目总账、项目画像、项目摘要、Claude/Codex 历史归类。

- 本地运行时库  
  [`/Users/sunxiangrong/dev/cli/git/agent-swarm-hub/.agent-swarm-hub.sqlite3`](/Users/sunxiangrong/dev/cli/git/agent-swarm-hub/.agent-swarm-hub.sqlite3)  
  负责 chat 绑定、task、phase、handoff、ephemeral、agent message 流。

## 文档

- [操作手册](/Users/sunxiangrong/dev/cli/git/agent-swarm-hub/docs/操作手册.md)
- [实现手册](/Users/sunxiangrong/dev/cli/git/agent-swarm-hub/docs/实现手册.md)
- [开发日志](/Users/sunxiangrong/dev/cli/git/agent-swarm-hub/docs/开发日志.md)
- [架构说明](/Users/sunxiangrong/dev/cli/git/agent-swarm-hub/docs/swarm-architecture.md)
