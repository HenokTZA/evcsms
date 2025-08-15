# csms/views_reports.py
from io import BytesIO
from decimal import Decimal
from datetime import datetime, time

from django.http import HttpResponse
from django.utils.timezone import make_aware, get_current_timezone
from django.contrib.auth import get_user_model

from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status

from .models import Transaction, ChargePoint

# Excel
import pandas as pd

# PDF
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

User = get_user_model()

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

