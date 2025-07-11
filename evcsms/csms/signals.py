# csms/signals.py
from django.db.models.signals import post_save
from django.dispatch           import receiver
from .models                   import User, Tenant

@receiver(post_save, sender=User)
def create_tenant_for_root(sender, instance, created, **kw):
    if created and instance.role == "root":
        Tenant.objects.create(name=f"{instance.username} Org", owner=instance, slug=instance.username)

