# ─── imports ──────────────────────────────────────────────────────────────
import asyncio
from datetime import datetime, timezone
from ocpp.routing import on

from asgiref.sync import sync_to_async           # ←  NEW
from django.core.management.base import BaseCommand
from django.db import models

import websockets
from ocpp.v16 import call_result, ChargePoint as CP
from csms.models import ChargePoint, Transaction

# ─── ChargePoint subclass ─────────────────────────────────────────────────
class MyChargePoint(CP):

    # ------------------------------------------------------------------ Boot
    @on("BootNotification")                       # ← match CP payload exactly
    async def on_boot_notification(self, charge_point_vendor, charge_point_model, **_):
        print(f"[Boot] {self.id}: {chargePointVendor}/{chargePointModel}")

        return call_result.BootNotificationPayload(
            currentTime=datetime.now(timezone.utc).isoformat(),
            interval=30,
            status="accepted",                   # lower-snake for ocpp-2.0.0
        )

    # ------------------------------------------------------------ CP status
    @on("StatusNotification")                     # ← camel-case again
    async def on_status_notification(self, connector_id, status, **_):
        await sync_to_async(ChargePoint.objects.update_or_create)(
            id=self.id,
            defaults={
                "name": self.id,
                "connector_id": connectorId,
                "status": status,
            },
        )
        print(f"[Status] {self.id} c{connectorId} → {status}")
        return call_result.StatusNotificationPayload()

    # -------------------------------------------------------------- Heartbeat
    @on("Heartbeat")
    async def on_heartbeat(self):
        return call_result.Heartbeat(
            current_time=datetime.now(timezone.utc).isoformat()
        )

    # ----------------------------------------------------------- DataTransfer
    @on("DataTransfer")
    async def on_data_transfer(self, vendorId, **_):
        status = "accepted" if vendorId == "generalConfiguration" else "rejected"
        return call_result.DataTransferPayload(status=status)

    # -------------------------------------------------------------- Authorize
    @on("Authorize")
    async def on_authorize(self, idTag, **_):
        return call_result.AuthorizePayload(idTagInfo={"status": "accepted"})

    # -------------------------------------------------- StartTransaction
    @on("StartTransaction")
    async def on_stop_transaction(self, meter_stop, transaction_id, timestamp, **_):
        @sync_to_async
        def _create_tx():
            next_id = (
                (Transaction.objects.order_by("-tx_id").first() or models.SimpleLazyObject(lambda: {"tx_id": 0}))
                ["tx_id"]
                + 1
            )
            Transaction.objects.create(
                tx_id=next_id,
                cp_id=self.id,
                user_tag=idTag,
                start_wh=meterStart,
                latest_wh=meterStart,
                start_time=timestamp,
            )
            return next_id

        tx_id = await _create_tx()
        print(f"[StartTx] #{tx_id} on {self.id} meterStart={meterStart}Wh")

        return call_result.StartTransactionPayload(
            transactionId=tx_id, idTagInfo={"status": "accepted"}
        )

    # ------------------------------------------------------ MeterValues
    @on("MeterValues")
    async def on_meter_values(self, connectorId, transactionId, meterValue, **_):
        tx = await sync_to_async(Transaction.objects.filter(pk=transactionId).first)()
        if not tx:
            return call_result.MeterValuesPayload()

        energy = None
        for sample in meterValue:
            for sv in sample.get("sampledValue", []):
                if sv.get("measurand") == "Energy.Active.Import.Register":
                    energy = float(sv["value"])

        if energy is not None:
            if tx.start_wh is None:
                tx.start_wh = energy
            tx.latest_wh = energy
            await sync_to_async(tx.save)(update_fields=["start_wh", "latest_wh"])
            print(f"[Meter] tx={transactionId} energy={energy} Wh")

        return call_result.MeterValuesPayload()

    # ----------------------------------------------------- StopTransaction
    @on("StopTransaction")
    async def on_stop_transaction(self, meterStop, transactionId, timestamp, **_):
        tx = await sync_to_async(Transaction.objects.filter(pk=transactionId).first)()
        if tx:
            tx.stop_time = timestamp
            tx.latest_wh = meterStop
            await sync_to_async(tx.save)(update_fields=["stop_time", "latest_wh"])
            print(f"[StopTx] #{transactionId} → {tx.kwh:.3f} kWh")
        return call_result.StopTransactionPayload(idTagInfo={"status": "accepted"})

# ─── WebSocket bootstrap & management command (unchanged) ────────────────
async def on_connect(ws, path):
    cp_id = path.strip("/").split("/")[-1] or "NO_ID"
    await MyChargePoint(cp_id, ws).start()

class Command(BaseCommand):
    help = "Run OCPP-1.6 CSMS WebSocket server on ws://0.0.0.0:9000"

    def handle(self, *args, **opts):
        asyncio.run(self._serve())

    async def _serve(self):
        await websockets.serve(on_connect, "0.0.0.0", 9000, subprotocols=["ocpp1.6"])
        self.stdout.write(self.style.SUCCESS("🟢  OCPP 1.6 listening on ws://0.0.0.0:9000"))
        await asyncio.Future()  # run forever

