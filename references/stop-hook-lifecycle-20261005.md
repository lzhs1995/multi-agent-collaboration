# Stop Hook 重入与回调状态

适用于所有 cmux Claude Code 的共享协作 Hook，不依赖 surface 白名单。

Stop/SubagentStop 输入的 `stop_hook_active` **严格等于布尔 true** 时，必须在读取任务门禁前成功退出（exit 0），防止 Hook 自己触发无限续轮。不得通过提高 `CLAUDE_CODE_STOP_HOOK_BLOCK_CAP` 掩盖循环。

首次 Stop（false 或缺 flag）仍校验原任务、回调和报告哈希；字符串或数字不能冒充 true，其他事件无此豁免。下一正常 turn 仍检查原证据。

允许结束不等于任务完成、回调送达或共识通过。不补造 receipt、不删除 active marker、不改原 task pack。回调不确定时保留原 journal，只核原次，禁止重新粘贴；已经真实确定未提交时，沿既有受保护的原次恢复流程处理。

prompt 和 callback 两方向均在 Enter 后检查接收端真实消费；paste/Enter ACK、队列和本地 DONE 不等于消费。执行者持续失败时，按用户已有授权由 supervisor 接管，明确 solo_self_review，不因握手或回调故障中止整体工作。

回归必须通过真实 stdin/子进程/退出码覆盖首次拒绝、Stop 和 SubagentStop 重入、类型边界、下一轮重检、证据不变以及有效回调/报告漂移。共享脚本每次新调用读取安装字节；旧 release 和任务 pins 保留，不宣称旧客户端配置已热加载。
