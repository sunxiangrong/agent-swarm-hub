# agent-swarm-hub

项目级 agent 入口层。

它把这几件事统一起来：

- 项目路径
- Claude / Codex 原生对话
- 项目长期记忆
- 本地原生 CLI 入口
- 本地 swarm shell
- Telegram / 飞书远程聊天入口

目标不是再做一个聊天壳，而是让“进入项目”这件事本身就等于回到这个项目的工作上下文。

## 它解决什么问题

在日常使用里，Claude、Codex、远程聊天、tmux、ccb、脚本入口往往是分散的：

- 对话很多，但不稳定归属于某个项目
- 原生会话能恢复，但很难和项目路径、项目记忆一起管理
- 远程聊天适合管理任务，但不适合直接承载完整实现上下文
- 本地 CLI 能干活，但进入时经常回不到正确的项目状态

`agent-swarm-hub` 的核心做法是把 `project` 作为第一对象，而不是把 provider session id 当主对象。

## 核心模型

每个项目至少有这些信息：

- `path`
  这个项目真实对应的工作目录
- `summary`
  进入项目前显示的稳定项目摘要
- `project_memory`
  动态工作记忆，用于承接最近状态
- `global_memory`
  跨项目共享规则、偏好、环境约定
- `provider_bindings`
  当前默认恢复哪条 Claude / Codex 原生会话
- `provider_sessions`
  这个项目下归档过的原生会话历史
- `active / archived`
  原生会话生命周期状态

关系是：

- `summary` 是稳定项目视图
- `project_memory` 是动态工作记忆
- `global_memory` 是跨项目共享层
- `provider_bindings` 决定默认恢复哪条原生会话
- `active / archived` 只是原生会话的保留状态，不等于当前绑定

进一步说，当前记忆主线已经收口成三层：

- `project_memory`
  - 项目内运行时主记忆源
  - 直接服务项目启动、恢复、dashboard 展示
- `global_memory`
  - 从项目记忆中“保守提升”得到的共享规则层
  - 只存适合跨项目复用的偏好、环境约束、工作规则
- OpenViking 项目树 / memory bundle
  - 导出和同步后的资源视图
  - 不是原始写入点

## 使用入口

最常用的入口只有三类。

### 1. 本地原生 CLI

```bash
ash-chat codex
ash-chat claude
```

适合：

- 进入某个项目后直接继续原生 Codex / Claude 工作
- 在正确项目路径下恢复历史原生对话
- 用项目摘要和项目记忆保持上下文连续

进入流程：

1. 选择项目
2. 显示项目摘要
3. 回车确认进入
4. 切到项目 `path`
5. 优先恢复该项目当前绑定的原生会话
6. 如果当前 provider 没有绑定会话，则在正确项目路径里启动 fresh native session
7. 退出 native CLI 后回写项目记忆、刷新项目摘要，并在需要时归档旧会话

### 2. 本地 swarm shell

```bash
ash-swarm codex
ash-swarm claude
```

适合：

- 本地多 agent 协作
- 项目级任务拆解和调度
- 在一个统一命令层里看项目状态、任务和会话

### 3. 本地 dashboard

适合：

- 同时查看多个项目
- 看当前 focus / state / next step
- 看当前绑定 session 和 runtime workspace session
- 先观察，再决定切哪个项目继续工作

### 4. 远程聊天入口

适合：

- 项目管理
- 监控执行状态
- 让 Claude 汇报，让 Codex执行
- 不在本地终端时继续管理项目

当前支持：

- Telegram
- 飞书

## 快速开始

最短路径如下。

### 本地原生 CLI

```bash
./scripts/start-chat.sh codex
./scripts/start-chat.sh claude
```

When run inside `tmux`, the script sets the pane title to `ash-chat | <project> | <provider>` for dashboard pane detection.

Core regression:

```bash
./scripts/test-core.sh
```

This covers the current core chain:
- native entry via `ash-chat` / `local-native`
- Codex/Claude session reuse and project memory injection
- global memory promotion and shared-memory export
- OpenViking support and overview fallback
- runtime cleanup
- tmux/swarm launch behavior
- dashboard snapshot behavior

### 本地 swarm shell

```bash
./scripts/start-swarm.sh codex
./scripts/start-swarm.sh claude
```

When run inside `tmux`, the script sets the pane title to `ash-swarm | <project> | <provider>`.

### 本地 dashboard

```bash
PYTHONPATH=src conda run -n cli python -m agent_swarm_hub.cli dashboard
```

默认地址：

```text
http://127.0.0.1:8765
```

### 远程入口

```bash
./scripts/start-telegram.sh
./scripts/start-lark.sh
```

## 典型工作流

### 本地原生 CLI 工作流

```text
选择项目
  -> 看项目摘要
  -> 回车确认
  -> 进入项目路径
  -> 恢复绑定的原生 Claude/Codex 会话
  -> 或启动 fresh native session
  -> 继续实现
  -> 退出后自动回写项目记忆与摘要
```

### 远程聊天工作流

```text
Telegram / Lark
  -> 项目命令层
  -> Claude 讨论 / 拆解 / 汇报
  -> Codex 实现 / 执行 / 验证
```

### swarm 工作流

```text
进入项目
  -> 进入 swarm shell
  -> 统一命令层
  -> task / worker / execute / sessions
```

### dashboard 工作流

```text
打开 dashboard
  -> 看多项目总览
  -> 看 current focus / state / next step
  -> 看当前绑定 session 与 runtime session
  -> 再切到 ash-chat / ash-swarm
```

## 当前重点能力

- 项目进入与项目路径绑定
- Claude / Codex 原生会话恢复
- 项目 `summary` / `project_memory` / `global_memory` 分层
- 原生会话按项目归档
- `provider_bindings` 管当前默认恢复会话
- `active / archived` 管原生会话生命周期
- 远程聊天与本地原生 CLI 共用同一套项目模型

## 常用命令

- `/projects`
- `/use <workspace>`
- `/where`
- `/write <task>`
- `/execute [notes]`
- `/worker`
- `/tasks`
- `/sessions`

原生 CLI 里还可以直接运行：

- `ash-where`
- `ash-where --json`

用于查看当前项目、路径、provider 和 session 状态。

项目级 native 会话维护命令：

- `project-sessions current <project>`
- `project-sessions list <project>`
- `project-sessions use <project> <provider> <session-id>`
- `project-sessions sync-memory <project>`
- `project-sessions sync-memory --all`
- `project-sessions cleanup-runtime`（先 dry-run）
- `project-sessions cleanup-runtime --apply`（执行清理）

推荐清理口径（保留核心运行态，清历史残留）：

- `project-sessions cleanup-runtime --tmux-grace-minutes 20 --stale-workspace-days 7 --pane-log-days 7 --ccb-registry-days 7`
- 若要连 OpenViking 孤儿导入目录一起清理：追加 `--prune-openviking-imports --openviking-import-days 14`

## 数据层

系统目前有两层主要数据：

### 共享项目库

`/Users/sunxiangrong/dev/cli/local-skills/project-session-manager/data/sessions.sqlite3`

负责：

- 项目总账
- 项目画像
- 项目摘要
- 项目长期记忆（`project_memory`）
- 跨项目共享记忆（`global_memory`）
- Claude / Codex 原生会话归类
- 当前绑定会话

导出文件：

- 每个项目工作区内：
  - `PROJECT_MEMORY.md`
  - `PROJECT_SKILL.md`
- 共享数据库目录内：
  - `SHARED_MEMORY.md`

### 本地运行时库

`/Users/sunxiangrong/dev/cli/git/agent-swarm-hub/var/db/agent-swarm-hub.sqlite3`

负责：

- chat 绑定
- task
- phase
- handoff
- worker 运行态
- 临时消息与执行流

## 管理边界

现在建议把整套系统理解成六条管理线：

### 1. 项目管理

负责回答“这个项目是谁、在哪、默认怎么进入”：

- 项目 id
- workspace path
- 项目 summary
- 项目默认 shared scopes

主入口：

- `workspace_ops.py`
- 共享项目库里的 `projects`

### 2. 会话管理

负责回答“这个项目当前绑定哪个 provider 会话、历史有哪些会话”：

- `provider_bindings`
- `provider_sessions`
- `active / archived`

主入口：

- `native_entry.py`
- `project-sessions current/list/use`

### 3. 记忆管理

负责回答“项目自己记住什么、共享层记住什么”：

- `project_memory`
- `global_memory`
- `project_memory_scopes`

主入口：

- `project_context.py`

### 4. 共享记忆管理

共享记忆现在不是单层，而是三层：

- `project`
  - 单项目使用
- `shared:<group>`
  - 一组项目共享
  - 例如 `shared:all-projects`、`shared:bioinfo`、`shared:nwafu`
- `global`
  - 所有项目都通用

当前原则：

- 项目细节留在 `project_memory`
- 跨项目规则进入 `shared:<group>`
- 真正所有项目都适用的默认行为才进入 `global`

### 5. OpenViking 管理

负责回答“本地项目视图如何导出并同步到 OV”：

- `PROJECT_MEMORY.md`
- `PROJECT_SKILL.md`
- `SHARED_MEMORY.md`
- `var/openviking/imports/...`
- `var/openviking/data/viking/...`

主入口：

- `cli_ops.py`
- `openviking_support.py`
- `agent-swarm-hub openviking|ov ...`

### 6. 运行时清理管理

负责回答“哪些是正在运行的状态，哪些是历史残留”：

- tmux sessions / panes
- pane logs
- runtime workspace sessions
- ccb registry
- OV orphan imports

主入口：

- `project-sessions cleanup-runtime`

一句话收口：

- `workspace/project` 管身份
- `session` 管当前入口
- `memory` 管上下文
- `shared/global` 管跨项目复用规则
- `OV` 管导出和检索视图
- `runtime cleanup` 管残留回收

## 代码结构

当前入口已经按职责拆分，`cli.py` 只保留总入口、命令分发和少量兼容包装：

- `src/agent_swarm_hub/cli.py`
  - 总入口、命令分发、兼容层
- `src/agent_swarm_hub/workspace_ops.py`
  - 项目选择、workspace 校验、新项目建档
- `src/agent_swarm_hub/local_chat.py`
  - `local-chat` 交互循环与记忆 checkpoint / finalize
- `src/agent_swarm_hub/native_entry.py`
  - `local-native` 启动链、session 恢复、上下文注入、postrun 回写
- `src/agent_swarm_hub/cli_ops.py`
  - OpenViking 管理、project session 命令、runtime cleanup
- `src/agent_swarm_hub/project_context.py`
  - 项目记忆、全局记忆、导出文件、记忆 consolidation

这意味着现在的主从关系是：

- shell / tmux 入口负责触发
- `cli.py` 负责路由
- 各子模块负责各自 workflow
- `project_context.py` 负责记忆和导出

## 记忆流转

简化后的记忆流转如下：

```text
项目对话 / 原生会话
  -> project_memory
  -> AI 提取 shared/global memory candidates
  -> 提升到 shared:<group> 或 global
  -> 导出 PROJECT_MEMORY.md / PROJECT_SKILL.md / SHARED_MEMORY.md
  -> 再同步到 OpenViking 项目树与 memory bundle
```

所以现在的写入优先级是：

1. 先写项目记忆
2. 再让 AI 提取跨项目共享候选
3. 再按作用域写入 `shared:<group>` 或 `global`
4. 再导出成本地视图
5. 再同步成 OV 视图

### 共享/全局记忆提取规则

共享记忆不是直接把项目记忆整段塞进去，而是走两层：

- AI 候选提取
  - 从 consolidation 结果里提取跨项目候选
  - 只允许输出长期有效的规则、偏好、环境约定
- 作用域归类
  - 若只对某些项目组有效，进入 `shared:<group>`
  - 若对所有项目都有效，进入 `global`

AI 侧约束重点：

- 允许：
  - 本地/服务器默认行为
  - 代理、tmux、MCP、OpenViking、provider 使用约定
  - 持续有效的工作习惯和偏好
- 禁止：
  - 项目专属任务
  - 一次性排障状态
  - 直接项目名
  - 绝对路径

如果某条内容带有项目名或路径，但确实有跨项目价值，应该先抽象成不依赖项目名和路径的通用规则，再进入共享记忆层。

### 共享记忆钩子

项目现在会自动带上“访问共享记忆的钩子”：

- `PROJECT_MEMORY.md`
  - `## Shared Memory Hooks`
- `PROJECT_SKILL.md`
  - `## Shared Memory Hooks`
- native env
  - `ASH_SHARED_MEMORY_SUMMARY`
  - `ASH_SHARED_MEMORY_HINTS`
  - `ASH_SHARED_MEMORY_SCOPES`
- compatibility env
  - `ASH_GLOBAL_MEMORY_SUMMARY`
  - `ASH_GLOBAL_MEMORY_HINTS`

这意味着项目在启动时不只知道“有全局记忆”，还知道自己绑定了哪些 `shared:<group>`，以及该去哪里读共享规则。

### AI 全局候选提取开关

默认开启：

```text
ASH_ENABLE_AI_GLOBAL_MEMORY_CANDIDATES=1
```

如果要退回到“只用本地规则提升，不采纳 AI 候选”的模式，可以设置：

```text
ASH_ENABLE_AI_GLOBAL_MEMORY_CANDIDATES=0
```

## 路径约定

默认项目目录根是：

`/Users/sunxiangrong/dev/cli/projects`

也就是通过 `add-project` 新建项目时，会默认创建到：

`/Users/sunxiangrong/dev/cli/projects/<project-id>`

如果需要覆盖，可以设置：

`ASH_PROJECTS_DIR=/your/projects/root`

运行日志、pane 输出和临时运行产物建议统一视为 runtime 数据，不作为仓库内容本身的一部分。

## 推荐配置

`.env.local` 至少建议包含：

```bash
ASH_EXECUTOR=claude
ASH_EXECUTOR_TRANSPORT=ccb
ASH_PROXY_URL=http://127.0.0.1:6789
```

说明：

- `ASH_EXECUTOR` 是默认 workspace 后端
- workspace 级配置会覆盖全局默认值
- `transport=ccb` 时优先走 `ask/askd + ccb`

## 当前状态

目前已经比较稳定的主线是：

- `ash-chat` 进入前显示结构化项目摘要
- 进入项目时切到正确路径
- 优先恢复项目当前绑定的 Claude / Codex 原生会话
- 无绑定时在正确路径里启动 fresh native session
- 退出 native CLI 后自动回写项目长期记忆和动态记忆
- 原生会话按项目归档，切换当前绑定时自动归档旧会话

仍然在持续打磨的部分主要是：

- 更自动化的原生会话分类
- 更稳定的项目摘要生成
- 更清晰的 swarm 与远程聊天协作边界

## 文档

- [文档索引](/Users/sunxiangrong/dev/cli/git/agent-swarm-hub/docs/README.md)
- [操作手册](/Users/sunxiangrong/dev/cli/git/agent-swarm-hub/docs/操作手册.md)
- [实现手册](/Users/sunxiangrong/dev/cli/git/agent-swarm-hub/docs/实现手册.md)
- [开发日志](/Users/sunxiangrong/dev/cli/git/agent-swarm-hub/docs/开发日志.md)
- [架构说明](/Users/sunxiangrong/dev/cli/git/agent-swarm-hub/docs/swarm-architecture.md)
