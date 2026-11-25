---
description: 開工：一頁式 digest（狀態、昨日未收尾、健康檢查、board、待辦、未決、建議下一步），預設唯讀
argument-hint: "[--unattended]"
---

# /exo:onboard

需要的引擎版本：`0.5.0`

引數：`$ARGUMENTS`

確定性的資料（ref 狀態、工作樹分類、專案進度、findings）一律由 CLI 算好；這份指令只負責判讀、詢問與呈現。
**預設唯讀**：除了第 9 步使用者明確同意的 `sync` 與委派給 `/exo:content commit` 的提交之外，不寫任何東西；不 force、不切分支、不自己用 git 指令寫任何 ref。

## 0. 前置

1. **取得執行器**：讀工作區根目錄（`git rev-parse --show-toplevel`）的 `.exocortex/instance.toml` 的 `[executor]`（同 `/exo:project`）。檔案不存在或沒有 `[executor].python`：**停下**，請使用者建立，不要猜路徑。
   - 以下 `RUN <模組>` 代表：在工作區根目錄執行，先設好 `env` 的每個環境變數，再跑 `<python> -m exocortex_engine.<模組>`。例：`PYTHONUTF8=1 ./.venv/Scripts/python.exe -m exocortex_engine.day survey --fetch --json`。
2. **版本檢查**：跑 `RUN day version`，第一行 `version: <x.y.z>` 必須與本檔開頭「需要的引擎版本」**完全相等**。不相等或跑不起來：**停下**，印出兩邊版本與兩組更新指令（套件：把工作區 `pyproject.toml` 的引擎依賴 tag 改成 `v<本檔版本>` 後 `uv sync --active`；plugin：`claude plugin marketplace update exocortex-engine` 再 `claude plugin update exo@exocortex-engine`）。
3. **有沒有人**：引數帶 `--unattended`（排程叫起一律帶）就是**無人**；沒帶、由使用者在這個 session 觸發就是**有人**。不偵測環境。無人時第 9 步不發問、不寫入。
4. **讀輸出**：第一行 `result: ok|noop|skipped|rejected|error|usage-error`；帶 `--json` 時 stdout 是一個 JSON 物件，看它的 `result` 鍵（第一行是 `{`，不是 `result:`）。退出碼 0 成功、1 被拒（零寫入）、2 用法錯誤。
   - **判斷「跑不起來」**：stdout 能解析成帶 `result` 鍵的 JSON（不帶 `--json` 時：第一行是 `result:`），代表模組有在跑，依 `result` 與退出碼判讀；解析不到（stdout 空或不是那個形狀，例如 stderr 為 `No module named …`），才是模組未安裝或跑不起來。兩者的退出碼可能同樣是 1，**不能**只看退出碼區分。

## 1. 取狀態

跑 `RUN day survey --fetch --json`，以下各步都讀這一份（鍵名見 `docs/exo-day.md`）。

- `result: rejected`（main 不存在或 `projects/INDEX.md` 讀不了）：把 `refused:` 行轉告使用者並停下，不出 digest。
- `fetch.ok == false`：不中止；digest 第一行寫「⚠️ fetch 失敗，以下為上次快照：<`fetch.error`>」。

## 2. 昨日未收尾

`content_branches[]` 中 `past == true` 且 `landed == false` 者（CLI 已排成最舊在前）：每條一行「`<date>`：<commits> 個 commit 未落地（<location>）」。`location: remote` 的附上「只在遠端，`land content` 前要先取回」；這些分支在第 7 步不再重複列。有任何一條就在建議下一步列 `/exo:eod`。

## 3. 狀態：dirty 與 main

- `main.state`：`in-sync` 寫「main 與 origin 同步」；`behind`／`ahead` 寫出 `behind`／`ahead` 數；`diverged` 寫「main 與 origin 分歧（ahead N／behind M），需手動處理」；`no-remote` 寫「沒有 remote-tracking ref」。
- `worktree.clean == false`：一行摘要「工作樹：待提交 <to_commit>（其中 DELETE <deletes 數>）、已提交待落地 <waiting>、不歸 `/exo:content` 管 <not_handled 數>」，`refused[]` 非空時另起一行原樣轉告。
- 這兩件事的詢問留到第 9 步（先讓使用者看到整頁再決定）；無人時只列在這裡。

## 4. board 摘要

跑 `RUN project board`，照 `/exo:project board` 的規則：每個專案一行「專案、狀態、最後更新（距今天數）、下一步第一條」，距今超過 14 天的 `active` 專案標出來。不要自行補 CLI 沒給的下一步。失敗（非 0 結束）：這段只放錯誤首行，其餘照常。

## 5. 待辦摘要

跑 `RUN todo list --json`（只列 Active）。

- 跑不起來（照第 0 步第 4 點：stdout 解析不到帶 `result` 鍵的 JSON，例如 stderr 為 `No module named exocortex_engine.todo`）：整段換成一行「待辦：todo 未安裝（<stderr 首行>）」。
- 被拒或失敗（JSON 的 `result` 不是 `ok`／`noop`）：整段換成一行「待辦：讀取失敗（<`messages` 裡的 `refused:`／`error:` 行>）」。
- `exists == false`：整段換成一行「待辦：尚未建立 `inbox/todos.md`（`/exo:todo add` 會建立）」。
- 以上三種都**不得**省略成「沒有待辦」。檔案存在且 `count == 0` 時整段省略。
- 否則：「待辦 <count> 項，逾期或今天到期 <N> 項」，N 是 `todos[]` 中 `state == "open"`、`due` 不是 `null` 且 `due <= survey 的 today` 的條數；再列前 3 條（逾期／到期者在前，其餘照檔案順序），每條「`<ticket>` <title>（due <due>）」。`warnings` 非空時附一行「另有 <數> 則格式警告，`/exo:todo list` 查看」。

## 6. 未決盤點

對 survey `projects[]` 的每個專案跑 `RUN next_items show <name> --json`。只讀三個鍵：`count`、`any_failed`、`sources`。

- `count > 0` 或 `any_failed == true`：一行「`<name>`：未決 <count> 項，`/exo:next <name>` 展開」，`any_failed` 為真時附「（部分來源讀取失敗）」。**不得**只顯示較小的數字或略掉失敗。
- `count == 0` 且 `any_failed == false`：不列。
- 跑不起來（照第 0 步第 4 點：stdout 解析不到帶 `result` 鍵的 JSON，例如 stderr 為 `No module named exocortex_engine.next_items`；退出碼同樣是 1，要靠這點與下一條區分）：整段換成一行「未決：next 未安裝（<stderr 首行>）」，不再逐專案嘗試。
- 該專案無法盤點（stdout 是帶 `result` 鍵的 JSON、退出碼非 0：`rejected` 退出碼 1，例如專案不存在；`usage-error` 退出碼 2，例如名字不是 kebab-case）：一行「`<name>`：無法盤點（<`messages` 裡的 `refused:`／`error:` 行>）」，其他專案照常盤點。
- 一行都沒有時整段省略。

## 7. 健康檢查

- `findings[]` 中 `subject` 已在第 2 步呈現過的（例如 `content-branch-remote-only` 指向的昨日分支）不重複列；其餘每條一行「[<severity>] <message>」，有 `suggest` 時附「建議：`<command>`」，`mutates == true` 的加註「（會寫入，請自行執行）」。**一律只印出，絕不自動執行。**
- 判「有沒有稽核規則」只看 `audit.rules`，不看 `findings` 是否為空（survey 內建的 finding 不算規則）：
  - `audit.rules == 0`：一行「稽核：未註冊規則」。
  - `audit.rules > 0` 且沒有規則命中：一行「稽核：<rules> 條規則，0 命中」。
- 不寫「無 finding」。

## 8. digest

固定順序：**狀態**（fetch 警告、main、工作樹）→ **昨日未收尾** → **健康檢查** → **board** → **待辦** → **未決** → **建議下一步**。

- 空段整段省略（連標題一起）；「狀態」段永遠有一行 main 的狀態。
- **建議下一步**只列可複製的指令，不代為執行：昨日未收尾 → `/exo:eod`；工作樹有待提交 → `/exo:content commit`；有逾期待辦 → `/exo:todo list`；有未決 → `/exo:next <name>`；board 標出的舊專案 → `/exo:project update <name>`；findings 的 `suggest.command`。
- 整頁超過 40 行：每段只留前 3 條，段尾附「另有 N 條」。
- 不顯示 persona 狀態：SessionStart hook 已在 session 開頭處理（`docs/exo-persona.md`），這裡不另寫偵測。

## 9. 詢問（只在有人時）

digest 印完後，視狀態**一次問完**（無人時跳過整步，第 3 步已列在狀態區）：

1. `worktree.to_commit > 0`：「要先把工作樹的內容提交到分支嗎？」→ 同意則照 `/exo:content commit` 的流程做（它自己會再單次確認路徑與 message）。
2. `main.state == "behind"`：
   - `worktree.clean == true`：「main 落後 origin <behind> 個 commit，要 fast-forward 嗎？」→ 同意則跑 `RUN day sync --json`。
   - 否則不問，告訴使用者：`sync` 只在工作樹完全乾淨時才動（untracked 也算），提交過的檔仍留在工作樹，所以要等 `/exo:eod` 落地之後，或使用者自己處理工作樹後再跑 `/exo:onboard`。

`sync` 的輸出：`ok` 回報前後 commit；`noop` 說已同步；`skipped` 逐條轉告 `skip[]` 的理由（零寫入，不是錯誤）。使用者沒同意的事不做。
