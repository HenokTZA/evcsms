# csms/management/commands/runocpp.py
"""
Async OCPP 1.6 CSMS worker – runs as a Django management command.

• Listens on ws://0.0.0.0:9000 with sub-protocol “ocpp1.6”.
• Persists charge-point status + transactions in the Django ORM.
• No external scheduler/threading: everything lives in one asyncio event loop.
"""

import asyncio
from datetime import datetime, timezone

from django.core.management.base import BaseCommand
from django.db import transaction as db_tx

import websockets
from ocpp.routing import on
from ocpp.v16 import ChargePoint as CP, call_result
from ocpp.v16.enums import (
    Action,
    AuthorizationStatus,
    RegistrationStatus,
    DataTransferStatus,
)

from csms.models import ChargePoint, Transaction


# ──────────────────────────────────────────────────────────────────────────────
#  Custom ChargePoint class with handlers
# ──────────────────────────────────────────────────────────────────────────────
"""
class MyChargePoint(CP):
    # ---------- Core ---------------------------------------------------------

    @on(Action.boot_notification)
    async def on_boot_notification(self, charge_point_vendor, charge_point_model, **_):
        print(f"[Boot] {self.id}: {charge_point_vendor}/{charge_point_model}")
        return call_result.BootNotification(
            current_time=datetime.now(timezone.utc).isoformat(),
            interval=30,
            status=RegistrationStatus.accepted,
        )

    @on(Action.status_notification)
    async def on_status_notification(self, connector_id, status, **_):
        with db_tx.atomic():
            ChargePoint.objects.update_or_create(
                id=self.id,
                defaults={
                    "name": self.id,
                    "connector_id": connector_id,
                    "status": status,
                },
            )
        print(f"[Status] {self.id} c{connector_id} → {status}")
        return call_result.StatusNotification()

    @on(Action.heartbeat)
    async def on_heartbeat(self):
        now = datetime.now(timezone.utc).isoformat()
        return call_result.Heartbeat(current_time=now)

    @on(Action.data_transfer)
    async def on_data_transfer(self, vendor_id, **_):
        status = (
            DataTransferStatus.accepted
            if vendor_id == "generalConfiguration"
            else DataTransferStatus.Rejected
        )
        print(f"[DataTx] {self.id} vendor={vendor_id} → {status.value}")
        return call_result.DataTransfer(status=status, data="")

    @on(Action.authorize)
    async def on_authorize(self, id_tag, **_):
        return call_result.Authorize(
            id_tag_info={"status": AuthorizationStatus.Accepted}
        )

    # ---------- Transaction lifecycle ---------------------------------------

    @on(Action.start_transaction)
    async def on_start_transaction(
        self, connector_id, id_tag, meter_start, timestamp, **_
    ):
        with db_tx.atomic():
            next_id = (
                (Transaction.objects.order_by("-tx_id").first() or {"tx_id": 0}).get(
                    "tx_id", 0
                )
                + 1
            )
            Transaction.objects.create(
                tx_id=next_id,
                cp_id=self.id,
                user_tag=id_tag,
                start_wh=meter_start,
                latest_wh=meter_start,
                start_time=timestamp,
            )
        print(f"[StartTx] #{next_id} on {self.id} meterStart={meter_start} Wh")
        return call_result.StartTransaction(
            transaction_id=next_id,
            id_tag_info={"status": AuthorizationStatus.Accepted},
        )

    @on(Action.meter_values)
    async def on_meter_values(self, connector_id, transaction_id, meter_value, **_):
        tx = Transaction.objects.filter(pk=transaction_id).first()
        if not tx:
            return call_result.MeterValues()

        for sample in meter_value:
            for sv in sample.get("sampled_value") or sample.get("sampledValue", []):
                if sv.get("measurand") == "Energy.Active.Import.Register":
                    val = float(sv.get("value", 0))
                    if tx.start_wh is None and val > 0:
                        tx.start_wh = val
                    tx.latest_wh = val
                    tx.save(update_fields=["start_wh", "latest_wh"])
                    print(f"[Meter] tx={transaction_id} energy={val} Wh")
        return call_result.MeterValues()

    @on(Action.stop_transaction)
    async def on_stop_transaction(self, meter_stop, transaction_id, timestamp, **_):
        tx = Transaction.objects.filter(pk=transaction_id).first()
        if not tx:
            print(f"[StopTx] unknown #{transaction_id}")
            return call_result.StopTransaction(
                id_tag_info={"status": AuthorizationStatus.Accepted}
            )

        tx.stop_time = timestamp
        tx.latest_wh = meter_stop
        tx.save(update_fields=["stop_time", "latest_wh"])
        print(f"[StopTx] #{transaction_id} → {tx.kwh:.3f} kWh")
        return call_result.StopTransaction(
            id_tag_info={"status": AuthorizationStatus.Accepted}
        )
"""


class MyChargePoint(CP):
    # -------------------------------------------------------------------------
    @on(Action.status_notification)
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
        return call_result.StatusNotificationPayload()
    # -------------------------------------------------------------------------
    @on(Action.start_transaction)
    async def on_start_transaction(
        self, connector_id, id_tag, meter_start, timestamp, **_
    ):
        # get next tx_id atomically in one sync function
        @sync_to_async
        def _create_tx():
            next_id = (
                (Transaction.objects.order_by("-tx_id").first() or {"tx_id": 0})
                .get("tx_id", 0)
                + 1
            )
            Transaction.objects.create(
                tx_id=next_id,
                cp_id=self.id,
                user_tag=id_tag,
                start_wh=meter_start,
                latest_wh=meter_start,
                start_time=timestamp,
            )
            return next_id

        next_id = await _create_tx()
        print(f"[StartTx] #{next_id} on {self.id} meterStart={meter_start} Wh")
        return call_result.StartTransactionPayload(
            transaction_id=next_id,
            id_tag_info={"status": AuthorizationStatus.accepted},
        )
    # -------------------------------------------------------------------------
    @on(Action.meter_values)
    async def on_meter_values(self, connector_id, transaction_id, meter_value, **_):
        tx = await sync_to_async(Transaction.objects.filter(pk=transaction_id).first)()
        if not tx:
            return call_result.MeterValuesPayload()

        energy = None
        for sample in meter_value:
            for sv in sample.get("sampled_value") or sample.get("sampledValue", []):
                if sv.get("measurand") == "Energy.Active.Import.Register":
                    energy = float(sv.get("value", 0))

        if energy is not None:
            if tx.start_wh is None and energy > 0:
                tx.start_wh = energy
            tx.latest_wh = energy
            await sync_to_async(tx.save)(update_fields=["start_wh", "latest_wh"])
            print(f"[Meter] tx={transaction_id} energy={energy} Wh")

        return call_result.MeterValuesPayload()
    # -------------------------------------------------------------------------
    @on(Action.stop_transaction)
    async def on_stop_transaction(self, meter_stop, transaction_id, timestamp, **_):
        tx = await sync_to_async(Transaction.objects.filter(pk=transaction_id).first)()
        if not tx:
            print(f"[StopTx] unknown #{transaction_id}")
            return call_result.StopTransactionPayload(
                id_tag_info={"status": AuthorizationStatus.accepted}
            )

        tx.stop_time = timestamp
        tx.latest_wh = meter_stop
        await sync_to_async(tx.save)(update_fields=["stop_time", "latest_wh"])
        print(f"[StopTx] #{transaction_id} → {tx.kwh:.3f} kWh")
        return call_result.StopTransactionPayload(
            id_tag_info={"status": AuthorizationStatus.accepted}
        )



# ──────────────────────────────────────────────────────────────────────────────
#  WebSocket server bootstrap
# ──────────────────────────────────────────────────────────────────────────────
async def on_connect(websocket, path):
    cp_id = path.lstrip("/").split("/")[-1] or "UNKNOWN_CP"
    cp = MyChargePoint(cp_id, websocket)
    await cp.start()


class Command(BaseCommand):
    help = "Run the OCPP 1.6 CSMS WebSocket server (ws://0.0.0.0:9000)"

    def handle(self, *args, **options):
        asyncio.run(self._serve())

    async def _serve(self):
        server = await websockets.serve(
            on_connect, "0.0.0.0", 9000, subprotocols=["ocpp1.6"]
        )
        self.stdout.write(
            self.style.SUCCESS("🟢  OCPP 1.6 listening on ws://0.0.0.0:9000")
        )
        await server.wait_closed()
