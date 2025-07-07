from rest_framework import serializers
from .models import ChargePoint, Transaction

class ChargePointSerializer(serializers.ModelSerializer):
    class Meta:
        model = ChargePoint
        fields = ["cp_id", "connector", "status", "last_seen"]

class TransactionSerializer(serializers.ModelSerializer):
    kwh = serializers.ReadOnlyField()
    class Meta:
        model = Transaction
        fields = ["tx_id", "cp", "user_tag", "kwh", "start_time", "stop_time"]
