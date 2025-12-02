import ipaddress
from django.http import HttpResponseForbidden

class AdminOnlyFromPrivateIPMiddleware:
  """
  Blockiert alle Admin‑Zugriffe, die nicht von privaten IP‑Räumen stammen.
  """

  # Private Netzwerke gemäß RFC1918 + Loopback
  PRIVATE_RANGES = [
      ipaddress.ip_network('10.0.0.0/8'),
      ipaddress.ip_network('172.16.0.0/12'),
      ipaddress.ip_network('192.168.0.0/16'),
      ipaddress.ip_network('127.0.0.0/8'),  # localhost
  ]

  def __init__(self, get_response):
      self.get_response = get_response

  def __call__(self, request):
      # Nur für Pfade, die mit /admin/ beginnen
      if request.path.startswith('/admin/'):
          client_ip = self._get_client_ip(request)

          try:
              ip_obj = ipaddress.ip_address(client_ip)
              if not any(ip_obj in net for net in self.PRIVATE_RANGES):
                  return HttpResponseForbidden(
                      'Forbidden: Admin only accessible from private networks.'
                  )
          except ValueError:
              # ungültige IP‑Adresse
              return HttpResponseForbidden('Forbidden: Invalid IP address.')

      # Alles weiterverarbeiten
      response = self.get_response(request)
      return response

  @staticmethod
  def _get_client_ip(request):
      """
      Holt die IP aus X-Forwarded-For (wenn vorhanden) oder REMOTE_ADDR.
      """
      x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
      if x_forwarded_for:
          # Mehrere IPs möglich – die erste ist die Client‑IP
          ip = x_forwarded_for.split(',')[0].strip()
      else:
          ip = request.META.get('REMOTE_ADDR')
      return ip