# csms/models.py
from django.db import models
from django.contrib.auth.models import AbstractUser
from django.db import models
from django.conf import settings
from django.utils.crypto import get_random_string

# ──────────────────────────────────────────
#  AUTH – three user roles
# ──────────────────────────────────────────
class User(AbstractUser):
    ROLE_CHOICES = (
        ("customer", "Customer"),
        ("admin",    "Site Admin"),
        ("root",     "Super Admin"),
    )
    role = models.CharField(max_length=10, choices=ROLE_CHOICES,
                            default="customer")

    # helpers
    @property
    def is_customer(self):   return self.role == "customer"
    @property
    def is_cp_admin(self):   return self.role == "admin"
    @property
    def is_root_admin(self): return self.role == "root"

"""
# ──────────────────────────────────────────
#  TENANT  (one per super-admin)
# ──────────────────────────────────────────
class Tenant(models.Model):

    owner      = models.OneToOneField(User, on_delete=models.CASCADE,
                                      related_name="tenant")
    name       = models.CharField(max_length=100, blank=True)
    ws_secret  = models.CharField(max_length=32, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name or f"tenant-{self.pk}"
"""


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

    @property
    def kwh(self):
        if self.start_wh is None or self.latest_wh is None:
            return 0
        return max(self.latest_wh - self.start_wh, 0) / 1000

    def __str__(self):
        return f"#{self.tx_id} on {self.cp_id}"

