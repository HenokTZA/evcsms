# csms/views.py
from django.contrib.auth import get_user_model
from rest_framework import generics, permissions, status
from rest_framework.views import APIView
from rest_framework.response import Response
from django.shortcuts import get_object_or_404
from rest_framework_simplejwt.views import TokenObtainPairView
from csms.ocpp_bridge import enqueue
from asgiref.sync import async_to_sync
from .models      import ChargePoint, Transaction, Tenant
from .serializers import (
    ChargePointSerializer,
    TransactionSerializer,
    SignUpSerializer,
    MeSerializer,
    TokenObtainPairPatchedSerializer,
    UserSerializer,
)
from .permissions import IsRootAdmin, IsCpAdmin   # keep for later fine-graining
from .helpers     import _tenant_qs

User = get_user_model()
# ────────────────────────────────────────────────────────────────
#  Helpers
# ────────────────────────────────────────────────────────────────


"""
# csms/views.py  (only this helper)
def _tenant_qs(model, user, *, with_owner_split=False):

    try:
        tenant = user.tenant                    # reverse OneToOne from User → Tenant
    except Tenant.DoesNotExist:
        return model.objects.none()

    if model is Transaction:                    # 🔑 keep this branch!
        qs = model.objects.filter(cp__tenant=tenant)
    else:
        qs = model.objects.filter(tenant=tenant)

    if with_owner_split and user.is_cp_admin:
        qs = qs.filter(owner=user)

    return qs
"""

class ChargePointList(generics.ListAPIView):
    serializer_class   = ChargePointSerializer
    permission_classes = [IsRootAdmin | IsCpAdmin]
    """
    def get_queryset(self):
        return _tenant_qs(
            ChargePoint,
            self.request.user,
            with_owner_split=True,
        )
    """
    def get_queryset(self):
        return _tenant_qs(ChargePoint, self.request.user)



class TransactionList(generics.ListAPIView):
    serializer_class   = TransactionSerializer
    permission_classes = [permissions.IsAuthenticated]
    """
    def get_queryset(self):
        return (
            _tenant_qs(Transaction, self.request.user)
            .order_by("-pk")
        )
    """

    def get_queryset(self):
        # return the current user's tenant → all their transactions
        return (
            _tenant_qs(Transaction, self.request.user)
            .order_by("-start_time")  # newest first
        )


class RecentSessions(generics.ListAPIView):
    """
    Convenience: just the last 10 sessions for the current tenant.
    """
    serializer_class   = TransactionSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return (
            _tenant_qs(Transaction, self.request.user)
            .order_by("-pk")[:10]
        )


# ────────────────────────────────────────────────────────────────
#  Auth / profile
# ────────────────────────────────────────────────────────────────
class SignupView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        ser = SignUpSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        ser.save()
        return Response({"detail": "account created"}, status=status.HTTP_201_CREATED)


class LoginView(TokenObtainPairView):
    """
    POST → {username, password}
    ←    {access, refresh}
    """
    serializer_class   = TokenObtainPairPatchedSerializer
    permission_classes = [permissions.AllowAny]



class MeView(generics.RetrieveAPIView):
    serializer_class   = MeSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_object(self):
        return self.request.user

    def get_serializer_context(self):          #  ← add this
        ctx = super().get_serializer_context()
        ctx["request"] = self.request
        return ctx


"""
# csms/views.py  (append at the end)

class ChargePointDetail(generics.RetrieveAPIView):
    #permission_classes = [permissions.IsAuthenticated]
    permission_classes = [permissions.IsAuthenticated & (IsRootAdmin | IsCpAdmin)]
    #permission_classes = [IsRootAdmin | IsCpAdmin]
    serializer_class   = ChargePointSerializer
    queryset           = ChargePoint.objects.all()

    def get_queryset(self):
        # reuse earlier helper to respect tenancy
        return _tenant_qs(ChargePoint, self.request.user)
"""

class ChargePointDetail(generics.RetrieveUpdateAPIView):
    """
    • GET    /api/charge-points/<id>/   → details
    • PATCH  /api/charge-points/<id>/   → partial update
    • PUT    /api/charge-points/<id>/   → full update
    """
    serializer_class = ChargePointSerializer
    permission_classes = [permissions.IsAuthenticated & (IsRootAdmin | IsCpAdmin)]

    def get_queryset(self):
        # only CPs that belong to the current tenant
        return _tenant_qs(ChargePoint, self.request.user)


class ChargePointCommand(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk):
        cp = get_object_or_404(
            _tenant_qs(ChargePoint, request.user), pk=pk
        )
        action = request.data.get("action")
        params = request.data.get("params", {})

        if not action:
            return Response({"detail": "action required"}, status=400)

        # plain, synchronous call – that’s it
        enqueue(cp.id, action, params)

        return Response({"detail": "queued"}, status=status.HTTP_202_ACCEPTED)


class CpCommandView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, cp_id):
        cp = get_object_or_404(ChargePoint, pk=cp_id, tenant=request.user.tenant)

        action  = request.data.get("action")
        params  = request.data.get("params", {})

        if not action:
            return Response({"detail": "action required"}, status=400)

        # ── put it in the queue ───────────────────────────────────────
        asyncio.create_task(enqueue(cp.id, action, params))   # fire-and-forget
        return Response({"detail": "queued"}, status=202)

