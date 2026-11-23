---
description: >
  離線批次決策的交換契約：deterministic renderer 產零依賴單檔 HTML → 人在瀏覽器逐條標記 →
  export decisions JSON → 各佇列自備的 applier 套用。涵蓋「批量可延遲 vs 單一即時」的路由判準、
  decisions schema v1、applier 的 dry-run／fail-fast／冪等三項義務，以及用引擎 CLI
  （`render`／`validate`）產頁與驗檔的方法。Use when: /exo:decision-ui、要為某個待處理佇列做人工
  分流介面、猶豫該做成按鈕還是決策頁、要接新的決策頁 consumer、或排查 decisions JSON 被 applier 拒收。
argument-hint: "[render <spec.json> --out <page.html> | validate <decisions.json> --kind <kind>]"
---

# /exo:decision-ui：離線批次決策交換

需要的引擎版本：`0.4.0`

引數：`$ARGUMENTS`

## 呼叫引擎（先做一次）

- 讀工作區根目錄的 `.exocortex/instance.toml` 的 `[executor]`（格式同 `/exo:project`）。以下 `PY`
  代表：在工作區根目錄執行，先設好 `env` 的每個環境變數，再跑 `<python> -m exocortex_engine.<模組>`。
- 跑 `PY` 的 `content version`（模組 `exocortex_engine.content`），第一行 `version: <x.y.z>` 必須與本檔
  開頭「需要的引擎版本」**完全相等**。
- 檔案不存在、沒有 `[executor].python`、或版本不相等／跑不起來：停下，照 `/exo:content` 的前置段
  告訴使用者怎麼修（建立 instance.toml、或更新套件／plugin）。**不要猜路徑、不要改用裸 `python`／
  `uv run`**——Windows 上裸 `python` 常是商店空殼。
- 方法論（下面「什麼時候用」到「接新 consumer」）不需要引擎；只有產頁與驗檔需要。

## 什麼時候用這個模式

先答兩個問題：**項目有幾條？判斷能不能延後？**

| 情境 | 選擇 |
|---|---|
| 項目多（十幾條以上）且判斷可以分次做完 | **決策檔**（本模式） |
| 單一物件，而且要立刻看到結果 | 按鈕或指令（直接做，不經決策檔） |
| n < 10 且要即時 | **不要用本模式**——多一次檔案往返只是變慢 |

決策檔獨有、按鈕給不了的三件事：

1. **export 出的 JSON 是可審計的中間 artifact**——套用前看得到、可存查、可重跑
2. **applier 能先 dry-run**——按鈕一按就生效，沒有預覽
3. **判斷與執行可以在不同機器上發生**——在 A 機標記、把 JSON 帶到 B 機套用

反過來按鈕獨有的是即時回饋與零檔案往返。

## 三段結構

```
producer  →  render（引擎 CLI 或 render_decision_page）→  單檔 HTML（零外部依賴）
human     →  瀏覽器逐條標記（localStorage 續標）→ Export decisions JSON
applier   →  consumer 自備，套用決策
```

引擎只提供前兩段的產頁與驗檔；applier 必然綁著各佇列的實際操作，由 consumer 自己寫。

### 產頁：`render`

寫一份 spec JSON（UTF-8），再跑 `PY` 的 `decision_ui render <spec.json> --out <page.html>`：

```json
{"kind": "inbox_triage",
 "title": "Inbox triage",
 "id_key": "id",
 "generated_on": "2026-11-23",
 "vocabulary": ["keep", {"action": "drop", "label": "丟棄"},
                {"action": "later", "label": "之後", "requires_note": true, "hint": "寫明何時"}],
 "suggested_key": "suggested",
 "filters": ["bucket", {"key": "stars", "label": "stars", "sort": true}],
 "allow_bulk_adopt": false,
 "items": [{"id": "a1", "label": "標題", "sublabel": "次要資訊", "body": "主要內容\n保留換行",
            "url": "https://example.com/a1", "scope": {"row": 1},
            "bucket": "docs", "stars": 12, "suggested": "keep"}]}
```

- 必填：`kind`、`title`、`id_key`、`generated_on`、`vocabulary`、`items`；選填：`suggested_key`、
  `filters`、`allow_bulk_adopt`（預設 true）。spec 有不認得的欄位時會被拒，不會默默忽略。
- 第一行 `result: ok|invalid|error`；`invalid` 時後面逐行列出問題，**一個檔都不寫**（exit code `1`）。
- Python 呼叫端也可以直接 `from exocortex_engine.decision_ui import render_decision_page`，參數與
  spec 欄位一一對應；產出的 HTML 兩種路徑逐位元組相同。
- item 顯示契約（除 id 外皆選填）：`label`（標題）／`sublabel`（次要資訊）／`body`（主要內容，保留
  換行）／`url`（有值則 label 變連結）／`scope`（原樣帶進 decisions）；`filters` 與
  `suggested_key` 指向 item 上的欄位。

兩條容易踩的硬約束：

- **零外部引用。** 產物必須能被 scp／AirDrop／email 搬到任何機器、以 `file://` 離線開啟。任何
  CDN、外部字型、`fetch` 都會讓它在他機失效，而「A 機判斷、B 機執行」正是這個模式存在的理由。
- **不得讀時鐘。** renderer 要 deterministic（同輸入 byte-identical），所以 `generated_on` 是輸入、
  只用來導 localStorage key；決策檔的 `generated_at` 由頁面在 export 當下用 JS 蓋章，不影響頁面 bytes。

決策頁與 spec 是一次性的工作產物：放在使用者指定的位置或系統暫存目錄，**不放進 `contexts/`、不
commit**。把頁面路徑交給使用者就好。

### decisions JSON schema v1

```json
{"schema_version": 1, "kind": "<producer identifier>", "generated_at": "<ISO-8601>",
 "decisions": [{"id": "<producer-unique key>", "scope": {}, "action": "<vocabulary 成員>", "note": null}]}
```

- `id` 由 producer 定義，必須能被 applier 唯一定位
- `scope` 是 producer 專屬的自由 object，applier 自行解讀（型別檢查因此落在 applier）
- **`kind` 是防呆關鍵**：把 A 佇列的決策檔餵給 B 佇列的 applier，靠這個欄位擋

### 驗檔：`validate`

`PY` 的 `decision_ui validate <decisions.json> --kind <kind> [--vocab a,b,c] [--note-required x,y]`：

- `--kind` 必填，就是這個 applier 只吃的那個 kind；`--vocab` 省略時不檢查 action 是否在詞彙內；
  `--note-required` 列出「選了就必須填 note」的 action。
- 合法：`result: ok` 與 `decisions: <N>`，exit code `0`；任何錯誤：`result: invalid`，每個問題一行，
  exit code `1`（一次列出全部，不是遇到第一個就停）。
- applier 自己是 Python 時，可直接 `from exocortex_engine.decision_ui import validate`，介面同上
  （`validate(doc, *, expected_kind, vocabulary=None, note_required=())`，回傳錯誤清單，空 = 合法，不拋例外）。

### Applier 的三項義務

applier 由 consumer 自備，但每個都 SHALL 做到：

1. **`--dry-run`**——印出逐筆計畫，什麼都不寫
2. **fail-fast 驗證**——任何一筆有問題，就一筆都不套並回非零 exit code（先跑上面的驗檔）
3. **冪等**——目標已不存在（已被套用過）計入 absent 並略過，不報錯；同一份 JSON 連跑兩次不得重複生效

這三項是敢一次套用上百筆的全部原因。applier 若會改工作區的 `contexts/` 檔案，寫入照常經
`/exo:content`（見 `docs/exo-content.md`），不要自己用 git 指令寫分支。

## 操作節奏

1. 產頁 → 把路徑交給使用者
2. 使用者逐條標記（localStorage 續標，可以分次做完）
3. Export → **立刻 dry-run**（這一步別省，它是唯一的預覽）
4. 確認計畫無誤 → 正式套用

**提醒使用者**：選擇狀態綁本機瀏覽器，換機器或換瀏覽器不會帶著走，要在標記的那台按 Export。

## 接新 consumer 的檢查表

- [ ] vocabulary 設計：動詞要能對應到 applier 的實際操作，不要留「之後再說」這種無法執行的選項
- [ ] `allow_bulk_adopt` 該開還該關？**只要該佇列的誤判有外部後果（貼上對外系統、送出郵件、刪資料），就關掉**——讓意圖變成結構強制（關掉後批次控制項與對應 JS 整條從 HTML 消失），不要靠註解提醒後人
- [ ] `suggested_key` 有沒有可信的建議來源？沒有就別傳，省得人被錯誤預選帶著走。**不傳 `suggested_key` 會連帶關掉批次鈕**，這是刻意的耦合：想要批次，就得先有可信的建議
- [ ] applier 三項義務逐條對照

## 前人踩過的坑

- **決策檔只帶「決定」，顯示用的資料不會自動流到目標。** applier 需要標題、摘要之類，得自己去讀；否則落地的是裸 URL。
- **目標落點要驗，不能靠 regex 猜。** 找不到預期的區段時要 fail loud，不要靜默 append 到檔尾。
- **多值輸入借 `note` 欄位，不要擴 schema。** 例如「合併到哪一篇」：把 `requires_note` 掛在該 action 上，頁面擋空值，applier 再擋語意（目標存在、不是自己）。
- **改 frontmatter 一律 line-level 手術，禁止 `load` 再 `dump` 回寫。** 未加引號且含「空格＋井號」的值，YAML 會把井號後面當註解丟掉，往返一次就真的沒了。
- **先全部算好、驗過，再進寫入迴圈。** 這樣 fail-fast、dry-run、冪等共用同一條路徑——dry-run 只是「算完不寫」，不是另一條分支。
- **不要在處置結果裡寫時間戳。** 同一份 decisions 連跑兩次要 byte-identical，冪等才是結構保證而不是口號；時間由 git 歷史回答。
- **決策若會搬動檔案，定位要查所有可能的落點**，否則重跑時會把「已套用」誤判成「不存在」；順序固定為先寫內容、再搬。
- **「零外部依賴」的測試要斷言會發出請求的引用**（`<script src>`、`<link>`、`fetch(`），不是「頁面裡不准出現 http」——item 內容本來就可能帶 URL。
- **localStorage 綁瀏覽器**，頁首要明講，否則使用者會以為標記跟著檔案走。
- **不得已要對真實資料驗收時，複製一份來跑**：dry-run 只證明「不寫」，證明不了「寫得對」。

## 反合理化

| Rationalization | Reality |
|---|---|
| 「這個佇列只有 8 條，順手也做成決策頁吧」 | n < 10 且要即時 → 檔案往返只是變慢，直接做按鈕或指令 |
| 「加個 CDN 的表格套件會好看很多」 | 產物就再也不能離線／跨機開了，模式的核心價值歸零 |
| 「renderer 裡直接讀今天日期比較方便」 | 直接違反 determinism；日期是輸入不是副作用（`generated_on`） |
| 「批次採用建議很方便，先開著吧」 | 誤判有外部後果的佇列，方便正是風險本身；旗標關掉才是結構強制 |
| 「applier 就我自己用，dry-run 之後再補」 | dry-run 是唯一的套用前預覽，之後永遠不會補 |
| 「decisions 檔是我剛 export 的，不用驗 kind」 | 目錄裡躺著三份不同佇列的 JSON 時，你會餵錯 |
