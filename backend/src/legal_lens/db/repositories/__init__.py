"""SQL lives here and nowhere else.

Two rules hold across every module in this package.

1. **Every method takes `owner_id` and every statement filters on it.** Written that
   way through Phase 1, when there was no login and the value was always
   `PHASE1_OWNER_ID` — which is what made Phase 2 authentication a change to
   `deps.current_owner_id` rather than an audit of every query (BRD §6.1). The
   exception is `users.py`, which owns identity itself and so has nothing above it to
   filter by.
2. **Rows become schema objects here.** A router never sees an `aiosqlite.Row`, so the
   JSON columns and the integer-as-boolean columns are decoded in exactly one place.
"""

from .audit import AuditRepository
from .conversations import ConversationRepository
from .documents import DocumentRepository
from .matters import MatterRepository
from .messages import MessageRepository
from .users import User, UserRepository

__all__ = [
    "AuditRepository",
    "ConversationRepository",
    "DocumentRepository",
    "MatterRepository",
    "MessageRepository",
    "User",
    "UserRepository",
]
