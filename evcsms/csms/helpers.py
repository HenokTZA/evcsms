# csms/helpers.py
"""
Tiny utilities that are reused by several views / serializers.
"""
from django.db.models import Q

"""
def _tenant_qs(model, user, *, with_owner_split=False):

    # avoid circular import
    from .models import Tenant, Transaction

    try:
        tenant = user.tenant                       # reverse OneToOne from User
    except Tenant.DoesNotExist:
        return model.objects.none()

    if model is Transaction:
        qs = model.objects.filter(cp__tenant=tenant)
    else:
        qs = model.objects.filter(tenant=tenant)

    if with_owner_split and getattr(user, "is_cp_admin", False):
        qs = qs.filter(owner=user)

    return qs
"""

def _tenant_qs(model, user):
    """
    Scope model querysets by tenancy/ownership.

    - super_admin: everything
    - owner with org: anything whose ChargePoint owner is in the same org
    - owner without org: anything whose ChargePoint owner == user
    - normal/end users: only their own sessions (if you allow them to list)
    """
    qs = model.objects.all()

    role = getattr(user, "role", None)
    if role == "super_admin" or getattr(user, "is_superuser", False):
        return qs

    # If you have an organization/tenant model on users & owners, prefer org scoping.
    if hasattr(user, "org_id") and user.org_id:
        # Owner/admins in the same org see all CPs owned by that org
        return qs.filter(cp__owner__org_id=user.org_id)

    # Fallback: user owns the CPs directly
    if model.__name__ == "Transaction":
        # Owners see everything that happened on their charge points
        owner_q = Q(cp__owner=user)

        # Optional: allow end users to see *their own* sessions (keep if you need user history page)
        user_sessions_q = Q(user_tag__user=user) if hasattr(model, "user_tag") else Q()

        # If you only want owners to hit this endpoint, drop `user_sessions_q`
        return qs.filter(owner_q | user_sessions_q).distinct()

    # Default fallback for other models (CPs, etc.)
    return qs.filter(cp__owner=user)
