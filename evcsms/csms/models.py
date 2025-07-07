# csms/models.py
from django.db import models


class ChargePoint(models.Model):
    """
    One physical charge point. A single connector_id is stored
    (sufficient for most residential AC chargers). Extend as needed.
    """
    id = models.CharField(primary_key=True, max_length=40)
    name = models.CharField(max_length=100, blank=True)
    connector_id = models.PositiveSmallIntegerField(default=0)
    status = models.CharField(max_length=30, default="Unknown")
    updated = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
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
