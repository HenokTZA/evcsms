from rest_framework import serializers
from .models import ChargePoint, Transaction


class ChargePointSerializer(serializers.ModelSerializer):
    class Meta:
        model  = ChargePoint
        # ↓ This is probably where the wrong names are listed
        fields = [
            "id",           # primary key
            "name",
            "connector_id",
            "status",
            "updated",      # <- correct timestamp field
            # "created",    # <- REMOVE or rename to "updated"
            # "cp_id",      # <- REMOVE (model doesn’t have this)
        ]
        read_only_fields = ["id", "updated"]


"""
class TransactionSerializer(serializers.ModelSerializer):
    kwh = serializers.ReadOnlyField()
    class Meta:
        model = Transaction
        fields = ["tx_id", "cp", "user_tag", "kwh", "start_time", "stop_time"]
"""


"""
class TransactionSerializer(serializers.ModelSerializer):
    id      = serializers.IntegerField(source='tx_id')
    cp      = serializers.CharField(source='cp_id')
    user    = serializers.CharField(source='user_tag')
    kwh     = serializers.DecimalField(max_digits=10, decimal_places=2, source='kwh')
    started = serializers.DateTimeField(source='start_time')
    ended   = serializers.DateTimeField(source='stop_time', allow_null=True)

    class Meta:
        model  = Transaction
        fields = ('id', 'cp', 'user', 'kwh', 'started', 'ended')
"""


class TransactionSerializer(serializers.ModelSerializer):
    id      = serializers.IntegerField(source="tx_id")
    cp      = serializers.CharField(source="cp_id")
    user    = serializers.CharField(source="user_tag")
    kWh     = serializers.SerializerMethodField()
    Started = serializers.DateTimeField(source="start_time")
    Ended   = serializers.DateTimeField(source="stop_time")

    def get_kWh(self, obj):
        # assuming `obj.kwh` returns a Decimal or float
        return float(obj.kwh or 0)

    class Meta:
        model  = Transaction
        fields = ["id","cp","user","kWh","Started","Ended"]

