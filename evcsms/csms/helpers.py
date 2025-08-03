# csms/helpers.py
"""
Tiny utilities that are reused by several views / serializers.
"""

def _tenant_qs(model, user, *, with_owner_split=False):
    """
    Return a queryset limited to the user’s tenant.

    *   For Transaction -> follow cp__tenant.
    *   For every other model that has a direct tenant FK -> tenant.
    """
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

