# csms/models.py
from django.db import models
from django.contrib.auth.models import AbstractUser
from django.db import models
from django.conf import settings
from django.utils.crypto import get_random_string
from django.utils import timezone
from decimal import Decimal, ROUND_HALF_UP

# ──────────────────────────────────────────
#  AUTH – two user roles
# ──────────────────────────────────────────
class User(AbstractUser):
    ROLE_CHOICES = (
        ("user",         "Normal user"),
        ("super_admin",  "Super Admin"),
    )
    role = models.CharField(max_length=12, choices=ROLE_CHOICES, default="user")
    phone = models.CharField(max_length=32, blank=True)

    # helpers
    @property
    def is_normal_user(self) -> bool:
        return self.role == "user"

    @property
    def is_super_admin(self) -> bool:
        return self.role == "super_admin"

    # Backward-compat so old code doesn't explode (optional but handy)
    @property
    def is_customer(self):   return self.is_normal_user
    @property
    def is_cp_admin(self):   return self.is_super_admin
    @property
    def is_root_admin(self): return self.is_super_admin





class Tenant(models.Model):
    owner   = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="tenant",
    )
    ws_key  = models.CharField(
        max_length=64,
        unique=True,
        help_text="Per-tenant secret shown in the WebSocket URL.",
    )
    name    = models.CharField(max_length=100, blank=True)

    def __str__(self):
        return self.name or f"tenant-{self.pk}"


# ──────────────────────────────────────────
#  CHARGE POINT
# ──────────────────────────────────────────
class ChargePoint(models.Model):

    id            = models.CharField(primary_key=True, max_length=40)
    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        related_name="cps",
        null=True,
        blank=True,
    )

    vendor       = models.CharField(max_length=60, blank=True)
    model        = models.CharField(max_length=60, blank=True)
    fw_version   = models.CharField(max_length=60, blank=True)

    name          = models.CharField(max_length=100, blank=True)
    connector_id  = models.PositiveSmallIntegerField(default=0)
    status        = models.CharField(max_length=30, default="Unknown")
    updated       = models.DateTimeField(auto_now=True)
    price_per_kwh  = models.DecimalField(max_digits=8, decimal_places=3, null=True, blank=True)
    price_per_hour = models.DecimalField(max_digits=8, decimal_places=3, null=True, blank=True)
    location       = models.CharField(max_length=255, blank=True, default="", null=True)
    lat = models.DecimalField(max_digits=9, decimal_places=6, blank=True, null=True)
    lng = models.DecimalField(max_digits=9, decimal_places=6, blank=True, null=True)

    def has_coords(self):
        return self.lat is not None and self.lng is not None

    def __str__(self):
        return self.name or self.id


# ──────────────────────────────────────────
#  TRANSACTION (= session)
# ──────────────────────────────────────────
class Transaction(models.Model):
    tx_id      = models.PositiveIntegerField(primary_key=True)
    cp         = models.ForeignKey(ChargePoint, on_delete=models.CASCADE,
                                   related_name="transaction")
    user_tag   = models.CharField(max_length=50, blank=True)
    start_wh   = models.FloatField(null=True, blank=True)
    latest_wh  = models.FloatField(null=True, blank=True)
    start_time = models.DateTimeField()
    stop_time  = models.DateTimeField(null=True, blank=True)
    price_kwh_at_start  = models.DecimalField(max_digits=8, decimal_places=3,
                                              null=True, blank=True)
    price_hour_at_start    = models.DecimalField(max_digits=8, decimal_places=3,
                                              null=True, blank=True)

    @property
    def kwh(self):
        if self.start_wh is None or self.latest_wh is None:
            return 0
        #return max(self.latest_wh - self.start_wh, 0) / 1000
        return (Decimal(self.latest_wh) - Decimal(self.start_wh)) / Decimal("1000")

    def __str__(self):
        return f"#{self.tx_id} on {self.cp_id}"
    """
    def total_price(self):
        if self.price_kwh_at_start is None and self.price_hour_at_start is None:
            return None                      # nothing to add up

        # energy component
        energy_cost = (self.kwh or 0) * (self.price_kwh_at_start or 0)

        # time component  (use now() for still-running sessions)
        duration = (self.stop_time or timezone.now()) - self.start_time
        hours    = duration.total_seconds() / 3600
        time_cost = hours * (self.price_hour_at_start or 0)

        return round(Decimal(energy_cost + time_cost), 3)
    """

    def total_price(self):
        # No pricing yet?
        if self.price_kwh_at_start is None and self.price_hour_at_start is None:
            return None

        total = Decimal("0")

        # ---- energy cost (kWh * price_kwh) ----
        kwh = self.kwh
        if kwh is None:
            kwh = Decimal("0")
        elif not isinstance(kwh, Decimal):
            kwh = Decimal(str(kwh))  # defend if kwh is a float

        if self.price_kwh_at_start is not None:
            total += kwh * self.price_kwh_at_start

        # ---- time cost (hours * price_hour) ----
        end = self.stop_time or timezone.now()
        delta = end - self.start_time
        # build seconds as Decimal (no float)
        secs = (Decimal(delta.days) * Decimal("86400")
                + Decimal(delta.seconds)
                + (Decimal(delta.microseconds) / Decimal("1000000")))
        hours = secs / Decimal("3600")

        if self.price_hour_at_start is not None:
            total += hours * self.price_hour_at_start

        return total.quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)

# csms/models.py  (add at the bottom, then makemigrations)
class CPCommand(models.Model):
    """
    One row = one OCPP command that should be sent to a charge-point.
    """
    cp       = models.ForeignKey(
        "ChargePoint", on_delete=models.CASCADE, related_name="cmd_queue"
    )
    action   = models.CharField(max_length=40)          # e.g. RemoteStartTransaction
    payload  = models.JSONField(default=dict)           # parameters dict
    created  = models.DateTimeField(auto_now_add=True)
    done_at  = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=["cp", "done_at", "created"])]

