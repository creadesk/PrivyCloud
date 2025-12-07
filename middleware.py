import ipaddress
from django.http import HttpResponseForbidden
from django.conf import settings
from core.settings import STRING_TO_ADMIN_PATH

class AdminOnlyFromPrivateIPMiddleware:
    """
    Blockiert Admin‑Zugriffe, wenn die IP nicht in einem der
    erlaubten privaten Netzwerke liegt.
    Das Ganze kann über die Umgebungsvariable
    ADMIN_IP_LIMITER_ENABLED (default: True) an‑ oder ausgeschaltet werden.
    Der erlaubte Adressbereich wird über PRIVATE_IP_RANGES definiert.
    """

    def __init__(self, get_response):
        self.get_response = get_response

        # 1) Prüfe, ob der Filter aktiv ist
        self.enabled = getattr(settings, 'ADMIN_IP_LIMITER_ENABLED', True)
        if not self.enabled:
            # Kein Parsing nötig – der Filter ist abgeschaltet
            self.private_ranges = []
            return

        # 2) Lade die erlaubten Netzwerke
        raw_ranges = getattr(settings, 'PRIVATE_IP_RANGES', [])
        if not raw_ranges:
            # Fallback auf RFC‑1918 + Loopback
            raw_ranges = [
                '10.0.0.0/8',
                '172.16.0.0/12',
                '192.168.0.0/16',
                '127.0.0.0/8',
            ]

        # Die Variable kann als Liste oder als CSV‑String kommen
        if isinstance(raw_ranges, str):
            raw_ranges = [r.strip() for r in raw_ranges.split(',') if r.strip()]

        # Parsen zu ip_network‑Objekten – ungültige Einträge werden ignoriert
        self.private_ranges = []
        for net_str in raw_ranges:
            try:
                self.private_ranges.append(ipaddress.ip_network(net_str))
            except ValueError:
                # Logge, falls du ein logging‑Setup hast
                # logger.warning(f"Invalid CIDR in PRIVATE_IP_RANGES: {net_str}")
                pass

    def __call__(self, request):
        # 3) Nur für Admin‑Pfad prüfen
        if not self.enabled or not request.path.startswith(f'/{STRING_TO_ADMIN_PATH}/'):
            return self.get_response(request)

        # 4) Client‑IP ermitteln
        client_ip = self._get_client_ip(request)

        # 5) Prüfen, ob IP gültig und in erlaubtem Netzwerk liegt
        try:
            ip_obj = ipaddress.ip_address(client_ip)
            if not any(ip_obj in net for net in self.private_ranges):
                return HttpResponseForbidden(
                    'Forbidden: Admin only accessible from private networks.'
                )
        except ValueError:
            # Ungültige IP‑Adresse
            return HttpResponseForbidden('Forbidden: Invalid IP address.')

        # 6) Alles weiterreichen
        return self.get_response(request)

    @staticmethod
    def _get_client_ip(request):
        """
        Ermittelt die Client‑IP aus X-Forwarded-For (wenn vorhanden) oder
        REMOTE_ADDR. Für die meisten Setups hinter einem Reverse‑Proxy
        ist X-Forwarded-For korrekt.
        """
        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded_for:
            # Mehrere IPs möglich – die erste ist die ursprüngliche Client‑IP
            return x_forwarded_for.split(',')[0].strip()
        return request.META.get('REMOTE_ADDR')