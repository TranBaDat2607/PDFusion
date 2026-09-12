"""Why a chat question could not be answered (#31).

A failure used to come back as an ordinary answer, "Sorry, I cannot answer this
question due to an error: …", shown in the chat as if the document had said
it. These end the ask job with an `error` event instead.

Stdlib-only: `api/routes/rag.py` imports it at module level, on the boot path,
so the route can tell the failures apart without importing chromadb.
"""


class IndexUnavailableError(RuntimeError):
    """A ready index's chunks can't be read: its collection is gone, or empty.

    The records said the index was ready, so `/rag/ask` accepted the question.
    The route deletes the row, and the document is indexed again.
    """

    def __init__(self, index_id: str):
        super().__init__(f"The chunks of chat index {index_id} are unavailable")
        self.index_id = index_id


class AnswerGenerationError(RuntimeError):
    """The model writing the answer failed. The message is the sentence the
    chat panel shows."""
