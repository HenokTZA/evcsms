# csms/management/commands/runocpp.py
# --------------------------------------------------------------------------
import asyncio
from datetime import datetime, timezone
from decimal import Decimal
import logging
import json

import websockets
from asgiref.sync import sync_to_async
from django.core.management.base import BaseCommand
from ocpp.routing import on
from ocpp.v16 import ChargePoint as CP, call_result
from ocpp.v16 import call_result as cr
from ocpp.v16 import call as c
from websockets.exceptions import ConnectionClosed
from csms.models import ChargePoint, Transaction, Tenant     # your own models
# --------------------------------------------------------------------------

logging.basicConfig(level=logging.INFO)
logging.getLogger("websockets.server").setLevel(logging.WARNING)
log = logging.getLogger("ocpp")


# ------------------------------------------------------------------------
@sync_to_async
def _get_tenant(ws_key: str) -> Tenant | None:
    try:
        return Tenant.objects.get(ws_key=ws_key.lower())
    except Tenant.DoesNotExist:
        return None


@sync_to_async
def _upsert_cp(tenant: Tenant, cp_id: str,
               vendor: str, model: str, fw: str):
    ChargePoint.objects.update_or_create(
        id=cp_id,
        defaults=dict(
            tenant      = tenant,
            vendor      = vendor,
            model       = model,
            fw_version  = fw,
            status      = "Available",
            name        = cp_id,          # keep simple
            connector_id=0,
        ),
    )
# ------------------------------------------------------------------------



class SanitizingWS:
    def __init__(self, ws):
        self._ws = ws

    async def recv(self):
        raw = await self._ws.recv()

        # first do your context fixes
        if isinstance(raw, str):
            raw = raw.replace("TransactionBegin", "Transaction.Begin") \
                     .replace("TransactionEnd",   "Transaction.End")

        # now try to JSON-parse & inject missing timestamps
        try:
            msg = json.loads(raw)
            # OCPP call messages are arrays: [ messageTypeId, uniqueId, action, payload ]
            if (
                isinstance(msg, list) and
                len(msg) == 4 and
                msg[0] == 2 and  # Call message
                msg[2] == "StopTransaction"
            ):
                payload = msg[3]
                outer_ts = payload.get("timestamp")
                td = payload.get("transactionData", [])
                # for each entry, if no timestamp, inject the outer one
                for entry in td:
                    if "timestamp" not in entry and outer_ts is not None:
                        entry["timestamp"] = outer_ts
                raw = json.dumps(msg)
        except Exception:
            # if parsing/patching fails, just fall back to the original string
            pass

        return raw

    async def send(self, msg):
        return await self._ws.send(msg)

    def __getattr__(self, name):
        return getattr(self._ws, name)



# ───────────────────────── helper to survive lib-renames ───────────────────
def _cr(name: str, **payload):
    """
    Return the correct call_result class for both python-ocpp 0.26 (BootNotification)
    and ≥2.0 (BootNotificationPayload).  Just use _cr("BootNotification", …).
    """
    cls = getattr(call_result, f"{name}Payload", None) or getattr(call_result, name)
    return cls(**payload)


# ──────────────────────────── ChargePoint handler ──────────────────────────
class MyChargePoint(CP):
    """
    # --------------------------- BootNotification --------------------------
    @on("BootNotification")
    async def on_boot_notification(
        self, charge_point_vendor, charge_point_model, **_
    ):
        print(f"[Boot] {self.id}: {charge_point_vendor}/{charge_point_model}")

        return _cr(
            "BootNotification",
            current_time=datetime.now(timezone.utc).isoformat(),
            interval=30,
            status="Accepted",
        )
    """

    """
    @on("BootNotification")
    async def on_boot_notification(self, charge_point_vendor, charge_point_model, **_):
        print(f"[Boot] {self.id}: {charge_point_vendor}/{charge_point_model}")

        # this is the v16 call-result object you just imported:
        return cr.BootNotification(
            current_time=datetime.now(timezone.utc).isoformat(),
            interval=30,
            status="Accepted",
        )
    """

    @on("BootNotification")
    async def on_boot_notification(
        self, charge_point_vendor, charge_point_model,
        chargePointSerialNumber=None, firmwareVersion=None, **_
    ):
        print(f"[Boot] {self.id}: {charge_point_vendor}/{charge_point_model}")

        tenant = await _get_tenant(self.tenant_key)
        if tenant:          # guards against bogus ws_key
            await _upsert_cp(tenant, self.id,
                             charge_point_vendor,
                             charge_point_model,
                             firmwareVersion or "")

        return cr.BootNotification(
            current_time=datetime.now(timezone.utc).isoformat(),
            interval=30,
            status="Accepted",
        )




    # ----------------------------- Heartbeat -------------------------------
    @on("Heartbeat")
    async def on_heartbeat(self):
        return _cr(
            "Heartbeat",
            current_time=datetime.now(timezone.utc).isoformat(),
        )

    """
    # -------------------------- StatusNotification ------------------------
    @on("StatusNotification")
    async def on_status_notification(self, connector_id, status, **_):
        await sync_to_async(ChargePoint.objects.update_or_create)(
            id=self.id,
            defaults={
                "name": self.id,
                "connector_id": connector_id,
                "status": status,
            },
        )
        print(f"[Status] {self.id} c{connector_id} → {status}")
        return _cr("StatusNotification")
    """

    # ------------------------------------------------------------------------
    # 2. live-status updates from the charge-point
    # ------------------------------------------------------------------------
    @on("StatusNotification")
    async def on_status_notification(self, connector_id: int, status: str, **_):
        # update only the live fields – don't touch tenant, name, etc.
        await sync_to_async(ChargePoint.objects.filter(id=self.id).update)(
            connector_id=connector_id,
            status=status,
            updated=datetime.now(timezone.utc),
        )

        print(f"[Status] {self.id} c{connector_id} → {status}")
        return _cr("StatusNotification")


    # ------------------------------ DataTransfer ---------------------------
    @on("DataTransfer")
    async def on_data_transfer(self, vendor_id, **_):
        status = "Accepted" if vendor_id == "generalConfiguration" else "Rejected"
        return _cr("DataTransfer", status=status)

    # ------------------------------- Authorize -----------------------------
    @on("Authorize")
    async def on_authorize(self, id_tag, **_):
        return _cr("Authorize", id_tag_info={"status": "Accepted"})



    @on("StartTransaction")
    async def on_start_transaction(
        self,
        id_tag: str,
        meter_start: int,
        timestamp: str,
        **_
    ):
        # pick your own transaction id logic; here's a simple auto-increment:
        @sync_to_async
        def _next_tx_id():
            last = Transaction.objects.order_by("-tx_id").first()
            return (last.tx_id if last else 0) + 1

        tx_id = await _next_tx_id()

        await sync_to_async(Transaction.objects.create)(
            tx_id=tx_id,
            cp_id=self.id,
            user_tag=id_tag,
            start_wh=meter_start,
            latest_wh=meter_start,
            start_time=timestamp,
        )
        print(f"[StartTx] #{tx_id} on {self.id} meterStart={meter_start}Wh")

        return _cr(
            "StartTransaction",
            transaction_id=tx_id,
            id_tag_info={"status": "Accepted"},
        )


    @on("StopTransaction")
    async def on_stop_transaction(
        self,
        meter_stop: int,
        transaction_id: int,
        timestamp: str,
        reason: str | None = None,
        id_tag: str | None = None,
        transaction_data: list | None = None,
        **_
    ):
        tx = await sync_to_async(
            Transaction.objects.filter(pk=transaction_id).first
        )()
        if tx:
            tx.stop_time = timestamp
            tx.latest_wh = meter_stop
            await sync_to_async(tx.save)(
                update_fields=["stop_time", "latest_wh"]
            )
            print(f"[StopTx] #{transaction_id} → {tx.kwh:.3f} kWh")

        return _cr("StopTransaction", id_tag_info={"status": "Accepted"})



    # ----------------------------- MeterValues -----------------------------
    @on("MeterValues")
    async def on_meter_values(
        self,
        connector_id: int,
        meter_value: list,
        transaction_id: int | None = None,
        **_,
    ):
        tx = await sync_to_async(
            Transaction.objects.filter(pk=transaction_id).first
        )()
        if not tx:
            return _cr("MeterValues")

        energy_wh = None
        for sample in meter_value:
            for sv in sample.get("sampledValue", []):
                if sv.get("measurand") == "Energy.Active.Import.Register":
                    energy_wh = Decimal(sv["value"])
                    break

        if energy_wh is not None:
            if tx.start_wh is None:
                tx.start_wh = energy_wh
            tx.latest_wh = energy_wh
            await sync_to_async(tx.save)(update_fields=["start_wh", "latest_wh"])
            print(f"[Meter] tx={transaction_id} energy={energy_wh} Wh")

        return _cr("MeterValues")




# csms/management/commands/runocpp.py
"""
async def _on_connect(websocket, path):

    # trim leading slash, split into segments
    parts = path.lstrip("/").split("/")

    # must be exactly ["api","v16", ws_key, cp_id]
    if len(parts) != 4 or parts[0:2] != ["api", "v16"]:
        await websocket.close(code=1008, reason="Bad URL")
        return

    _, _, ws_key, cp_id = parts
    ws_key = ws_key.lower()

    # wrap and start your charge-point
    sanitized = SanitizingWS(websocket)
    charge_point = MyChargePoint(cp_id, sanitized)
    await charge_point.start()
"""

"""
async def _on_connect(websocket, path):   # <- two parameters again
    # path looks like: "/api/v16/<ws_key>/<cp_id>"
    parts = path.lstrip("/").split("/")
    if parts[:2] != ["api", "v16"] or len(parts) != 4:
        await websocket.close(code=1008, reason="Bad URL")
        return

    _, _, ws_key, cp_id = parts
    ws_key = ws_key.lower()

    sanitized = SanitizingWS(websocket)
    await MyChargePoint(cp_id, sanitized).start()

    cp = MyChargePoint(cp_id, sanitized)

    try:
        await cp.start()                     # <= this returns only when the socket dies
    except ConnectionClosed as e:
        # peer closed the socket – *not* an error for us
        log.info("[%s] WebSocket closed (%s %s)", cp.id, e.code, e.reason or "")
    except Exception:
        # real bug – keep the traceback but don’t kill the whole server
        log.exception("[%s] unexpected exception", cp.id)
"""

"""
async def _on_connect(websocket, path):
    # Expect /api/v16/<ws_key>/<cp_id>
    parts = path.lstrip("/").split("/")
    if parts[:2] != ["api", "v16"] or len(parts) != 4:
        await websocket.close(code=1008, reason="Bad URL")
        return

    _, _, ws_key, cp_id = parts
    ws_key = ws_key.lower()

    cp = MyChargePoint(cp_id, SanitizingWS(websocket))
    cp.tenant_key = ws_key                # <- used in Boot handler above

    try:
        await cp.start()                  # runs until socket closes
    except ConnectionClosed:
        pass                              # peer hung-up – fine
    except Exception:
        log.exception("[%s] unexpected error", cp.id)
"""

# ------------------------------------------------------------------------
# 1. connection entry-point  (/api/v16/<ws_key>/<cp_id>)
# ------------------------------------------------------------------------
async def _on_connect(websocket, path):
    # expected path: /api/v16/<ws_key>/<cp_id>
    parts = path.lstrip("/").split("/")
    if len(parts) != 4 or parts[:2] != ["api", "v16"]:
        await websocket.close(code=1008, reason="Bad URL")
        return

    _, _, ws_key, cp_id = parts
    ws_key = ws_key.lower()

    # ── ❶ who owns this key? ────────────────────────────────────────────
    tenant = await sync_to_async(
        lambda k: Tenant.objects.filter(ws_key=k).first()
    )(ws_key)
    if tenant is None:
        await websocket.close(code=1008, reason="Unknown tenant key")
        return

    # ── ❷ make sure the CP row exists & is linked to that tenant ───────
    def _ensure_cp():
        cp, created = ChargePoint.objects.get_or_create(
            id=cp_id,
            defaults={"name": cp_id, "tenant": tenant},
        )
        # if the row pre-exists but has no tenant yet → attach it now
        if cp.tenant_id is None:
            cp.tenant = tenant
            cp.save(update_fields=["tenant"])
        return cp

    await sync_to_async(_ensure_cp)()

    # ── ❸ start the OCPP handler ────────────────────────────────────────
    sanitized = SanitizingWS(websocket)
    cp = MyChargePoint(cp_id, sanitized)
    cp.tenant = tenant                # keep reference in the handler
    cp.tenant_key = ws_key

    try:
        await cp.start()              # returns only when the socket closes
    except ConnectionClosed as e:
        log.info("[%s] websocket closed (%s %s)", cp.id, e.code, e.reason or "")
    except Exception:
        log.exception("[%s] unexpected exception", cp.id)



# ───────────────────── Django management-command shell ────────────────────
class Command(BaseCommand):
    help = "Run an OCPP-1.6 CSMS on ws://0.0.0.0:9000"

    def handle(self, *args, **options):
        asyncio.run(self._serve())

    async def _serve(self):
        await websockets.serve(
            _on_connect, host="0.0.0.0", port=9000, subprotocols=["ocpp1.6"]
        )
        self.stdout.write(
            self.style.SUCCESS("🟢  OCPP 1.6 listening on ws://0.0.0.0:9000")
        )
        await asyncio.Future()  # keep the loop alive

