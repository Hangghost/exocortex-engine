---
description: 內容提交：把工作樹上寫好的內容與專案檔提交到 content/<date>、project/<name>，不切分支；land 落地到 main
argument-hint: "[status] | commit [<描述>] | land [<date>]"
---

# /exo:content

需要的引擎版本：`0.4.1`

引數：`$ARGUMENTS`

確定性的步驟（分類、逐檔前提、INDEX 重算、寫入、push）一律交給 CLI；這份指令只負責呈現、確認與產生 commit message。
**不要自己用 git 指令寫 `content/*`、`project/*`、`main`，也不要 checkout 這些分支，也不要刪除或還原工作樹的檔案**：提交後檔案留在工作樹，使用者整天看得到自己寫的東西，落地時 CLI 會處理。

## 0. 前置（每個子命令都先做）

1. **取得執行器**：讀工作區根目錄（`git rev-parse --show-toplevel`）的 `.exocortex/instance.toml` 的 `[executor]`（同 `/exo:project`）。檔案不存在或沒有 `[executor].python`：**停下**，請使用者建立，不要猜路徑。
   - 以下 `EXO` 代表：在工作區根目錄執行，先設好 `env` 的每個環境變數，再跑 `<python> -m exocortex_engine.content`。例：`PYTHONUTF8=1 ./.venv/Scripts/python.exe -m exocortex_engine.content status`。
2. **版本檢查**：跑 `EXO version`，第一行 `version: <x.y.z>` 必須與本檔開頭「需要的引擎版本」**完全相等**。不相等或跑不起來：**停下**，印出兩邊版本與兩組更新指令（套件：把工作區 `pyproject.toml` 的引擎依賴 tag 改成 `v<本檔版本>` 後 `uv sync --active`；plugin：`claude plugin marketplace update exocortex-engine` 再 `claude plugin update exo@exocortex-engine`）。
3. **讀輸出**：第一行 `result: ok|noop|rejected|error|usage-error`；退出碼 0 成功、1 被拒（**零寫入**）、2 用法錯誤。被拒時把 `refused:`／`REFUSED` 各行原樣轉告使用者並停下，不要自己繞過。`push <ref>: failed: …` 代表本機已寫入、push 失敗，照實回報，不要重試寫入。

依 `$ARGUMENTS` 的第一個詞選下面一段；沒給時當作 `commit`。

## status

1. 跑 `EXO status`，依各段整理給使用者：
   - `## content/<date>`、`## project/<name>`：這次會提交的路徑。`write` 是新增或修改；**`DELETE` 是工作樹裡刪掉的已追蹤檔，提交後分支上也會刪除**（分支還不存在時，新分支的第一個 commit 就含這個刪除）；`regen` 是會跟著重算的 `contexts/<folder>/INDEX*.md`；`note:` 原樣轉告。
   - `## already committed, waiting to land`：看得到、但已經提交過、內容與分支相同的檔，晚上 `land` 時不會擋路。
   - `## not handled by /exo:content`：架構類路徑（`rules/`、`.claude/`、`docs/`、`infra/`、設定檔…）在單機工作區由使用者自己直接 commit 到 `main`；生成的 INDEX 檔會在分支上重算，本機那份請丟棄（`git checkout -- <path>`，未追蹤就刪掉）。
   - `## content branches not landed yet`：還沒落地的日期。
2. 不寫入任何東西。

## commit `[<描述>]`

1. 跑 `EXO status`（同上）。`result: noop` → 告訴使用者沒有要提交的東西，停下。`result: rejected` → 轉告 `REFUSED` 各行（通常是分支上的版本在別處被改過）並停下。
2. 依 status 擬 commit message（使用者給了描述時以描述為主）：
   - `content/<date>`：依內容選前綴，例如只有 survey／討論紀錄 `docs: add survey on <主題>`、只有 work_log `logs: <主題>`，混合時取最主要的一類，必要時 `content: <一句話>`。
   - `project/<name>`：`project(<name>): <一句話>`。
3. **單次確認**：把「要提交的路徑（`DELETE` 行要特別標出）＋擬好的 message」一次列給使用者，使用者回「好」即執行；要修改就照改後再跑。
4. 跑 `EXO commit -m "<content message>" [--project-message <name> "<message>"]…`（每個有變更的專案一組；只提交部分路徑時加 `--only <path>…`）。訊息含引號等特殊字元時先寫成暫存檔再以 `$(cat 檔)` 帶入。
5. 回報 CLI 輸出：每個分支的 commit、push 結果；提醒「工作樹沒有改動，這些檔已與分支相同，`land` 時會一起落地」。

## land `[<date>]`

1. 跑 `EXO land [<date>]`（省略日期即今天；等同 `/exo:project land content`）。它把 `content/<date>` squash 成 `content: <date> daily snapshot` 落到 `main` 並刪除該分支。
2. 工作樹裡「已提交、內容相同」的檔不會擋路，輸出會列在 `already committed, left as they are`，內容不變。
3. 被拒時：列出的路徑是**提交之後又改過**的檔——先 `/exo:content commit` 再重跑 `land`；若是 `contexts/*/INDEX*.md`，丟棄本機那份後重跑。專案檔用 `/exo:project land project <name> "<milestone>"` 落地。
