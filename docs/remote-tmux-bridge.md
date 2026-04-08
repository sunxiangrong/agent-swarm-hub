# Remote Tmux Bridge

`ash` 的第一阶段远端 bridge，不是完整 MCP 平台，也不是全开放的 tmux pane 控制器。

它的工作定义是：

- 一个带项目边界控制的远端 tmux AI bridge
- 让 agent 可以和 tmux 里的 `ssh` shell pane 交互
- 保持 `read -> act -> read` 的受控工作流

## 范围

第一阶段只做四件事：

- 复用 tmux pane 原语：
  - `list`
  - `read`
  - `type`
  - `keys`
  - `name`
- 把 `ssh` shell pane 视为一种可控 pane 类型
- 在项目层定义 pane / path / command 边界
- 让这套 bridge 可以被 `ash` 作为一个 runtime surface 调用

第一阶段明确不做：

- 完整 MCP server 重写
- 多 agent 编排平台
- 远端工作目录恢复
- conda/env 自动恢复
- SSH 权限系统全家桶

## 目标模型

当前要支持的 pane 类型只有两类：

- `agent pane`
  - 例如运行 `codex` / `claude`
- `ssh shell pane`
  - 例如已经进入 `ssh xinong`
  - 或 `ssh ias`

桥接的重点不是 pane 里运行的是谁，而是：

- 这个 pane 是否属于当前项目
- 这个 pane 是否允许 AI 交互
- AI 是否先读过 pane 再操作

## 分层

### 1. tmux primitives

职责：

- 列出 pane
- 按 pane 名称定位 pane
- 读取 pane 内容
- 向 pane 输入文本
- 发送按键
- 设置 pane 名称

这一层不做：

- 项目判断
- 远端判断
- 权限判断

### 2. pane classification

职责：

- 区分 pane 类型：
  - `agent`
  - `ssh-shell`
  - `manual`
- 给 pane 打逻辑角色名，而不是依赖 `%3` 这种编号

这一层的目的是让 `ash` 能说：

- `ssh:xinong`
- `ssh:ias`
- `agent:codex`
- `manual`

### 3. project boundary policy

职责：

- 定义当前项目允许控制哪些 pane
- 定义哪些路径只读、哪些可写
- 定义哪些命令默认允许、哪些必须人工确认

这是第一阶段最关键的差异层。

和 `tmux-bridge-mcp` 的差别不在原语，而在这里：

- 原项目偏“任意 agent 可交互任意 pane”
- `ash` 版本必须偏“当前项目内受控 pane + 受控边界”

### 4. ash integration

职责：

- 把 bridge 当成一个 runtime surface
- 把当前项目和当前 bridge 会话绑定
- 后续再接 dashboard、follow-up、supervisor

第一阶段这里保持很薄。

## 推荐拆分

方案 A 下，这套实现不继续堆进 `ash` 仓库。

推荐拆分是：

- `tmux-bridge-mcp`
  - 继续承载原语层和 pane I/O
- `ash`
  - 只保留：
    - 设计文档
    - 项目边界策略
    - 项目级 bridge policy 文件
    - env 导出接口
    - 后续调用接口

也就是说：

- `ash` 不做新的 tmux 原语实现
- `ash` 只定义“哪些 pane / path / command 可用”

## 最小接口

第一阶段最值得先实现的接口：

- `bridge_list(project_id)`
- `bridge_read(project_id, pane, lines=80)`
- `bridge_type(project_id, pane, text)`
- `bridge_keys(project_id, pane, keys)`
- `bridge_name(project_id, pane, name)`
- `bridge_panes(project_id)`
  - 返回逻辑 pane 视图：
    - 名称
    - 类型
    - 是否可控
    - 当前路径
    - 当前命令

这里 `pane` 默认应使用逻辑名称，而不是 tmux pane id。

当前 `ash` 已有的薄接口是：

- `project-sessions bridge-policy <project> --init`
  - 初始化或查看项目的 `.ash/bridge-policy.json`
- `project-sessions bridge-env <project>`
  - 输出可直接提供给 `tmux-bridge-mcp` 的 `export ...` 环境变量
- `project-sessions bridge-status <project> --provider codex --exports`
  - 查看当前项目 tmux 工作台的 session、pane 标签和 bridge policy/env 摘要
- `project-sessions open-tmux <project> --provider codex`
  - 复用项目 tmux 会话，并在新的 macOS Terminal 窗口中 attach
  - 可选 `--bridge-layout --ssh-target xinong --ssh-target ias`
    - 自动给当前窗口补 `manual` pane 和 `ssh:<target>` pane

## 默认工作流

agent 的默认工作模式应固定为：

1. `read`
2. `type` 或 `keys`
3. `read`

不允许把 bridge 当成盲发命令通道。

## 边界模型

第一阶段至少支持三类边界：

### pane boundary

- AI 只能操作项目允许的 pane
- `manual` pane 默认只读或不可控
- 不允许跨项目 pane 控制

### path boundary

- 声明哪些路径只读
- 声明哪些路径可写
- ssh shell pane 中若当前路径超出项目声明范围，应拒绝写操作

### command boundary

默认允许：

- `pwd`
- `ls`
- `cat`
- `head`
- `tail`
- `grep`
- `find`
- 项目显式允许的检查命令

默认需要人工确认或拒绝：

- `rm -rf`
- `sudo`
- `reboot`
- 系统级 kill
- 明显越界写操作

## 与 tmux-bridge-mcp 的关系

可以直接复用的部分：

- pane 名称解析
- `list / read / type / keys / name`
- `read-before-act` guard

不应直接照搬的部分：

- 默认全开放 pane 控制模型
- 把所有 pane 当成同等对象
- 缺少项目边界和命令边界

一句话：

- `tmux-bridge-mcp` 提供原语层
- `ash` 提供项目边界层
- 推荐把 `ash-workbench` 作为运行台项目名，用来承载 tmux、ccb、ssh pane、follow-up/monitor 的默认操作记忆与规则

## 第一阶段完成标准

做到以下几点即可认为第一阶段成立：

- 可以识别并列出当前项目可见的 tmux pane
- 可以按逻辑名称读取和输入指定 pane
- `ssh` shell pane 可被当作 bridge 目标
- 默认执行流保持 `read -> act -> read`
- pane / path / command 边界已落在策略层

做到这些后，再考虑：

- supervisor pane
- follow-up job
- dashboard 可视化
- 更深的 ccb 集成
