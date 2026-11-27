# Herdr 内层快车道与 Magpie/AGY 兼容指南 / Herdr Fast-Path

当协作双方位于同一个 Herdr 会话（或本机另一个具名 Herdr `--session`）内时，无需使用纯 cmux 时代的重型终端粘贴锁或多轮空跑 ACK，优先使用原生 `herdr agent prompt` 通道。

## 1. 环境识别（兼容 `HERDR_ENV=0 mg ...` 历史面板）

优先检查：
```zsh
# 标准面板 HERDR_ENV=1；曾以 HERDR_ENV=0 启动但仍继承 HERDR_PANE_ID 与有效 HERDR_SOCKET_PATH 的面板也可识别：
/Users/lzhs/.local/bin/herdr-roster caller-env
```
- 若 `inside_herdr=true`：当前会话位于 Herdr 面板内，使用 `$HERDR_SOCKET_PATH`（或显式 `--session <name>`）调用 `herdr` CLI。
- **新建面板启动建议**：
  - 启动 Claude Code 直接运行 `mg cc linxi -k linxi-s1`（不加 `HERDR_ENV=0`，使 `~/.claude/hooks/herdr-agent-state.sh` 正常向 Herdr 上报原生 `session_id` 以支持重启续跑）。
  - 启动 Antigravity 运行 `/Users/lzhs/.local/bin/agy`；启动 Grok 运行 `grok`。
  - 启动 Codex 运行 `mg cx agentrouter-a`（若重启后 `ccc-codex-host` 因 `st_dev` 变更拒启，可临时调 `/Users/lzhs/.local/bin/codex-toolchain-launch`；`herdr-roster` 通过 `pane process-info` 识别已知包装进程名）。

## 2. 标准协作闭环与实测边界

1. **查花名册与状态（只读）**：
   ```zsh
   /Users/lzhs/.local/bin/herdr-roster snapshot --session "${HERDR_SESSION:-default}"
   ```
   若新拆面板尚未命名，用 `herdr workspace/tab/pane/agent rename`（或 `herdr-roster apply`）补齐 `[T<idx>·<方位>|<pID>] <角色>-<CLI> · <职责>` 与小写短代号（如 `t1-rev-cc1`）。
2. **发前核空草稿 + 首包直发（免预握手）**：
   - **发送前检查输入框**。Herdr 显示 `idle` 时，输入框仍可能留有草稿，直接发送会将草稿合并提交。2026-11-27 本机已安装三态只读检查。显式填写已核实的会话和面板：
     ```zsh
     /Users/lzhs/.local/bin/herdr-roster check-draft --session c4-multiagent --pane w1:pC
     ```
     - `compose_state=empty`，退出码 `0`：观察到完整边界内的空输入框。
     - `compose_state=draft`，退出码 `2`：发现单行或多行内容；不发送、不清空用户草稿。
     - `compose_state=unknown`，退出码 `3`：未找到输入框、边界不完整、读屏失败或格式不识别；不能当作空输入框，不自动发送。
     输出只含状态与原因，不回显草稿。此检查与随后发送不是原子操作；退出码 `0` 只证明观察时刻为空，不能保证用户随后没有输入，也不能作为安全发送凭据。遇 `draft` 或 `unknown`，先保存待交付文件并暂缓终端注入。下面仅是完成目标身份和输入保护后的发送格式，不与检查命令自动串接：
   ```zsh
   herdr agent prompt t1-rev-cc1 "[TASK:ch3-r1 FROM:t1-sup-cx1 MODE:pipeline] 请审阅 /abs/path/ch3.md §3.2，区分 BLOCKER 与 ADVISORY，完成后用 herdr agent prompt t1-sup-cx1 回报路径与结论。"
   ```
   - 2026-11-26 本机 `c4-multiagent` 实测（`w1:pE` → `w1:pC`，`n=1`）：`herdr agent prompt` 本地 IPC 返回耗时 `326.6 ms`，从发起到对端 Claude 转录落盘总耗时 `1.018 s`（提交返回后 `0.692 s` 落盘，不含模型推理耗时）。
   - **包装版 Codex 读取注意**。未进入 `herdr agent list` 的面板可用 `herdr pane list` 定位，再用 `herdr pane read <pane_id> --source visible --format text` 读取当前画面。
3. **回传、超时读回与 60 秒求派规则**：
   - **回传**：协作者完成后执行 `herdr agent prompt <sup_code> "[DONE:<id>] 结论与文件路径"` 唤醒或入队通知主管。
   - **Codex 压缩期超时保护（重要实测边界）**：对端处于 `blocked` 时 `herdr agent prompt` 会直接报错 `agent_blocked` 拒发；而对端 Codex 正在上下文压缩（`compacting`）时，11-04 实测 `herdr agent prompt` 可能返回 `timeout`，但原消息会在约 `170s` 压缩完成后才落盘。因此**凡遇 `timeout` 或结果不明，必须先用 `herdr agent read <target>` 或核查转录尾部，严禁盲目重发**。
   - **60 秒主动求派触发界定**：凡用户通过 `/loop` 启动、或指令明确包含「持续推进 / 没回就反复问 / 做完继续求派」的任务，均属已授权持续求派范围——若主管收到 `[DONE]` 后超过 60 秒仍未派发下一包且总任务未完结，执行者按至少 60 秒间隔主动追问；普通单次问答不自动开启循环追问。
