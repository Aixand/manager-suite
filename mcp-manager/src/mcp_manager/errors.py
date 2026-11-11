"""Public operational errors without application dependencies."""


class McpError(Exception):
    def __init__(self, code, message, *, uncertain=False, operation_id=None, recovery=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.uncertain = uncertain
        self.operation_id = operation_id
        self.recovery = recovery

    def to_dict(self):
        return {"code": self.code, "message": self.message, "uncertain": self.uncertain,
                "operation_id": self.operation_id, "recovery": self.recovery}


def require(condition, message, code="validation"):
    if not condition:
        raise McpError(code, message)
