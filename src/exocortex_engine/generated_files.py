"""Which files under ``contexts/<folder>/`` are generated (single source of truth).

The navigation index writer (:mod:`exocortex_engine.contexts_index`), the content
commit (which recomputes these files instead of copying them from the working tree)
and the landing checks all ask this module. The rule covers any direct sub-folder of
``contexts/``, not a list of folders: an index can exist in a folder that is not in the
writer's default list.
"""

from __future__ import annotations

import re

INDEX_NAME = "INDEX.md"
ARCHIVE_NAME = "INDEX_archive.md"
REVIEWS_NAME = "INDEX_reviews.md"

#: Generated aggregate files; never treated as content.
CONTEXTS_INDEX_FILES: frozenset[str] = frozenset({INDEX_NAME, ARCHIVE_NAME, REVIEWS_NAME})

#: ``contexts/<folder>/<generated index>``; group 1 is the folder.
CONTEXTS_INDEX_PATH_RE: re.Pattern[str] = re.compile(
    r"^contexts/([^/]+)/(?:" + "|".join(re.escape(n) for n in sorted(CONTEXTS_INDEX_FILES)) + r")$"
)


def is_contexts_index_path(path: str) -> bool:
    """Whether ``path`` (repository-relative, ``/``-separated) is a generated index."""
    return CONTEXTS_INDEX_PATH_RE.fullmatch(path) is not None
