import json
from django.core.cache import cache
from config.models import PlatformSetting

CACHE_TIMEOUT = 60 * 5  # 5 Minuten

def get_app_setting(key, default=None, cast_type=str):
  """
  Liefert einen Wert aus der Datenbank / dem Cache.
  * cast_type: str, int, bool, json, ...
  """
  cache_key = f"app_setting:{key}"
  cached = cache.get(cache_key)
  if cached is not None:
      return cast_type(cached)

  try:
      setting = PlatformSetting.objects.get(key=key)
  except PlatformSetting.DoesNotExist:
      return default

  val = setting.value
  # ggf. typkonvertieren
  try:
      if cast_type == bool:
          val = val.lower() in ("true", "1", "yes", "on")
      elif cast_type == int:
          val = int(val)
      elif cast_type == float:
          val = float(val)
      elif cast_type == list or cast_type == dict:
          val = json.loads(val)
  except Exception:
      # Fallback: roher String
      pass

  # Cache
  cache.set(cache_key, val, CACHE_TIMEOUT)
  return val