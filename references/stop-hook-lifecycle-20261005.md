# Stop Hook 重入与回调状态

适用于所有 cmux Claude Code 的共享协作 Hook，不依赖 surface 白名单。

Stop/SubagentStop 输入的 `stop_hook_active` **严格等于布尔 true** 时，必须在读取任务门禁前成功退出（exit 0），防止 Hook 自己触发无限续轮。不得通过提高 `CLAUDE_CODE_STOP_HOOK_BLOCK_CAP` 掩盖循环。

首次 Stop（false 或缺 flag）仍校验原任务、回调和报告哈希；字符串或数字不能冒充 true，其他事件无此豁免。下一正常 turn 仍检查原证据。

允许结束不等于任务完成、回调送达或共识通过。不补造 receipt、不删除 active marker、不改原 task pack。回调不确定时保留原 journal，只核原次，禁止重新粘贴；已经真实确定未提交时，沿既有受保护的原次恢复流程处理。

prompt 和 callback 两方向均在 Enter 后检查接收端真实消费；paste/Enter ACK、队列和本地 DONE 不等于消费。执行者持续失败时，按用户已有授权由 supervisor 接管，明确 solo_self_review，不因握手或回调故障中止整体工作。

回归必须通过真实 stdin/子进程/退出码覆盖首次拒绝、Stop 和 SubagentStop 重入、类型边界、下一轮重检、证据不变以及有效回调/报告漂移。共享脚本每次新调用读取安装字节；旧 release 和任务 pins 保留，不宣称旧客户端配置已热加载。

## finalized ≠ 已投递（2026-10-08）

`draft:false` 只说明 task pack 定稿，不说明任务已送达执行者或业务已执行。缺 completion receipt 时，Stop guard 只读核对 supervisor 的 `task-dispatch-v1/<sha256([supervisor_uuid, task_id])>` 原 journal：仅当 journal 存在、每个 attempt 都是绑定本 pack SHA/本执行者/本任务的 `NO_INPUT` 且无事件、无 receipt、发送锁未被持有时，才判「未投递、尚不欠回调」并放行。缺 journal、空 journal、PREPARED、已粘贴/Enter/排队/未知、已确认投递、发送中、绑定不符、pack 变更一律仍要回执，并在拒绝信息里写出状态名。执行者侧已有 callback attempts 目录（业务已执行并报告）时永不经派发侧放行。放行不许可任何证据性声明，诚实 REPORT_READY 交接与 `stop_hook_active` 重入规则不变。


### NO_INPUT 的完整证据与保守兼容

原派单未发送不能覆盖执行方已产生报告这一事实。报告、callback attempts
或 receipt 路径只要存在（含断开的符号链接），均不能经 NO_INPUT 放行。
每次原 attempt 必须同时绑定任务包 SHA、task、workspace、supervisor、executor
与目标 pane。缺字段不推定。读取方持原共享锁并核对文件身份及读取前后字节；
派单方将 delivery.lock 的 device/inode 写入原 attempt，并在输入前重核。
没有该锁身份的旧 NO_INPUT 记录保持 UNKNOWN，不补造字段、不删除旧锁来重试。

## 辅助 Stop evaluator 与主会话可用性

界面的 Stop 错误可能来自内置目标检查的 prompt evaluator，也可能来自本地
command hook。先按实际 handler 和源码核配置来源，不能只看 settings 中的
command 列表，也不能将辅助模型报错当成主工作模型失败。修复已确认的辅助
模型映射时保留主会话、主模型和目标；配置写入、原会话热加载、真实请求成功
分别记录。限流或服务错误仍按其真实状态报告，不因改了映射宣布全部恢复。

主执行者的连续 300 秒失败门槛、门槛后的新鲜失败和成功重置规则保持不变。
已核收、裁定并 disarm 的任务结束其回调探针；后续维护给单独明确范围，
不要让目标检查把“等主管派单”重新扩大成后台轮询。
