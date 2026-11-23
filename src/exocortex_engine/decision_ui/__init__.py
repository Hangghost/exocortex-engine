"""decision_ui — offline batch-decision exchange.

「一批待處理項目 → 人逐條下判斷 → 機器批次執行」的共用契約，三段：

    producer  →  render_decision_page(...) 產單檔靜態 HTML（零外部依賴）
    human     →  瀏覽器標記（localStorage 續標）→ export decisions JSON
    applier   →  consumer 自備，套用決策；義務見 skill `decision-ui`

本模組只管前兩段的**渲染**與**驗證**：`render.py` 是純函數 renderer（零 I/O、
domain-agnostic），`schema.py` 是 decisions JSON v1 的驗證器。applier 必然是
domain-specific，本模組不提供。

路由判準（何時該用本模式）：項目多且判斷可延遲 → 決策檔；單一物件且要即時看到
結果 → 直接做按鈕或指令。

CLI（不必寫 Python）：`python -m exocortex_engine.decision_ui render|validate`，
見 `__main__.py`。
"""

from .render import render_decision_page
from .schema import SCHEMA_VERSION, validate

__all__ = ["render_decision_page", "validate", "SCHEMA_VERSION"]
