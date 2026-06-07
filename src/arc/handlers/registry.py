from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, ClassVar

from arc.core.results import HandlerOutput, ResolvedInputs


class CheckHandler(ABC):
    """Pure-compute check handler."""

    check_id: ClassVar[str] = ""
    handler_version: ClassVar[str] = ""
    check_grain: ClassVar[str | None] = None

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if getattr(cls, "__abstractmethods__", None):
            return
        if not cls.check_id:
            raise TypeError(f"{cls.__name__} must declare non-empty check_id")
        if not cls.handler_version:
            raise TypeError(f"{cls.__name__} must declare non-empty handler_version")

    @abstractmethod
    def execute(self, inputs: ResolvedInputs, spec_slice: dict[str, Any]) -> HandlerOutput:
        """Same inputs must produce same output."""


HANDLERS: dict[str, CheckHandler] = {}


def register(handler_cls: type[CheckHandler]) -> type[CheckHandler]:
    if not isinstance(handler_cls, type) or not issubclass(handler_cls, CheckHandler):
        raise TypeError("@register expects a CheckHandler subclass")
    if handler_cls.check_id in HANDLERS:
        existing = type(HANDLERS[handler_cls.check_id]).__name__
        raise ValueError(
            f"check_id={handler_cls.check_id!r} already registered by {existing}; "
            "give the new handler a distinct check_id"
        )
    HANDLERS[handler_cls.check_id] = handler_cls()
    return handler_cls


def get_handler(check_id: str) -> CheckHandler:
    try:
        return HANDLERS[check_id]
    except KeyError as exc:
        raise KeyError(f"no handler registered for check_id={check_id!r}; known: {sorted(HANDLERS)}") from exc


def clear_registry() -> None:
    HANDLERS.clear()
