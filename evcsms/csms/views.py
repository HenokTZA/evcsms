# csms/views.py
# csms/views.py
from __future__ import annotations
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated, AllowAny
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
from .models      import ChargePoint, Transaction, Tenant, ChargePointUserPrice
from .serializers import (
    ChargePointSerializer,
    TransactionSerializer,
    SignUpSerializer,
    MeSerializer,
    TokenObtainPairPatchedSerializer,
    UserSerializer,
    PasswordResetRequestSerializer,
    PasswordResetConfirmSerializer, 
    ChargePointUserPriceSerializer,
)
from .permissions import IsRootAdmin, IsCpAdmin, IsAdminOrReadOnly   # keep for later fine-graining
from .helpers     import _tenant_qs
from .permissions import IsAdminOrReadOnly

from django.contrib.auth.models import User
from django.contrib.auth.tokens import default_token_generator
from django.utils.http import urlsafe_base64_encode
from django.utils.encoding import force_bytes
from django.core.mail import send_mail
from django.db.models import Q
from django.utils.timezone import now, localtime

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

from django.db.models import Count

from .serializers import PublicSignupSerializer
from django.conf import settings
#from .ocpp_bridge import send_cp_command

from .serializers import PublicChargePointSerializer

import stripe
from .ocpp_bridge import enqueue

from datetime import datetime, timezone as dt_tz
from decimal import Decimal
from django.utils import timezone
from django.db.models.functions import Coalesce
from django.db.models import Q

from .pagination import StandardResultsSetPagination
from rest_framework.pagination import LimitOffsetPagination


User = get_user_model()
stripe.api_key = settings.STRIPE_SECRET_KEY

# ────────────────────────────────────────────────────────────────
#  Helpers
# ────────────────────────────────────────────────────────────────

def _is_super_admin(user):
    # Works for: role field, legacy roles, Django flags, even when user is a SimpleLazyObject
    u = getattr(user, "_wrapped", user)  # unwrap if needed
    role = (getattr(u, "role", None) or "").lower()
    legacy_super = {"super_admin", "root", "admin", "cp_admin"}
    return (
        role in legacy_super
        or getattr(u, "is_super_admin", False)
        or getattr(u, "is_superuser", False)
        or getattr(u, "is_staff", False)   # include only if staff means super in your app
    )

def _cp_queryset_for_user(user):
    qs = ChargePoint.objects.all()

    # Normal users see ALL CPs
    if not _is_super_admin(user):
        return qs

    # Super admins see ONLY their CPs
    field_names = {f.name for f in ChargePoint._meta.get_fields()}
    cond = Q()

    # Try common direct owner fields
    for fname in ("owner", "created_by", "user", "added_by", "admin", "cp_admin"):
        if fname in field_names:
            cond |= Q(**{fname: user})

    # Fallback via tenant.owner (if present)
    try:
        cond |= Q(tenant__owner=user)
    except Exception:
        pass

    # If nothing matched, show none (avoid leaking others’ CPs)
    return qs.filter(cond) if cond else qs.none()


def get_cp_for_user(cp_key, user):
    """
    Resolve a charge point the current user owns/has access to.
    Works for both numeric and string primary keys.
    """
    qs = _tenant_qs(ChargePoint, user)
    return get_object_or_404(qs, pk=cp_key)



class RevenueMoM(APIView):
    """
    GET /api/sessions/revenue/mom/?cp=<optional CP id or CP code>
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        cp_filter = request.query_params.get("cp")

        now = timezone.now()
        this_start = self._month_start(now)
        next_start = self._next_month_start(this_start)
        last_start = self._prev_month_start(this_start)

        qs = Transaction.objects.all()

        # Optional: filter to a single charge point by numeric id or by code (ChargePoint.cp_id)
        if cp_filter:
            if str(cp_filter).isdigit():
                qs = qs.filter(cp_id=int(cp_filter))
            else:
                qs = qs.filter(cp__cp_id=cp_filter)

        # Use stop_time when available, otherwise start_time
        qs = qs.annotate(event_time=Coalesce("stop_time", "start_time"))

        this_qs = qs.filter(event_time__gte=this_start, event_time__lt=next_start)
        last_qs = qs.filter(event_time__gte=last_start, event_time__lt=this_start)

        this_total = self._sum_revenue(this_qs)
        last_total = self._sum_revenue(last_qs)

        delta_abs = (this_total - last_total).quantize(Decimal("0.01"))
        delta_pct = None if last_total == 0 else float((delta_abs / last_total) * Decimal(100))
        direction = "up" if delta_abs > 0 else "down" if delta_abs < 0 else "flat"

        data = {
            "this_month": {
                "start": this_start.isoformat(),
                "end":   (next_start - timezone.timedelta(microseconds=1)).isoformat(),
                "total": str(this_total),
            },
            "last_month": {
                "start": last_start.isoformat(),
                "end":   (this_start - timezone.timedelta(microseconds=1)).isoformat(),
                "total": str(last_total),
            },
            "delta": {
                "absolute": str(delta_abs),
                "percent":  delta_pct,  # None if last month was 0
                "direction": direction,
            },
        }
        return Response(data, status=200)

    # ----- helpers -----

    def _month_start(self, dt):
        dt = timezone.localtime(dt) if timezone.is_aware(dt) else dt
        return dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0, tzinfo=dt.tzinfo)

    def _next_month_start(self, dt):
        if dt.month == 12:
            return dt.replace(year=dt.year + 1, month=1)
        return dt.replace(month=dt.month + 1)

    def _prev_month_start(self, dt):
        if dt.month == 1:
            return dt.replace(year=dt.year - 1, month=12)
        return dt.replace(month=dt.month - 1)

    def _sum_revenue(self, qs):
        """
        Calculate revenue per Transaction:

          energy_cost = ((latest_wh - start_wh) / 1000) * price_kwh_at_start
          time_cost   = hours_between(start_time, stop_time) * price_hour_at_start

        Falls back to 0 where pieces are missing. Adjust if you store a final
        amount on the transaction (then prefer that).
        """
        D = lambda x: Decimal(str(x)) if x is not None else Decimal("0")
        total = Decimal("0")

        for t in qs.select_related("cp"):
            # Energy component
            kwh = None
            if getattr(t, "latest_wh", None) is not None and getattr(t, "start_wh", None) is not None:
                try:
                    kwh_val = (t.latest_wh - t.start_wh) / 1000.0
                    if kwh_val > 0:
                        kwh = kwh_val
                except Exception:
                    kwh = None
            ppk = getattr(t, "price_kwh_at_start", None)
            energy_cost = (D(kwh) * D(ppk)) if kwh is not None and ppk is not None else Decimal("0")

            # Time component (only if we have a stop_time)
            time_cost = Decimal("0")
            if getattr(t, "start_time", None) and getattr(t, "stop_time", None):
                try:
                    dur_hours = (t.stop_time - t.start_time).total_seconds() / 3600.0
                    if dur_hours > 0 and getattr(t, "price_hour_at_start", None) is not None:
                        time_cost = D(dur_hours) * D(t.price_hour_at_start)
                except Exception:
                    pass

            total += (energy_cost + time_cost)

        return total.quantize(Decimal("0.01"))






class SessionsRevenueStats(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        qs = _tenant_qs(Transaction, request.user).select_related("cp")

        tznow = now()
        start_of_month = tznow.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        as_dec = lambda x: Decimal(str(x or 0))

        lifetime = Decimal("0")
        month    = Decimal("0")

        for t in qs:
            cp  = getattr(t, "cp", None)

            # energy part
            kwh = as_dec(getattr(t, "kwh", None) or getattr(t, "energy", None) or 0)
            ppk = as_dec(getattr(cp, "price_per_kwh", None) or 0)
            by_energy = kwh * ppk

            # time part (optional)
            pph = as_dec(getattr(cp, "price_per_hour", None) or 0)
            st  = getattr(t, "start_time", None)
            et  = getattr(t, "stop_time", None) or getattr(t, "end_time", None)
            by_time = Decimal("0")
            if pph and st and et:
                try:
                    hours = Decimal(str(max(0.0, (et - st).total_seconds() / 3600.0)))
                    by_time = hours * pph
                except Exception:
                    pass

            amt = (by_energy + by_time).quantize(Decimal("0.01"))
            lifetime += amt

            # count into this month (use stop/end if present, else start)
            pivot = et or st
            if pivot:
                try:
                    when = localtime(pivot)
                except Exception:
                    when = pivot
                if when >= start_of_month:
                    month += amt

        return Response({
            "lifetime": float(lifetime),
            "month": float(month),
            "month_label": tznow.strftime("%B %Y"),
        }, status=200)




class AdminCPStatusStats(APIView):
    """
    GET /api/admin/charge-points/stats/
    Returns:
    {
      "by_status": {"Available": 3, "Charging": 2, ...},
      "totals":    {"available": 3, "unavailable": 1, "charging": 2, "occupied": 0, "preparing": 1, "other": 0}
    }
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        # Only super_admin’s own CPs
        qs = ChargePoint.objects.filter(tenant__owner=user)

        # Aggregate by raw status value
        rows = qs.values("status").annotate(count=Count("id")).order_by()
        by_status = { (r["status"] or "Unknown"): r["count"] for r in rows }

        # Build the requested buckets (case-insensitive)
        lm = { (k or "unknown").lower(): v for k, v in by_status.items() }
        available   = lm.get("available", 0)
        unavailable = lm.get("unavailable", 0)
        charging    = lm.get("charging", 0)
        occupied    = lm.get("occupied", 0)
        preparing   = lm.get("preparing", 0)
        other = sum(lm.values()) - (available + unavailable + charging + occupied + preparing)

        data = {
            "by_status": by_status,
            "totals": {
                "available": available,
                "unavailable": unavailable,
                "charging": charging,
                "occupied": occupied,
                "preparing": preparing,
                "other": other,
            },
        }
        return Response(data, status=200)




class PublicChargePointList(generics.ListAPIView):
    """
    GET /api/public/charge-points/
    Logged-in normal users see CPs under tenants owned by superadmins (role='root').
    """
    permission_classes = [permissions.IsAuthenticated]
    serializer_class   = PublicChargePointSerializer

    def get_queryset(self):
        return ChargePoint.objects.filter(tenant__owner__role='super_admin')

    def get_serializer_context(self):
        ctx = super().get_serializer_context()
        ctx["request"] = self.request
        return ctx

class PublicChargePointDetail(generics.RetrieveAPIView):
    """
    GET /api/public/charge-points/<id>/
    """
    permission_classes = [permissions.IsAuthenticated]
    serializer_class   = PublicChargePointSerializer

    def get_queryset(self):
        return ChargePoint.objects.filter(tenant__owner__role='super_admin')

    def get_serializer_context(self):
        ctx = super().get_serializer_context()
        ctx["request"] = self.request
        return ctx


# ── Admin: per-user prices on a specific CP ─────────────────────────

class _CPAdminMixin:
    permission_classes = [IsAuthenticated]  # and your IsRootAdmin/IsCpAdmin if you want

    def _get_cp(self, request, pk):
        # reuse your scope helper if you want to keep tenancy filtering tight
        cp = get_object_or_404(_tenant_qs(ChargePoint, request.user), pk=pk)
        return cp


class CPUserPriceListCreate(_CPAdminMixin, APIView):
    """
    GET  /api/charge-points/<pk>/user-prices/   → list overrides
    POST /api/charge-points/<pk>/user-prices/   → create {email, price_per_kwh?, price_per_hour?}
    """
    def get(self, request, pk):
        cp = self._get_cp(request, pk)
        qs = cp.user_prices.select_related("user").order_by("user__email")
        ser = ChargePointUserPriceSerializer(qs, many=True)
        return Response(ser.data, status=200)

    def post(self, request, pk):
        cp = self._get_cp(request, pk)
        ser = ChargePointUserPriceSerializer(data=request.data, context={"cp": cp})
        ser.is_valid(raise_exception=True)
        obj = ser.save()
        return Response(ChargePointUserPriceSerializer(obj).data, status=201)


class CPUserPriceDetail(_CPAdminMixin, APIView):
    """
    PATCH /api/charge-points/<pk>/user-prices/<upid>/
    DELETE /api/charge-points/<pk>/user-prices/<upid>/
    """
    def _get_obj(self, request, pk, upid):
        cp = self._get_cp(request, pk)
        return get_object_or_404(ChargePointUserPrice, pk=upid, cp=cp)

    def patch(self, request, pk, upid):
        obj = self._get_obj(request, pk, upid)
        ser = ChargePointUserPriceSerializer(obj, data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        ser.save()
        return Response(ser.data, status=200)

    def delete(self, request, pk, upid):
        obj = self._get_obj(request, pk, upid)
        obj.delete()
        return Response(status=204)





class PublicCreateCheckoutSession(APIView):
    """
    POST /api/public/charge-points/<pk>/checkout/
    Body: { "amount_cents": 500, "currency": "eur" }  # defaults ok
    Returns: { "url": "https://checkout.stripe.com/..." }
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk):
        cp = get_object_or_404(ChargePoint, pk=pk, tenant__owner__role='super_admin')
        amount_cents = int(request.data.get("amount_cents", 500))  # €5 default top-up
        currency     = request.data.get("currency", "eur")

        success_url = f"{settings.FRONTEND_BASE}/app/map/{cp.pk}?session_id={{CHECKOUT_SESSION_ID}}"
        cancel_url  = f"{settings.FRONTEND_BASE}/app/map/{cp.pk}?cancelled=1"

        session = stripe.checkout.Session.create(
            mode="payment",
            payment_method_types=["card"],
            line_items=[{
                "price_data": {
                    "currency": currency,
                    "product_data": {"name": f"Charging session for {cp.name or cp.pk}"},
                    "unit_amount": amount_cents,
                },
                "quantity": 1,
            }],
            success_url=success_url,
            cancel_url=cancel_url,
            metadata={
                "cp_id": cp.pk,
                "user_id": str(request.user.id),
                "user_email": request.user.email or "",
            }
        )
        return Response({"url": session.url}, status=200)


class PublicStartAfterCheckout(APIView):
    """
    POST /api/public/charge-points/<pk>/start-after-checkout/
    Body: { "session_id": "cs_test_..." }
    Verifies payment == paid and then enqueues RemoteStartTransaction.
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk):
        cp = get_object_or_404(ChargePoint, pk=pk, tenant__owner__role='super_admin')
        session_id = request.data.get("session_id")
        if not session_id:
            return Response({"detail": "session_id required"}, status=400)

        ses = stripe.checkout.Session.retrieve(session_id)
        if ses.get("payment_status") != "paid":
            return Response({"detail": "Payment not completed"}, status=400)

        # Start charging using connector_id and an idTag for this user
        id_tag = request.user.username or request.user.email or "user"
        enqueue(cp.id, "RemoteStartTransaction", {
            "connectorId": cp.connector_id,
            "idTag": id_tag
        })

        return Response({"detail": "started"}, status=200)


class PublicStopCharging(APIView):
    """
    POST /api/public/charge-points/<pk>/stop/
    Finds active tx on cp and enqueues RemoteStopTransaction.
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk):
        cp = get_object_or_404(ChargePoint, pk=pk, tenant__owner__role='root')
        tx = Transaction.objects.filter(cp=cp, stop_time__isnull=True).order_by("-start_time").first()
        if not tx:
            return Response({"detail": "No active session"}, status=404)

        enqueue(cp.id, "RemoteStopTransaction", {"transactionId": tx.tx_id})
        return Response({"detail": "stopping"}, status=200)




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
    permission_classes = [AllowAny]

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
    permission_classes = [AllowAny]

    def post(self, request):
        ser = self.get_serializer(data=request.data)
        ser.is_valid(raise_exception=True)
        ser.save()
        return Response({"detail": "Password has been reset"}, status=status.HTTP_200_OK)



class ChargePointList(generics.ListAPIView):
    serializer_class   = ChargePointSerializer
    permission_classes = [IsAdminOrReadOnly]
    def get_queryset(self):
        return _cp_queryset_for_user(self.request.user)

    def perform_create(self, serializer):
        # make sure we stamp ownership on create so filtering works
        user = self.request.user
        data = {}
        # Prefer explicit owner field if it exists
        if "owner" in {f.name for f in ChargePoint._meta.get_fields()}:
            data["owner"] = user
        # else, if you use tenant.owner, ensure tenant defaults to user's tenant here
        serializer.save(**data)

class ChargePointDetail(generics.RetrieveUpdateDestroyAPIView):
    serializer_class   = ChargePointSerializer
    permission_classes = [IsAdminOrReadOnly]
    def get_queryset(self):
        return _cp_queryset_for_user(self.request.user)
    #lookup_field       = "pk"  # default anyway; explicit for clarity


class ChargePointByCode(generics.RetrieveAPIView):
    serializer_class   = ChargePointSerializer
    permission_classes = [IsAdminOrReadOnly]
    lookup_field = "cp_id"      # adjust to your field name that stores "THIRD"
    def get_queryset(self):
        # Scope by role exactly like the others
        return _cp_queryset_for_user(self.request.user)

    # Optional: case-insensitive lookup
    def get_object(self):
        code = self.kwargs["cp_id"]
        return generics.get_object_or_404(self.get_queryset(), cp_id__iexact=code)


class ThirtyLimitOffset(LimitOffsetPagination):
    default_limit = 30
    max_limit = 200


"""
class TransactionList(generics.ListAPIView):
    serializer_class   = TransactionSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        # all sessions for the current tenant/org
        qs = (
            _tenant_qs(Transaction, self.request.user)
            .order_by("-start_time", "-pk")  # newest first; pk tiebreaker
        )

        # lightweight limit/offset
        qp = self.request.query_params
        try:
            limit = int(qp.get("limit", "0"))
        except (TypeError, ValueError):
            limit = 0
        try:
            offset = int(qp.get("offset", "0"))
        except (TypeError, ValueError):
            offset = 0

        if limit > 0:
            limit = min(limit, 200)  # safety cap
            return qs[offset: offset + limit]
        if offset > 0:
            # if someone passes offset without limit, give a default window
            return qs[offset: offset + 50]

        return qs
"""


class TransactionList(generics.ListAPIView):
    serializer_class = TransactionSerializer
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = ThirtyLimitOffset

    def get_queryset(self):
        qs = (
            _tenant_qs(Transaction, self.request.user)
            .order_by("-start_time", "-pk")
        )

        qp = self.request.query_params
        if "limit" in qp:  # manual slice mode for dashboard
            try:
                limit = min(int(qp.get("limit", "0")), 200)
            except Exception:
                limit = 0
            try:
                offset = max(int(qp.get("offset", "0")), 0)
            except Exception:
                offset = 0

            if limit > 0:
                return qs[offset: offset + limit]
            if offset > 0:
                return qs[offset: offset + 50]

        return qs

    def paginate_queryset(self, queryset):
        # If using manual slicing (limit present), skip DRF pagination so response is a plain array
        if "limit" in self.request.query_params:
            return None
        return super().paginate_queryset(queryset)




class SignupView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        ser = SignUpSerializer(data=request.data)
        if ser.is_valid():
            ser.save()
            return Response({"detail": "ok"}, status=status.HTTP_201_CREATED)
        return Response(ser.errors, status=status.HTTP_400_BAD_REQUEST)


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
