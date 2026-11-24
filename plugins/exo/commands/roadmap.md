---
description: 專案結構 SSOT：synthesize 首次起草 ROADMAP.yaml、revise 依新材料改版並列 diff、diff 唯讀比對
argument-hint: "<name> synthesize | <name> revise | <name> diff"
---

# /exo:roadmap

需要的引擎版本：`0.4.1`

引數：`$ARGUMENTS`

`projects/<name>/ROADMAP.yaml` 住在 `project/<name>` 分支。驗證與比對交給 CLI（`roadmap validate`／`roadmap diff`），寫入一律走 `/exo:content commit`；這份指令只負責讀材料、起草或改寫 YAML 原文、呈現與確認。
**不要自己用 git 指令寫 `project/*`、`main`，也不要 checkout 這些分支**；**不要用任何 YAML 套件 dump 或重排檔案**：落地的就是你寫的原文，註解與排版原樣保留。

## 0. 前置（每個子命令都先做）

1. **取得執行器**：讀工作區根目錄（`git rev-parse --show-toplevel`）的 `.exocortex/instance.toml` 的 `[executor]`（同 `/exo:project`）。檔案不存在或沒有 `[executor].python`：**停下**，請使用者建立，不要猜路徑、不要改用 `python`／`uv run`。
   - 在工作區根目錄執行，先設好 `env` 的每個環境變數，再跑 `<python> -m exocortex_engine.<模組>`。以下 `EXO_ROADMAP`、`EXO_PROJECT`、`EXO_CONTENT` 分別代表模組 `roadmap`、`project`、`content`。例：`PYTHONUTF8=1 ./.venv/Scripts/python.exe -m exocortex_engine.roadmap diff alpha`。
2. **版本檢查**：跑 `EXO_ROADMAP version`，第一行 `version: <x.y.z>` 必須與本檔開頭「需要的引擎版本」**完全相等**。不相等或跑不起來：**停下**，印出兩邊版本與兩組更新指令（套件：把工作區 `pyproject.toml` 的引擎依賴 tag 改成 `v<本檔版本>` 後 `uv sync --active`；plugin：`claude plugin marketplace update exocortex-engine` 再 `claude plugin update exo@exocortex-engine`）。
3. **讀輸出**：第一行 `result: ok|noop|rejected|error|usage-error`；退出碼 0 成功、1 被拒、2 用法錯誤。`validate` 被拒時每筆 finding 一行 `- [規則] line N id: 說明`，**全部列給使用者**後依下方流程處理，不要自己繞過 CLI。
4. `<name>` 必須是 kebab-case；第二個詞不是 `synthesize`／`revise`／`diff` 時列出三個子命令後停下。

## 材料（synthesize 與 revise 共用）

一律讀 git ref，不讀工作樹副本；缺的項目在確認畫面列出「缺：…」，不要靜默略過。

1. `PROJECT.md` 全文：`git show project/<name>:projects/<name>/PROJECT.md`（分支不存在時讀 `main`）。**讀不到就中止**，請使用者先 `/exo:project new <name>`。
2. `context/HISTORY.md`：`git show project/<name>:projects/<name>/context/HISTORY.md`，有就讀。
3. 最近 5 筆該專案的 work_log：`EXO_PROJECT load <name> --json` 的 `worklogs` 給最近 3 筆（`<ref>:<path>`，含還沒落地的 `content/*`），不足 5 筆再用 `git grep -l -E "^project:[[:space:]]*<name>[[:space:]]*$" main -- contexts/work_logs/` 依檔名日期補 `main` 上較舊的；逐筆 `git show <ref>:<path>`。
4. `projects/INDEX.md` 該專案那一列：`git show main:projects/INDEX.md`。

## YAML 寫法（CLI 只讀得懂這個子集，違反會被 `parse-failed` 拒絕）

- 兩格空白縮排，不用 tab；頂層鍵依序 `project`、`revised`、`revised_by`、`since`、`now`、`phases`、`lines`、`tasks`、`ledger`、`decisions`、`signals`；清單條目寫成 `  - id: …` 再接同欄的鍵。
- **值含 ` #` 或 `: ` 時整個值加雙引號**（`title: "第 #2 版"`）；行尾註解與內容**至少隔兩個空白**（`since: 2026-11-24  # 基準日`）。`foo #bar` 會被 YAML 截成 `foo`，CLI 以 `yaml-hash-truncation` 拒絕。
- 長文字用 `>-` 折疊區塊時，**每一行縮排相同**，不要在區塊內再縮排更深（例如條列）；需要條列就拆成多個欄位或寫成一般單行字串。
- 不用錨點（`&`／`*`）、標籤（`!`）、`{ }` 行內 mapping、多行 `[ ]`；id 清單可以寫成一行 `[a, b]` 或區塊清單。日期一律 `YYYY-MM-DD`；`gate.evaluated` 未評估寫 `null`。

## Schema 與原則

```yaml
project: <name>                 # 等於目錄名
revised: <今天>
revised_by: <讀了哪些材料，一句話>
since: <diff 基準日>             # synthesize 時＝今天；revise 時沿用
now: [<task-id>, ...]           # 唯一的推進路徑：有序、全是 tasks 的 id、不重複
phases:  [{id, title, state: done|now|later, done_when?, gate?: {id, condition, evaluated: null|日期, note?}}]
lines:   [{id, title, clearer}]  # clearer 必填：寫「誰、做了什麼，這條線上的票就清掉」
tasks:   [{id, title, line, phase, status: open|done|cancelled, after?: [task-id], clears?: [id], due?: 日期, why_here?, done_note?}]
ledger:  [{id, title, line, why?, cleared_by?: id}]          # 可省
decisions: [{id, text, superseded_by?: id, superseded_note?}] # 可省；不得有 status
signals: [{id, date, text, informs?: [id]}]                   # 可省；不得有 status
```

七條原則：id 是身份（kebab-case，整份檔案跨清單唯一）；線按「誰來清」切；閘門只掛在階段上；定案是約束不是任務（`decisions` 沒有 status）；訊號是證據不是待辦（`signals` 沒有 status）；只有一條 `now`；帳（`ledger`：待解的問題）與票（`tasks`：要做的事）分開，票以 `clears` 指向它清掉的帳。

## synthesize

1. 跑 `git show project/<name>:projects/<name>/ROADMAP.yaml`：**已存在就停下**，請使用者改用 `/exo:roadmap <name> revise`。
2. 讀「材料」，依 schema 與原則起草全文：`## 下一步` 與 work_log 裡的待做事項成為 `tasks`，未解的問題成為 `ledger`，已拍板的事成為 `decisions`，觀察到的事實成為 `signals`。`now` 只放眼前真正要推的 1～5 張票。
3. 把全文寫到**工作區外**的暫存檔，跑 `EXO_ROADMAP validate <name> --file <暫存檔> --new`。被拒：依 finding 修正原文再驗，直到 `result: ok`；`already-exists`／`no-project-branch` 照第 1 步處理。
4. 進「共同收尾」。

## revise

1. 讀舊版：`git show project/<name>:projects/<name>/ROADMAP.yaml`。**不存在就停下**，請使用者改用 `synthesize`。
2. 讀「材料」與本次對話。**以舊版原文為底改寫**，不要從頭重寫：既有 id **逐字保留**；票換了線或階段要補 `why_here`；完成的票改 `status: done`（可補 `done_note`），不要刪；`revised` 改成今天、`revised_by` 改成這次讀的材料；`since` 不動。
3. 寫到工作區外的暫存檔，跑 `EXO_ROADMAP validate <name> --file <暫存檔>`，被拒就修到 `ok`。
4. 跑 `EXO_ROADMAP diff <name> --file <暫存檔>`：
   - `result: noop`（只有 `revised`／`revised_by` 變了以外沒有差異）：回「ROADMAP 無變化」，**不提問**，直接進「共同收尾」第 2 步落地（`revised` 仍然前進）。
   - 舊版無法解析（`result: rejected`、`old_status: failed`）：列出舊版的 finding，告知「無法逐條比對，新版會整份取代舊版」，在確認畫面明講這件事。
   - 其餘：把 `removed` **逐條列出（id 與標題）**，連同 `added`、`moved`（線或階段前後）、`changed`（只列 id）、`now` 前後放進確認畫面。`removed` 非空時確認句必須明問「這些條目會從 ROADMAP 消失，確定？」；同一件事換了 id 會顯示成一刪一增，請使用者確認是否該保留舊 id。
5. 進「共同收尾」。

## 共同收尾

1. **單次確認**：一個畫面列出 validate 摘要（階段／線／票（open）／帳／定案／訊號數、`now` 路徑）、缺的材料、revise 的 diff，問一次；使用者回「好」才繼續，要修改就改完重跑 validate（revise 也重跑 diff）再確認。
2. 把暫存檔內容**原樣**寫到工作樹的 `projects/<name>/ROADMAP.yaml`（UTF-8，覆蓋即可），再跑 `EXO_ROADMAP validate <name> --file projects/<name>/ROADMAP.yaml` 驗落地檔；被拒就停下回報。
3. 跑 `EXO_CONTENT commit --only projects/<name>/ROADMAP.yaml --project-message <name> "project(<name>): roadmap <synthesize|revise> <今天>"`，回報 commit 與 push 結果（`/exo:content commit` 的規則照常適用，它會再驗一次 ROADMAP）。
   - `REFUSED … has a version that is neither your checkout's HEAD version nor your working-tree content`：分支上的 ROADMAP 是在別處寫的。請使用者先 `/exo:project land project <name> "<里程碑>"`，讓 `main` 帶上分支版，再重跑本指令；不要自己繞過。
4. 提醒：檔案留在工作樹、已與 `project/<name>` 相同，隨 `land project` 落到 `main`。

## diff

1. 跑 `EXO_ROADMAP diff <name>`（比對工作樹的 `projects/<name>/ROADMAP.yaml` 與 `project/<name>` 上的版本；另給 `--file <檔>` 可比對別的檔）。
2. 依輸出列出 `added`／`removed`／`moved`／`changed`／`now` 前後；`result: noop` 回「ROADMAP 無變化」。`old_status: absent` 代表分支上還沒有 ROADMAP。
3. 唯讀：不寫任何檔、不提問、不呼叫其他子命令。
