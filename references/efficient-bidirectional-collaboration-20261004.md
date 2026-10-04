# 高效握手、多执行者与双向投递

维护版本 efficient-bidirectional.2026.10.04.4。本文是本次维护后的有效入口；同日其他握手/Enter文档保留为历史，不再作为并列操作说明。

## 先备任务，再握手

先备好任务目标、精确输入、允许写入目录、报告路径、完成条件和验证方法，再对当前同workspace的原会话做真实身份核验、握手与finalize。健康ACK不重复获取；迟到ACK先核原task/nonce。分别记录准备、握手、投递、执行、回调、核收耗时。握手上限不是强制等待，不因设置600秒就等满600秒；观察预算过短不能归因执行者，不强制使用实测时间的三倍。

## 以任务收益决定一个或两个执行者

复用用户指定且携带上下文的会话。第二执行者仅接独立工作，例如数据文件核查与协作工具审查；各有task_id、nonce、输出根，互不改对方结果。只有supervisor整合最终材料和维护公共脚本。两个会话占同一pane的不同tab时如实记拓扑，不称两个额外side split。共享Stata/Office/网盘仍服从原资源队列，executor数量不等于资源并发数。

只有需要计划共识的工作才走既有共识流程；普通已授权的一次审阅不因此增加科研审轮。执行者仅在任务包授权目录写入；supervisor的安装权限另列且须来自用户，不能反向改写已经冻结的任务包。

明确retryable服务故障结束后，同会话最多三次外层重试、每次至少60秒；认证、欠费、额度失败不盲试。投递不确定也不能重贴。持续失效时沿用户既有授权固定原终态、冻结executor写入并转solo_self_review，继续本机能做的工作。保留原会话，不clear、不新建替代。恢复协作在安全边界使用原UUID。

## 双向发送铁律：Enter后必须读回

supervisor→executor的prompt和executor→supervisor的callback都通过受保护bridge。每次粘贴/按键前重核caller、同workspace、指定目标UUID；首次输入前SHELL/UNKNOWN零输入；已有paste_intent后若识别失败，记为投递未确认，停止按键并核收原次，不能改判为从未发送。按键返回0、文字出现在旧转录块、marker消失或无关新工具输出均不能证明本次消息已消费。

当前bridge粘贴一次并按Enter，随后必须读同一接收端；发送命令成功本身不作送达证据。确认需要：本次新marker关联的新活动、输入框空、marker不在compose或pending queue。排队、已消费、报告已审阅分别记录。若完整待提交文字仍逐字匹配自己的payload，且没有用户新增、排队、压缩或重连，bridge至多补一次Enter，不重新粘贴。

实测Codex忙时可显示`tab to queue message`：仅精确提示、Codex字形、完整自身payload均匹配且没有压缩/重连/队列时，允许一次Tab，之后再读回；进入队列仍不算消费。该路径目前有离线测试，不能称全部真实UI版本均已验证。未知多行输入按用户草稿保护，不通过force清除。

## 回调日志与旧任务收尾

先保存固定报告，再调用`submit-completion-callback --task-pack /absolute/task-pack.json`。实际尝试目录与任务包的`completion_receipt`同目录，名称为回执stem加`-attempts/`。新版journal绑定任务包SHA、报告SHA、nonce、原executor UUID与目标，并先写PASTE_INTENT后输入；同inode锁避免两个进程重复回调。记录为零输入的失败最多另试一次；任何可能已粘贴的尝试禁止重贴。

`--reconcile-only`只读取已有真实尝试和接收端，终端输入为0；可以持久化本次观察，且仅在真实消费证据满足原绑定时保存回执。不另发恢复消息。若旧`<receipt>.pending.json`存在，即使内容损坏也拒绝新发送和自动迁移，交supervisor按原证据结案。缺journal不能补造历史尝试，报告SHA出现在supervisor文件只能证明该报告被核查，不能自动生成transport receipt。滚屏丢失证据时如实保留未确认。

旧任务若真实callback已经进入supervisor会话且报告已独立核收，可由supervisor保存原marker及核收依据后，仅`disarm --task-id`该已终态任务。明确记录正式bridge receipt缺失；不得伪造receipt、反复回调、全局禁用Stop hook或让已完成executor无限修复回调。Stop hook提供完成门禁，supervisor负责真实旧任务的有据结案。

## 安装、复测与版本

维护源与实际安装两边都保留本文及对应入口。实体目录安装若被manage_install.py判为foreign，保留目录；按已授权窄维护调用现有mutation_locks及replace_bytes，先核原字节，备份后安装，保留mode与before/after SHA。不得将实体目录强换symlink或整树覆盖。回调journal先安装、bridge后安装。

安装完成、测试通过、新进程实际导入和旧客户端热加载是四件事；不为更新skill重启正在工作的应用。已冻结任务包保留旧pins，后继显式记录维护版本，不冒称旧输入未变。通用文档不含研究数据；本机维护patch另归档，无Git元数据不得声称已提交或发布。
