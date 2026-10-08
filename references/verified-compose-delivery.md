# 原生投递、原次恢复与有界等待

本页是 prompt、任务包、callback、封口及 idle 等待的统一操作合同。
`SKILL.md` 和各客户端适配层引用本页；旧屏幕判据、独立 Enter/Tab
预算和无限求派说明不再作为当前规则。执行使用原任务固定的完整控制器；
文档更新不迁移在途任务，不证明已安装或已在真实客户端通过。

## 只有原接收端新增的完整 user 记录能确认收到

输入前，原 controller 必须把以下信息固定到原 attempt，并在输入和核收时复核：

- 真实 caller 与接收方的同 workspace、surface/pane UUID、TTY，以及原生
  进程 PID、出生身份和可执行文件身份。焦点、标题、继承环境不代替身份。
- 接收方原生 session 与对应 transcript 的确定路径、device/inode、已有
  字节和边界哈希。只能从目标进程/会话绑定该文件，不能按日期、mtime
  或全局 marker 搜索选择一个看似匹配的会话。
- 完整 payload 及其 SHA、原 task/nonce、任务包和报告等适用 pins。
  `PASTE_INTENT` 必须在真实输入前持久化，并固定当时新鲜 EOF fence。

只有**同一 workspace/surface/process/session/transcript，在原
PASTE_INTENT 的新鲜 EOF fence 之后新增、全文完全相等的 native user**
才为 `NATIVE_RECEIVED`。保留空格、制表符、空行、换行和字面转义；
不得 trim、拼接多个记录、只查子串/nonce 或用引用替代完整入站记录。
assistant、tool、hook 输出和用户引用旧消息都不是本次入站证据。

| 证据 | 可以报告的事实 |
| --- | --- |
| 粘贴、Enter/Tab、exit 0、空 composer、屏幕活动或 ACK | 仅对应动作或观察；不能确认收到 |
| Claude `queued_command` 或 UI queue | `NATIVE_QUEUED`/pending；不能确认收到 |
| 原绑定及原 fence 后新增的完整 native user | `NATIVE_RECEIVED`；只证明接收 |
| 原 controller 核证据后发布正式 receipt | 原次传输已核收；不授业务接受 |
| 主管独立审读报告及原件并裁定 | 对应业务范围已接受；资源释放另验 |

缺绑定、旧 fence、文件被替换/截断、正文被改写或证据不可读，均保留未确认。
旧尝试缺少输入前 binding/fence 时，拒绝追补；不重发、不把当前 EOF
冒充过去的输入边界。报告被发现、被读取与消息被收到是不同事实。

## 首次输入只有一次粘贴和一次提交键

复用受保护 bridge。首次输入前确认 agent TUI 和空 composer；SHELL、
UNKNOWN、压缩、重连、外来草稿或未识别结构不得输入。原 attempt 只粘贴
一次，再有界只读等待**完整且稳定的原草稿**。等待耗尽就保留原次，不按键。

忙碌 Codex 若明确显示 `tab to queue message`，且完整原草稿及已测
Codex 结构均匹配，首次直接使用 `tab`。其他清晰且受支持的提交状态使用
`enter`。不先给忙碌 Codex 一个可能被解释为换行的 Enter；不另造
Ctrl+Enter 或裸键绕路。Tab 结果仍是 pending，直到原生 user 证据成立。

每次粘贴/按键前都重核身份，按键意图在动作前持久化。折叠粘贴摘要、残缺
首帧和未知 footer 不能证明完整草稿。已测显示等价只用于草稿保护，
不能降低原生全文比较的严格程度。未知状态不清空，不重复粘贴。

## 原次恢复共用最多一次补键

先通过**原 sender/controller** 对原 attempt 做零输入核收，检查迟到的
完整 native user。已收到或已排队时不补键。只有同时满足下列条件，原
controller 支持的恢复入口才可在同一 attempt 内补一个现场支持的 Enter
或 Tab：

- 原报告、任务包、payload、nonce、双方身份及原生 binding/fence 不变；
- 当前完整未改草稿仍属于原次，结构稳定，原持久锁及 inode 可核；
- 原历史及当前观察中没有 UNKNOWN、compaction、queue、重连、结构变化
  或用户编辑等失去草稿所有权的证据；
- 原 controller 能证明这一个共享补键预算尚未消耗。

自动路径与显式恢复**共享最多一次补键**，不能分别建 Enter、Tab 预算。
补键意图落盘即消耗，动作失败、进程中断、重启或未确认均不重置。
诊断工具不能授予补键；手动见到草稿不构成授权。原版本不支持恢复时交主管，
不把新模块单独覆盖到旧 runtime。预算耗尽仍可有界零输入核收。

普通消息、任务派单和 callback 各用自己的原 journal 与原 controller。
`--reconcile-only` 是零输入核收。当前 CLI 的 `--recover-stranded`
只属于 `submit-text`：原次须为 `NATIVE_PENDING`、原记录为
`PASTE_INTENT`/`ENTER_SENT`，并通过上述全部门禁及未用补键预算。
`submit-task-pack` 和 `submit-completion-callback` 仅核原次，不能追加
该选项。callback 的旧 queue-only Python 接口不提供额外 Tab 配额。不能改 nonce、换会话、删 journal
或重写旧 attempt 来取得新发送槽。

## 封口、Stop 与主管报告发现

报告完成后先冻结报告、task pack 和原 attempt 既有记录，再走原 callback。
封口阻止继续扩展测试、改报告、追加科研或重复发送；允许严格 task-bound
的只读诊断及原 controller 零输入 reconcile。核收如需追加观察或原子
发布 receipt，必须沿原控制器及持久锁，不改写历史。诊断入口是
`scripts/cmux_callback_diagnose.py --task-pack <原任务包绝对路径>`；
它不发键、不造回执、不清 marker，也不开放任意 shell/文件访问。

绑定报告与原回调终态成立后，Stop 可接受普通诚实说明，例如“报告已冻结，
回调尚未确认，等待主管核收”；不要求唯一精确 STATUS 模板。合法等待
返回 `WAITING_SUPERVISOR`、`continue:false`、`suppressOutput:true`，
保留 task/receipt 状态。明确声称 callback confirmed、共识或业务通过而
无对应证据时仍拒绝。缺失、漂移或在途证据不能被包装成合法等待。

Stop/SubagentStop 仅对严格布尔 `stop_hook_active is True` 处理重入；
字符串和数字不享该边界。重入处理不生成 receipt、不 disarm、不授验收，
下一正常回合仍核原证据。具体 hook 出口见
[Stop 生命周期](stop-hook-lifecycle-20261005.md)。

主管通过认证的 active markers 有界发现本 workspace、本 supervisor
所属的冻结报告；由 PostToolUse 入口 `scripts/cmux_supervisor_report_guard.py` 记录
`REPORT_DISCOVERED` 并提供报告上下文。不扫全盘，
不借 marker 文本越过身份核验。发现报告不是投递确认、业务接受或 disarm。
主管可先独立读固定报告推进工作，再按原证据分别核收通信、裁决业务及
定向解除原任务。执行者不因回执待核无限续跑；主线不依赖该回执的工作继续。

## idle 与 CCC 都有明确等待期限

任务 disarm 后，idle Stop 只保存一次持久 idle 状态并允许结束；
没有求派记录、后台进程或主管答复都不能成为 Stop 阻断理由。
`cmux_executor_idle_guard.py` 不自动粘贴求派，不要求每 60 秒催派。

需要主动请求时，显式 `executor_ready.py ask`（旧拼写 `request`）
最多提交同一原请求一次。未确认保留同一 payload、marker、nonce 和原
attempt；后继只能核原次，不能周期性重贴。主管答复必须绑定原请求，
沿原 bridge 到达原 surface，或写到该请求给出的精确 mailbox。
mailbox 必须携带原 loop record 的 `episode_id`、`caller_surface_uuid`
和 `task_id`（即使 task_id 是空字符串也不能省略）。idle binding 通知的
独立 `episode_id` 不代替 loop record，也不证明已建立等待段。

显式 `executor_ready.py persist` 只读原请求、绑定 transcript 和 mailbox，
默认 60 秒、最长 300 秒；原期限与已用预算沿原 episode 保存，不因重启
延长。任务、主管或绑定 transcript 改变时保留原段并记
`UNRESOLVED_IDLE_EPISODE`，不自动另开一段；损坏的原请求/停止记录
保守终止，不自动重发。真实答复、新任务、
operator stop 或期限耗尽都结束观察。`WAITING_DEPENDENCY`、
`TIMED_OUT` 与收到答复分别记录；超时不是投递失败证明、业务完成或
下一轮自动催派许可。保持原状态，等待有实际授权的新事件。

CCC 在合法报告等待边界与 `WAITING_SUPERVISOR` 同步，并保留有限期限；
不得把等待改写为无限模型续轮。native Goal 是独立机制，不能从 CCC 配置、
本地 hook 放行或源码修改推断其实际状态。等待结束、工具可用性、任务接受
和新任务派发分别验收，不通过全局关闭保护来消除循环。

## helper 只保留一个受保护发送入口

普通消息的 helper 必须由 `scripts/render_cmux_agent.py` 从固定基线渲染。
只有绝对可执行路径、完整渲染字节、同一 release 的 adapter 与相同 Python
全部通过守卫，直接 `ask/send/broadcast/reconcile` 才准入；可加 `rtk` 或
`rtk proxy` 前缀。shell/env 包装、嵌套、重定向、裸 cmux 和替代发送器拒绝。
helper 仍调用唯一 guarded bridge；正式 task/callback 保留专用入口。
reconcile 须绑定原 intent，零输入；缺证明只保留 pending，不报收到。

## 一致版本与证据边界

发送器、journal reader、PostToolUse、Stop、idle、启动器和技能适配层
必须来自相容的完整固定版本。`cmux_native_delivery_guard.py` 只核当前
投递调用及其原 attempt；未确认不能报成功，普通工具不扫旧账。它不发键、
不造 receipt；不能关闭或改成 advisory 来绕过证据。旧屏幕/send-proof
hook 的退役由受支持安装器限定本包注册，保留其他任务及 foreign 配置。

配置落盘、完整版本安装、新进程导入、真实 hook 执行、当前客户端加载、
实际 native user 入站、Stop/CCC/Goal 等待及业务验收分别留证。真实验收
必须覆盖 Claude → supervisor；反方向成功不能替代。新任务解析当前
skill 的真实完整 release，在途原次仍保留原 controller。
离线测试不替代现场；一次成功也不能保证未来所有 UI 传输绝无失败。
能保证的合同是：未知不报成功、保留原次、恢复有界、证据不被重写。
