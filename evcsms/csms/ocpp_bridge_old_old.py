# csms/ocpp_bridge.py
from django.utils import timezone
from csms.models import ChargePoint, CPCommand
from asgiref.sync import sync_to_async


# ── plain sync helper ───────────────────────────────────────────
def enqueue(cp_id: str, action: str, params: dict):
    """
    Synchronous: insert one command row.
    Called from the REST view (sync code).
    """
    cp = ChargePoint.objects.get(pk=cp_id)
    CPCommand.objects.create(cp=cp, action=action, payload=params)

# ── async helper for the OCPP side ──────────────────────────────
@sync_to_async
def next_for(cp_id: str):
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
    return cmd.action, cmd.payload

