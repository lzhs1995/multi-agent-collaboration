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
   - 用 `submit_task_pack` 派发下一包。之后出现一次「本主管→该执行者、晚于请求时间」且 `phase=CONFIRMED` 的 task-dispatch attempt，即自动结清。`NO_INPUT`/`POST_ENTER_OBSERVATION`（排队、未确认送达）不算，请求保持 pending；journal 核收后转 CONFIRMED 才算。
   - `cmux_idle_pull.py --ack <executor> --workspace <ws> --supervisor <me> --reason "WAITING_DEPENDENCY: …"`。ack 绑定请求的 sha，执行者发出新请求后会重新打开。空 reason 和他人代 ack 都会被拒：身份取自现役调用方（`caller_snapshot`+`resolve_snapshot`，与 bridge 发送同一套），必须同工作区且 surface 正是请求里的主管，`--supervisor` 参数不提供身份。不符报 `ACK_CALLER_MISMATCH`，解析失败报 `ACK_CALLER_UNRESOLVED`，都不落 ack。

请求格式损坏时一律视为未结（只给路径，不删除）。`stop_hook_active=true` 时，主管侧的提醒照常放行，不会无限递归；执行者自己的未回复请求例外，见下文。

## 反复问直到主管回复（cmux_idle_push）

主管侧 Stop 只在回合结束时触发；Codex 一个 goal 回合可以跑好几个小时，期间听不到文件请求。所以第 1 步的命令在写完请求后，还会启动一个脱离会话的后台催办器 `scripts/cmux_idle_push.py`（每个执行者一个，靠 flock 保证单例）：

- **节奏**：立即问一次，之后**每 60 秒**再问一次（间隔恒定，满足非递减），最长 24 小时。每次都是带新 marker 的 `STATUS:` 普通消息（`IDLEPUSH-<executor前8位>-<第几次>-<请求sha前8位>`），经 journaled bridge `submit_text` 发出，不是重发旧消息，也不是重发 callback。
- **不叠加**：主管输入框里还留着上一条时，bridge 返回 COMPOSE_OCCUPIED，不输入任何内容。这次的结果记账，下一个 60 秒用新 marker 再试。投递异常只记账，不终止催办。
- **停止条件 = 主管回复**，满足任一即停：
  - 带理由的 `--ack`，且绑定当前请求 sha；
  - 晚于请求且 CONFIRMED 的 task-dispatch，方向是本主管→本执行者；
  - 晚于请求且 CONFIRMED 的普通消息，同为本主管→本执行者（message-dispatch journal 首个 PASTE_INTENT 时刻）。未确认送达的消息不算回复，继续每 60 秒催。
- **到 24 小时**：记 `EXHAUSTED`。执行者下一次交付时会写新请求，重新开始。
- **新请求**（下一任务交付）：同一个催办器发现请求 sha 变了，就从第 1 次重新开始，不会出现新请求无人催办的情况。
- **状态**：`~/.local/state/multi-agent-collaboration/idle-push-v1/<ws>/<executor>/status.json`（pid、每次的 marker/时刻/结果、ANSWERED/EXHAUSTED），日志写在同目录的 `push.log`。
- **执行者 Stop**：放行精确 REPORT_READY 行需要同时满足两点：请求与冻结报告绑定，且催办器持有锁或请求已被回复（`ready_for`）。只写了请求、催办器没起来，照样拦截。
- **测试**：只能通过 `CMUX_IDLE_PUSH_BRIDGE=<假 bridge .py>` 注入。测试用的 `idle_push_fixture.py` 只把调用写进 JSONL，结束时杀掉催办器。`supervisor_answers(home)` 替主管写绑定 sha 的 ack，仅供测试。

## 任何时候等主管：`--request` 和前台 `--wait`

交接不是唯一需要主管回话的时刻。执行者在任务途中需要主管决定，或者通知在 COMPOSE_OCCUPIED 后没被看到，都不准停下等。规则是：**任何等待都必须走 `--request`**。

1. **登记**：`cmux_idle_pull.py --request --workspace WS --executor EX --supervisor SUP --supervisor-ref <surface> --report <绝对路径> --reason <在等什么>`。
   - 只认真实调用者：live CLI 解析出的工作区和 surface 必须正好是 `WS`/`EX`，否则报 `REQUEST_CALLER_MISMATCH`。
   - 写完请求后，启动同一个 60 秒催办器。
2. **执行者 Stop 一直拦**：只要本执行者还有未回复的请求，Stop 就返回 `EXECUTOR_AWAITING_SUPERVISOR`，并给出唯一一条前台等待命令。
   - Stop hook 重入（`stop_hook_active`）**照样拦**。
   - 不受 24 小时上限影响，只有主管回复才放行。
   - 主管侧的提醒在重入时仍然跳过。
3. **前台等待**：`rtk proxy <release>/scripts/cmux_idle_pull.py --wait --workspace WS --executor EX`。每次最多等约 100 秒，结果有两种：
   - 主管已回复：打印 `ANSWERED` 和回复内容。
   - 尚未回复：打印 `WAITING`，提示原样重跑。如果催办器已经不在跑，会先把它重新拉起。

   封口守卫只放行这条命令本身：必须是前台 Bash，不能后台跑，也不能加 `; true` 之类的尾巴。

这样主管忙只会拖慢回复，不会让会话停下。执行者要么在前台等，要么被 Stop 拦回来；催办器同时每 60 秒再问一次。

## 边界

- 这不是送达确认，不是业务接受，也不能用来 disarm。callback 核收仍由主管在原始尝试上零输入完成。
- 不准用 idle 请求重发旧 callback，也不准用它补造回执。
- 主管选「等依赖」是合法结清，但必须写明原因。不允许静默。
