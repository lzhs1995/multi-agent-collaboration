# 五大技能/工具项目实战经验沉淀与 GitHub 优化总结（区分 Agent 公用经验 vs AGY CLI 专属经验）

- **沉淀日期**：`2026-11-01`
- **关联任务**：`CMAverse` 因果中介论文 `V3h -> V3i` 推进稿生成、跨软件统计锚点核验、OOXML 修订编译与 GitHub 发布
- **涵盖项目**：
  1. `clauder-rstudio-workbench`（含 `comparegroups-guide`、`cmaverse-paired-mval`）：`https://github.com/lzhs1995/clauder-rstudio-workbench`
  2. `stata-workbench-shared-session` / `stata-workbench-shared-session-skill`：`https://github.com/lzhs1995/stata-workbench-shared-session-skill`
  3. `officecli-word-revision`：`https://github.com/lzhs1995/officecli-word-revision`
  4. `thesis-refiner`：`https://github.com/lzhs1995/thesis-refiner`
  5. `multi-agent-collaboration`：`https://github.com/lzhs1995/multi-agent-collaboration`

---

## 第一部分：Agent 公用经验（Cross-Agent Shared Experience，适用于 Codex / Claude Code / AGY CLI）

### 1. `stata-workbench-shared-session-skill`：VSIX 内置工具路径同步与 R 导出 CSV 的 `"NA"` 强类型导入规范
- **经验 1A（工具脚本版本与 VSIX 扩展目录对齐）**：
  - **现象**：当 VS Code / Cursor 独立窗口升级至新版 VSIX（如 `lzhs1995.stata-workbench-shared-session-0.1.3-rc.7.45`），而 `~/.local/share/stata-workbench-shared-session/releases/` 下仍驻留旧版文档快照（如指向 `rc.7.39`）时，调用旧快照中的 `verified_workbench.py --doctor` 会因寻找旧版 `rc.7.39/dist/extension.js` 报错。
  - **公用规范**：VSIX 扩展安装目录 `<profile>/extensions/lzhs1995.stata-workbench-shared-session-<version>/tools/` 内自带与当前运行扩展同版本的 `verified_workbench.py` 与 `shared_stata.py`。所有 Agent 应优先解析 `<profile>/extensions/lzhs1995.stata-workbench-shared-session-*/tools/` 下最新版本的工具脚本，确保 `expected_extension_sha256` 与运行中扩展 100% 匹配。
- **经验 1B（R 导出含 `NA` 的统计汇总表在 Stata 中的 `destring` 规范）**：
  - **现象**：R `write.csv(..., row.names = FALSE)` 默认将缺失值写为字符串 `"NA"`（例如两自由度整体检验无单独置信区间半宽时）。Stata `import delimited` 会将该列自动识别为 `str` 类型，导致后续 `summarize` 返回 `Obs = 0`、`r(max) = .`。
  - **公用规范**：在 R $\rightarrow$ Stata 跨软件交叉核验脚本中，`import delimited` 后应显式对数值列执行 `destring <num_cols>, ignore("NA") replace`，随后再运行 `assert` 断言。

### 2. `clauder-rstudio-workbench` + `cmaverse-paired-mval`：“统计冻结期”论文推进的三大锚点提炼范式
- **经验 2A（窄区间零效应界 / Tightly Bounded Null 提炼法）**：
  - **现象**：当大规模因果中介探索（如 8 条主链 + 120 项搜索 + 新 14 项）在多重校正后未获显著间接效应时，若仅写“未发现显著中介”，极易被审稿人质疑为“统计检验力不足或研究失败”。
  - **公用规范**：在不新增任何主效应搜索或改写冻结 CSV 的前提下，应编写只读提取脚本（如 `extract_three_statistical_anchors_v3i.R`），计算各中介规格 95% 置信区间的**最大绝对边界（`max_abs_bound = max(|ci_low|, |ci_high|)`）**与**半宽（`ci_half_width`）**。例如将“新 14 项概率中介不显著”转化为“间接概率效应 95% CI 严格收紧在 `[-0.168, +0.319]` 个百分点以内（`C1` 仅 `[-0.051, +0.052]` 个百分点），高精度排除任何 $\ge 0.32$ 个百分点的短期传导效应”，将消极零结果转化为高信息量的“窄区间零效应界”。
- **经验 2B（复合相对指标的 $2 \times 2$ 转移构成解构）**：
  - **公用规范**：对于“本人相对配偶位置跃迁（如 `FEAS_031`）”等复合二元指标，必须固定报告四个互斥/重叠构成比例：（1）伴随本人提升比例（`28.35%`）；（2）伴随配偶下降比例（`88.01%`）；（3）双方反向变动重叠比例（`16.36%`）；（4）纯由配偶下降且本人持平/下降实现的换序比例（`71.65%`）与纯由本人提升实现的比例（`11.99%`），用 `assert` 验证 $28.35\% + 88.01\% - 16.36\% = 100.00\%$ 及 $71.65\% + 16.36\% + 11.99\% = 100.00\%$。

### 3. `officecli-word-revision`：零域扰动（Zero-Field-Perturbation）段落级 OOXML 修订编译
- **经验 3A（避开 `fld=True` / `fn=True` 段落的邻域增强策略）**：
  - **现象**：在含有大量 Zotero 动态引用域（`ADDIN ZOTERO_ITEM`）和脚注锚点（`w:footnoteReference`）的成熟文稿上做大范围学术润色时，直接拆分含域段落存在触发字段缓存或 `noteIndex` 序号重算的风险。
  - **公用规范**：先通过 XML 扫描标定每个段落的 `(fld, fn)` 属性。对于含 Zotero 域的文献综述句（如 `P014`），保持其原段不动，在其紧邻的前后纯文本段落（如 `P013`、`P015`，均为 `fld=False, fn=False`）实施理论对话深化；对所有修订段使用 `difflib.SequenceMatcher(..., autojunk=False)` 从右向左（`reversed(opcodes)`）执行 `splice`，并在编译后强制校验三项不变量：
    1. `normalized(resolve(tracked, True)) == normalized(accepted)`
    2. `normalized(resolve(tracked, False)) == normalized(baseline_accepted)`
    3. 全部 34 张表格的 `normalized(tbl)` 逐表 100% 恒等，且全部 `MANUSCRIPT_LOCATIONS.json` 裁决锚点（`W01–W08`）匹配数不减。

### 4. `thesis-refiner`：`workflow.py` 命令行参数顺序与段落定位器映射关系
- **经验 4A（全局参数前置规则）**：
  - `thesis-refiner/scripts/workflow.py` 使用根 `ArgumentParser` 定义 `--input` 与 `--output`，子命令（`inventory`、`audit-chain`、`acceptance`、`affected`）位于末尾。正确调用格式为：
    `python workflow.py --input <in.docx> --output <out.json> inventory`（不可将 `--input` 写在 `inventory` 之后）。
- **经验 4B（`word/document.xml:paragraph:N` 与顶层正文段落索引的映射）**：
  - `workflow.py inventory` 生成的 `word/document.xml:paragraph:N` 是按 `root.iter(Q('p'))`（包含表格单元格内的段落）的 1-based 序号编号（即 `document_p_index_0based + 1`），而非 `body.findall(Q('p'))` 的顶层段落序号。在做 `inventory` 差异断言时，应使用 `list(root.iter(Q('p'))).index(p_node) + 1` 进行精确对齐。

---

## 第二部分：AGY CLI 专属经验（Antigravity CLI Seatbelt Sandbox Specific Experience）

在 macOS 上运行 Google Antigravity CLI（`agy`）时，其子进程运行在 macOS Seatbelt 沙箱内。相比非沙箱终端或普通 IDE 终端，存在两项关键系统调用边界及对应的工程级绕行方案：

### 1. AGY CLI 下 `/bin/ps` 受限（`[Errno 1] Operation not permitted: '/bin/ps'`）及对 `stata-workbench-shared-session` 的影响与优化
- **故障机理**：
  - AGY CLI 的 macOS Seatbelt 策略禁止直接执行 `/bin/ps`。
  - `stata-workbench-shared-session` 的 `verified_workbench.py`（`ps_Inventory()`）和 `shared_stata.py` 在执行 `--doctor`、`--status` 或提交代码前，会调用 `subprocess.check_output(["/bin/ps", "-axww", "-o", "pid=,ppid=,command="])` 枚举 VS Code 窗口进程。在 AGY CLI 内这会抛出 `PermissionError: [Errno 1] Operation not permitted: '/bin/ps'`，导致脚本误报 `status: "BLOCKED"`，即便此时本地回环桥接 `http://127.0.0.1:17507/status` 完全健康在线（`ok: true, trueReady: true`）。
- **AGY CLI 适配与代码级优化方案**：
  1. **直接回环探针法**：在 AGY CLI 中诊断共享会话状态时，直接请求 `http://127.0.0.1:<port>/status`（JSON 响应中已自带 `extensionHostPid`、`ownedBackendPids`、`bundleIdentityState: "OK"`、`trueReady: true`）。
  2. **`verified_workbench.py` / `shared_stata.py` 沙箱降级补丁（已在本次更新至技能仓）**：当 `/bin/ps` 抛出 `PermissionError`（`errno == 1`）时，自动回退至读取 `http://127.0.0.1:<port>/status` 的 `extensionHostPid` 与 `ownedBackendPids`（并可通过 `os.kill(pid, 0)` 验证进程存活），避免因沙箱禁用 `/bin/ps` 而阻断正常的 HTTP 共享会话交互。

### 2. AGY CLI 下 `osascript` 发送 AppleEvents 至 Microsoft Word 受限（`permErr = -54`）及双轨 PDF 导出方案
- **故障机理**：
  - 在 AGY CLI 内调用 `/usr/bin/osascript -e 'tell application "Microsoft Word" ...'` 会返回 `execution error: 檔案取用權錯誤。 (-54)`（macOS AppleEvent / Mach 沙箱权限错误 `-54`）。
- **AGY CLI 适配与双轨执行方案**：
  1. **OOXML 修订与双端点语义校验 100% 纯 Python 离线闭环**：使用 `lxml.etree` + `word_semantic_contract.compare`（`DUAL_VIEW_SEMANTIC_V1`）在沙箱内直接完成 `tracked.docx`、`accepted.docx`、`rejected.docx` 的生成与双视图等价性数学证明，不依赖 Word GUI 进程。
  2. **PDF 渲染回退至 LibreOffice Headless（`/opt/homebrew/bin/soffice --headless --convert-to pdf`）+ Pandoc HTML**：在 AGY CLI 沙箱内，`/opt/homebrew/bin/soffice --headless --convert-to pdf` 与 `/Applications/RStudio.app/Contents/Resources/app/quarto/bin/tools/aarch64/pandoc` 均不受 `-54` 限制，可稳定生成 52 页 A4 PDF 与 MathML HTML 供 `pdfinfo`、`pdftotext` 及视觉审阅使用；同时保留预编译 `.applescript` / `.scpt` 脚本供非沙箱宿主机一键复现原生 Word 往返。
