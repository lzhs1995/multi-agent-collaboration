# 四种合作模式、五大典型场景与实战边界 / Collaboration Modes & Scenarios

`multi-agent-collaboration` 是模型无关、主次可换的通用多 Agent 协作协议底座：`Codex (CX)`、`Claude Code (CC)`、`Antigravity (AGY)`、`Grok (GK)` 均可按当前任务特征或配额担任主控（Supervisor）或协作者（Executor/Reviewer）。

---

## 1. 四种合作模式（派单头通过 `MODE:<mode>` 声明，默认 `hub`）

| 模式代号 | 名称 | 核心机制与适用场景 | 角色与收敛规则 |
|---|---|---|---|
| **`hub`** | **主持派发**（默认） | 1 个主控统一拆解任务、派发独立子包、自跑测试验收并向用户汇总（适合常规工程开发、单章修订）。 | 协作者只向主控交结果与文件增量，不改无关模块。 |
| **`blind-peer`** | **独立盲出 · 对等互评** | 重大架构、研究设计或疑难决策时，主控将**用户原话 + 客观背景**（**不附带主控自身草稿或倾向，防锚定偏见**）发给 1–2 个异构客户端独立出方案，随后交叉审阅。 | 主控对照代码与实证数据合并为三栏：**一致采纳项 / 单方择优项 / 分歧待用户拍板项**。主管异常离线时可按已登记边界退化为对等推进（`CLAUDE_PEER`）。 |
| **`spec-module`** | **接口定死 · 隔离分包** | 主控预先定死输入输出接口与验收标准，将成块独立模块（后端、算法、大表重算）派给协作者在独立目录或 `git worktree` 中完成。 | **粒度门槛**：`<50 行` 或 5 分钟内可顺手改完、或需紧耦合反复联调的小改动，主控直接完成，避免派单与验收沟通成本倒挂。 |
| **`pipeline`** | **单向闭环流水线** | 适合博士论文与严谨报告：`主笔初稿 → 文风/逻辑审阅 → 实证/代码核验 → 主控合入`。 | 审查意见分两级：**`BLOCKER`**（算错数、丢数据、越权、破坏回归，须含具体触发路径）与 **`ADVISORY`**（建议项，记录不卡合入）；**最多 R1–R3 三轮封顶**，超限未决原样交用户拍板。 |

---

## 2. 五大典型场景端到端工作流（定位 → 命名 → 握手 → 交互 → 反馈 → 排队）

| 协作场景 | 1. 定位 | 2. 显式命名 | 3. 握手 | 4. 交互通道 | 5. 反馈与 60s 求派边界 | 6. 忙碌排队与压缩期注意事项 |
|---|---|---|---|---|---|---|
| **场景 A：向内协作**<br/>（同 Herdr 内主↔子/对等） | 核 `$HERDR_PANE_ID` + `herdr pane/agent list`（`unknown` 补查 `process-info`） | `herdr-roster` 标齐 `[T1·LU\|p1] SUP-CX1` 与短码 `t1-sup-cx1` | 首包头带 `[TASK:<id> FROM:<me> MODE:hub]` 即建立绑定，免额外空跑握手 | 直接 `herdr agent prompt <code\|pane_id> "..."`（短消息直发，长文传路径） | 完成后反向 `herdr agent prompt <sup_code> "[DONE:<id>] 摘要+路径"`。**凡用户启用 `/loop` 或指令含「持续推进/反复追问/做完继续要活」，执行者保持 60s 主动求派直至总任务结束**；单次点对点短问答回报一次即可 | `idle/done` 立即处理；普通 `working` 视客户端入队；**注意：向正在 `compacting`（压缩上下文）的 Codex 发送 `herdr agent prompt` 可能返回 `timeout` 而消息在约 170s 压缩完成后才落盘，遇 `timeout` 必须先 `agent read`/查转录核实，严禁盲重发**；`blocked`（弹窗）停发告警 |
| **场景 B：向外跨 Herdr**<br/>（cmux Workspace A 的 Herdr 主控 ↔ Workspace B 的 Herdr 主控） | `herdr-roster map --session <B>` 锁定目标 Herdr 的主控 `pane_id` 与 `Native SID` | 外层 cmux Workspace 名 + 内层 `T1` 主控短码双重锚定 | 首包携带跨章契约路径与 `Native SID` 锚点 | `herdr --session <B> agent prompt <lead_code> "..."` + 结构化 `chapter-delta.json`（由 `chapter_delta_validator.py` 校验） | 章节主控达成里程碑或阻塞时主动汇总上报总主控；`/loop` 长任务同样适用 60s 进度/求派约束 | 同场景 A；**严禁向承载 Herdr 的外层 cmux surface 裸发按键** |
| **场景 C：纯 cmux 同工作区**<br/>（不开 Herdr，同 Workspace 不同 Surface） | `cmux list-panels --id-format both` / `cmux-agent self` 锁 `surface_uuid` | `cmux rename-tab` 标注 `[S1] SUP-CX`、`[S2] REV-CC` | 首包绑定对端 `surface_uuid + native_session_id` | **新任务统一走当前快照 `skills/cmux-collaboration/SKILL.md` 及同版 `scripts/`（含 `official_codex` 支持 `codex-aarch64-apple-darwin`）**；仅已绑定 `.79` 的历史旧任务保留 `0.4.79` | 回复带 `DONE:` / `BLOCKED:` marker；`/loop` 或显式 `--continuing` 任务按 60s 间隔续询 | 共享输入区锁，允许工作期单次受保护入队，拒绝盲敲 `Enter` |
| **场景 D：纯 cmux 跨工作区**<br/>（不开 Herdr，Workspace A Surface ↔ Workspace B Surface） | 锁定双端 `workspace_uuid + surface_uuid + native_session_id` | Workspace 标题 + Surface 标签一一对应 | 跨工作区首包固定任务授权与文件路径 | 同场景 C（必须使用含 `official_codex` 的新版快照，避免 `.79` 对 `codex-aarch64-apple-darwin` 报 `NATIVE_TARGET_AGENT_ABSENT`） | 里程碑回报，保留原次去重账本 | 同场景 C |
| **场景 E：混合/跨机/降级**<br/>（Herdr ↔ 纯 cmux / Browser / Windows `xdev` / 远端 SSH） | 混合端按接收方真实容器选通道；跨机用 `herdr --machine <id>` 或 `xdev` | 标注 `[WIN-xdev]`、`[SSH-<host>]`、`[BROWSER]` | 按目标通道核验端点身份 | Herdr 接收走 `--session <name> agent prompt`；Windows 走 `xdev` SHA 传输；Word/Zotero 走 `resource_broker` 串行锁 | 主控连续两周期 API 故障时，按 `CODEX_SOLO` / `CLAUDE_PEER` 降级推进不死等 | 远端长任务走后台作业回执，不阻塞本地主控 |

---

## 3. 五条实战防乱准则

1. **派活有门槛（反形式主义拆单）**：5 分钟内或 `<50 行` 能顺手改完、或与当前上下文强耦合的小活，主控自己做完并验证；只把独立成块模块、异构盲审或耗时核验派给同伴。
2. **交接可溯源（带指纹的信）**：实质产物交接写明文件路径与版本/SHA256，接手方直接读文件；短问答直接走 `herdr agent prompt`，不强制写报告文件。
3. **压缩期与超时先读回（严禁盲重发）**：`herdr agent prompt` 返回 `timeout` 或 `UNKNOWN` 时（尤其对端 Codex 正在 `compacting` 时），必须先用 `herdr agent read` 或转录尾部核实原消息是否已入队/落盘，绝不直接重发第二遍。
4. **审查分两级、最多三轮**：只让附带具体触发路径的 `BLOCKER` 阻断流转，R1 全审、R2/R3 只复核改动点。
5. **错题变脚本、规则删废话**：凡被纠正过的错误，优先固化进脚本或单测（如 `herdr_roster.py`、`chapter_delta_validator.py`）；已被脚本守住的规则从全局指令文件中删去历史事故长文，只留命令入口与实测边界。
