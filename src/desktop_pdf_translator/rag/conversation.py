"""What a chat question is read with: the document's earlier exchanges.

Stdlib-only: `api/routes/rag.py` imports it at module level, on the boot
path, to hand `_run_ask`'s chain the saved conversation.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ChatTurn:
    """One earlier exchange in the same document's chat."""

    question: str
    answer: str


# Earlier exchanges a question is read with. A follow-up refers back a turn or
# two ("and in Q4?", "when is it payable?"); older turns cost tokens on every
# question and pull the rewrite toward topics the user has left.
MAX_HISTORY_TURNS = 3
