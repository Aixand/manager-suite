"""Stable diagnostic codes shared by the library and CLI."""

from functools import wraps
from typing import Callable, ParamSpec, TypeVar


class PromptError(ValueError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)

    def to_dict(self):
        return {"code": self.code, "message": str(self)}


def require(condition, message, code="validation"):
    if not condition:
        raise PromptError(code, message)


P = ParamSpec("P")
R = TypeVar("R")


def operation(method: Callable[P, R]) -> Callable[P, R]:
    """Normalize filesystem failures at public boundaries without masking domain errors."""

    @wraps(method)
    def call(*args: P.args, **kwargs: P.kwargs) -> R:
        try:
            return method(*args, **kwargs)
        except OSError as error:
            raise PromptError("io", str(error)) from error

    return call


def diagnostic(error: Exception, *, default_code: str = "integrity") -> dict[str, str]:
    """Retain domain codes; classify filesystem and malformed stored-record failures."""
    if isinstance(error, PromptError):
        return error.to_dict()
    return {"code": "io" if isinstance(error, OSError) else default_code, "message": str(error)}
