---
description: >
  將任意內容格式化為符合工作區規範的文檔：判斷類型、套用 frontmatter（含導航索引用的 `l0`）、
  訂出 `contexts/<folder>/` 下的路徑與檔名，預覽確認後經 `/exo:content commit` 提交。
  Use when: /exo:doc-formatter、使用者說「整理成文檔」「格式化這段文字」「建立文件」，或貼上外部資料
  想匯入工作區，或說「建立 Work Log／Survey」但未提供格式。
argument-hint: "[<類型：work_log|survey|thought_review|blog>] [<主題或要整理的內容>]"
---

# /exo:doc-formatter：把內容整理成合規文檔

需要的引擎版本：`0.5.0`

引數：`$ARGUMENTS`

## 觸發時機

- 使用者說「整理成文檔」「格式化這段文字」「建立文件」
- 使用者貼上外部資料（網頁、PDF、文字）想要匯入工作區
- 使用者說「建立 Work Log／Survey」但未提供格式

**不在本 skill 範疇**（各有自己的入口，不要在這裡重做一份）：

- 專案進度、`PROJECT.md`、專案的 work_log 段落 → `/exo:project`（`update` 會依契約寫 `projects/<name>/PROJECT.md`
  與 `contexts/work_logs/<date>_<name>_update.md`）
- 討論紀錄 → `/exo:discuss`（有自己的紀錄格式與狀態）
- 建立新的 skill → 用該 skill 作者自己的工具

## 呼叫引擎（落檔與重算索引才需要）

- 讀工作區根目錄的 `.exocortex/instance.toml` 的 `[executor]`（格式同 `/exo:project`）。以下 `EXO` 代表：
  在工作區根目錄執行，先設好 `env` 的每個環境變數，再跑 `<python> -m exocortex_engine.<模組>`。
- 跑 `EXO` 的 `content version`（模組 `exocortex_engine.content`），第一行 `version: <x.y.z>` 必須與本檔
  開頭「需要的引擎版本」**完全相等**。
- 不在 git repo、檔案不存在、沒有 `[executor].python`、或版本不相等／跑不起來：照常整理與預覽，
  最後把完整文檔印在對話裡、說明為何無法落檔與修法（照 `/exo:content` 的前置段）。**不要猜路徑、
  不要改用裸 `python`／`uv run`**。

## 觸發前：釐清三件事

在開始格式化前，確認（已能從內容判斷的就直接說出你的判斷，不要逐項盤問）：

1. **文檔類型？**
   - `work_log` — 任務執行過程、進度追蹤
   - `survey` — 深度調研的筆記與結論
   - `thought_review` — 思考、覆盤、方法論
   - `blog` — 部落格文章草稿與思考產物

2. **存放位置？**（預設見下方命名表，使用者指定時遵從；但引擎只認得 `contexts/<folder>/` 與
   `projects/<name>/`，其他路徑 `/exo:content` 不會提交，見「落檔」）

3. **檔案名稱？**（遵照命名慣例，日期預設今日）

---

## 命名慣例速查

| 類型 | 目錄 | 命名規則 | 範例 |
|------|------|----------|------|
| Work Log | `contexts/work_logs/` | `YYYY-MM-DD_<topic>.md` | `2026-04-24_api-refactor.md` |
| Survey | `contexts/survey_sessions/` | `YYYY-MM-DD_<topic>.md` | `2026-04-24_http-encryption.md` |
| Thought Review | `contexts/thought_review/` | `YYYY-MM-DD_<topic>.md` | `2026-04-24_agent-arch-review.md` |
| Blog | `contexts/blog/` | `YYYY-MM-DD_<topic>.md` | `2026-04-24_llm-context-design.md` |

`<topic>` 用 kebab-case 英數。專案的進度 work_log 檔名是 `YYYY-MM-DD_<專案名>_update.md`，由
`/exo:project update` 產生，不手建。

---

## Frontmatter 規則（四種類型共通）

導航索引 `contexts/<folder>/INDEX.md` 從 frontmatter 讀 `date`、`l0`、`tags`、`type`。**每份文檔 SHALL 含
`l0:`**：單行核心用途摘要，agent 之後靠這一行決定要不要開檔，缺了索引會標 `⚠️ 缺 l0`。

索引以窄版 YAML 讀取，frontmatter 只用簡單形狀，否則 `/exo:content commit` 會拒絕該資料夾：

- 純量、引號純量、單行 `[a, b]`、區塊列表與巢狀 mapping 可用；**不要用** anchor／alias、tag（`!!`）、
  單行 mapping `{a: b}`、tab 縮排、行內時間戳。
- **`l0`、`topic` 等純量內含 `: `（冒號加空格）或 ` #`（空格加井號）時整個值加引號**，否則會被當成
  YAML 錯誤或註解截斷。
- `date` 寫 `YYYY-MM-DD`，不加時間。

---

## 格式規範

### Work Log

```markdown
---
l0: <單行核心用途摘要，精簡>
date: YYYY-MM-DD
---

# YYYY-MM-DD <任務主題>

## 背景

## 過程

## 產出

## 反思
```

---

### Survey Session

frontmatter 除 `l0` 外帶 `date`／`topic`／`tags`：

```markdown
---
l0: <單行核心用途摘要，精簡>
date: YYYY-MM-DD
topic: <主題描述>
tags: [tag1, tag2]
---

# <主題>

## 調研目標

## 發現

## 結論
```

---

### Thought Review

```markdown
---
l0: <單行核心用途摘要，精簡>
date: YYYY-MM-DD
---

# <主題>

## 背景 / 問題

## 分析

## 結論 / 決策
```

---

### Blog

```markdown
---
l0: <單行核心用途摘要，精簡>
date: YYYY-MM-DD
---

# <標題>

## 摘要

<文章主體>
```

---

## 格式化步驟

1. **分析內容**——判斷類型、主題、適合的命名；貼進來的外部資料保留出處（URL 或檔名）寫在內文開頭，
   不要把外部全文當成自己的結論
2. **生成格式化內容**——補齊 frontmatter 與章節；原文沒有的資訊留空白章節或寫「（待補）」，**不要編造**
3. **預覽確認**——展示完整文檔預覽＋目標路徑＋是否會覆寫既有檔案，等使用者回「ok」再寫入；
   回答問題不等於同意寫入
4. **落檔**（見下）

## 落檔

寫入一律經 `/exo:content`（見 `docs/exo-content.md`），不要自己用 git 指令寫 `content/*`、`project/*`
或 `main`：

1. 用 `Write` 把文檔寫到工作樹的 `contexts/<folder>/<檔名>`。同名檔已存在 → 換檔名（日期後加主題
   區分），不覆寫，除非使用者明確要改那一份。
2. 跑 `EXO` 的 `content commit --only <檔案路徑> -m "docs: add <類型> <主題>"`。**`--only` 不可省略**：
   工作樹裡使用者其他還沒想提交的改動不該被一起帶走。訊息含引號等特殊字元時先寫成暫存檔再以
   `$(cat 檔)` 帶入。
3. 依輸出第一行 `result:` 回報：
   - `ok` → 回報 `content/<date>: <sha> <subject>` 那行與 push 結果；提醒「檔案留在工作樹，
     `/exo:content land` 時一起落地」。
   - `rejected` → 把 `refused:`／`REFUSED` 各行原樣轉告並停下（常見：該路徑在分支上已有別人寫的
     不同內容，或 frontmatter 超出上面的簡單形狀）。**不要改用 git 指令硬寫**；檔案留在工作樹，由使用者決定。
   - `push <ref>: failed: …` → 本機已寫入、push 失敗，照實回報，不要重試寫入。

### 導航索引

`commit` 會在目標分支上**自動**重算該資料夾的 `INDEX.md`（及 `INDEX_archive.md`、`INDEX_reviews.md`），
通常不必另外處理；它們是產生物，**不要手改**。

只有在要立刻看工作樹這一份的索引、而且還不打算 `commit` 時，才手動重算：

```
<python> -m exocortex_engine.contexts_index <folder>
```

照上面的 `EXO` 方式執行（`<folder>` 例如 `work_logs`；多個資料夾空白隔開，`--all` 為預設那四個）。第一行 `result: ok|error`；`error` 表示某份 frontmatter 超出簡單形狀（會指名檔案與行號），
該資料夾沒有被寫入。**注意**：工作樹裡與分支上不同的 `INDEX*.md` 會擋住之後的 `land`，
手動重算後若要落地，先 `commit`，或把工作樹那份索引還原。

---

## Guardrails

- 不編造內容；缺的資訊標明缺，不補。
- 唯一的寫入是「落檔」那兩步，而且要在使用者對預覽回「ok」之後。
- 被引擎拒絕時照實轉告，不繞過。
