# Executor idle pull：执行者不得死等

## 起因（2026-10-08，CMAverse r14–r19）

每轮执行者都是「报告→唯一一次 callback→`DISPATCH_UNCONFIRMED`（主管输入框忙）→closeout 守卫封死工具→只能回 REPORT_READY 行」。之后执行者无法再做任何事，主管又在忙别的，用户只能反复问「中断了么」。callback 未确认本身是正常的，问题在于：
- 没有一条不依赖主管终端空闲的「我空了，派活」渠道；
- 也没有机制逼主管处理这个请求。

## 机制

1. **执行者**：原 callback 返回后、交 REPORT_READY 行之前，运行唯一一条命令：
   ```
   rtk proxy <release>/scripts/cmux_idle_pull.py --task-pack <artifact_root>/task-pack.json
   ```
   - 写入：`~/.local/state/multi-agent-collaboration/idle-requests-v1/<workspace>/<executor>.json`（原子覆盖，每个执行者一份）和 `<artifact_root>/executor-idle-request.json`（任务证据目录副本）。
   - 不发终端输入。主管输入框再忙也不会丢，也不会覆盖草稿。
   - 前置条件：报告非空，且已有原 callback attempt 或 receipt。不满足就拒绝，不能用它来跳过 callback。
2. **closeout 守卫（PreToolUse）**：callback 返回后只放行这条精确命令。
   - 加 shell 尾巴、改用后台运行、换成别的 pack 路径，都会被拦截。
   - 拦截提示里会直接给出这条命令。
3. **Stop 守卫（执行者侧）**：精确的 REPORT_READY 行仍然只是交接，不算送达。但现在必须先存在一份与当前冻结报告（task_id + report_sha256）绑定的 idle 请求，否则返回 `EXECUTOR_IDLE_PULL_REQUIRED`，并给出命令。
4. **Stop 守卫（主管侧）**：只要 inbox 里有发给本主管的未结请求，就返回 `EXECUTOR_IDLE_REQUEST_PENDING`，不准结束本轮。结清方式二选一：
   - 用 `submit_task_pack` 派发下一包。之后出现一次「本主管→该执行者、晚于请求时间」的 task-dispatch attempt，即自动结清。
   - `cmux_idle_pull.py --ack <executor> --workspace <ws> --supervisor <me> --reason "WAITING_DEPENDENCY: …"`。ack 绑定请求的 sha，执行者发出新请求后会重新打开。空 reason 和他人代 ack 都会被拒。

请求格式损坏时一律视为未结（只给路径，不删除）。`stop_hook_active=true` 时照常放行，不会无限递归。

## 边界

- 这不是送达确认，不是业务接受，也不能用来 disarm。callback 核收仍由主管在原始尝试上零输入完成。
- 不准用 idle 请求重发旧 callback，也不准用它补造回执。
- 主管选「等依赖」是合法结清，但必须写明原因。不允许静默。
