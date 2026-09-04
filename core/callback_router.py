from dataclasses import dataclass
from typing import Awaitable, Callable, Optional


CallbackHandler = Callable[..., Awaitable[bool]]


@dataclass(frozen=True)
class ExactRoute:
    key: str
    handler: CallbackHandler


@dataclass(frozen=True)
class PrefixRoute:
    prefix: str
    handler: CallbackHandler


class CallbackRouter:
    def __init__(self):
        self._exact: dict[str, CallbackHandler] = {}
        self._prefix: list[PrefixRoute] = []

    def add_exact(self, key: str, handler: CallbackHandler) -> None:
        self._exact[key] = handler

    def add_prefix(self, prefix: str, handler: CallbackHandler) -> None:
        self._prefix.append(PrefixRoute(prefix=prefix, handler=handler))

    def resolve(self, data: str) -> Optional[CallbackHandler]:
        if data in self._exact:
            return self._exact[data]
        for route in self._prefix:
            if data.startswith(route.prefix):
                return route.handler
        return None
