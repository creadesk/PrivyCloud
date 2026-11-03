'''
Die beiden Basis-Zeilen sind immer vorhanden.
Alle drei Hidden-Service-Ports werden korrekt eingetragen.
Ein API-Port mit Wert 1 wird nicht berücksichtigt.
Ein Hidden-Service-Port mit Wert 1 wird nicht berücksichtigt.
Die Bedingungen für Web, API und API 2 funktionieren unabhängig voneinander.
Der Rückgabestring endet immer mit einem \n.
Die Reihenfolge der Einträge ist korrekt.
tor_auth_type verändert aktuell nicht das Ergebnis (der Parameter wird in der Funktion noch nicht verwendet).
'''

from django.test import SimpleTestCase

from paas.models import AppDefinition
from paas.tasks import _build_torrc

"""Tests für die Erstellung der Tor-Konfiguration."""
class BuildTorrcTests(SimpleTestCase):
    def setUp(self):
        """Gemeinsame Testparameter."""

        self.socks_port = 9050
        self.hidden_dir = "/var/lib/tor/my_hidden_service"

        self.free_port_web = 8080
        self.free_port_api = 8081
        self.free_port_api_2 = 8082

    def create_app_def(
            self,
            app_port_intern_web=80,
            app_port_intern_api=1,
            app_port_intern_api_2=1,
            hiddenservice_port_web=80,
            hiddenservice_port_api=1,
            hiddenservice_port_api_2=1,
    ):
        """
        Erzeugt eine AppDefinition ohne Datenbankzugriff.
        Für _build_torrc werden nur die Port-Attribute benötigt.
        """

        return AppDefinition(
            app_port_intern_web=app_port_intern_web,
            app_port_intern_api=app_port_intern_api,
            app_port_intern_api_2=app_port_intern_api_2,
            hiddenservice_port_web=hiddenservice_port_web,
            hiddenservice_port_api=hiddenservice_port_api,
            hiddenservice_port_api_2=hiddenservice_port_api_2,
        )

    def build_torrc(self, app_def, tor_auth_type=None):
        """Hilfsmethode zum Aufruf der getesteten Funktion."""

        return _build_torrc(
            app_def=app_def,
            socks_port=self.socks_port,
            hidden_dir=self.hidden_dir,
            free_port_web=self.free_port_web,
            free_port_api=self.free_port_api,
            free_port_api_2=self.free_port_api_2,
            tor_auth_type=tor_auth_type,
        )

    def test_base_lines_are_always_present(self):
        """SocksPort und HiddenServiceDir müssen immer vorhanden sein."""

        app = self.create_app_def(
            app_port_intern_web=1,
            app_port_intern_api=1,
            app_port_intern_api_2=1,
            hiddenservice_port_web=1,
            hiddenservice_port_api=1,
            hiddenservice_port_api_2=1,
        )

        result = self.build_torrc(app)

        self.assertEqual(
            result,
            "SocksPort 9050\n"
            "HiddenServiceDir /var/lib/tor/my_hidden_service\n"
        )

    def test_all_hidden_service_ports_are_added(self):
        """Alle drei Hidden-Service-Ports werden korrekt eingetragen."""

        app = self.create_app_def(
            app_port_intern_web=80,
            app_port_intern_api=5000,
            app_port_intern_api_2=5001,
            hiddenservice_port_web=80,
            hiddenservice_port_api=8080,
            hiddenservice_port_api_2=8081,
        )

        result = self.build_torrc(app)

        expected = (
            "SocksPort 9050\n"
            "HiddenServiceDir /var/lib/tor/my_hidden_service\n"
            "HiddenServicePort 80 127.0.0.1:8080\n"
            "HiddenServicePort 8080 127.0.0.1:8081\n"
            "HiddenServicePort 8081 127.0.0.1:8082\n"
        )

        self.assertEqual(result, expected)

    def test_web_port_is_added(self):
        """Der Web-Port wird eingetragen, wenn beide Ports ungleich 1 sind."""

        app = self.create_app_def(
            app_port_intern_web=8080,
            hiddenservice_port_web=80,
        )

        result = self.build_torrc(app)

        self.assertIn(
            "HiddenServicePort 80 127.0.0.1:8080\n",
            result,
        )

    def test_web_port_is_not_added_if_internal_port_is_one(self):
        """Kein Web-Eintrag, wenn app_port_intern_web == 1."""

        app = self.create_app_def(
            app_port_intern_web=1,
            hiddenservice_port_web=80,
        )

        result = self.build_torrc(app)

        self.assertNotIn("HiddenServicePort", result)

    def test_web_port_is_not_added_if_hiddenservice_port_is_one(self):
        """Kein Web-Eintrag, wenn hiddenservice_port_web == 1."""

        app = self.create_app_def(
            app_port_intern_web=80,
            hiddenservice_port_web=1,
        )

        result = self.build_torrc(app)

        self.assertNotIn("HiddenServicePort", result)

    def test_api_port_is_added(self):
        """Der API-Port wird unabhängig vom Web-Port berücksichtigt."""

        app = self.create_app_def(
            app_port_intern_web=1,
            app_port_intern_api=5000,
            hiddenservice_port_api=8080,
        )

        result = self.build_torrc(app)

        self.assertIn(
            "HiddenServicePort 8080 127.0.0.1:8081\n",
            result,
        )

    def test_api_port_is_not_added_if_internal_port_is_one(self):
        """Kein API-Eintrag, wenn app_port_intern_api == 1."""

        app = self.create_app_def(
            app_port_intern_web=1,
            app_port_intern_api=1,
            hiddenservice_port_api=8080,
        )

        result = self.build_torrc(app)

        self.assertNotIn("HiddenServicePort 8080", result)

    def test_api_port_is_not_added_if_hiddenservice_port_is_one(self):
        """Kein API-Eintrag, wenn hiddenservice_port_api == 1."""

        app = self.create_app_def(
            app_port_intern_web=1,
            app_port_intern_api=5000,
            hiddenservice_port_api=1,
        )

        result = self.build_torrc(app)

        self.assertNotIn("HiddenServicePort", result)

    def test_api_2_port_is_added(self):
        """Der zweite API-Port wird unabhängig berücksichtigt."""

        app = self.create_app_def(
            app_port_intern_web=1,
            app_port_intern_api=1,
            app_port_intern_api_2=5001,
            hiddenservice_port_api_2=8081,
        )

        result = self.build_torrc(app)

        self.assertIn(
            "HiddenServicePort 8081 127.0.0.1:8082\n",
            result,
        )

    def test_api_2_port_is_not_added_if_internal_port_is_one(self):
        """Kein API-2-Eintrag, wenn app_port_intern_api_2 == 1."""

        app = self.create_app_def(
            app_port_intern_web=1,
            app_port_intern_api=1,
            app_port_intern_api_2=1,
            hiddenservice_port_api_2=8081,
        )

        result = self.build_torrc(app)

        self.assertNotIn("HiddenServicePort", result)

    def test_api_2_port_is_not_added_if_hiddenservice_port_is_one(self):
        """Kein API-2-Eintrag, wenn hiddenservice_port_api_2 == 1."""

        app = self.create_app_def(
            app_port_intern_web=1,
            app_port_intern_api=1,
            app_port_intern_api_2=5001,
            hiddenservice_port_api_2=1,
        )

        result = self.build_torrc(app)

        self.assertNotIn("HiddenServicePort", result)

    def test_hidden_service_ports_are_independent(self):
        """Ein deaktivierter Port darf andere Ports nicht beeinflussen."""

        app = self.create_app_def(
            app_port_intern_web=1,
            app_port_intern_api=5000,
            app_port_intern_api_2=5001,
            hiddenservice_port_web=80,
            hiddenservice_port_api=8080,
            hiddenservice_port_api_2=8081,
        )

        result = self.build_torrc(app)

        self.assertNotIn("HiddenServicePort 80 ", result)
        self.assertIn("HiddenServicePort 8080 127.0.0.1:8081\n", result)
        self.assertIn("HiddenServicePort 8081 127.0.0.1:8082\n", result)

    def test_result_ends_with_newline(self):
        """Der torrc-String muss mit einem Zeilenumbruch enden."""

        app = self.create_app_def()

        result = self.build_torrc(app)

        self.assertTrue(result.endswith("\n"))

    def test_line_order_is_correct(self):
        """Die Zeilen müssen in der vorgesehenen Reihenfolge stehen."""

        app = self.create_app_def(
            app_port_intern_web=80,
            app_port_intern_api=5000,
            app_port_intern_api_2=5001,
            hiddenservice_port_web=80,
            hiddenservice_port_api=8080,
            hiddenservice_port_api_2=8081,
        )

        result = self.build_torrc(app)

        lines = result.splitlines()

        self.assertEqual(lines[0], "SocksPort 9050")
        self.assertEqual(lines[1], "HiddenServiceDir /var/lib/tor/my_hidden_service")
        self.assertEqual(lines[2], "HiddenServicePort 80 127.0.0.1:8080")
        self.assertEqual(lines[3], "HiddenServicePort 8080 127.0.0.1:8081")
        self.assertEqual(lines[4], "HiddenServicePort 8081 127.0.0.1:8082")

    def test_tor_auth_type_does_not_change_result(self):
        """
        tor_auth_type wird aktuell noch nicht ausgewertet.
        Daher muss das Ergebnis unabhängig vom Parameter sein.
        """

        app = self.create_app_def(
            app_port_intern_web=80,
            hiddenservice_port_web=80,
        )

        result_none = self.build_torrc(app, tor_auth_type=None)
        result_cert = self.build_torrc(app, tor_auth_type="cert")
        result_password = self.build_torrc(app, tor_auth_type="password")

        self.assertEqual(result_none, result_cert)
        self.assertEqual(result_none, result_password)