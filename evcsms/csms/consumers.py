import json
from datetime import datetime, timezone
from channels.generic.websocket import AsyncWebsocketConsumer
from ocpp.v16 import ChargePoint as CP
from ocpp.v16.enums import (
    Action, RegistrationStatus, AuthorizationStatus, DataTransferStatus
)
from ocpp.v16 import call_result
from .models import ChargePoint, Transaction

class OCPPConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.cp_id = self.scope["url_route"]["kwargs"]["cp_id"]
        await self.accept(subprotocol="ocpp1.6")
        print(f"→ CP connected: {self.cp_id}")

        # Wrap self as a python-ocpp CP
        self.cp = CP(self.cp_id, self)

        # Dispatch python-ocpp handlers
        await self.cp.start()

    async def receive(self, text_data):
        await self.cp._message_handler(text_data)

    async def send(self, text_data=None, bytes_data=None):
        # channels expects exactly this signature
        await super().send(text_data=text_data, bytes_data=bytes_data)

    # ---- now register your handlers on self.cp ----
    @CP.on(Action.boot_notification)
    async def on_boot_notification(self, charge_point_vendor, charge_point_model, **_):
        ChargePoint.objects.update_or_create(
            cp_id=self.cp_id,
            defaults={"status": "Accepted"})
        now = datetime.now(tz=timezone.utc).isoformat()
        return call_result.BootNotification(current_time=now, interval=30,
                                            status=RegistrationStatus.accepted)

    @CP.on(Action.status_notification)
    async def on_status_notification(self, connector_id, status, **_):
        ChargePoint.objects.update_or_create(
            cp_id=self.cp_id,
            defaults={"connector": connector_id, "status": status})
        return call_result.StatusNotification()

    @CP.on(Action.heartbeat)
    async def on_heartbeat(self):
        now = datetime.now(tz=timezone.utc).isoformat()
        return call_result.Heartbeat(current_time=now)

    @CP.on(Action.data_transfer)
    async def on_data_transfer(self, vendor_id, **_):
        ok = vendor_id == "generalConfiguration"
        status = DataTransferStatus.accepted if ok else DataTransferStatus.rejected
        return call_result.DataTransfer(status=status, data="")

    @CP.on(Action.authorize)
    async def on_authorize(self, id_tag, **_):
        return call_result.Authorize(id_tag_info={"status": AuthorizationStatus.accepted})

    @CP.on(Action.start_transaction)
    async def on_start_transaction(self, connector_id, id_tag, meter_start, timestamp, **_):
        tx, _ = Transaction.objects.get_or_create(
            tx_id=int(timestamp.timestamp()),  # or your own ID logic
            defaults={
                "cp": ChargePoint.objects.get(cp_id=self.cp_id),
                "user_tag": id_tag,
                "start_wh": meter_start,
                "latest_wh": meter_start,
                "start_time": timestamp,
            })
        return call_result.StartTransaction(
            transaction_id=tx.tx_id,
            id_tag_info={"status": AuthorizationStatus.accepted})

    @CP.on(Action.meter_values)
    async def on_meter_values(self, transaction_id, meter_value, **_):
        try:
            tx = Transaction.objects.get(tx_id=transaction_id)
        except Transaction.DoesNotExist:
            return call_result.MeterValues()

        for sample in meter_value:
            for sv in sample.get("sampled_value", []) + sample.get("sampledValue", []):
                if sv.get("measurand") == "Energy.Active.Import.Register":
                    val = float(sv.get("value", 0))
                    if not tx.start_wh and val > 0:
                        tx.start_wh = val
                    tx.latest_wh = val
        tx.save()
        return call_result.MeterValues()

    @CP.on(Action.stop_transaction)
    async def on_stop_transaction(self, meter_stop, transaction_id, timestamp, **_):
        try:
            tx = Transaction.objects.get(tx_id=transaction_id)
            tx.stop_time = timestamp
            tx.save()
        except Transaction.DoesNotExist:
            pass
        return call_result.StopTransaction(id_tag_info={"status": AuthorizationStatus.accepted})
