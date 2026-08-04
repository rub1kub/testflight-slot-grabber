from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Dict, List, Optional


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class PageState(str, Enum):
    BETA_FULL = "beta_full"
    AVAILABLE = "available"
    INVITATION_INVALID = "invitation_invalid"
    BUILD_UNAVAILABLE = "build_unavailable"
    UNEXPECTED_RESPONSE = "unexpected_response"
    RATE_LIMITED = "rate_limited"
    NETWORK_ERROR = "network_error"


@dataclass(frozen=True)
class HttpResponse:
    status_code: int
    final_url: str
    headers: Dict[str, str]
    body: str
    elapsed_ms: int
    diagnostics: Dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class PageObservation:
    state: PageState
    reason: str
    status_code: Optional[int]
    final_url: str
    app_name: Optional[str]
    signals: List[str] = field(default_factory=list)
    body_bytes: int = 0
    elapsed_ms: Optional[int] = None
    checked_at: str = field(default_factory=utc_now)

    def to_dict(self) -> Dict[str, object]:
        result = asdict(self)
        result["state"] = self.state.value
        return result


@dataclass(frozen=True)
class AcceptanceResult:
    success: bool
    accepted: bool
    installed: bool
    dry_run: bool
    reason: str
    details: Dict[str, object] = field(default_factory=dict)
    completed_at: str = field(default_factory=utc_now)

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)
