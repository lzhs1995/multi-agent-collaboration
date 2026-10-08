# 执行者交付后收口：运行约束与边界

本次实测的低效不是单一握手故障：报告已回传后仍追加记忆和自测；
另一执行者收到收口指令后仍扩展探针。仅增加“必须回调”的文字规则不够，
Stop 强制回执而缺少终态出口会把通信排障变成新一轮工作。

## 运行中的两个配套守卫

- `cmux_executor_closeout_guard.py`（PreToolUse）读取现有 callback journal。
  同任务、同执行者、同 workspace、同报告和任务包 SHA 全部对应，且原
  attempt 有真实返回记录、原持久锁可非阻塞取得时，阻止后续工具调用。
  包括追加测试、记忆、轮询器和重复回调。主管与其他任务的执行者不受该任务封口。
- `cmux_consensus_stop_guard.py` 允许该终态报告用下面的**精确诚实模板**
  结束 turn。这里不生成 confirmed receipt、不 disarm、不授予共识或论文通过。
  在途锁、未经限定的结束后事件、报告/任务包漂移或错误身份均不接受：

```text
STATUS: REPORT_READY TASK_ID=<原task_id> CALLBACK_UNCONFIRMED REPORT=<原报告绝对路径> supervisor_reconciliation_required
```

两守卫共用 `executor_closeout.py`，只读原记录，不创建新的发送协议。
原次 NO_INPUT 返回也可交主管处理，不强迫 executor 消耗第二次重试。
原 journal/锁缺失的旧任务不补造记录；沿原历史证据和已有 SOLO 授权安全收尾。
已有真实回执的普通结束仍按原 Stop 判据检查。模糊 pending 或伪称送达不能替代模板。

### 原次 Enter 留在输入框时的一次排队接续

原 journal 已记一次粘贴和 Enter，完整原消息仍在接收端输入框、界面要求 Tab
排队时，执行者封口不能把原控制器唯一的 queue-only 恢复入口也永久封死。
`cmux_callback_queue_resume.py --task-pack <原绝对路径>` 仅加载原 task pack
固定的控制器并调用 `resume_queue_only=True`，不粘贴、不 Enter、不迁移 journal。
PreToolUse 仅放行该脚本的精确同步 `rtk proxy` 命令；环境前缀、shell 尾部、
其他路径、后台执行、有回执或已经 Tab 的原次均不放行。真实身份、持久锁、
完整消息和界面是否可排队仍由原控制器在每次输入前核查。

旧控制器若在 queue-only 返回不确定后未更新 `ended_at_epoch`，只接受原前缀
事件仍在旧结束时间内、尾部仅有一次 `QUEUE_TAB_INTENT` 及可选的
`POST_QUEUE_TAB_OBSERVATION`。原锁必须已空闲。该状态只允许诚实交接并再次
封口，不证明送达，也不许可第二次 Tab；原生入站后才由主管核收。

主管在原共享目录直接读固定报告，独立判断可用结论和未决，沿原回执入口核收
原尝试。下一任务前先安全结案/disarm；不能为绕过封口重写报告或另造 marker。
如果工作尚未完成，不要提前发完成回调；需要补充工作，由主管给明确的新范围。

封口只作用于仍 armed 的当前任务，不证明 API 失败，也不永久禁止后续授权工作。
核收及 disarm 后，主管沿原 bridge 同步结论、下一动作/依赖及负责人；通知待核
不改变已经 confirmed 的原回调。新用户询问时以实际新证据回答，不重复旧 recap、
不让用户替已有主管通道转话。具体交接见
[核收后反馈规则](efficiency-and-closeout.md#核收后把结论和下一步交回执行者)。
本次 hook 只补充收口指引；精确交接模板、返回码、证据门槛及 disarm 权限不变。

## 握手和派单不再拖住主线

使用原健康会话。首次挑战只含身份、固定 skill 和 pending receipt 路径；
ACK 到即继续，不把 600 秒默认值当作必须等待时长。
握手守卫尊重原 receipt 的有限正数预算；600 秒是默认值而非握手硬上限。
业务 review-round 的 600 秒新鲜度限制保持独立。已 confirmed 的回执也必须
核完整任务包 SHA，确认后任务包漂移或旧回执缺 hash 均不能冒称核收。
握手、业务复核、交付核收三者不相互追加轮次。prompt 与 callback 均复用现有受保护 bridge：
Enter 后核原完整消息；compose/queued/消费分列，unknown 不重贴。
已经收到/排队的收口消息不能因为检测器未确认就再发一份。

每个业务任务在派单时给出有界交付：要核的具体问题、必要验证、停止条件、
输出目录。完成必要验证就写报告并回调，不以持续活跃代替进展。两 executor
仅分担互不依赖的待办；共享文件一人整合。无独立待办可待命。
provider 持续失败时沿既有有限重试和 SOLO 授权，确认无并发写后接管。
不要给故障会话叠单，也不要让健康会话等待另一位恢复。

## 验证与发布

测试通过真实 stdin 和子进程退出码覆盖封口、诚实 Stop、未知不能变 confirmed、
在途锁、时间/身份/SHA 漂移和主管豁免。新增源码、CI、配置安装、运行中客户端
加载、现场停止及研究结果分别报告；配置写入不是热加载证明。
旧固定任务包及失败记录保持原字节。升级只替换匹配的 hook，保留其他设置；
正在使用的身份修复必须先比对，不能用旧 checkout 覆盖新运行入口。
