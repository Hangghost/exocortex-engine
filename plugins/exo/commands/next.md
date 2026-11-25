---
description: 單專案未決盤點＋最多 3 波推進（唯讀）：ROADMAP、PROJECT.md 的下一步與待辦合成一張清單，每條一個標籤，結尾只問「採用第 1 波？」
argument-hint: "[<name>]"
---

# /exo:next

需要的引擎版本：`0.5.0`

引數：`$ARGUMENTS`

分組、合併、計數、帳本衛生、來源總表一律由 CLI（`next_items show`）確定性算好；這份指令只負責**判讀標籤、排波次、呈現**。
**唯讀**：不寫任何檔、不執行任何 git 寫操作、不讀外部 tracker、不自己讀 `PROJECT.md`、`ROADMAP.yaml`、`inbox/todos.md` 補資料（條目原文用 JSON 的 `raw`）。

## 0. 前置

1. **取得執行器**：讀工作區根目錄（`git rev-parse --show-toplevel`）的 `.exocortex/instance.toml` 的 `[executor]`（同 `/exo:project`）。檔案不存在或沒有 `[executor].python`：**停下**，請使用者建立，不要猜路徑、不要改用 `python`／`uv run`。
   - 以下 `EXO` 代表：在工作區根目錄執行，先設好 `env` 的每個環境變數，再跑 `<python> -m exocortex_engine.next_items`。例：`PYTHONUTF8=1 ./.venv/Scripts/python.exe -m exocortex_engine.next_items show alpha --json`。
2. **版本檢查**：跑 `EXO version`，第一行 `version: <x.y.z>` 必須與本檔開頭「需要的引擎版本」**完全相等**。不相等或跑不起來：**停下**，印出兩邊版本與兩組更新指令（套件：把工作區 `pyproject.toml` 的引擎依賴 tag 改成 `v<本檔版本>` 後 `uv sync --active`；plugin：`claude plugin marketplace update exocortex-engine` 再 `claude plugin update exo@exocortex-engine`）。
3. **讀輸出**：一律加 `--json`。`result` 為 `ok|rejected|error|usage-error`；退出碼 0 成功、1 被拒、2 用法錯誤。

## 1. 解析對象

跑 `EXO target [<name>] --json`：

- exit 0 → 對象是 `name`。`defaulted: true` 時輸出第一行寫「對象：<name>（未指定，唯一的 active 專案）」，否則「對象：<name>」。
- exit 1 且有 `suggestions` → 回「找不到專案 `<輸入>`。你是指 `<x>` 嗎？」後**停下**；**不得**以最接近者代為執行。
- exit 1 且有 `active`（沒給名字、active 專案不是剛好一個）→ 列出 `active` 各名稱，請使用者以 `/exo:next <name>` 重跑，**停下**（這裡不提問）。

## 2. 取資料

跑 `EXO show <name> --json`。之後每一步**只消費這份 JSON**。重要鍵：

- `sources[]`：`{source, state, ref, note?}`，`source` ∈ `roadmap`／`next_step`／`todo`，`state` ∈ `ok`／`absent`／`failed`。
- `groups[]`：`{key, title, kind, state, count, items}`，順序即呈現順序。
- `items[]`：`key`、`kind`（`task`／`next_step`／`todo`）、`title`、`group`、`origin`（如 `roadmap+todo`）、`line`、`clearer`、`phase`、`after`、`after_open`、`now_rank`、`due`、`overdue`、`tag`、`raw`、`also[]`（併入這條的其他來源原文）。
- `lines[]`、`now`、`hygiene[]`、`source_summary[]`、`merged`、`count`、`has_roadmap`、`roadmap_state`、`suggest_synthesize`。

## 3. 判讀（只有這裡由你判斷）

### 3a. 線 → 誰來清

`has_roadmap` 為真時，對 `lines[]` 每條依 `clearer` 文字判一次，對應到 🤔 要我決定／✋ 要我親手做／🤖 可以派工／📨 等外部／⏳ 等時間或資料 之一。ROADMAP 票（`kind: task`）繼承其線的判讀。

### 3b. 每條一個標籤

優先序由高到低：**📅 有期限 > 🤔 要我決定 > ✋ 要我親手做 > 🤖 可以派工 > 📨 等外部 > ⏳ 等時間或資料**。

- `tag` 為 `📅` 的條目一律是 📅，**不可改判**；寫成 `📅<MM/DD><誰來清的 emoji>`（例 `📅03/05✋`），後綴是補註、不是第二個標籤。`overdue: true` 時在說明註明「已過」。
- 其餘：ROADMAP 票取 3a 的繼承值；`next_step`／`todo` 依 `raw` 判讀（`raw` 沒有線索時判 ✋）。
- **一句說明**：標題後以「 — 」接一句 ≤ 30 字，**只取自** `raw` 與 `also[].raw`；只有標題、沒有其他資訊就省略，不硬湊、不讀別的檔補背景。

### 3c. 波次

- 最多 **3 波**，每波固定四欄、順序不換：**我決定／我動手／派出去／解鎖什麼**；某欄沒有條目寫「無」。「解鎖什麼」引用做完本波後變得可動的 key。
- 排序：`after_open` 非空的條目，不得排在其前置所在波次之前（同一波也不行）；有期限者依日期先排；`now_rank` 有值的票依序優先；可以派工的盡量並進同一波。
- 某波「我決定」有 3 條以上時，依主題收成至多 3 場：「<主題>：<key>、<key> → `/exo:discuss`」；主題只作用在這一欄，不回頭改盤點分組。
- 未排入任何一波者進**停車場**，只列 key 與標題。

## 4. 呈現

依序輸出，空段省略：

```
對象：<name>
圖例：📅 有期限｜🤔 我決定｜✋ 我動手｜🤖 可派工｜📨 等外部｜⏳ 等時間／資料
<sources 中 state=failed 者逐條「⚠️ <source>：讀取失敗（<note>）」——不得省略，也不得與「沒有條目」同外觀>

🧭 線 → 誰來清（has_roadmap 時）
  <line id> → <emoji>（<clearer 摘要>）

🧹 帳本衛生（hygiene 非空時；照抄 rule、objects、detail）
  - now-not-open：<objects>（<detail>）
  - item-overdue：<objects>

📊 來源總表（照抄 source_summary 與 merged，不自己數）
  | 來源 | 件數 | 組成 |
  | ROADMAP | <count> | <breakdown：label n、…> |
  | 下一步 | <count> | — |
  | 待辦 | <count> | <breakdown> |
  | 確定合併 | −<merged> | |
  | 合計 | <count（頂層）> | |
  <state=absent 的列件數寫「未啟用」、failed 寫「讀取失敗」，不寫 0>

📋 盤點（共 <count> 條）
## <groups[].title>（<groups[].count>）
  - <標籤> <title> — <一句說明>  [<origin>]
  ...

🚦 推進
第 1 波
  我決定：…
  我動手：…
  派出去：…
  解鎖什麼：…
第 2 波 …
第 3 波 …
🅿️ 停車場：<key 標題>、…

<suggest_synthesize 為真：一行「條目已達 <synthesize_at> 條，建議 `/exo:roadmap <name> synthesize` 建立 ROADMAP」>

採用第 1 波？
```

- 組的順序與組名**照抄** `groups`，不改名、不合併、不新增組。
- 盤點條數與「合計」都必須等於頂層 `count`。
- `origin` 含 `+` 的條目是 CLI 已確定合併的同一工作項，照常列一條並保留標註。

## 5. 採用（唯一的提問）

使用者回答採用時，**本指令不寫任何檔**，只輸出下一步指令與第 1 波條目：

- `has_roadmap` 為真：`/exo:roadmap <name> revise`，並附「把 `now` 更新為第 1 波：<keys>」。
- 否則：`/exo:project update <name>`，並附「把 `## 下一步` 頂端改為第 1 波：<條目>」。`roadmap_state` 為 `failed` 時另加一行：ROADMAP 讀取失敗，先以 `/exo:roadmap <name> revise` 修好。

不採用 → 結束，不追問。

## Guardrails

- 唯讀：不寫檔、不執行 git 寫操作、不另存分波快照。
- 只消費 CLI JSON：分組、合併、計數、衛生不重算；看起來重複但 CLI 沒合併的兩條，兩條都留著。
- 不讀外部 tracker；整個指令只問一個問題；波次不超過 3。
- 帳本衛生只列出，不順手修：交給 `/exo:roadmap <name> revise` 或 `/exo:project update <name>`。
