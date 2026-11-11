from functools import wraps


class SkillError(Exception):
    """A stable local-operation error, suitable for JSON consumers."""

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
        raise SkillError(code, message)


def sdk_operation(method):
    """Normalize filesystem/input failures without changing public API boundaries."""
    @wraps(method)
    def call(*args, **kwargs):
        try:
            return method(*args, **kwargs)
        except OSError as error:
            raise SkillError("not_found" if isinstance(error, FileNotFoundError) else "io", str(error)) from error
        except (TypeError, ValueError, UnicodeError, RecursionError) as error:
            raise SkillError("validation", str(error)) from error
    return call
