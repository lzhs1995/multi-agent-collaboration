# Herdr 多 Agent 显式空间命名与极简侧栏规范 / Explicit Spatial Naming

Herdr 原生支持 `workspace rename`、`tab rename`、`pane rename` 与 `agent rename` 显示元数据。本规范固定**四层显式空间命名体系**，并收敛 `Herdr` 内侧栏与外层 `cmux` 侧栏噪声。

## 1. 四层命名语法（显示导航 ≠ 安全身份）

| 层级 | 命令入口 | 语法标准 | 示例 | 用途与边界 |
|---|---|---|---|---|
| **Workspace 短项目名** | `herdr workspace rename <ws_id> <label>` | `<项目短名>`（≤12 字） | `网关总控`、`MAC协作`、`Stata插件` | 替换默认抽象的母目录名 `cnm` |
| **Tab 分区名** | `herdr tab rename <tab_id> <label>` | `T<序号>-<分区短名>`（≤16 字） | `T1-主控双审`、`T2-升级规划` | 消除无意义的默认数字 `1`、`2`、`6` |
| **Pane 空间角色标签** | `herdr pane rename <pane_id> <label>` | `[T<序号>·<方位>\|<pID>] <角色>-<CLI> · <职责>`（≤36 字） | `[T2·LU\|pB] SUP-CX(S40) · MAC维护`<br/>`[T2·RD\|pE] ARC-AGY · 总规划` | 在 Herdr 面板边框第一顺位展示，不受子进程动态标题覆盖 |
| **Agent 短代号** | `herdr agent rename <pane_id> <code_name>` | `t<序号>-<role>-<cli><编号>`（严格全小写 `^[a-z][a-z0-9_-]{0,31}$`） | `t2-sup-s40`、`t2-rev-cc1`、`t2-arc-agy` | 供用户或主管直接调用 `herdr agent prompt/wait <code_name>` |
| **底层安全身份锚** | 只读核验（任务记录固定） | `socket + pane_id + terminal_id + native_session_id` | `/.../herdr.sock` + `w1:pB` + `term_...` + `01a1d601-...` | **唯一安全鉴权依据**；显示标签永不充当会话身份或写入授权 |

- **空间方位码（`<方位>`）**：由 `scripts/herdr_roster.py` 读取 `herdr pane layout --pane <id>` 的真实矩形 `(x, y, width, height)` 自动计算（单面板 `FULL`；双面板 `L/R` 或 `U/D`；四宫格 `LU/LD/RU/RD`；多列网格 `L/C/R` × `U/M/D`）。
- **角色与工具简码**：角色 `SUP`（主管）/ `REV`（审阅）/ `EXE`（执行）/ `ARC`（规划）/ `SH`（终端）；客户端 `CC`（Claude Code）/ `CX`（Codex）/ `GK`（Grok）/ `AGY`（Antigravity），支持 `mg cc`、`mg cx`、`agy` 等自定义启动器自动识别。

## 2. Herdr 内部左侧栏默认隐藏（释放 26 列窗口宽度）

在 `~/.config/herdr/config.toml` 中设置：
```toml
[keys]
toggle_sidebar = "prefix+b"

[ui]
sidebar_start_collapsed = true
sidebar_collapsed_mode = "hidden"
status_indicators = "symbols"

[ui.sidebar.agents]
row_gap = 0
rows = [["state_icon", "tab", "pane"], ["agent"]]
```
- 新会话启动即零宽度隐藏内侧栏；已打开会话随时按 **`Ctrl+B` 再按 `b`**（或点左下角 `«`）一键收起/展开，且展开时不再重复刷屏 `cnm`。

## 3. 外层 `cmux` 侧栏极简降噪规范（严禁显示任务回复正文）

1. **`~/.config/cmux/cmux.json` 禁用侧栏正文刷屏**：设置 `"sidebar": { "showNotificationMessage": false, "showLog": false, "showProgress": false }`，禁止将回合结束的“任务结果具体内容回复”渲染到侧栏卡片。
2. **单一层级「按 Tab 聚合各工具状态」**（`scripts/cmux_herdr_status_sync.py`）：
   - 顶部 `description`：`Herdr · 2T/9P · ⚠️1 🔨2 ⏸6`
   - 每个 Tab 固定对应 **1 行胶囊**（稳定按 `T1, T2, T3` 排列），只显示该 Tab 内各工具状态与方位（如 `T1 网关监控: 🔨CC(L) 🔨GK(RU) ⏸CX(LD) ⏸SH`；超过 4 面板时自动聚合空闲项如 `T1 插件研发: 🔨CC(CU) · ⏸6(CX×3,CC,SH×2)`），避免 Tab 与 Agent 混排及长文本截断。
