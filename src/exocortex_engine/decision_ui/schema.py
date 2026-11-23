"""decisions JSON schema v1 — 驗證器。

文件形狀：

    {"schema_version": 1,
     "kind": "<producer identifier>",
     "generated_at": "<ISO-8601>",
     "decisions": [{"id": "<producer-unique key>",
                    "scope": {...},
                    "action": "<member of vocabulary>",
                    "note": "<optional string or null>"}]}

`id` 由 producer 定義、必須能被 applier 唯一定位；`scope` 是 producer 專屬的
自由 object，applier 自行解讀；`kind` 讓 applier fail-fast 拒絕餵錯的檔案
（把 A 佇列的決策檔餵給 B 佇列的 applier 這種事，靠這個欄位擋）。

`validate()` 回傳錯誤字串清單，空清單代表合法——**不拋例外**，讓 caller 能一次
呈現所有問題再決定。applier 的義務是「有任何錯誤就一筆都不寫」。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

SCHEMA_VERSION = 1

_TOP_KEYS = ("schema_version", "kind", "generated_at", "decisions")
_DECISION_KEYS = ("id", "action")


def validate(
    doc: Any,
    *,
    expected_kind: str,
    vocabulary: Iterable[str] | None = None,
    note_required: Iterable[str] = (),
) -> list[str]:
    """Return a list of validation errors (empty list == valid).

    `vocabulary` 給定時，每筆 decision 的 action 必須是其成員。
    `note_required` 列出「選了就必須填 note」的 action。
    """
    errors: list[str] = []

    if not isinstance(doc, Mapping):
        return [f"decisions 文件必須是 object，實得 {type(doc).__name__}"]

    for key in _TOP_KEYS:
        if key not in doc:
            errors.append(f"缺少頂層欄位 {key!r}")

    version = doc.get("schema_version")
    if "schema_version" in doc and version != SCHEMA_VERSION:
        errors.append(
            f"schema_version 不符：預期 {SCHEMA_VERSION}，實得 {version!r}")

    kind = doc.get("kind")
    if "kind" in doc and kind != expected_kind:
        errors.append(
            f"kind 不符：本 applier 只吃 {expected_kind!r}，實得 {kind!r}"
            "（這份決策檔是別的 consumer 產的）")

    decisions = doc.get("decisions")
    if "decisions" not in doc:
        return errors  # 缺欄位已記錄，無從逐筆檢查
    if isinstance(decisions, (str, bytes)) or not isinstance(decisions, Sequence):
        errors.append(f"decisions 必須是 list，實得 {type(decisions).__name__}")
        return errors

    allowed = set(vocabulary) if vocabulary is not None else None
    needs_note = set(note_required)
    seen_ids: set[str] = set()

    for i, d in enumerate(decisions):
        if not isinstance(d, Mapping):
            errors.append(f"decisions[{i}] 必須是 object")
            continue
        for key in _DECISION_KEYS:
            if not d.get(key):
                errors.append(f"decisions[{i}] 缺少必填欄位 {key!r}")

        did = d.get("id")
        if did:
            if did in seen_ids:
                errors.append(f"decisions[{i}] id 重複：{did!r}")
            seen_ids.add(did)

        action = d.get("action")
        if action and allowed is not None and action not in allowed:
            errors.append(
                f"decisions[{i}] 未知 action {action!r}"
                f"（合法值：{', '.join(sorted(allowed))}）")

        if action in needs_note and not str(d.get("note") or "").strip():
            errors.append(
                f"decisions[{i}] action {action!r} 需要非空 note（id={did!r}）")

        scope = d.get("scope")
        if scope is not None and not isinstance(scope, Mapping):
            errors.append(f"decisions[{i}] scope 必須是 object 或省略")

    return errors
