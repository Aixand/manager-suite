"""Bounded final assistant message, scoped by the adapter to one live turn."""

LIMIT = 250000


class TurnOutput:
    def __init__(self):
        self.identity = None
        self.text = None
        self.boundary = False
        self.truncated = False

    def tool(self):
        # Agents without message IDs separate commentary from their final
        # answer by a tool exchange. Do not concatenate those two messages.
        self.boundary = True

    def append(self, text, identity=None):
        if self.boundary or identity is not None and identity != self.identity:
            self.text, self.truncated = None, False
        self.identity, self.boundary = identity, False
        current = self.text or ""
        self.truncated |= len(current) + len(text) > LIMIT
        self.text = (current + text)[:LIMIT]

    def replace(self, text, identity=None):
        self.text, self.identity, self.boundary = text[:LIMIT], identity, False
        self.truncated = len(text) > LIMIT

    def result(self):
        return {"final_text": self.text, "truncated": self.truncated}
