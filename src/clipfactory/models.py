"""Job states.

Clip:   discovered -> fetched -> transcribed -> rendered
        (or skipped: no permission / duplicate / overlap; failed: error recorded)
Render: draft -> sent -> approved -> posted
        (or rejected; superseded when re-rendered with another hook)
"""

from enum import StrEnum


class ClipStatus(StrEnum):
    DISCOVERED = "discovered"
    SKIPPED = "skipped"
    FETCHED = "fetched"
    TRANSCRIBED = "transcribed"
    RENDERED = "rendered"
    FAILED = "failed"


class RenderStatus(StrEnum):
    DRAFT = "draft"
    SENT = "sent"
    APPROVED = "approved"
    POSTED = "posted"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"
