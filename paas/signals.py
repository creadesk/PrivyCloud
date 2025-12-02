'''
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone
from .models import ProvisionedApp
from .tasks import delete_container_by_id
import logging

logger = logging.getLogger(__name__)

@receiver(post_save, sender=ProvisionedApp)
def schedule_auto_delete(sender, instance: ProvisionedApp, created: bool, **kwargs):
  if not created:
      return  # Nur bei *erster* Save

  now = timezone.now()
  if instance.expires_at <= now:
      # Schon abgelaufen – sofort löschen
      delete_container_by_id.delay(instance.id)
      logger.info(f"[auto-delete] {instance} sofort gelöscht (abgelaufen).")
  else:
      delta = (instance.expires_at - now).total_seconds()
      delete_container_by_id.apply_async(args=[instance.id], countdown=delta)
      logger.info(f"[auto-delete] {instance} geplant in {delta:.0f}s.")
'''