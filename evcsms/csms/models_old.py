# csms/models.py
from django.db import models
from django.contrib.auth.models import AbstractUser
from django.db import models
from django.conf import settings
from django.utils.crypto import get_random_string

"""
class ChargePoint(models.Model):
    id = models.CharField(primary_key=True, max_length=40)
    name = models.CharField(max_length=100, blank=True)
    connector_id = models.PositiveSmallIntegerField(default=0)
    status = models.CharField(max_length=30, default="Unknown")
    updated = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return self.name or self.id
"""

class ChargePoint(models.Model):
    id           = models.CharField(primary_key=True, max_length=40)
    name         = models.CharField(max_length=100, blank=True)
    connector_id = models.PositiveSmallIntegerField(default=0)
    status       = models.CharField(max_length=30, default="Unknown")
    updated      = models.DateTimeField(auto_now=True)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="cps")

    # NEW: which admin owns this CP
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="charge_points",
        null=True,           # allow null until you create real users/CPs
        blank=True,
    )

    def __str__(self):
        return self.name or self.id

class Transaction(models.Model):
    """
    One charging session (StartTx → StopTx).
    Energy values are in watt-hours, mirroring OCPP 1.6 samples.
    """
    tx_id = models.PositiveIntegerField(primary_key=True)
    cp = models.ForeignKey(ChargePoint, on_delete=models.CASCADE)
    user_tag = models.CharField(max_length=50, blank=True)
    start_wh = models.FloatField(null=True, blank=True)
    latest_wh = models.FloatField(null=True, blank=True)
    start_time = models.DateTimeField()
    stop_time = models.DateTimeField(null=True, blank=True)

    @property
    def kwh(self) -> float:
        """Energy delivered so far, in kWh."""
        if self.start_wh is None or self.latest_wh is None:
            return 0.0
        return max(self.latest_wh - self.start_wh, 0) / 1000

    def __str__(self) -> str:
        return f"#{self.tx_id} on {self.cp_id}"


class User(AbstractUser):
    ROLE_CHOICES = (
        ("customer", "Customer"),     # EV driver
        ("admin",    "Site Admin"),   # owns one or more CPs
        ("root",     "Super Admin"),  # platform owner
    )
    role = models.CharField(
        max_length=10,
        choices=ROLE_CHOICES,
        default="customer",
    )

    # handy helper flags
    @property
    def is_customer(self):   return self.role == "customer"
    @property
    def is_cp_admin(self):   return self.role == "admin"
    @property
    def is_root_admin(self): return self.role == "root"


class Tenant(models.Model):
    """
    One “super-admin” organization / white-label customer.
    """
    name   = models.CharField(max_length=100)
    slug   = models.SlugField(unique=True)                # for nice URLs (/tenant-a/)
    ws_key = models.CharField(max_length=12, unique=True) # used in ws path (/api/v16/<ws_key>)
    owner  = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="tenant",
    )

    def save(self, *args, **kwargs):
        if not self.ws_key:
            self.ws_key = get_random_string(10).lower()
        super().save(*args, **kwargs)

    def websocket_url(self, request):
        host = request.get_host()
        return f"ws://{host}/api/v16/{self.ws_key}"
