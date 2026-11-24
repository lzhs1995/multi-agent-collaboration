# Herdr 多 Agent 显式空间命名与 cmux 侧边栏同步规范 / Explicit Spatial Naming

Herdr 原生支持 `tab rename`、`pane rename` 与 `agent rename` 三层显示元数据，但不会自动为多面板团队生成可读角色与方位标签。当同一 Herdr 会话包含多个 Tab 或 6 个以上面板时，依赖动态终端标题（如 `Implement the plan | cnm`、`Claude Code`）会导致用户与主管无法快速定位目标面板。

本规范固定 Herdr 团队的**四层显式命名体系**，并与安全身份校验严格分离。

## 1. 四层命名语法（显示导航 ≠ 安全身份）

| 层级 | 命令入口 | 语法标准 | 示例 | 用途与边界 |
|---|---|---|---|---|
| **Tab 分区名** | `herdr tab rename <tab_id> <label>` | `T<序号>-<分区短名>`（≤16 字） | `T1-主控双审`、`T2-升级规划` | 消除无意义的默认数字 `1`、`2`、`6` |
| **Pane 空间角色标签** | `herdr pane rename <pane_id> <label>` | `[T<序号>·<方位>\|<pID>] <角色>-<CLI> · <职责>`（≤36 字） | `[T2·LU\|pB] SUP-CX(S40) · MAC维护`<br/>`[T2·RD\|pE] ARC-AGY · 总规划` | 在 Herdr 面板边框与 cmux status pill 第一顺位展示，不受子进程动态标题覆盖 |
| **Agent 短代号** | `herdr agent rename <pane_id> <code_name>` | `t<序号>-<role>-<cli><编号>`（严格全小写 `^[a-z][a-z0-9_-]{0,31}$`） | `t2-sup-s40`、`t2-rev-cc1`、`t2-arc-agy` | 供用户或主管直接调用 `herdr agent prompt/wait <code_name>` |
| **底层安全身份锚** | 只读核验（任务记录固定） | `socket + pane_id + terminal_id + native_session_id` | `/.../herdr.sock` + `w1:pB` + `term_...` + `01a1d601-...` | **唯一安全鉴权依据**；显示标签永不充当会话身份或写入授权 |

### 空间方位码（`<方位>`）几何映射规则

由 `scripts/herdr_roster.py` 读取 `herdr pane layout --pane <id>` 的真实矩形 `(x, y, width, height)` 自动计算，不依赖焦点切换：
- **单面板**：`FULL`
- **双面板**：左右 `L` / `R`；上下 `U` / `D`
- **双列四宫格**：`LU`（左上）、`LD`（左下）、`RU`（右上）、`RD`（右下）
- **三列及多行网格（如 5–10 面板）**：列前缀 `L`（左）/ `C`（中）/ `R`（右）组合行后缀 `U`（上）/ `M`（中）/ `D`（下），如 `CU`、`CM`、`CD`、`RM`；超过 3×3 自动回退为 `C1R1` 矩阵坐标。

### 角色与 CLI 简码约定
- **角色码**：`SUP`（主管 Supervisor）、`REV`（审阅 Reviewer）、`EXE`（执行 Executor）、`ARC`（架构/规划）、`SH`（待命/辅助 Shell）。
- **CLI 码**：`CX`（Codex）、`CC`（Claude Code）、`AGY`（Antigravity CLI）、`GK`（Grok）。

## 2. 标准工具用法（零终端输入）

```bash
# 只读生成单会话或全部活跃 Herdr 会话的空间拓扑表
python3 -B scripts/herdr_roster.py map --session c4-multiagent
python3 -B scripts/herdr_roster.py map --all

# 预演并应用经 terminal_id / native_session_id 校验的显式命名清单
python3 -B scripts/herdr_roster.py apply --manifest /absolute/roster.json --dry-run
python3 -B scripts/herdr_roster.py apply --manifest /absolute/roster.json
```

`herdr_roster.py` 只调用只读查询和 UI 元数据重命名命令（`tab/pane/agent rename`），不发送任何按键、文本或生命周期终止信号；若面板 `terminal_id` 或 `native_session_id` 与清单不一致，立即拒绝（Fail-closed）。

## 3. 多 Tab / 6+ 面板下的 cmux 侧边栏自适应同步规则

配套守护程序 `scripts/cmux_herdr_status_sync.py`（部署于 `~/.local/share/herdr-agent-launchers/cmux-herdr-status.py`）解决社区 `cmux-herdr` 在多面板下的三项缺陷：
1. **补齐 `unknown` 面板漏检**：结合 `pane list` 与 `pane process-info` 识别二进制别名（如 `codex-aarch64-apple-darwin`）及显式命名的辅助面板，不再因 `agent list` 缺项而隐身。
2. **>6 面板分级折叠（防侧边栏刷屏截断）**：
   - 总面板数 `<= 6`：逐面板展示独立状态药丸。
   - 总面板数 `> 6`：处于 `blocked`（⚠️ 90）、`working`（🔨 80）、`done`（✅ 60）的面板逐条置顶单列显示精确方位与角色名；同一 Tab 内的 `idle` / `shell` 面板自动折叠为 1 条汇总药丸（`T1-主控(⏸3): ...`），并清理过期的旧单面板药丸。
3. **全会话紧凑副标题（含 `default`）**：Workspace `description` 统一输出如 `herdr [2T/9P]: 🔨2(REV-CC1, ARC-AGY) · ⏸7`，避免无名 `codex idle · codex idle` 溢出截断。
