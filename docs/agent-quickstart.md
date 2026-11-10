# Agent quickstart / Agent 入門

Read this checkout's [SKILL.md](../SKILL.md) before acting. The [README](../README.md) describes the published version and [繁中介紹](../README.zh-TW.md) provides the same onboarding in Traditional Chinese.

1. Identify the user's task, original controller and existing context-bearing participants. A moved pane does not change ownership.
2. Inspect the environment and current transport. This `main` release provides macOS cmux integration; the Herdr lane is linked from the README.
3. Assign independent outputs and one writer for each shared file. Record the input version, allowed changes and acceptance criteria.
4. Use the documented delivery contract. Preserve drafts and reconcile uncertain original input; do not reset or replace a participant for convenience.
5. Review actual evidence, accept the bounded result, release owned resources and record unresolved work.

## Client entrypoints

| Client | Entry | Boundary |
|---|---|---|
| Codex | `AGENTS.md`, then `SKILL.md` | Installer support does not prove a running client reloaded hooks |
| Claude Code | `CLAUDE.md` imports `AGENTS.md`, then read `SKILL.md` | Native session and receipt still need verification |
| Gemini CLI / another agent | `GEMINI.md` or manually open `AGENTS.md` and `SKILL.md` | Readable instructions; packaged live transport is not certified for every client |

The aliases contain navigation only. They do not authorize sending, enrollment, application writes, model changes or hook installation. Follow the user's authorization and the pinned task contract.

任何 Agent 均可讀取契約；「讀得到」「安裝完成」「執行中的客戶端已載入」「真實任務已驗收」是不同層次。先核原會話與任務版本，按獨立產物分工，保留不明輸入的原紀錄，最後依實際證據裁定。不把新增入口檔當成全客戶端自動支援。
