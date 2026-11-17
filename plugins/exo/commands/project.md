---
description: 專案脈絡管理：new 建立、load 載入、update 更新進度、board 總覽、land 落地到 main
argument-hint: "new <name> | load <name> | update <name> | board | land content [<date>] | land project <name> \"<milestone>\""
---

# /exo:project

需要的引擎版本：`0.1.0`

引數：`$ARGUMENTS`

確定性的步驟（讀 git ref、驗證、寫入、push）一律交給 CLI；這份指令只負責問答與改寫 PROJECT.md。
**不要自己用 git 指令寫 `project/*`、`content/*`、`main`，也不要 checkout 這些分支**：CLI 以 ref-anchored commit 寫入，工作樹不切分支。

## 0. 前置（每個子命令都先做）

1. **取得執行器**：讀工作區根目錄（`git rev-parse --show-toplevel`）的 `.exocortex/instance.toml`：

   ```toml
   [executor]
   python = "./.venv/Scripts/python.exe"   # Windows；macOS／Linux 為 "./.venv/bin/python"
   env = { PYTHONUTF8 = "1" }
   ```

   - 檔案不存在，或沒有 `[executor].python`：**停下**，告訴使用者要建立這個檔（貼上上方範例），不要猜路徑、不要改用 `python`／`uv run`。
   - 以下 `EXO` 代表：在工作區根目錄執行，先設好 `env` 的每個環境變數，再跑 `<python> -m exocortex_engine.project`。例：`PYTHONUTF8=1 ./.venv/Scripts/python.exe -m exocortex_engine.project board`。
2. **版本檢查**：跑 `EXO version`。第一行應為 `version: <x.y.z>`。
   - 與本檔開頭「需要的引擎版本」**完全相等**才繼續（v0.x 期間不接受任何差異）。
   - 不相等，或指令跑不起來（找不到模組、執行器不存在、非 0 結束）：**停下，不要繼續任何子命令**，照實印出兩邊版本（CLI 那邊跑不起來就寫「無法執行」並附錯誤首行），再印出兩組更新指令，由使用者決定更新哪一邊：
     - 套件：把工作區 `pyproject.toml` 的 `exocortex-engine @ git+https://github.com/Hangghost/exocortex-engine@v<版本>` 改成本檔需要的版本，再跑 `uv sync --active`
     - plugin：`claude plugin marketplace update exocortex-engine`，再 `claude plugin update exo@exocortex-engine`（更新後重開 session）
3. **讀輸出**：CLI 第一行是 `result: ok|noop|rejected|error|usage-error`。退出碼 0 成功、1 被拒（前提不符，**零寫入**）、2 用法錯誤。被拒時把 `refused:`／`conflict:` 那行原樣轉告使用者並停下，不要自己繞過。`push <ref>: failed: …` 代表本機已寫入、push 失敗，照實回報，不要重試寫入。

依 `$ARGUMENTS` 的第一個詞選下面一段；沒給或不認得時，列出可用子命令後停下。

## new `<name>`

1. `<name>` 必須是 kebab-case；不是就請使用者改。
2. 問使用者（一次問完）：標題、目標／動機（一兩句）、現況（可略）、下一步（可略）。使用者已在引數或對話中給了的就不再問。
3. 跑 `EXO new <name> --title "<標題>" --goal "<目標>" [--current "<現況>"] [--next "<下一步>"]`。多行內容用引號包好；含引號等特殊字元時改用 heredoc 或先寫暫存檔再以 `$(cat 檔)` 帶入。
4. 回報 CLI 輸出：建立的分支、併入 main 的 commit、push 結果。

## load `<name>`

1. 跑 `EXO load <name>`。
2. 依輸出摘要目標、現況、下一步，列出最近的 work_log 路徑；有 `hint:` 行時一併轉告（工作樹裡的 PROJECT.md 落後分支，以分支為準）。
3. 需要更多脈絡時，用 `git show <ref>:<path>` 讀輸出列出的檔案（**讀 ref，不讀工作樹副本**）。

## update `<name>`

1. 跑 `EXO update <name> gather`。它會列出：上次改 PROJECT.md 之後的 commit、main 上這個專案的 work_log、當日 work_log 是否已存在，以及目前的 PROJECT.md 全文。
2. 參考 gather 結果與本次對話，**一次問完三個問題**（每題附你根據脈絡擬的建議答案，使用者回「好」即採用）：
   1. 進展補充：這次做了什麼、結果如何？
   2. 經驗與觀察：有什麼值得記下的教訓或發現？
   3. 下一步：接下來第一個動作是什麼？
3. 依答案改寫 PROJECT.md 全文：
   - 保留 frontmatter 的其他欄位；`status` 不變；`l0` 重寫為一行最新摘要（≤120 字）；`last_updated` 由 CLI 設為今天。
   - 依標題文字改 `## 現況`、`## 下一步`（第一條寫成可以立刻動手的項目），需要時改其他段落。
   - **不得刪除任何 `## ` 段落**（CLI 會拒絕）；不認得的段落原樣保留；`<!-- 待填入 -->` 是空段落的佔位符。
4. 寫 work_log 區段（只寫三個 `###` 子段，不寫 `## ` 標題、不寫 frontmatter）：

   ```markdown
   ### 進展

   - …

   ### 經驗與觀察

   - …

   ### 下一步

   - …
   ```

5. 把兩份內容寫成工作區外的暫存檔（例如系統暫存目錄），以 UTF-8 存檔，再跑：
   `EXO update <name> apply --project-md <PROJECT.md 暫存檔> --worklog-section <區段暫存檔> --worklog-l0 "<今天這個專案的一行總結>"`
6. 被拒時（例如缺段落、frontmatter 不合格），依錯誤訊息修正暫存檔後重跑 `apply`；兩項寫入是整批的，被拒代表都沒寫。
7. 回報寫入的分支與 commit、push 結果，並刪除暫存檔。

## board

1. 跑 `EXO board`。
2. 以表格呈現：專案、狀態、最後更新（距今天數）、下一步第一條。距今超過 14 天的 `active` 專案標出來。不要自行補 CLI 沒給的下一步。

## land content `[<date>]`

1. 跑 `EXO land content [<date>]`（省略日期即今天）。它把 `content/<date>` squash 成一個 `content: <date> daily snapshot` commit 落到 main，並刪除該分支（本機與 remote）。
2. 被拒的常見原因：main 在某個工作樹裡 checkout 且有未提交的改動（請使用者先 commit 或 stash）；同一檔案在兩邊都被改過。

## land project `<name>` `"<milestone>"`

1. 沒給 milestone 時問使用者一句話的里程碑描述。
2. 跑 `EXO land project <name> "<milestone>"`。它以 `--no-ff` 語意把 `project/<name>` 的 `projects/<name>/**` 與 INDEX 中該專案那一列落到 main，分支保留。
3. 有 `warning: outside the project's scope` 時轉告使用者：那些路徑不屬於專案分支的範圍，沒有落地。
