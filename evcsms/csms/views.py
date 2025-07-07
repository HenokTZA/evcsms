from rest_framework import generics
from .models import ChargePoint, Transaction
from .serializers import ChargePointSerializer, TransactionSerializer

class ChargePointList(generics.ListAPIView):
    queryset = ChargePoint.objects.all()
    serializer_class = ChargePointSerializer

class TransactionList(generics.ListAPIView):
    queryset = Transaction.objects.order_by("-tx_id")[:20]
    serializer_class = TransactionSerializer
