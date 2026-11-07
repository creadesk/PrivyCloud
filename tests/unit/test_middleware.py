'''
Admin-Zugriff aus einem erlaubten privaten Netzwerk → erlaubt.
Admin-Zugriff aus einer öffentlichen IP → HTTP 403.
Zugriff auf normale Seiten → ungeprüft durchlassen.
Middleware deaktiviert → ungeprüft durchlassen.
Standardnetzwerke bei fehlender Konfiguration → korrekt verwenden.
Eigene Netzwerke aus einer Liste → korrekt verwenden.
Eigene Netzwerke als CSV-String → korrekt verwenden.
Ungültige CIDR-Einträge → ignorieren.
Ungültige Client-IP → HTTP 403.
X-Forwarded-For → erste IP verwenden.
Fallback auf REMOTE_ADDR.
Fehlende Client-IP → HTTP 403.
'''

import ipaddress

from django.test import SimpleTestCase, RequestFactory, override_settings
from django.http import HttpResponse

from middleware import AdminOnlyFromPrivateIPMiddleware

from core.settings import STRING_TO_ADMIN_PATH

class AdminOnlyFromPrivateIPMiddlewareTests(SimpleTestCase):

    def get_admin_path(self):
        return f"/{STRING_TO_ADMIN_PATH}/"

    def setUp(self):
        self.factory = RequestFactory()
        self.get_response = lambda request: HttpResponse("OK")

    def get_middleware(self):
        return AdminOnlyFromPrivateIPMiddleware(self.get_response)

    def get_request(self, path=None, ip="192.168.1.100", forwarded_for=None):
        if path is None:
            path = self.get_admin_path()

        request = self.factory.get(path, REMOTE_ADDR=ip)

        if forwarded_for:
            request.META["HTTP_X_FORWARDED_FOR"] = forwarded_for

        return request

    # ---------------------------------------------------------
    # Grundlegendes Verhalten
    # ---------------------------------------------------------

    @override_settings(
        ADMIN_IP_LIMITER_ENABLED=True,
        PRIVATE_IP_RANGES=["192.168.0.0/16"],
    )
    def test_admin_access_from_allowed_private_ip(self):
        """Admin-Zugriff aus einem erlaubten privaten Netzwerk ist möglich."""

        middleware = self.get_middleware()
        request = self.get_request(ip="192.168.1.100")

        response = middleware(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"OK")

    @override_settings(
        ADMIN_IP_LIMITER_ENABLED=True,
        PRIVATE_IP_RANGES=["192.168.0.0/16"],
    )
    def test_admin_access_from_public_ip_is_forbidden(self):
        """Admin-Zugriff aus einem öffentlichen Netzwerk wird blockiert."""

        middleware = self.get_middleware()
        request = self.get_request(
            path=self.get_admin_path(),
            ip="8.8.8.8"
        )

        response = middleware(request)

        self.assertEqual(response.status_code, 403)


    @override_settings(
        ADMIN_IP_LIMITER_ENABLED=True,
        PRIVATE_IP_RANGES=["192.168.0.0/16"],
    )
    def test_non_admin_path_is_not_checked(self):
        """Normale Seiten sind unabhängig von der Client-IP erreichbar."""

        middleware = self.get_middleware()
        request = self.get_request(
            path="/",
            ip="8.8.8.8"
        )

        response = middleware(request)

        self.assertEqual(response.status_code, 200)

    # ---------------------------------------------------------
    # Aktivierung / Deaktivierung
    # ---------------------------------------------------------

    @override_settings(
        ADMIN_IP_LIMITER_ENABLED=False,
        PRIVATE_IP_RANGES=["192.168.0.0/16"],
    )
    def test_disabled_middleware_allows_public_ip(self):
        """Bei deaktiviertem Filter sind auch öffentliche IPs erlaubt."""

        middleware = self.get_middleware()
        request = self.get_request(
            path=self.get_admin_path(),
            ip="8.8.8.8"
        )

        response = middleware(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(middleware.private_ranges, [])

    # ---------------------------------------------------------
    # Standardnetzwerke
    # ---------------------------------------------------------

    @override_settings(
        ADMIN_IP_LIMITER_ENABLED=True,
        PRIVATE_IP_RANGES=[],
    )
    def test_default_private_ranges_are_used(self):
        """Ohne eigene Konfiguration werden die Standardnetzwerke verwendet."""

        middleware = self.get_middleware()

        self.assertEqual(
            middleware.private_ranges,
            [
                ipaddress.ip_network("10.0.0.0/8"),
                ipaddress.ip_network("172.16.0.0/12"),
                ipaddress.ip_network("192.168.0.0/16"),
                ipaddress.ip_network("127.0.0.0/8"),
            ],
        )

    @override_settings(
        ADMIN_IP_LIMITER_ENABLED=True,
        PRIVATE_IP_RANGES=None,
    )
    def test_default_ranges_are_used_when_setting_is_none(self):
        """Auch bei None wird auf die Standardnetzwerke zurückgegriffen."""

        middleware = self.get_middleware()

        self.assertEqual(len(middleware.private_ranges), 4)

    # ---------------------------------------------------------
    # Eigene Netzwerkkonfiguration
    # ---------------------------------------------------------

    @override_settings(
        ADMIN_IP_LIMITER_ENABLED=True,
        PRIVATE_IP_RANGES=["10.10.0.0/16", "192.168.5.0/24"],
    )
    def test_custom_private_ranges_from_list(self):
        """Eigene Netzwerke als Liste werden korrekt geladen."""

        middleware = self.get_middleware()

        self.assertEqual(
            middleware.private_ranges,
            [
                ipaddress.ip_network("10.10.0.0/16"),
                ipaddress.ip_network("192.168.5.0/24"),
            ],
        )

    @override_settings(
        ADMIN_IP_LIMITER_ENABLED=True,
        PRIVATE_IP_RANGES="10.10.0.0/16, 192.168.5.0/24",
    )
    def test_custom_private_ranges_from_csv_string(self):
        """Eigene Netzwerke als CSV-String werden korrekt geladen."""

        middleware = self.get_middleware()

        self.assertEqual(
            middleware.private_ranges,
            [
                ipaddress.ip_network("10.10.0.0/16"),
                ipaddress.ip_network("192.168.5.0/24"),
            ],
        )

    @override_settings(
        ADMIN_IP_LIMITER_ENABLED=True,
        PRIVATE_IP_RANGES=[
            "192.168.0.0/16",
            "INVALID",
            "10.0.0.0/8",
        ],
    )
    def test_invalid_network_entries_are_ignored(self):
        """Ungültige CIDR-Einträge werden ignoriert."""

        middleware = self.get_middleware()

        self.assertEqual(
            middleware.private_ranges,
            [
                ipaddress.ip_network("192.168.0.0/16"),
                ipaddress.ip_network("10.0.0.0/8"),
            ],
        )

    # ---------------------------------------------------------
    # Client-IP-Ermittlung
    # ---------------------------------------------------------

    @override_settings(
        ADMIN_IP_LIMITER_ENABLED=True,
        PRIVATE_IP_RANGES=["192.168.0.0/16"],
    )
    def test_x_forwarded_for_is_ignored(self):
        """
        X-Forwarded-For wird ignoriert.
        Entscheidend ist ausschließlich REMOTE_ADDR.
        """

        middleware = self.get_middleware()

        request = self.get_request(
            ip="8.8.8.8",
            forwarded_for="192.168.1.100",
        )

        response = middleware(request)

        self.assertEqual(response.status_code, 403)

    @override_settings(
        ADMIN_IP_LIMITER_ENABLED=True,
        PRIVATE_IP_RANGES=["192.168.0.0/16"],
    )
    def test_private_remote_addr_allowed_even_with_public_forwarded_for(self):
        """
        Eine erlaubte REMOTE_ADDR bleibt erlaubt,
        auch wenn X-Forwarded-For eine öffentliche IP enthält.
        """

        middleware = self.get_middleware()

        request = self.get_request(
            ip="192.168.1.100",
            forwarded_for="8.8.8.8",
        )

        response = middleware(request)

        self.assertEqual(response.status_code, 200)


    @override_settings(
        ADMIN_IP_LIMITER_ENABLED=True,
        PRIVATE_IP_RANGES=["192.168.0.0/16"],
    )
    def test_remote_addr_is_used_without_forwarded_for(self):
        """Ohne X-Forwarded-For wird REMOTE_ADDR verwendet."""

        middleware = self.get_middleware()

        request = self.get_request(ip="192.168.1.100")

        self.assertEqual(
            middleware._get_client_ip(request),
            "192.168.1.100",
        )

    # ---------------------------------------------------------
    # Fehlerhafte IP-Adressen
    # ---------------------------------------------------------


    @override_settings(
        ADMIN_IP_LIMITER_ENABLED=True,
        PRIVATE_IP_RANGES=["192.168.0.0/16"],
    )
    def test_invalid_client_ip_is_forbidden(self):
        """Ungültige Client-IP führt zu HTTP 403."""

        middleware = self.get_middleware()
        request = self.get_request(ip="invalid-ip")

        response = middleware(request)

        self.assertEqual(response.status_code, 403)
        self.assertEqual(
            response.content,
            b"Forbidden: Invalid IP address.",
        )

    @override_settings(
        ADMIN_IP_LIMITER_ENABLED=True,
        PRIVATE_IP_RANGES=["192.168.0.0/16"],
    )
    def test_missing_client_ip_is_forbidden(self):
        """Fehlende Client-IP führt ebenfalls zu HTTP 403."""

        middleware = self.get_middleware()
        request = self.factory.get(self.get_admin_path())

        response = middleware(request)

        self.assertEqual(response.status_code, 403)