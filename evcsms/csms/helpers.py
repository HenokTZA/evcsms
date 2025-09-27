# csms/helpers.py
"""
Tiny utilities that are reused by several views / serializers.
"""

from django.db.models import Q
from typing import Set



"""
def _tenant_qs(model, user):

    qs = model.objects.all()

    # Charge points, commands, per-CP prices → all hang off a CP
    if model.__name__ in ("ChargePoint", "CPCommand", "ChargePointUserPrice"):
        return qs.filter(tenant__owner=user)

    # Transactions → traverse via CP. A super admin only sees sessions performed on his/her CPs.
    if model.__name__ == "Transaction":
        owner_cp_q = Q(cp__tenant__owner=user)

        # Optional: allow end users to see their own sessions by tag (keep if you have such a flow).
        # If you *don’t* want this, just:  return qs.filter(owner_cp_q)
        try:
            user_sessions_q = Q(user_tag__user=user)
        except Exception:
            user_sessions_q = Q()

        return qs.filter(owner_cp_q | user_sessions_q).distinct()

    # Default (defensive) — also scope through CP → tenant → owner
    if hasattr(model, "_meta") and any(f.name == "cp" for f in model._meta.fields):
        return qs.filter(cp__tenant__owner=user)

    # If nothing applies, safest is to return nothing
    return qs.none()
"""

def _tenant_qs(model, user):
    """
    Return a queryset limited to the current owner's tenant scope.

    - ChargePoint-like models: tenant__owner = user
    - Transaction: cp__tenant__owner = user
    - Any model with a 'cp' FK: scope via cp__tenant__owner
    - Otherwise: return none (defensive)
    """
    qs = model.objects.all()

    name = model.__name__

    # Anything directly owned by a tenant via ChargePoint.tenant
    if name in ("ChargePoint", "CPCommand", "ChargePointUserPrice"):
        return qs.filter(tenant__owner=user)

    # Transactions must be limited to CPs owned by the current super admin
    if name == "Transaction":
        return qs.filter(cp__tenant__owner=user).distinct()

    # Defensive default: if model has a 'cp' FK, scope via cp → tenant → owner
    if hasattr(model, "_meta") and any(f.name == "cp" for f in model._meta.fields):
        return qs.filter(cp__tenant__owner=user)

    # If nothing matches, safest is to return empty
    return qs.none()



def get_user_identifiers(user):
    vals = set()

    # common: stable “idTag” we create for this user when starting public sessions
    vals.add(f"user-{user.id}")

    # add other fields if you have them
    for attr in ("rfid_tag", "rfid", "card_uid", "id_tag", "email", "username"):
        v = getattr(user, attr, None)
        if v:
            vals.add(str(v))

    return [v for v in vals if v]
