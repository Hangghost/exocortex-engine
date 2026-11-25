---
description: 待辦：add 捕獲、list 列舉、set 改狀態／掛專案／補備註、done 完成或放棄、triage 待分診；寫入 content/<today>，不切分支
argument-hint: "add <描述> | list [--project <name>] [--all] | set <ticket> … | done <ticket> [--dropped] | triage [<project>]"
---

# /exo:todo

需要的引擎版本：`0.5.0`

引數：`$ARGUMENTS`

待辦存在 `inbox/todos.md`。確定性的步驟（格式驗證、ticket 唯一、寫入、push）一律交給 CLI；這份指令只負責把使用者的話變成 CLI 引數、分診時問答。
**不要自己編輯 `inbox/todos.md`，也不要用 git 指令寫 `content/*`**：CLI 以 ref-anchored commit 寫入，工作樹不動。因此工作樹的 `inbox/todos.md` 在 `land` 之前可能是舊的，要看現況一律用 `list`（第一次寫入後提醒使用者一次）。

## 0. 前置（每個子命令都先做）

1. **取得執行器**：讀工作區根目錄（`git rev-parse --show-toplevel`）的 `.exocortex/instance.toml` 的 `[executor]`（同 `/exo:project`）。檔案不存在或沒有 `[executor].python`：**停下**，請使用者建立，不要猜路徑。
   - 以下 `EXO` 代表：在工作區根目錄執行，先設好 `env` 的每個環境變數，再跑 `<python> -m exocortex_engine.todo`。例：`PYTHONUTF8=1 ./.venv/Scripts/python.exe -m exocortex_engine.todo list`。
2. **版本檢查**：跑 `EXO version`，第一行 `version: <x.y.z>` 必須與本檔開頭「需要的引擎版本」**完全相等**。不相等或跑不起來：**停下**，印出兩邊版本與兩組更新指令（套件：把工作區 `pyproject.toml` 的引擎依賴 tag 改成 `v<本檔版本>` 後 `uv sync --active`；plugin：`claude plugin marketplace update exocortex-engine` 再 `claude plugin update exo@exocortex-engine`）。
3. **讀輸出**：第一行 `result: ok|noop|rejected|error|usage-error`；退出碼 0 成功、1 被拒、2 用法錯誤，被拒與用法錯誤都**零寫入**。被拒時把 `refused:` 那行原樣轉告使用者並停下，不要自己繞過：`land content <日期>` 照指示請使用者執行；「checkout 有未提交的手改」請使用者先 `/exo:content commit --only inbox/todos.md` 或丟棄手改。`push <ref>: failed: …` 代表本機已寫入、push 失敗，照實回報，不要重試寫入。
4. **多行內容**一律先寫成暫存檔再用 `--body-file`（原生 Windows 的引號與 stdin 都不可靠）；子項每行自己帶 `- `，CLI 會統一縮排兩格。

依 `$ARGUMENTS` 的第一個詞選下面一段。

## add `<描述>`

使用者給了描述就直接寫入（指令即授權），不先問。

1. 跑 `EXO list` 看有沒有疑似重複（標題或摘要語意相近）。有 → **只問這一題**：仍新增／在既有條目 `set --note`／放棄新增。
2. 擬 `--ticket`（小寫英數與 `-`，英文短語，**不得與 Active 或 Done 的任何 ticket 重複**，被拒就換一個）、`--title`（單行、不含 `**`）、`--summary`（一句話，可省）。使用者提到專案才加 `--project <name>`（必須是 `projects/INDEX.md` 的名稱）、提到期限才加 `--due YYYY-MM-DD`；使用者明說可以直接做才加 `--status ready`，否則用預設 `needs-triage`。
3. 跑 `EXO add --ticket … --title … [--summary …] [--project …] [--status …] [--due …] [--body-file …]`，回報 ticket 與結果。

## list `[--project <name>] [--status <s>] [--all]`

跑 `EXO list …`，把每條（ticket、status、project、標題、期限）整理給使用者；`warnings` 原樣轉告。唯讀。

## set `<ticket>` / done `<ticket>`

使用者指名某條要改狀態、掛專案、補備註或做完了：跑 `EXO set <ticket> [--status …] [--project …] [--due …] [--note …]` 或 `EXO done <ticket> [--note …]`。放棄用 `EXO done <ticket> --dropped --note "<理由>"`，**理由必填**，使用者沒說就問一句。ticket 不明確時先 `EXO list` 讓使用者指認。

## triage `[<project>]`

1. 跑 `EXO list --status needs-triage [--project <project>]`；沒有就告訴使用者並停下。
2. 對每條先做**唯讀查證**（讀相關檔案、`git log`），不寫任何東西。
3. **單次**詢問全部：每條列出查證結論與建議，讓使用者逐條選「登記為某 status（`ready`／`discuss-first`／`needs-analysis`）＋查證備註」、「放棄＋理由」或「暫緩」。
4. 依答案逐條跑 `EXO set <ticket> --status <s> --note "<查證備註>"` 或 `EXO done <ticket> --dropped --note "<理由>"`；備註與理由不得為空。暫緩的不動。
