from django.apps import AppConfig
import logging

logger = logging.getLogger(__name__)

class PaasConfig(AppConfig):
  name = 'paas'

  def ready(self):
      # Importiere Signals hier, damit sie beim App‑Start registriert werden.
      import paas.signals  # noqa
      logger.debug("Paas signals loaded.")
