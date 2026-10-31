"""SQL lives here and nowhere else.

Two rules hold across every module in this package.

1. **Every method takes `owner_id` and every statement filters on it.** Phase 1 has no
   login and `owner_id` is always `PHASE1_OWNER_ID`, but writing it in now is what
   makes Phase 2 a middleware change rather than an audit of every query (BRD §6.1).
2. **Rows become schema objects here.** A router never sees an `aiosqlite.Row`, so the
   JSON columns and the integer-as-boolean columns are decoded in exactly one place.
"""

from .audit import AuditRepository
from .conversations import ConversationRepository
from .documents import DocumentRepository
from .matters import MatterRepository
from .messages import MessageRepository

__all__ = [
    "AuditRepository",
    "ConversationRepository",
    "DocumentRepository",
    "MatterRepository",
    "MessageRepository",
]
