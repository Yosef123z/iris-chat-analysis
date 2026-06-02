"""In-memory conversation history manager."""

import logging
from typing import Dict, List

logger = logging.getLogger(__name__)


class ConversationManager:
    """Manages conversation history for multiple sessions."""

    def __init__(self, limit: int = 10):
        self.limit = limit
        self._memory: Dict[str, List[Dict[str, str]]] = {}

    async def get_history(self, session_id: str) -> List[Dict[str, str]]:
        return list(self._memory.get(session_id, []))

    async def update_history(
        self, session_id: str, user_msg: str, assistant_msg: str
    ) -> None:
        history = list(self._memory.get(session_id, []))
        history.append({"role": "user", "content": user_msg})
        history.append({"role": "assistant", "content": assistant_msg})

        max_messages = self.limit * 2
        if len(history) > max_messages:
            history = history[-max_messages:]
            logger.debug(
                "Trimmed history for session %s to %d messages",
                session_id,
                max_messages,
            )

        self._memory[session_id] = history

    async def clear_session(self, session_id: str) -> None:
        self._memory.pop(session_id, None)
        logger.info("Cleared history for session %s", session_id)

    async def get_all_sessions(self) -> List[str]:
        return list(self._memory.keys())
