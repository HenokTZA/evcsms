from rest_framework import generics, permissions
from .models import ChargePoint, Transaction
from .serializers import ChargePointSerializer, TransactionSerializer, SignUpSerializer
from .permissions import IsCpAdmin, IsRootAdmin
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import AllowAny
from django.contrib.auth import get_user_model
from .serializers import (
    UserSerializer,                 # already there
    TokenObtainPairPatchedSerializer,   # see below
)

from rest_framework_simplejwt.views import (
    TokenObtainPairView,
    TokenRefreshView,
)

User = get_user_model()

class ChargePointList(generics.ListAPIView):
    queryset = ChargePoint.objects.all()
    serializer_class = ChargePointSerializer
    permission_classes = [IsCpAdmin | IsRootAdmin]


    def get_queryset(self):
        user = self.request.user
        if user.is_root_admin:
            return ChargePoint.objects.all()
        # admin sees only his CPs
        return ChargePoint.objects.filter(owner=user)

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

class SignupView(APIView):
    #authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = SignUpSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response({"detail": "account created"}, status=status.HTTP_201_CREATED)


class LoginView(TokenObtainPairView):
    """
    POST { "username": "...", "password": "..." }
    ← { "access": "...", "refresh": "..." }
    """
    serializer_class   = TokenObtainPairPatchedSerializer
    permission_classes = [AllowAny]


# csms/views.py
class MeView(generics.RetrieveAPIView):
    serializer_class    = MeSerializer
    permission_classes  = [permissions.IsAuthenticated]

    def get_object(self):
        return self.request.user

