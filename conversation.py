"""Temporary user-facing context; no disk storage or tool transcripts."""
from tools import redact_secrets


class ConversationSession:
    def __init__(self, max_messages=20):
        if type(max_messages) is not int or max_messages < 2 or max_messages % 2:
            raise ValueError("max_messages must be a positive even integer of at least 2")
        self.max_messages = max_messages
        self._messages = []
        # Feedback targets are session metadata, not conversational messages.
        self.last_experience_id = None
        self.last_experience_task = None

    def recent_messages(self):
        # The agent mutates its request during tool execution, not our history.
        return [dict(message) for message in self._messages]

    def append_turn(self, task, answer):
        self._messages.extend([
            {"role": "user", "content": task},
            {"role": "assistant", "content": answer},
        ])
        self._messages = self._messages[-self.max_messages:]

    def clear(self):
        self._messages.clear()
        self.last_experience_id = None
        self.last_experience_task = None

    def display(self):
        if not self._messages:
            return "Conversation history is empty."
        lines = []
        for message in self._messages:
            text = redact_secrets(message["content"]).replace("\n", " ")
            if len(text) > 300:
                text = text[:287] + " …[truncated]"
            lines.append(f"{message['role']}: {text}")
        return "\n".join(lines)
