from django.urls import path
from .views import ChargePointList, TransactionList

urlpatterns = [
    path("charge-points/", ChargePointList.as_view(), name="charge-points"),
    path("sessions/",      TransactionList.as_view(), name="sessions"),
]
