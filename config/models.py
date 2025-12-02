from django.db import models

class PlatformSetting(models.Model):
  """
  Ein Schlüssel‑Wert‑Paar für app‑spezifische Einstellungen.
  """
  key = models.CharField(max_length=200, unique=True)
  value = models.TextField()  # JSON/Text/Int etc.
  description = models.CharField(max_length=500, blank=True)

  class Meta:
      verbose_name = "Platform Setting"
      verbose_name_plural = "Platform Settings"

  def __str__(self):
      return f"{self.key} = {self.value}"