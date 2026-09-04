from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class ActionResult:
    ok: bool
    message: str = ""
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class Actor:
    actor_id: int
    username: str
    display_name: str
    role: str = "admin"
