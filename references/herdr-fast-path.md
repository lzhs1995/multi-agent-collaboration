# Herdr 内层秒级快车道与 Magpie/AGY 兼容指南 / Herdr Fast-Path

当协作双方位于同一个 Herdr 会话（或本机另一个具名 Herdr `--session`）内时，**严禁套用纯 cmux 时代的重型粘贴锁、多轮 ACK 握手或转录文件全量扫描**，一律走原生 `herdr` 秒级快车道。

## 1. 环境识别（兼容 `HERDR_ENV=0 mg ...` 历史面板）

优先检查：
```zsh
# 标准面板 HERDR_ENV=1；即使曾以 HERDR_ENV=0 启动，只要继承了 HERDR_PANE_ID 与有效 HERDR_SOCKET_PATH 仍在 Herdr 内：
/Users/lzhs/.local/bin/herdr-roster caller-env
```
- 若 `inside_herdr=true`：当前会话即为 Herdr 内层成员，使用 `$HERDR_SOCKET_PATH`（或显式 `--session <name>`）调用 `herdr` CLI。
- **新建面板启动最佳实践**：
  - 启动 Claude Code 直接运行 `mg cc linxi -k linxi-s1`（**不要加 `HERDR_ENV=0`**！保留 `HERDR_ENV=1` 才能让 `~/.claude/hooks/herdr-agent-state.sh` 向 Herdr 上报原生 `session_id`，实现断电重启自动续会话）。
  - 启动 Antigravity 直接运行 `/Users/lzhs/.local/bin/agy`；启动 Grok 直接运行 `grok`。
  - 启动 Codex 运行 `mg cx agentrouter-a`（若遇重启后 `ccc-codex-host` 报 `st_dev` 变更，仅临时绕行 `ccc-codex-host` 或直接调 `/Users/lzhs/.local/bin/codex-toolchain-launch`，`herdr-roster` 对两种启动方式均能 100% 自动识别为 `CX`）。

## 2. 三步秒级协作闭环（耗时 < 1 秒）

1. **查花名册与状态（只读）**：
   ```zsh
   /Users/lzhs/.local/bin/herdr-roster map --session "${HERDR_SESSION:-default}"
   ```
   若新拆面板尚未命名，用 `herdr tab/pane/agent rename`（或 `herdr-roster apply`）补齐 `[T<idx>·<方位>|<pID>] <角色>-<CLI> · <职责>` 与小写短代号（如 `t1-rev-cc1`）。
2. **首包直发（免预握手）**：
   ```zsh
   herdr agent prompt t1-rev-cc1 "[TASK:ch3-r1 FROM:t1-sup-cx1 MODE:pipeline] 请审阅 /abs/path/ch3.md §3.2，区分 BLOCKER 与 ADVISORY，完成后用 herdr agent prompt t1-sup-cx1 回报路径与结论。"
   ```
   - 若目标面板为包装进程未出现在 `herdr agent list` 中，先检查目标不处于弹窗阻塞后按 `pane_id` 受控投递，不盲补按键。
3. **回传与异常处理**：
   - 协作者完成后直接反向执行 `herdr agent prompt t1-sup-cx1 "[DONE:ch3-r1] 0 BLOCKER, 2 ADVISORY，详见 /abs/path/review.md"`。
   - 若 `agent prompt` 超时或返回 `UNKNOWN`，**先执行 `herdr agent read t1-rev-cc1` 只读核查对端是否已收到**，绝不盲目重发第二遍。
