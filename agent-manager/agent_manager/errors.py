"""Shared exceptions without dependencies on Manager or provider services."""


class Error(Exception):
    pass


class BusyError(Error):
    """An operation has not acquired its resource; no launch was submitted."""
    code = "busy"
    retryable = True

    def __init__(self, key):
        super().__init__(f"Busy: {key}; retry after the active operation finishes")


class OperationError(Error):
    def __init__(
        self, code, message, *, uncertain=False, operation_id=None, recovery=None
    ):
        super().__init__(message)
        self.code, self.message, self.uncertain = code, message, uncertain
        self.operation_id, self.recovery = operation_id, recovery or {}

    def to_dict(self):
        return dict(
            code=self.code,
            message=self.message,
            uncertain=self.uncertain,
            operation_id=self.operation_id,
            recovery=self.recovery,
        )


# Structured errors shared by native observations and integrations.
ResourceError = OperationError
