from rest_framework import generics
from .models import ChargePoint, Transaction
from .serializers import ChargePointSerializer, TransactionSerializer


class ChargePointList(generics.ListAPIView):
    queryset = ChargePoint.objects.all()
    serializer_class = ChargePointSerializer

"""
class ChargePointList(generics.ListAPIView):
    queryset = Transaction.objects.order_by('-pk')[:10]
    serializer_class = TransactionSerializer
"""

class TransactionList(generics.ListAPIView):
    queryset = Transaction.objects.order_by("-tx_id")[:20]
    serializer_class = TransactionSerializer

class RecentSessions(generics.ListAPIView):
    queryset = Transaction.objects.order_by('-pk')[:10]
    serializer_class = TransactionSerializer
