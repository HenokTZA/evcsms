# csms/views.py
from django.contrib.auth import get_user_model
from rest_framework import generics, permissions, status
from rest_framework.views import APIView
from rest_framework.response import Response

from rest_framework_simplejwt.views import TokenObtainPairView

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


User = get_user_model()
# ────────────────────────────────────────────────────────────────
#  Helpers
# ────────────────────────────────────────────────────────────────

"""
def _tenant_qs(model, user, *, with_owner_split=False):

    qs = model.objects.select_related("tenant")

    if user.is_root_admin:
        return qs.filter(tenant=user.tenant)

    if user.is_cp_admin:
        base = qs.filter(tenant=user.tenant)
        if with_owner_split:                       # ✎ only if you have `.owner`
            return base.filter(owner=user)
        return base

    # customers see nothing here
    return qs.none()
"""

"""
def _tenant_qs(model, user, *, with_owner_split=False):

    tenant = user.tenant                      # ⇐ safe: user always has one now
    if model._meta.get_field_names().count("tenant"):
        qs = model.objects.filter(tenant=tenant)
    elif model is Transaction:                # indirect link via ChargePoint
        qs = model.objects.filter(cp__tenant=tenant)
    else:
        raise ImproperlyConfigured(
            f"_tenant_qs: {model.__name__} has no tenant relation"
        )

    if with_owner_split:
        return qs.select_related("owner")     # nice-to-have for ChargePoint
    return qs
"""
"""
# helper that understands which models have .tenant and which don’t
def _tenant_qs(model, user, *, with_owner_split=False):
    tenant = getattr(user, "tenant", None)
    qs = model.objects

    # Model has a tenant FK
    if model._meta.get_field_names(include_m2m=False).__contains__("tenant"):
        qs = qs.filter(tenant=tenant)

    # Model is Transaction – follow cp__tenant
    elif model.__name__ == "Transaction":
        qs = qs.filter(cp__tenant=tenant)

    # split between “mine” and “others” for CP list if requested
    if with_owner_split and model.__name__ == "ChargePoint":
        qs = qs.annotate(is_mine=Case(
            When(owner=user, then=Value(True)),
            default=Value(False),
            output_field=BooleanField(),
        )).order_by("-is_mine", "name")

    return qs
"""

"""
def _tenant_qs(model, user):

    if not hasattr(user, "tenant"):
        return model.objects.none()

    tenant = user.tenant

    if model is Transaction:
        return model.objects.filter(cp__tenant=tenant)

    # everything else that has a direct tenant FK
    return model.objects.filter(tenant=tenant)
"""


# csms/views.py  (only this helper)
def _tenant_qs(model, user, *, with_owner_split=False):
    """
    Return a queryset limited to the user’s tenant.
    Transaction → follow cp__tenant
    every other model that has tenant FK directly → tenant
    """
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
# ────────────────────────────────────────────────────────────────
#  REST endpoints
# ────────────────────────────────────────────────────────────────
class ChargePointList(generics.ListAPIView):

    serializer_class   = ChargePointSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return _tenant_qs(ChargePoint, self.request.user, with_owner_split=True)
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


"""
class TransactionList(generics.ListAPIView):

    serializer_class   = TransactionSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return _tenant_qs(Transaction, self.request.user).order_by("-pk")
"""

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

"""
class MeView(generics.RetrieveAPIView):

    serializer_class   = MeSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_object(self):
        return self.request.user
"""
"""
class MeView(generics.RetrieveAPIView):
    serializer_class   = UserSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_object(self):
        return self.request.user
"""

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
def _tenant_qs(model, user, *, with_owner_split=False):

    try:
        tenant = user.tenant          # ⇦ FK reverse accessor
    except Tenant.DoesNotExist:
        return model.objects.none()

    qs = model.objects.filter(tenant=tenant)
    if with_owner_split and user.is_cp_admin:
        qs = qs.filter(owner=user)
    return qs
"""
