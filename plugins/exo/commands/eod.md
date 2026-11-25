---
description: 收尾：落地過去日期、逐專案 update、提交工作樹、milestone、落地今天、push，最後出報告；無人值守需 --allow
argument-hint: "[--unattended] [--allow commit,land,push]"
---

# /exo:eod

需要的引擎版本：`0.4.1`

引數：`$ARGUMENTS`

這份指令只**編排**既有原語：寫入一律走 `project update apply`、`content commit`、`project land`、`day push`，沒有自己的 git 寫入。
**不要自己用 git 指令寫 `main`、`content/*`、`project/*`，不要 checkout 這些分支，不 force，不繞過任何被拒**。

## 0. 前置與模式判定

1. **取得執行器**：讀工作區根目錄（`git rev-parse --show-toplevel`）的 `.exocortex/instance.toml` 的 `[executor]`（同 `/exo:project`）。檔案不存在或沒有 `[executor].python`：**停下**，請使用者建立，不要猜路徑。
   - 以下 `RUN <模組>` 代表：在工作區根目錄執行，先設好 `env` 的每個環境變數，再跑 `<python> -m exocortex_engine.<模組>`。例：`PYTHONUTF8=1 ./.venv/Scripts/python.exe -m exocortex_engine.day push --json`。
2. **版本檢查**：跑 `RUN day version`，第一行 `version: <x.y.z>` 必須與本檔開頭「需要的引擎版本」**完全相等**。不相等或跑不起來：**停下**，印出兩邊版本與兩組更新指令（套件：把工作區 `pyproject.toml` 的引擎依賴 tag 改成 `v<本檔版本>` 後 `uv sync --active`；plugin：`claude plugin marketplace update exocortex-engine` 再 `claude plugin update exo@exocortex-engine`）。
3. **讀輸出**：第一行 `result: ok|noop|skipped|rejected|error|usage-error`（帶 `--json` 時 stdout 是一個 JSON 物件，第一行是 `{`，看它的 `result` 鍵）；退出碼 0 成功、1 被拒（**零寫入**）、2 用法錯誤。被拒時把 `refused:`／`conflict:` 行原樣轉告；`push <ref>: failed: …` 代表本機已寫入、push 失敗，照實列入「待手動處理」，不要重試寫入。
4. **有沒有人**：引數帶 `--unattended`（排程叫起一律帶）就是**無人**；沒帶、由使用者在這個 session 觸發就是**有人**。不偵測環境。
5. **`--allow`**：逗號分隔，值只能是 `commit`、`land`、`push`；有其他值就印出正確用法後停下。只在無人時有意義，有人時忽略並說一句「有人值守，`--allow` 不使用，每步照常確認」。
   - 授權的是步驟，不是 push 本身：`content commit` 與 `land` 照原語的行為各自 push 它寫的 ref；`push` 授權的是第 8 步。

**停下**的意思：後續步驟全部不執行；報告頂端寫明停在第幾步、為什麼、缺哪些旗標，以及可複製的重跑指令。

## 1. 取狀態

跑 `RUN day survey --fetch --json`（鍵名見 `docs/exo-day.md`）。之後每個寫入步驟之前都以 `RUN day survey --json`（不 fetch）或該原語自己的唯讀子命令重算，不沿用舊快照。

- `result: rejected`：轉告並停下。
- `fetch.ok == false`：不中止，報告頂端標「⚠️ fetch 失敗，remote 狀態為上次快照」。之後的 push 若被遠端拒絕，照實列入「待手動處理」。
- `main.state` 是 `behind` 或 `diverged`：**停下**（在任何寫入之前）。在落後的 main 上落地會讓 main 與 origin 分歧。`behind` 且 `worktree.clean` 時建議先跑 `/exo:onboard` 同意 `sync`；否則請使用者自己讓 main 跟上 origin（例如 `git merge --ff-only origin/main`），再重跑 `/exo:eod`。

**無人時的授權預檢**（有人時跳過）：依第 1 步的 survey 與 `RUN day push --no-push --json`（只列出、不 push）算出需要的旗標，缺任何一個就在**第一個寫入之前停下**：

| 旗標 | 需要的條件 |
|---|---|
| `commit` | `worktree.to_commit > 0` |
| `land` | 有第 2 步要落地的日期；或今天的 `content/<today>` 在本機且 `landed == false`；或 `worktree.plan.targets` 中 `content/<today>` 有 `writes`／`deletes` |
| `push` | dry run 的 `branches[]` 有 `state` 為 `ahead` 或 `new` 的分支 |

重跑指令寫成 `/exo:eod --unattended --allow <已給的 ∪ 缺的，依 commit,land,push 排序>`，並逐項寫出缺的旗標與它對應的條件。預檢通過後若某個寫入步驟仍發現自己沒有授權（狀態在中途變了），同樣在那一步停下。

## 2. 落地過去日期

候選：survey 的 `content_branches[]` 中 `past == true`、`landed == false`、`location` 為 `local` 或 `both`，最舊的先。`location: remote` 的不落地，列入「待手動處理」並附 finding 的取回指令（只印出，不執行）。

- 有人：**一題**列出全部日期與 commit 數請使用者確認；確認後依序跑 `RUN project land content <date>`。
- 無人：有 `land` 才做（預檢已保證）。
- 被拒（兩邊都改過）：轉告衝突路徑並**停下**，不繞過；已落地的日期照實列入報告。

## 3. 逐專案 update（只在有人時）＋ 4. 一題教訓

「有進度」＝ `projects[].progress.has_progress == true` 且 `today_worklog_on` 為空（今天已寫過 work_log 的專案算已更新，列入報告的「已跳過」並註明「今天已更新」；這也讓同一天第二次 eod 不再重問）。

**無人**：兩步都跳過，即使有 `--allow` 也不做：進展是第一人稱，不以空答案或只依 commit 代寫 work_log。有進度的專案列入報告的「無人值守而未做」。

**有人**：

1. 重跑 `RUN day survey --json`；有進度的專案要 update，其餘列入報告的「已跳過（無進度）」。
2. 對每個要 update 的專案跑 `RUN project update <name> gather`。
3. **整輪一次問完**（一則訊息）：每個專案照 `/exo:project update` 的三題（進展補充、經驗與觀察、下一步，各附依 gather 與對話擬的建議答案），最後加一題「今天有什麼值得記下的教訓？（可留白）」並請使用者標明歸屬的專案（可不歸屬）。
4. 每個專案照 `/exo:project update` 的第 3–7 步改寫 PROJECT.md、寫 work_log 區段、跑 `RUN project update <name> apply …`。被拒照該指令的規則修正後重跑；**一個專案失敗不中止其他專案**，失敗的列入「待手動處理」。
5. 教訓（第 4 步，寫入走第 5 步）：
   - 留白：no-op。
   - 歸屬到這一輪有 update 的專案：併進那個專案 work_log 區段的 `### 經驗與觀察`，與第 4 點同一次 `apply`（所以要在 apply 之前問完）。
   - 其他情況（不歸屬、或歸屬的專案這一輪沒有 update）：寫到工作樹的 `contexts/thought_review/<today>_eod_lessons.md`（已存在就在檔尾追加一段 `## <HH:MM>`）。新檔的 frontmatter 只用 `date: <today>`、`type: lessons`、`l0: <一行摘要>`，歸屬專案時加 `project: <name>`。這個檔在第 5 步一起提交。

## 5. 提交工作樹

1. 跑 `RUN content status --json`。`result: noop`：跳過這步。`result: rejected`：轉告 `REFUSED` 各行並**停下**。
2. 有人：照 `/exo:content commit` 的流程，把要提交的路徑（`targets[].deletes` 是 `DELETE`，要特別標出）與擬好的 message 一次列出、單次確認後跑 `RUN content commit -m "<message>" [--project-message <name> "<message>"]…`。`other[]`（不歸 `/exo:content` 管的路徑）不會被提交，列入報告的「待手動處理」。
3. 無人（有 `commit`）：任何 `targets[].deletes` 非空就**停下**（刪除要人確認）；否則跑 `RUN content commit -m "content: <today> eod (unattended)"`，每個有變更的專案加 `--project-message <name> "project(<name>): eod (unattended)"`。
4. `rejected`：轉告並**停下**。

## 6. milestone（只在有人時）＋ 7. 落地今天

1. 重跑 `RUN day survey --json`。
2. milestone 候選：`projects[].milestone.ahead > 0` 的專案。**無人**：跳過（里程碑名稱只有人能給），候選列入「無人值守而未做」。
3. 今天：`content_branches[]` 中 `date == today`、`location` 為 `local` 或 `both`、`landed == false` 者要落地；`location: remote` 列入「待手動處理」；沒有就說「今天沒有要落地的 content」。
4. 有人：**一則訊息**問兩題：
   - milestone（multi-select，候選為空就不問）：每個候選列出 `ahead`、`days_since_landed`（`null` 寫「從未落地」），`recommend == true` 的標「（建議）」並列出 `reasons[]` 原文；選中者請使用者給一句里程碑描述。不自行補判準。
   - 落地今天：「把 `content/<today>` 落到 main 並 push？」
5. 依答案**先**對每個選中的專案跑 `RUN project land project <name> "<milestone>"`（有 `warning: outside the project's scope` 時轉告），**再**跑 `RUN project land content`。無人（有 `land`）：只跑 `RUN project land content`。
6. 被拒：轉告並**停下**（`land content` 被拒時的處理見 `/exo:content land` 第 3 點，交給使用者，不繞過）。

## 8. push 其餘

跑 `RUN day push --json`（無人時需要 `push`）。

- `pushed[]`：列入報告。
- `diverged[]`、`failed[]`：不處理，列入「待手動處理」（`failed` 代表遠端在上次 fetch 之後又動過；不 force）。
- `behind[]`：列入「待手動處理」，說明本機落後、沒有東西要 push。

## 9. 報告

跑 `RUN day survey --json` 與 `RUN day push --no-push --json`（都唯讀），整理成下列各段，**有內容的段才出現**：

1. （停下時在最頂端）停在第幾步、原因、缺的旗標、可複製的重跑指令。
2. **已落地**：日期與 commit；milestone 的專案與描述。
3. **已更新專案**：專案與寫入的 commit。
4. **已跳過專案（無進度）**。
5. **無人值守而未做**：被跳過的步驟與涉及的專案（update、教訓、milestone）；隔天 `/exo:onboard` 會再呈現。
6. **待手動處理**：遠端獨有的分支（附取回指令）、分歧、push 失敗、不歸 `/exo:content` 管的路徑、失敗的專案。
7. **稽核（收尾後量測）**：照 `/exo:onboard` 第 7 步的呈現規則（`audit.rules == 0` 寫「稽核：未註冊規則」；`suggest` 只印出）。

末行：最終 survey 的 `main.state == "in-sync"`、沒有 `landed == false` 的 content 分支、dry run 沒有 `ahead`／`new`／`diverged`、且「待手動處理」為空時寫「✅ remote 全部最新」；否則寫「⚠️ 還有 <N> 項待處理（見上）」。什麼都沒做（例如第二次跑）時只寫「沒有要收尾的東西」與末行。
