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

from csms.models import ChargePoint, Transaction     # your own models
# --------------------------------------------------------------------------

logging.basicConfig(level=logging.INFO)

"""
class SanitizingWS:
    def __init__(self, ws):
        self._ws = ws

    async def recv(self):
        raw = await self._ws.recv()
        if isinstance(raw, str):
            raw = (
                raw
                .replace("TransactionBegin", "Transaction.Begin")
                .replace("TransactionEnd",   "Transaction.End")
            )
        return raw

    async def send(self, msg):
        return await self._ws.send(msg)

    def __getattr__(self, name):
        return getattr(self._ws, name)
"""

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

    # ----------------------------- Heartbeat -------------------------------
    @on("Heartbeat")
    async def on_heartbeat(self):
        return _cr(
            "Heartbeat",
            current_time=datetime.now(timezone.utc).isoformat(),
        )

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

"""
    # ---------------------------- StopTransaction --------------------------
    @on("StopTransaction")
    async def on_stop_transaction(
        self, meter_stop, transaction_id, timestamp, **_
    ):
        tx = await sync_to_async(
            Transaction.objects.filter(pk=transaction_id).first
        )()
        if tx:
            tx.stop_time = timestamp
            tx.latest_wh = meter_stop
            await sync_to_async(tx.save)(update_fields=["stop_time", "latest_wh"])
            print(f"[StopTx] #{transaction_id} → {tx.kwh:.3f} kWh")

        return _cr("StopTransaction", idTagInfo={"status": "accepted"})

"""

# ───────────────────────────── websocket server ───────────────────────────
async def _on_connect(ws, path):
    cp_id = (path.strip("/") or "NO_ID").split("/")[-1]
    sanitized_ws = SanitizingWS(ws)
    await MyChargePoint(cp_id, sanitized_ws).start()
    #await MyChargePoint(cp_id, ws).start()


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

