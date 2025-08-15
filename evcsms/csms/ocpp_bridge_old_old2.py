# csms/ocpp_bridge.py
from __future__ import annotations

from typing import Any, Dict, Optional, Union
from django.utils import timezone
from asgiref.sync import sync_to_async

from csms.models import ChargePoint, CPCommand

ALLOWED_ACTIONS = {"Reset", "GetDiagnostics", "FirmwareStatusNotification"}


def _normalize_payload(action: str, params: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    p = dict(params or {})
    if action == "Reset":
        t = p.get("type") or p.get("resetType") or "Soft"
        t = "Hard" if str(t).lower() == "hard" else "Soft"
        return {"type": t}
    if action == "GetDiagnostics":
        out: Dict[str, Any] = {}
        for k in ("location", "retries", "retryInterval", "startTime", "stopTime"):
            if k in p and p[k] not in (None, ""):
                out[k] = p[k]
        return out
    if action == "FirmwareStatusNotification":
        status = p.get("status") or p.get("Status") or "Downloaded"
        return {"status": status}
    return p


def enqueue(cp_id: Union[str, int], action: str, params: Optional[Dict[str, Any]] = None) -> int:
    action = str(action).strip()
    if action not in ALLOWED_ACTIONS:
        raise ValueError(f"Unsupported action '{action}'. Allowed: {sorted(ALLOWED_ACTIONS)}")
    payload = _normalize_payload(action, params)
    cp = ChargePoint.objects.get(pk=cp_id)  # adjust to .get(ocpp_id=cp_id) if needed
    cmd = CPCommand.objects.create(cp=cp, action=action, payload=payload)
    return cmd.id


# 👉 Alias expected by views.py
def send_cp_command(cp_id: Union[str, int], action: str, params: Optional[Dict[str, Any]] = None) -> int:
    """Compatibility wrapper so views can import send_cp_command()."""
    return enqueue(cp_id, action, params)


@sync_to_async
def next_for(cp_id: Union[str, int]):
    cmd = (
        CPCommand.objects
        .filter(cp_id=cp_id, done_at__isnull=True)
        .order_by("created")
        .first()
    )
    if not cmd:
        return None
    cmd.done_at = timezone.now()
    cmd.save(update_fields=["done_at"])
    return {"id": cmd.id, "action": cmd.action, "payload": cmd.payload}


