# csms/management/commands/runocpp.py
# --------------------------------------------------------------------------
import asyncio
from datetime import datetime, timezone
from decimal import Decimal
import logging
import json
from csms.ocpp_bridge import next_for
import websockets
from asgiref.sync import sync_to_async
from django.core.management.base import BaseCommand
from ocpp.routing import on
from ocpp.v16 import ChargePoint as CP, call_result
from ocpp.v16 import call_result as cr
from ocpp.v16 import call as c
from websockets.exceptions import ConnectionClosed
from csms.models import ChargePoint, Transaction, Tenant     # your own models
import re
from csms.ocpp_hub import hub
import django.utils.timezone as dj_timezone

from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_datetime

# --------------------------------------------------------------------------

logging.basicConfig(level=logging.INFO)
logging.getLogger("websockets.server").setLevel(logging.WARNING)
log = logging.getLogger("ocpp")


_camel = re.compile(r"(?<!^)([A-Z])")

def camel_to_snake(s: str) -> str:
    """transactionId → transaction_id,  connectorId → connector_id…"""
    return _camel.sub(r"_\1", s).lower()



# --- helper: resolve a Django user id from the idTag -------------------------
@sync_to_async
def _resolve_user_id_from_idtag(id_tag: str):
    """
    Try to map an OCPP idTag to a Django user id via several common schemes:
    - "user-<pk>"     (your special case)
    - RFIDCard(uid)   -> owner
    - UserTag(value)  -> user
    - username/email  -> user
    Returns user_id or None.
    """
    from django.contrib.auth import get_user_model
    User = get_user_model()

    # 1) Special form "user-<pk>"
    if isinstance(id_tag, str) and id_tag.startswith("user-"):
        try:
            pk = int(id_tag.split("-", 1)[1])
            return User.objects.only("id").get(pk=pk).id
        except Exception:
            pass

    # 2) RFID card mapping (adjust model/field names to your schema)
    try:
        from csms.models import RFIDCard  # if you have it
        card = RFIDCard.objects.select_related("owner").get(uid__iexact=id_tag)
        if card.owner_id:
            return card.owner_id
    except Exception:
        pass

    # 3) Tag table mapping (adjust model/field names to your schema)
    try:
        from csms.models import UserTag  # if you have it
        tag = UserTag.objects.select_related("user").get(value__iexact=id_tag)
        if tag.user_id:
            return tag.user_id
    except Exception:
        pass

    # 4) Fallback: username / email equals id_tag
    user = (
        User.objects.filter(Q(username__iexact=id_tag) | Q(email__iexact=id_tag))
        .only("id")
        .first()
    )
    return user.id if user else None
# -----------------------------------------------------------------------------


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


class ChargePointHandler(ChargePoint):  # whatever class you use
    def __init__(self, id, connection, db_cp_id: int, *args, **kwargs):
        super().__init__(id, connection, *args, **kwargs)
        self.db_cp_id = db_cp_id

    async def on_connect(self):
        # Called after handshake is done and we know which DB CP this is
        await hub.register(self.db_cp_id, self)

    async def on_disconnect(self):
        await hub.unregister(self.db_cp_id)

    # You probably already have lifecycle hooks; call register/unregister there.
    # If not, call register right after you create the CP handler and
    # make sure to unregister in a finally: block when its websocket ends.

# Where you accept a socket and build the handler:
# - resolve db_cp_id from the OCPP identity (e.g., chargeBoxId -> your CP row)
# - then pass db_cp_id to handler and call handler.on_connect()
#
# On socket close/exception, call handler.on_disconnect()



# ──────────────────────────── ChargePoint handler ──────────────────────────
class MyChargePoint(CP):

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


    """
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
        cp_obj = await sync_to_async(ChargePoint.objects.get)(pk=self.id)

        await sync_to_async(Transaction.objects.create)(
            tx_id=tx_id,
            cp_id=self.id,
            user_tag=id_tag,
            start_wh=meter_start,
            latest_wh=meter_start,
            start_time=timestamp,
            price_kwh_at_start = cp_obj.price_per_kwh,
            price_hour_at_start   = cp_obj.price_per_hour,
        )
        print(f"[StartTx] #{tx_id} on {self.id} meterStart={meter_start}Wh")

        # NEW: if id_tag encodes a user-id, store txId in cache for (user, cp)
        try:
            if id_tag.startswith("user-"):
                user_id = int(id_tag.split("-", 1)[1])
                # defer import to avoid circulars
                from csms.tx_cache import set_active_tx
                set_active_tx(user_id=user_id, cp_id=str(self.id), tx_id=tx_id)
        except Exception as e:
            print(f"[StartTx] could not cache active tx: {e}")

        return _cr(
            "StartTransaction",
            transaction_id=tx_id,
            id_tag_info={"status": "Accepted"},
        )
    """

    @on("StartTransaction")
    async def on_start_transaction(self, id_tag: str, meter_start: int, timestamp: str, **_):
        # Generate your tx id
        @sync_to_async
        def _next_tx_id():
            last = Transaction.objects.order_by("-tx_id").only("tx_id").first()
            return (last.tx_id if last else 0) + 1

        tx_id = await _next_tx_id()

        # Resolve CP and price snapshot
        cp_obj = await sync_to_async(ChargePoint.objects.get)(pk=self.id)

        # Parse timestamp safely (fall back to now)
        ts = parse_datetime(timestamp) or timezone.now()

        # NEW: resolve the Django user id tied to this id_tag (if any)
        user_id = await _resolve_user_id_from_idtag(id_tag)

        # Create transaction (include user_id if found)
        @sync_to_async
        def _create_tx():
            return Transaction.objects.create(
                tx_id=tx_id,
                cp_id=self.id,
                user_tag=id_tag,
                user_id=user_id,                # <-- FK set here (requires Transaction.user FK field)
                start_wh=meter_start,
                latest_wh=meter_start,
                start_time=ts,
                price_kwh_at_start=cp_obj.price_per_kwh,
                price_hour_at_start=cp_obj.price_per_hour,
            )

        tx = await _create_tx()
        print(f"[StartTx] #{tx_id} on {self.id} meterStart={meter_start}Wh user_id={user_id}")

        # Cache active transaction for this user (if any)
        try:
            if user_id:
                from csms.tx_cache import set_active_tx  # defer import to avoid circulars
                set_active_tx(user_id=user_id, cp_id=str(self.id), tx_id=tx_id)
        except Exception as e:
            print(f"[StartTx] could not cache active tx: {e}")

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
        # IMPORTANT: look up by tx_id (the OCPP transactionId), not by pk
        tx = await sync_to_async(
            Transaction.objects.filter(tx_id=transaction_id).first
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



    # ------------------------------------------------------------------ #
    #  constructor – keep super() but initialise small in-memory stores   #
    # ------------------------------------------------------------------ #
    def __init__(self, charge_point_id, websocket):
        super().__init__(charge_point_id, websocket)

        # simple, mutable stores for demo purposes
        self.config: dict[str, str] = {        # “key” → “value”
            "HeartbeatInterval": "30",
            "ConnectionTimeOut": "180",
        }
        self.local_list_version: int = 1
        self.charging_profiles: dict[int, dict] = {}   # profileId → blob
    # ------------------------------------------------------------------ #

    # ─────────────────────────── GET-/CHANGE CONFIG ─────────────────── #
    @on("GetConfiguration")
    async def on_get_configuration(self, key: list[str] | None = None, **_):
        """
        • If key == [], return **all** keys.
        • Otherwise return only the requested ones, collect unknown ones.
        """
        if key is None:
            key = []

        if len(key) == 0:                       # “all keys” shortcut
            requested = list(self.config.items())
            unknown   = []
        else:
            requested = [(k, self.config[k]) for k in key if k in self.config]
            unknown   = [k for k in key if k not in self.config]

        cfg_list = [
            {"key": k, "value": v, "readonly": False}
            for k, v in requested
        ]

        return _cr(                             # ← helper from earlier
            "GetConfiguration",
            configuration_key=cfg_list,
            unknown_key=unknown,
        )

    @on("ChangeConfiguration")
    async def on_change_configuration(self, key: str, value: str, **_):
        """
        Accept every change as long as the key exists (demo-style).
        """
        if key not in self.config:
            return _cr("ChangeConfiguration", status="Rejected")

        self.config[key] = value
        return _cr("ChangeConfiguration", status="Accepted")
    # ─────────────────────────────────────────────────────────────────── #

    # ───────────────────────── LOCAL-LIST VERSION  ──────────────────── #
    @on("GetLocalListVersion")
    async def on_get_local_list_version(self, **_):
        return _cr("GetLocalListVersion", list_version=self.local_list_version)
    # ─────────────────────────────────────────────────────────────────── #

    # ─────────────────────── CHARGING-PROFILE CRUD  ─────────────────── #
    @on("SetChargingProfile")
    async def on_set_charging_profile(
        self,
        connector_id: int,
        cs_charging_profiles: dict,
        **_
    ):
        pid = cs_charging_profiles.get("chargingProfileId")
        if pid is None:
            return _cr("SetChargingProfile", status="Rejected")

        if not hasattr(self, "charging_profiles"):
            self.charging_profiles = {}

        self.charging_profiles[pid] = {
            "connectorId": connector_id,
            **cs_charging_profiles,
        }

        print(f"[Profile] saved profile #{pid} on {self.id}")
        return _cr("SetChargingProfile", status="Accepted")

    @on("ClearChargingProfile")
    async def on_clear_charging_profile(
        self,
        id: int | None = None,
        connector_id: int | None = None,
        charging_profile_purpose: str | None = None,
        stack_level: int | None = None,
        **_
    ):
        """
        Super simple: if id is given remove that one, otherwise wipe all.
        """
        if id is not None:
            self.charging_profiles.pop(id, None)
        else:
            self.charging_profiles.clear()

        return _cr("ClearChargingProfile", status="Accepted")

    @on("FirmwareStatusNotification")
    async def on_firmware_status_notification(self, status: str, **kwargs):
        """
        CP -> CSMS. Status is one of:
          Idle, Downloading, Downloaded, Installing, Installed, DownloadFailed, InstallationFailed
        """
        print(f"[FW]  {self.id} → {status}")
        # Optional: persist last known firmware status
        await sync_to_async(
            ChargePoint.objects.filter(id=self.id).update
        )(fw_status=status, updated=dj_timezone.now())  # remove/update fields if your model differs
        return _cr("FirmwareStatusNotification")


    @on("DiagnosticsStatusNotification")
    async def on_diagnostics_status_notification(self, status: str, **kwargs):
        """
        CP -> CSMS. Status is usually Uploading / Uploaded / UploadFailed / Idle.
        """
        print(f"[DIAG] {self.id} → {status}")
        # Optional: persist last diagnostics status
        await sync_to_async(
            ChargePoint.objects.filter(id=self.id).update
        )(diag_status=status, updated=dj_timezone.now())  # remove/update fields if your model differs
        return _cr("DiagnosticsStatusNotification")

    @on("GetCompositeSchedule")
    async def on_get_composite_schedule(
        self,
        connector_id: int,
        duration: int,
        charging_rate_unit: str | None = None,
        **_
    ):
        """
        Demo implementation:
        • If **any** profiles exist we’ll make up a 1-entry schedule.
        • Otherwise we report “Rejected”.
        """
        if not self.charging_profiles:
            return _cr("GetCompositeSchedule", status="Rejected")

        now_iso = datetime.now(timezone.utc).isoformat()

        schedule = {
            "duration": duration,
            "startSchedule": now_iso,
            "chargingRateUnit": charging_rate_unit or "A",
            "chargingSchedulePeriod": [
                {"startPeriod": 0, "limit": 16}
            ],
        }

        return _cr(
            "GetCompositeSchedule",
            status="Accepted",
            connector_id=connector_id,
            schedule_start=now_iso,
            charging_schedule=schedule,
        )
    # ─────────────────────────────────────────────────────────────────── #





    async def _command_poller(self):
        while True:
            cmd = await next_for(self.id)
            if cmd:
                action, params = cmd
                # --- NEW: translate payload keys ---------------------------
                snake_params = {camel_to_snake(k): v for k, v in params.items()}
                # -----------------------------------------------------------
                print(f"[CMD] {self.id} → {action} {snake_params}")
                if action == "FirmwareStatusNotification":
                    action = "TriggerMessage"
                    # Make sure we send the correct requested message
                    # connector_id is optional; set 0 by default.
                    snake_params = {"requested_message": "FirmwareStatusNotification",
                                    "connector_id": snake_params.get("connector_id", 0)}
                call_cls = getattr(c, action)          # e.g. c.RemoteStopTransaction
                try:
                    resp = await self.call(call_cls(**snake_params))
                    print(f"[CMD]  ↳  {resp}")
                except Exception as exc:
                    print(f"[CMD]  ↳  ERROR {exc}")
            await asyncio.sleep(1)



    # ───────────── override CP.start() to launch the poller ────────
    async def start(self):
        """
        Run the normal python-ocpp loop *and* a side-task that feeds
        commands coming from the REST API.
        """
        poller = asyncio.create_task(self._command_poller())
        try:
            await super().start()             # ← blocks until WS closes
        finally:
            poller.cancel()                   # tidy up when CP disconnects




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

