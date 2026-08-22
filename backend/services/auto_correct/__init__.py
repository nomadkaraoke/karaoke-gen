"""AI auto-correction suggestions for the lyrics review UI.

Stateless suggestion generation: the client posts its current working
segments, the service returns word-level suggestions (replace / delete /
insert_after) referencing word ids. Nothing is applied server-side — the
reviewer accepts or rejects each suggestion in the UI.
"""

from backend.services.auto_correct.service import (
    AutoCorrectService,
    AutoCorrectServiceError,
    get_auto_correct_service,
)

__all__ = [
    "AutoCorrectService",
    "AutoCorrectServiceError",
    "get_auto_correct_service",
]
