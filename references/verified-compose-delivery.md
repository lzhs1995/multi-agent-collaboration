# 原生投递、原次恢复与有界等待

本页是 prompt、任务包、callback、封口及 idle 等待的统一操作合同。
新的普通长消息及握手/复审提示按[短通知与固定正文](long-prompt-delivery.md)
先固定全文再投递短通知；原生回执只证明通知收到，不证明正文已读或任务已接受。
`SKILL.md` 和各客户端适配层引用本页；旧屏幕判据与独立 Enter/Tab
预算不再使用。用户明确授权的每60秒新 marker 求派保留在下述可选入口，
不与默认有界 persist 混为一谈。执行使用原任务固定的完整控制器；
文档更新不迁移在途任务，不证明已安装或已在真实客户端通过。

## 只有原接收端新增的完整 user 记录能确认收到

输入前，原 controller 必须把以下信息固定到原 attempt，并在输入和核收时复核：

- 真实 caller 与接收方的同 workspace、surface/pane UUID、TTY，以及原生
  进程 PID、出生身份和可执行文件身份。焦点、标题、继承环境不代替身份。
- 接收方原生 session 与对应 transcript 的确定路径、device/inode、已有
  字节和边界哈希。只能从目标进程/会话绑定该文件，不能按日期、mtime
  或全局 marker 搜索选择一个看似匹配的会话。
- 完整 payload 及其 SHA、原 task/nonce、任务包和报告等适用 pins。
  新 Codex 输入的 `NATIVE_INPUT_INTENT`、Claude 终端输入的 `PASTE_INTENT`
  必须在真实输入前持久化，并固定当时新鲜 EOF fence。

只有**同一 workspace/surface/process/session/transcript，在原输入意图的
新鲜 EOF fence 之后新增、全文完全相等的 native user**
才为 `NATIVE_RECEIVED`。保留空格、制表符、空行、换行和字面转义；
不得 trim、拼接多个记录、只查子串/nonce 或用引用替代完整入站记录。
assistant、tool、hook 输出和用户引用旧消息都不是本次入站证据。

| 证据 | 可以报告的事实 |
| --- | --- |
| 原生 RPC 返回接受或 turn id | 请求已被 API 接受；不能单独确认原生收到、消费或 callback 核收 |
| 粘贴、Enter/Tab、exit 0、空 composer、屏幕活动或 ACK | 仅对应动作或观察；不能确认收到 |
| Claude `queued_command` 或 UI queue | `NATIVE_QUEUED`/pending；不能确认收到 |
| 原绑定及原 fence 后新增的完整 native user | `NATIVE_RECEIVED`；只证明接收 |
| 原 controller 核证据后发布正式 receipt | 原次传输已核收；不授业务接受 |
| 主管独立审读报告及原件并裁定 | 对应业务范围已接受；资源释放另验 |

缺绑定、旧 fence、文件被替换/截断、正文被改写或证据不可读，均保留未确认。
旧尝试缺少输入前 binding/fence 时，拒绝追补；不重发、不把当前 EOF
冒充过去的输入边界。报告被发现、被读取与消息被收到是不同事实。

## 新 Codex 输入直接进入原会话（0.4.26）

新普通消息、握手、正式任务、callback 和获授权继任消息统一经同版共享入口。
接收端为 Codex 时，使用既有 control socket，校验 socket 的属主、0600 权限、
device/inode 和实际 peer UID；不启动另一 daemon、不恢复另一会话、不改设置。
只读查询原 thread 元数据与最新一轮的元数据，必须对应绑定 transcript，且
`canAcceptDirectInput=true`。不读完整历史来推断路由。

- 原轮正在运行：一次 `turn/steer`，固定刚核得的 `expectedTurnId`。
- 原会话空闲：一次 `turn/start`，只传原 threadId 和完整 text input。
- 状态矛盾或不可用：该次不输入；保留证据和已配置文件通道，独立工作继续。

唯一输入前持久化 `NATIVE_INPUT_INTENT`、精确请求及新鲜 EOF fence。
新 Codex 输入不读、不粘贴、不修改 composer，也不发 Enter/Tab，不进入
`queued follow-up inputs`。这条路径适用于 executor 回 supervisor 的新 callback。
RPC timeout、异常、回执未知或记录写入失败都不能授权第二次输入；只观察
原绑定日志。不得降级为终端排队、换 nonce 或重开一次发送。
原生收到后，正文消费、ACK、正式 callback receipt、自动 hook 与业务接受另验。

历史 queued callback、PASTE_INTENT 和冻结包保持原控制器，仅按原证据核收；
绝不把旧队列消息再经 native 路径发送。新 native attempt 不具有补键预算。

## Claude 终端首次输入只有一次粘贴和一次提交键

复用受保护 bridge。首次输入前确认 agent TUI 和空 composer；SHELL、
UNKNOWN、压缩、重连、外来草稿或未识别结构不得输入。原 attempt 只粘贴
一次，再有界只读等待**完整且稳定的原草稿**。等待耗尽就保留原次，不按键。

清晰且受支持的 Claude 提交状态使用 `enter`，不另造 Ctrl+Enter 或裸键绕路。
旧 Codex terminal attempt 中的 Tab 仅是历史待核证据，不再是新输入操作规则；
队列仍为 pending，不能据此宣称原生收到。

每次粘贴/按键前都重核身份，按键意图在动作前持久化。折叠粘贴摘要、残缺
首帧和未知 footer 不能证明完整草稿。已测显示等价只用于草稿保护，
不能降低原生全文比较的严格程度。未知状态不清空，不重复粘贴。

Claude 状态栏的识别只在完整匹配边框之外进行。已测可选计数行
`CLAUDE.md | 规则 | MCPs | 钩子` 中的规则数不能误算为草稿；相同文字
出现在输入框内仍是原文。未知字段或残缺边框保留 UNKNOWN，禁止清空绕过。
空输入与空闲回合分别核验，识别修复不改变完整草稿及原生接收门禁。
完整边框外的同行时间字段可带精确提示 `new task? /clear to save N tokens`（N 仅为 ASCII 整数，或整数/一位小数加小写 `k`）；正文同字仍是草稿，未知提示及折叠草稿仍不授权输入。

## 历史终端原次恢复共用最多一次补键

本节只适用于带原 PASTE_INTENT 的终端尝试。新 NATIVE_INPUT_INTENT
只能只读核收；不能补键、重试 RPC 或转成终端发送。

### Current status and nested hook identity

同一 hook evaluation 内，外层身份解析与内层 bridge 核收使用同一原生 caller
采集来源；每次边界仍重新核进程与 cmux 树，不缓存身份、不改写 daemon 环境。
否则外层识别正确、内层退回继承的 workspace 会错误阻止合法核收。

完整 Claude 边框成立时，只从最新顶格 activity 行判断当前压缩或重连；
完成报告及 recap 内引用的历史状态不参与。Codex 的 steer queue 标题不属于
当前状态行；独立 warnings footer 由已测规则识别。真正的 Compacting context、
Compacting conversation、Reconnecting 以及未知界面仍不准输入。
这些兼容规则不改变草稿逐字匹配和原 fence 后完整 native user 的验收标准。

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

原 callback 的发送者与已认证原接收者都可以通过原 controller 显式
`--reconcile-only` 核收。主管观察收到的原回调时，hook 按原 journal 的
反向端点认证，不把主管误判成一次向自己的新发送。此例外只接受真实布尔
只读参数；动态值、展开参数、重复选项及与 queue-resume 混用均不接受。
原 executor、workspace、pane、接收进程、原始 EOF fence、完整消息和
报告/任务包一致性仍逐项核验；普通发送不享有反向端点例外。

hook 的 callback 核收提示只能采用冻结包中一致的 `required_skill` 与
`completion_command_argv` / `callback_command`，保留原 Python 和原
controller，追加 `--reconcile-only`。原命令缺失或冲突时明确报不可用，
不得猜测当前安装路径；已有真实原生接收证据仍保留。hook 仅作只读观察，
正式 receipt 仍由原控制器发布。

## 封口、Stop 与主管报告发现

PostToolUse 成功结果须符合客户端官方 schema：仅通过
`hookSpecificOutput.hookEventName=PostToolUse` 与字符串 `additionalContext`
传回完整核验结果，不能把内部 `action/results` 直接放在 JSON 顶层。
Codex 对未知顶层字段拒绝整份输出；原生记录存在与自动 hook 输出被接受
分别核验。现役工作流观察器始终 exit 0；身份、遥测或发现失败不阻断工具，
也不生成收到或接受的证据。显式诊断的失败状态与全局 hook 分开记录。
离线核官方 schema 后，再取活跃客户端实际自动调用记录；手动调用不算。

只读 CLI 帮助不是一次投递：精确的 bridge `--help` / `-h` 调用，包括
Python 脚本启动及 Codex 并行工具中的独立命令，不要求 task pack、身份
查询或原生回执。同批次真实 send/callback 仍逐条核验；正文中的 `--help`、
Python `-c` 和动态改写命令不能享此例外。遇到只读帮助被误报，保留原次
工具证据并修解析器，不重发旧 callback，不补造任务包或回执。

报告完成后先冻结报告、task pack 和原 attempt 既有记录，再走原 callback。
原包提交后不得擅自扩展研究、改动回调绑定报告或重复发送；追加工作需有
独立明确范围。普通工具、维护及诚实结束回合不受旧任务封锁。
原 controller 可零输入 reconcile；核收如需追加观察或原子
发布 receipt，必须沿原控制器及持久锁，不改写历史。诊断入口是
`scripts/cmux_callback_diagnose.py --task-pack <原任务包绝对路径>`；
它不发键、不造回执、不清 marker；该诊断的范围不限制其他已授权工具。

现役 Stop/SubagentStop 只经 `cmux_workflow_advisory.py` 记录实际调用并放行，
不调用旧 checker，不输出 deny、block 或 `continue:false`。结束回合无需
先取得主管回复、callback receipt 或旧任务 disarm。诚实说明未核收边界，
不把正常结束冒称 callback confirmed、共识或业务通过。
[历史 Stop 生命周期](stop-hook-lifecycle-20261005.md) 仅供旧控制器诊断，
不能恢复全局会话门禁；原任务、报告和回执原样保留。

从 0.4.26 起，主管 PostToolUse 的 advisory 入口有界调用
`supervisor_inbox.discover`，核验当前主管后提供去重的文件请求上下文。
遥测失败不阻止这次独立发现。它不读取旧报告、不答复、不发键、不接受任务，
无工具事件时也不能唤醒空闲主管。主管仍显式读取固定报告或认证的 active
markers，再按原证据分别核收通信、裁决业务及定向解除原任务。
发现请求不是投递确认、业务接受或 disarm；不依赖回执的主线工作继续。

## 默认 idle 观察与 CCC 有明确等待期限

idle Stop 只记录实际调用并允许结束，不写旧式 idle binding、不启动求派。
没有求派记录、后台进程、主管答复或 disarm 都不能成为 Stop 阻断理由。
`cmux_executor_idle_guard.py` 的历史实现仅供显式诊断，不由现役观察器调用。

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

## 用户明确授权的每60秒主动求派

默认 executor_ready 的有界单次观察不撤销用户另行授权的主动求派。
显式 `scripts/executor_reask.py run` 执行期间每5秒观察、每60秒最多发一个
新 marker；单次轮询预算最多540秒，同步 I/O 可延长实际耗时。命令返回后
原 episode 仍可为 WAITING_REPLY，这不证明 runner 仍活跃。必要时按授权
继续原 episode，不重贴旧请求、不重置原 attempt 的按键预算。
全局 `cmux_executor_reask_stop_guard.py` 注册经 advisory 观察器，不强迫续轮。
绑定主管答复或 operator stop 可结束等待；旧 AUTHENTICATED_NEW_DISPATCH
出口依赖较新的 idle binding，而现役观察器不写它，不能仅凭新包声称已触发。

派单前由绑定主管先按原请求模板写精确 mailbox，令显式等待返回，再做新鲜
现场预检及正式派发。TASK 邮箱答复本身不是任务包。原接收进程退出时保留
terminal_pending、原 binding 和 fence；历史完整原生收件可独核，但不冒造
官方 receipt，不删除 pending 或重开 episode 来绕过去重。

新求派使用同版 executor_ready、正文存储和 bridge。超过700 UTF-8字节或
带尾换行时固定完整正文再发送短通知，返回的通知 receipt 不证明正文已读。
新 Codex 求派同样直接走原生 steer/start，不因忙碌或用户草稿而改走 Tab。
原未确认请求只读核收；已有不确定输入时后继请求仍使用已配置文件通道，
不重放原次。原生入口不可用或不适合 Claude 终端输入时使用文件通道；每轮记录实际写入或错误，
空 channels、轮数增长和历史 marker 均不证明送达。历史 marker 无原次
receipt 时只保留 UNVERIFIED；真实队列由同版 bridge 的实际结构识别。

这个兼容更新不宣告其他未完成的 reask 生命周期方案已上线。安装须保存原
active episode、旧控制器与未决证据，不把默认 idle hook 全局改成无限续轮。

## helper 只保留一个受保护发送入口

普通消息的 helper 必须由 `scripts/render_cmux_agent.py` 从固定基线渲染。
只有绝对可执行路径、完整渲染字节、同一 release 的 adapter 与相同 Python
全部通过守卫，直接 `ask/send/broadcast/reconcile` 才准入；可加 `rtk` 或
`rtk proxy` 前缀。shell/env 包装、嵌套、重定向、裸 cmux 和替代发送器拒绝。
helper 仍调用唯一 guarded bridge；正式 task/callback 保留专用入口。
reconcile 须绑定原 intent，零输入；缺证明只保留 pending，不报收到。

## 一致版本与证据边界

发送器、journal reader、PostToolUse、Stop、idle、启动器和技能适配层
必须来自相容的完整固定版本。实际 sender 与原 receipt validator 核当前
投递及其原 attempt；未确认不能报成功，普通工具不扫旧账。
`cmux_native_delivery_guard.py` 的全局注册同样经过 advisory 观察器，旧 checker
仅供显式诊断；这不降低 sender 的端点、草稿、去重及原生证据要求。
旧屏幕/send-proof hook 的退役由受支持安装器限定本包注册，保留其他任务及 foreign 配置。

配置落盘、完整版本安装、新进程导入、真实 hook 执行、当前客户端加载、
实际 native user 入站、Stop/CCC/Goal 等待及业务验收分别留证。真实验收
必须覆盖 Claude → supervisor；反方向成功不能替代。新任务解析当前
skill 的真实完整 release，在途原次仍保留原 controller。
离线测试不替代现场；一次成功也不能保证未来所有 UI 传输绝无失败。
能保证的合同是：未知不报成功、保留原次、恢复有界、证据不被重写。

## 新粘贴必须为单行（0.4.4）

普通消息及握手全文含任意 CR/LF/tab 或超过700 UTF-8字节时，保存完整只读正文，
再发单行 MESSAGE_REFERENCE_V2；保留全部字节和SHA。新任务使用专用
TASK_PACK_V2 单行通知并绑定完整任务包SHA；callback保持专用原文，不改成普通引用。
公共新粘贴门禁同时覆盖三条路径。旧V1四行通知仅沿原controller核收，不能重贴
或迁移原nonce。短通知原生收到只证明通知；完整正文另核读入，业务另行接受。

完整实机验收分别记录：原fence后完整 native user、真实callback原次及回执、
活跃客户端自动hook的实际调用。手动hook测试、配置存在和离线套件不能代替自动执行。
维护通过后接回用户原任务，沿原主管核未完边界，不擅自加派新研究或旧章节。
