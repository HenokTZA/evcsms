# csms/views.py
# csms/views.py
from __future__ import annotations
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status

#from .ocpp_bridge import send_cp_command
from .models import ChargePoint


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
    PasswordResetRequestSerializer,
    PasswordResetConfirmSerializer,
)
from .permissions import IsRootAdmin, IsCpAdmin   # keep for later fine-graining
from .helpers     import _tenant_qs
from .permissions import IsAdminOrReadOnly

from django.contrib.auth.models import User
from django.contrib.auth.tokens import default_token_generator
from django.utils.http import urlsafe_base64_encode
from django.utils.encoding import force_bytes
from django.core.mail import send_mail

from io import BytesIO
from decimal import Decimal
from datetime import datetime, time

from django.http import HttpResponse
from django.utils.timezone import make_aware, get_current_timezone
from rest_framework.permissions import IsAuthenticated
from rest_framework import status
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
import pandas as pd

from rest_framework_simplejwt.tokens import RefreshToken, TokenError

from django.utils.timezone import now
from typing import Any, Dict
from csms.ocpp_hub import hub

from .serializers import PublicSignupSerializer
from django.conf import settings
#from .ocpp_bridge import send_cp_command


User = get_user_model()
# ────────────────────────────────────────────────────────────────
#  Helpers
# ────────────────────────────────────────────────────────────────

class LogoutView(APIView):
    # permission_classes = [IsAuthenticated]  # ← remove; allow anonymous

    def post(self, request):
        refresh_token = request.data.get("refresh")
        if not refresh_token:
            # Be lenient: client may have already nuked storage
            return Response({"detail": "Logged out"}, status=status.HTTP_200_OK)

        try:
            token = RefreshToken(refresh_token)
            token.blacklist()
        except TokenError:
            # Token may be expired/invalid — treat as logged out
            return Response({"detail": "Logged out"}, status=status.HTTP_200_OK)

        return Response({"detail": "Logout successful"}, status=status.HTTP_200_OK)



class GenerateReportView(APIView):
    permission_classes = [IsAuthenticated]
    # If you’re using SessionAuthentication + CSRF and don’t want to pass a token,
    # you can exempt just this endpoint (uncomment next 2 lines):
    # from rest_framework.authentication import SessionAuthentication
    # authentication_classes = (CsrfExemptSessionAuthentication, )

    def post(self, request):
        data = request.data
        cp_ids   = data.get("cp_ids") or []
        start    = data.get("start")
        end      = data.get("end")
        tax_rate = Decimal(str(data.get("tax_rate") or "0"))
        fmt      = (data.get("format") or "pdf").lower()

        if not cp_ids or not start or not end:
            return Response({"detail": "cp_ids, start, end are required."},
                            status=status.HTTP_400_BAD_REQUEST)

        tz = get_current_timezone()
        try:
            d1 = make_aware(datetime.combine(datetime.fromisoformat(start).date(), time.min), tz)
            d2 = make_aware(datetime.combine(datetime.fromisoformat(end).date(),   time.max), tz)
        except Exception:
            return Response({"detail": "Invalid date format (YYYY-MM-DD)."},
                            status=status.HTTP_400_BAD_REQUEST)

        # Limit CPs to current user’s tenant/org if you have that relation:
        cps_qs = ChargePoint.objects.filter(id__in=cp_ids)
        # e.g. cps_qs = cps_qs.filter(tenant=request.user.tenant)

        cps = {cp.id: cp for cp in cps_qs}
        if not cps:
            return Response({"detail": "No accessible charge points."},
                            status=status.HTTP_400_BAD_REQUEST)

        tx_qs = (Transaction.objects
                 .filter(cp_id__in=cps.keys())
                 .filter(start_time__gte=d1, start_time__lte=d2))

        # Per-CP aggregation
        per_cp = {}  # cp_id -> {"name": ..., "kwh": Decimal, "earned": Decimal}
        def as_dec(x): return Decimal(str(x or 0))

        for t in tx_qs:
            cp = cps.get(t.cp_id)
            if not cp:
                continue
            key = t.cp_id
            if key not in per_cp:
                # Prefer a human name if you have it; fallback to f"CP {id}"
                cp_name = getattr(cp, "name", None) or f"CP {cp.id}"
                per_cp[key] = {"name": cp_name, "kwh": Decimal("0"), "earned": Decimal("0")}

            # Your model might expose kWh via a property or method; adjust as needed
            kwh = as_dec(getattr(t, "kwh", 0))
            price = t.total_price() if callable(getattr(t, "total_price", None)) else getattr(t, "total_price", 0)
            per_cp[key]["kwh"]    += as_dec(kwh)
            per_cp[key]["earned"] += as_dec(price)

        # Summaries
        subtotal = sum(v["earned"] for v in per_cp.values())
        tax_amount = (subtotal * tax_rate / Decimal("100")).quantize(Decimal("0.01"))
        total_after_tax = (subtotal - tax_amount).quantize(Decimal("0.01"))

        owner_name = getattr(request.user, "get_full_name", lambda: "")() or request.user.get_username()
        generated_on = datetime.now(tz).strftime("%Y-%m-%d %H:%M")

        # Build rows for export
        rows = []
        for v in per_cp.values():
            rows.append({
                "CP": v["name"],
                "kWh": float(v["kwh"]),
                "Earned (€)": float(v["earned"]),
            })

        filename = f"report_{start}_{end}"

        if fmt == "excel":
            return self._excel_response(
                owner_name, start, end, generated_on, tax_rate,
                rows, subtotal, tax_amount, total_after_tax, filename
            )
        else:
            return self._pdf_response(
                owner_name, start, end, generated_on, tax_rate,
                rows, subtotal, tax_amount, total_after_tax, filename
            )

    def _excel_response(self, owner, start, end, gen, tax_rate,
                        rows, subtotal, tax_amount, total_after_tax, filename):
        buf = BytesIO()
        df = pd.DataFrame(rows, columns=["CP", "kWh", "Earned (€)"])
        with pd.ExcelWriter(buf, engine="xlsxwriter") as writer:
            # Header sheet
            header = pd.DataFrame([
                ["Owner", owner],
                ["Generated on", gen],
                ["Period", f"{start} to {end}"],
                ["Tax rate (%)", float(tax_rate)],
            ], columns=["Field", "Value"])
            header.to_excel(writer, sheet_name="Summary", index=False, startrow=0)

            # Summary rows
            summary = pd.DataFrame([
                ["Subtotal (€)", float(subtotal)],
                [f"Tax ({tax_rate}%)", float(tax_amount)],
                ["Total after tax (€)", float(total_after_tax)],
            ], columns=["Item", "Value"])
            summary.to_excel(writer, sheet_name="Summary", index=False, startrow=7)

            # Detail
            df.to_excel(writer, sheet_name="By Charge Point", index=False)

        buf.seek(0)
        resp = HttpResponse(
            buf.getvalue(),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        resp["Content-Disposition"] = f'attachment; filename="{filename}.xlsx"'
        return resp

    def _pdf_response(self, owner, start, end, gen, tax_rate,
                      rows, subtotal, tax_amount, total_after_tax, filename):
        buf = BytesIO()
        c = canvas.Canvas(buf, pagesize=A4)
        width, height = A4

        y = height - 40
        c.setFont("Helvetica-Bold", 14); c.drawString(40, y, "EV Charging Report"); y -= 18
        c.setFont("Helvetica", 10)
        c.drawString(40, y, f"Owner: {owner}"); y -= 14
        c.drawString(40, y, f"Generated on: {gen}"); y -= 14
        c.drawString(40, y, f"Period: {start} to {end}"); y -= 14
        c.drawString(40, y, f"Tax rate: {tax_rate}%"); y -= 18

        # table header
        headers = ["CP", "kWh", "Earned (€)"]
        colx = [40, 360, 440]
        c.setFont("Helvetica-Bold", 10)
        for i, h in enumerate(headers): c.drawString(colx[i], y, h)
        y -= 12; c.line(40, y, width-40, y); y -= 10

        c.setFont("Helvetica", 10)
        for r in rows:
            if y < 90:
                c.showPage(); y = height - 40
                c.setFont("Helvetica-Bold", 10)
                for i, h in enumerate(headers): c.drawString(colx[i], y, h)
                y -= 12; c.line(40, y, width-40, y); y -= 10; c.setFont("Helvetica", 10)

            c.drawString(colx[0], y, r["CP"])
            c.drawRightString(colx[1]+60, y, f'{r["kWh"]:.3f}')
            c.drawRightString(colx[2]+60, y, f'{r["Earned (€)"]:.2f}')
            y -= 14

        y -= 10; c.line(40, y, width-40, y); y -= 16
        c.setFont("Helvetica-Bold", 11)
        c.drawRightString(width-40, y, f"Subtotal (€): {subtotal:.2f}"); y -= 14
        c.drawRightString(width-40, y, f"Tax ({tax_rate}%): {tax_amount:.2f}"); y -= 14
        c.drawRightString(width-40, y, f"Total after tax (€): {total_after_tax:.2f}")

        c.showPage(); c.save()
        buf.seek(0)
        resp = HttpResponse(buf.getvalue(), content_type="application/pdf")
        resp["Content-Disposition"] = f'attachment; filename="{filename}.pdf"'
        return resp



class PasswordResetRequestView(generics.GenericAPIView):
    serializer_class = PasswordResetRequestSerializer

    def post(self, request):
        ser = self.get_serializer(data=request.data)
        ser.is_valid(raise_exception=True)
        user = User.objects.get(email=ser.validated_data["email"], is_active=True)

        uid = urlsafe_base64_encode(force_bytes(user.pk))
        token = default_token_generator.make_token(user)
        reset_link = f"http://147.93.127.215:5173/reset-password/{uid}/{token}"

        send_mail(
            subject="Reset your password",
            message=f"Click here to reset your password:\n\n{reset_link}",
            from_email=None,
            recipient_list=[user.email],
        )
        return Response({"detail": "Password reset e-mail sent"}, status=status.HTTP_200_OK)


class PasswordResetConfirmView(generics.GenericAPIView):
    serializer_class = PasswordResetConfirmSerializer

    def post(self, request):
        ser = self.get_serializer(data=request.data)
        ser.is_valid(raise_exception=True)
        ser.save()
        return Response({"detail": "Password has been reset"}, status=status.HTTP_200_OK)



class ChargePointList(generics.ListAPIView):
    serializer_class   = ChargePointSerializer
    permission_classes = [IsAdminOrReadOnly]
    queryset           = ChargePoint.objects.all()  # ← NO tenant filter

class ChargePointDetail(generics.RetrieveUpdateDestroyAPIView):
    serializer_class   = ChargePointSerializer
    permission_classes = [IsAdminOrReadOnly]
    queryset = ChargePoint.objects.all()
    lookup_field       = "pk"  # default anyway; explicit for clarity


class ChargePointByCode(generics.RetrieveAPIView):
    serializer_class   = ChargePointSerializer
    permission_classes = [IsAdminOrReadOnly]
    lookup_field = "cp_id"      # adjust to your field name that stores "THIRD"
    queryset = ChargePoint.objects.all()


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
class SignupView(generics.CreateAPIView):
    permission_classes = [permissions.AllowAny]
    serializer_class = PublicSignupSerializer

    def perform_create(self, serializer):
        role = serializer.validated_data.get("role", "user")
        if not settings.ALLOW_PUBLIC_SUPER_ADMIN_SIGNUP and role == "super_admin":
            # Force downgrade if the flag is off
            serializer.validated_data["role"] = "user"
        serializer.save()


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
