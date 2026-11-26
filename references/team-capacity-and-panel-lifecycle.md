# 按任务扩缩与面板生命周期 / Team capacity and panel lifecycle

Herdr 向内组织同一团队的分工、核收与依赖；cmux 向外组织独立终端团队和跨工作区主管协作。两者沿用[共享契约](collaboration-contract-core.md)及[嵌套研究协作](nested-research-coordination.md)，不建立第二套收件、授权或资源规则。

## 按需要分配容量

先列出可独立交付的任务、唯一写者、当前依赖和验收人，再决定是否增加执行者。优先复用身份、上下文和原任务边界已核实的会话；原任务尚有写入、回收或未知投递时，不把它改作无关任务。新增参与者必须有具体交付物、资源范围和回报对象，不能只为填满面板或等待同一独占资源而扩容。

Herdr 的团队主管管理内层执行者；跨团队增减和资源冲突由 cmux 外层主管协调。复用现有适配器或宿主提供的生命周期能力，先读当前安装 CLI 的帮助核实命令。环境变量、面板标题或外层 surface 不能替代原生 session、endpoint、pane/terminal 和进程身份。已有任务的固定源码、controller、原次预算与 UNKNOWN 不迁移。

若主 tab 已有用户指定的核心面板布局，保持该布局；新增独立子任务放到单独任务 tab，不挤占核心面板。关闭内层 pane 与关闭外层 cmux surface 是不同操作：外层容器仍有其他任务或服务时，只收回已确认结束的内层对象。

## 完成后归还空闲容量

交付已核收且本方职责完成后，主管应主动收回本任务拥有的空闲面板，不让任务终态后的 shell 持续占位。用户已授权本任务编排与收尾时，使用该授权，不另加逐面板确认循环。只处理本方已确认的目标；用户或其他团队的面板保持原归属。

一次有依据的收尾顺序：

1. 对照原任务记录确认交付、核收和依赖；检查是否仍有 live goal、运行/排队输入、子进程、远端请求、共享租约、资源锁或未完成写入。`idle`、`done`、静屏和空输入框本身都不够。未知业务操作先沿原次结算；历史投递未证且与当前收尾无关时保留其状态，不制造 ACK 或重发。
2. 保存可恢复记录：任务与 owner、原生 SID、服务 selector/endpoint、pane/terminal 或 workspace/surface UUID、源码/控制器绑定、交付及验收路径、录本路径、未决事项、最后观察时间和原会话恢复命令。面板关闭不删除录本、原 session 或产物，也不把历史送达补记成功。
3. 在同一目标锁下重新核身份与输入状态，用宿主支持的正常退出关闭自己的 agent；核实退出到 shell 和无剩余工作，再关闭该空闲 shell/pane。没有受支持的安全关闭或原结果仍未知时保留目标并报告具体原因。不能为了收缩而 force-kill、清输入、跨 pane 发退出键或重置会话。
4. 回读原目标是否消失、其他任务和容器是否仍在；记录实际结果及恢复位置。需要新工作时优先按记录恢复或复用适合的原会话，避免复制同一任务的第二控制器。

上述收尾属于主管的正常职责，不通过模型计时器、常驻轮询或新增 Stop hook 自动实施。持续求派遵循当前用户明确指令及共享契约；整体任务结束、明确无新职责时停止求派并进入收尾。

## 只报告实际测得的收益

分别记录新增/复用/正常关闭的对象数、任务墙钟时间、资源等待、有效交付和质量验收，以及可取得的 token/费用。前后比较需同等任务、模型与验收口径，并说明样本量和缺失项。面板少了只能证明界面与容量已回收；没有匹配计时或成本数据时，不声称速度提升、费用下降或模型吞吐增加。安装文件可读、当前客户端已加载规则、实际正常关闭和业务收益是四项独立证据。

## English operational summary

Use Herdr within a team and cmux between independent terminal teams. Allocate
agents to concrete parallel deliverables and reuse verified context-bearing
sessions before creating more. Keep existing controllers and unresolved attempts.
Once owned duties are accepted and no active work remains, save recovery bindings,
exit the exact owned agent normally, verify an idle shell, and close only that
owned pane. Recheck identity and input state under the existing target lock; keep
other teams, shared containers, transcripts and unresolved delivery evidence.
Claim measured closure and matched quality/time/cost results separately. A saved
skill or fewer panes alone does not prove live loading or a speedup.
