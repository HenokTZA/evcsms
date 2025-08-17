# csms/permissions.py
from rest_framework.permissions import BasePermission, SAFE_METHODS

# Treat these as super-admin roles
LEGACY_SUPER_ROLES = {"super_admin", "root", "admin", "cp_admin"}

def is_super_admin(user):
    # Works with SimpleLazyObject-wrapped users
    u = getattr(user, "_wrapped", user)
    role = (getattr(u, "role", "") or "").lower()
    return (
        role in LEGACY_SUPER_ROLES
        or getattr(u, "is_super_admin", False)
        or getattr(u, "is_superuser", False)
        or getattr(u, "is_staff", False)  # keep only if staff == super in your app
    )

class IsCustomer(BasePermission):
    def has_permission(self, request, view):
        return request.user.is_authenticated and getattr(request.user, "is_customer", False)

class IsCpAdmin(BasePermission):
    def has_permission(self, request, view):
        return request.user.is_authenticated and getattr(request.user, "is_cp_admin", False)

class IsRootAdmin(BasePermission):
    def has_permission(self, request, view):
        # legacy support: role == root counts as super
        return request.user.is_authenticated and (
            getattr(request.user, "role", "").lower() == "root"
            or is_super_admin(request.user)
        )

class IsNormalUser(BasePermission):
    def has_permission(self, request, view):
        u = getattr(request.user, "_wrapped", request.user)
        role = (getattr(u, "role", "") or "").lower()
        return request.user.is_authenticated and role not in LEGACY_SUPER_ROLES

class IsSuperAdmin(BasePermission):
    def has_permission(self, request, view):
        return request.user.is_authenticated and is_super_admin(request.user)

class IsAdminOrReadOnly(BasePermission):
    """Authenticated users can read; only super admins can write."""
    def has_permission(self, request, view):
        if request.method in SAFE_METHODS:
            return request.user.is_authenticated
        return request.user.is_authenticated and is_super_admin(request.user)

    def has_object_permission(self, request, view, obj):
        # IMPORTANT: allow object-level GET/HEAD/OPTIONS for any authenticated user
        if request.method in SAFE_METHODS:
            return request.user.is_authenticated
        # For writes, reuse the same rule as above
        return self.has_permission(request, view)

