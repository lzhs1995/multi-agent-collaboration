# Herdr 觀察與證據判讀：五條經驗
# Five principles for interpreting Herdr observations

範圍：本文保留五條已核收的方法原則；實例、原始紀錄及身分對照留在私有證據中。本文件只說明觀察與報告，不新增輸入、重送、重啟、換模型、清鎖或自動恢復權限，也不更換既有任務的控制器或嘗試。

Scope: These five principles preserve the accepted observation methods; cases, original records and identity mappings remain private. This document concerns observation and reporting. It grants no new permission to send input, resend, restart, switch models, clear locks or recover automatically, and it does not replace an existing task's controller or attempt.

## 1. 分開記錄主機接受、接收端表示、正文讀取與任務驗收
## 1. Separate host acceptance, receiver records, body reading and task acceptance

繁中：主機回傳成功只證明該呼叫的返回結果。核接收端時，使用原接收會話自己的紀錄、原輸入前界線及唯一訊息標記。解析紀錄類型、角色與巢狀內容；Codex 的工具輸出、命令回顯或讀屏回顯不算獨立原生 `user` 訊息，也不要求每次都出現某一組同時刻事件。

Claude 的獨立 `user` 訊息、`queue-operation` 與 `attachment` 必須分列。真實 `queue-operation` 可支持「已排隊」；匹配的 `attachment` 可支持其實際附件表示，兩者都不能改稱獨立 `user` 回執。即使外層角色是 `user`，內含 `tool_result` 仍須排除為工具結果。短通知的原生回執不等於其引用正文已完整讀入；完整正文讀取與明確的任務接受、成果驗收也分別留證。逾時或暫未看到標記不授權重送。

English: A successful host return establishes that call's result only. Inspect the original receiver's own records against the original pre-input boundary and unique message marker. Parse record type, role and nested content. Codex tool output, command echoes and screen-read echoes are not independent native `user` messages; do not require one particular pair of simultaneous events in every case.

Record Claude's independent `user` messages, `queue-operation` records and `attachment` representations separately. A genuine queue record can establish queueing; a matched attachment can establish that attachment representation. Neither becomes an independent user receipt. An outer `user` role containing `tool_result` still represents a tool result. A native receipt for a short notice does not prove full reading of its referenced body. Keep complete body reading, explicit task acceptance and result acceptance separate. A timeout or an unobserved marker does not authorize a resend.

## 2. 依觀察窗口、站點、模型與供應商分層，逐組保留分母
## 2. Group by observation window, station, model and provider, retaining denominators

繁中：每項成功率或故障率都列明觀察窗口的起訖、站點（路由入口）、模型、供應商及請求分母。全天與局部窗口不能混算；同一請求的重試次數與請求數也應分開。沿原請求的 `tries[].error` 等實際錯誤欄位分類，保留原記錄及不確定原因。HTML 404 只支持「收到非預期的 API 回應」，不能單憑它斷言供應商宕機或模型不存在。HTTP 200 也不保證產出了有效內容；有些失敗不在相同的路由統計來源中。小樣本要如實標明，本文不制定通用最小樣本門檻。

English: State the start and end of each observation window, station or route entry, model, provider and request denominator for every success or failure rate. Keep whole-period and local-window statistics separate, and distinguish retry attempts from request counts. Classify actual fields such as `tries[].error` from the original request chain, retaining the source record and uncertain causes. An HTML 404 supports only an unexpected API response; it alone proves neither a provider outage nor an unavailable model. HTTP 200 does not guarantee useful output, and some failures may be absent from the routing source being counted. Report small samples honestly without inventing a universal minimum sample threshold.

## 3. 解析原始紀錄的語意，排除被引用的錯誤
## 3. Parse record semantics and exclude quoted errors

繁中：錯誤字串命中只是候選。繼續核 `type`、`payload.type`、角色及巢狀 `item.type`／內容，區分真正的 API 或壓縮錯誤、工具執行結果、命令、提示及歷史文字。單看 `event_msg` 不夠：其中的 `item_completed`／`CommandExecution` 可能只是讀屏回顯。單看 `task_complete` 也不能證明成功或健康；須核該筆實際結果與錯誤語意。保存誤報原件，另記撤回、理由及更正，不用後來的正確結果覆蓋原錯誤。

English: A matching error string is only a candidate. Inspect `type`, `payload.type`, role and nested `item.type` or content to distinguish genuine API or compaction errors from tool results, commands, prompts and quoted history. `event_msg` alone is insufficient: an `item_completed` or `CommandExecution` record may contain a screen-read echo. `task_complete` alone likewise proves neither success nor health; inspect the actual outcome and error semantics. Preserve false reports and record their retraction, reason and correction separately instead of overwriting them.

## 4. 說明尾窗實際覆蓋的時間；覆蓋不足時零筆只代表未觀測
## 4. Report the tail's actual time coverage; zero matches in a short window mean unobserved

繁中：讀取有界位元組尾窗或固定筆數時，記錄實際位元組範圍、最早與最晚可解析時間戳，以及是否覆蓋所需統計窗口。密集紀錄會縮短相同大小尾窗所涵蓋的時間，不能宣稱固定幾 MB 或固定筆數必然足夠。覆蓋不足、時間戳不可解析或紀錄不完整時，明確保留限制；零筆只能說本次範圍未觀測到，不能說整段期間沒有事件。需要補讀時只沿原會話與獲准範圍有界延伸，不能為等到預期結果無限輪詢。

English: For a bounded byte tail or record-count window, record the actual byte range, earliest and latest parseable timestamps, and whether it covers the required reporting interval. Dense logs shorten the time span covered by the same byte count, so no fixed number of megabytes or records is always sufficient. Retain limits caused by incomplete coverage, unparseable timestamps or incomplete records. Zero matches mean no event was observed in that inspected range, not that the whole interval was event-free. Any further read stays bounded to the original session and authorized scope; do not poll indefinitely for a desired result.

## 5. 以錯誤結束與後續恢復分開判定
## 5. Distinguish ending on an error from subsequent recovery

繁中：若最後一筆真正產出是 API 錯誤，就記錄該次以錯誤結束。後續恢復須另核錯誤之後的正常產出，或新回合中的實際進展與結果；僅出現回合開始或 `task_complete` 事件仍不充分。時間新鮮、host 的 `working`／`blocked` 等狀態、單屏橫幅，都不能單獨判定健康或持續停滯。依事件時間與原紀錄解讀，舊錯誤可能仍顯示在已有新進展的畫面上。未核到恢復時保留未知，不由觀察規則自動觸發重送或重啟。

勘誤保留：曾有觀察把「主機為 blocked、回合已結束」與腳本按新鮮度判健康混在一起，誤計成另一個主機狀態不一致案例。本說明撤去該無證計數，只保留「主機狀態不能單獨判健康」的方法結論，不保留任務代號、時長或個案身份。

English: If the last genuine output is an API error, record that attempt as ending on an error. Verify recovery separately through normal output after that error, or actual progress and outcome in a later turn. A turn-start or `task_complete` event alone is insufficient. Fresh timestamps, host labels such as `working` or `blocked`, and a single screen banner cannot independently establish health or continuing stagnation. Interpret original records in time order; an old error can remain on screen while newer work progresses. Keep recovery unknown when unverified, without automatically resending or restarting.

Correction retained: One observation conflated a blocked host and an ended turn with a script's freshness-based healthy verdict, then counted it as another host-state mismatch. This note withdraws that unsupported count and retains only the methodological conclusion that host status alone does not establish health. It omits task labels, durations and case identities.
