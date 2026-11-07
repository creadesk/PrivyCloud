'''
Alle drei Ports sind unterschiedlich.
Der Web-Port wird nur einmal abgefragt.
Doppelte API-Ports werden erneut abgefragt.
Doppelte API-2-Ports werden erneut abgefragt.
Die maximale Anzahl der Versuche wird eingehalten.
max_attempts=None erlaubt unbegrenzte Versuche.
Die Fehlermeldungen enthalten die korrekte Anzahl der Versuche.
Der SSH-Parameter wird korrekt weitergegeben.
'''

from django.test import SimpleTestCase
from unittest.mock import patch, call

from paas.tasks import _reserve_ports

class ReservePortsTests(SimpleTestCase):
    @patch('paas.tasks._get_free_port')
    def test_reserve_three_different_ports(self, mock_get_free_port):
        """Alle drei zurückgegebenen Ports müssen unterschiedlich sein."""

        mock_get_free_port.side_effect = [8000, 8001, 8002]

        result = _reserve_ports(ssh='fake_ssh')

        self.assertEqual(result, (8000, 8001, 8002))
        self.assertEqual(mock_get_free_port.call_count, 3)

    @patch('paas.tasks._get_free_port')
    def test_web_port_is_requested_only_once(self, mock_get_free_port):
        """Der Web-Port darf nicht erneut abgefragt werden."""

        mock_get_free_port.side_effect = [8000, 8001, 8002]

        _reserve_ports(ssh='fake_ssh')

        self.assertEqual(
            mock_get_free_port.call_count,
            3
        )

    @patch('paas.tasks._get_free_port')
    def test_api_port_must_differ_from_web_port(self, mock_get_free_port):
        """Ein identischer API-Port wird verworfen und erneut angefragt."""

        mock_get_free_port.side_effect = [
            8000,  # Web
            8000,  # API: identisch -> verwerfen
            8001,  # API: gültig
            8002,  # API 2: gültig
        ]

        result = _reserve_ports(ssh='fake_ssh')

        self.assertEqual(result, (8000, 8001, 8002))
        self.assertEqual(mock_get_free_port.call_count, 4)

    @patch('paas.tasks._get_free_port')
    def test_api2_must_differ_from_web_and_api(self, mock_get_free_port):
        """API 2 darf weder dem Web-Port noch API 1 entsprechen."""

        mock_get_free_port.side_effect = [
            8000,  # Web
            8001,  # API
            8000,  # API 2: identisch mit Web -> verwerfen
            8001,  # API 2: identisch mit API -> verwerfen
            8002,  # API 2: gültig
        ]

        result = _reserve_ports(ssh='fake_ssh')

        self.assertEqual(result, (8000, 8001, 8002))
        self.assertEqual(mock_get_free_port.call_count, 5)

    @patch('paas.tasks._get_free_port')
    def test_api_port_max_attempts_exceeded(self, mock_get_free_port):
        """Bei zu vielen ungültigen API-Ports wird RuntimeError ausgelöst."""

        mock_get_free_port.side_effect = [
            8000,  # Web
            8000,  # API Versuch 1
            8000,  # API Versuch 2
            8000,  # API Versuch 3
        ]

        with self.assertRaises(RuntimeError) as context:
            _reserve_ports(ssh='fake_ssh', max_attempts=3)

        self.assertEqual(
            str(context.exception),
            'Kein freier Port für API gefunden, nach 3 Versuchen.'
        )

        self.assertEqual(mock_get_free_port.call_count, 4)

    @patch('paas.tasks._get_free_port')
    def test_api2_max_attempts_exceeded(self, mock_get_free_port):
        """Bei zu vielen ungültigen API-2-Ports wird RuntimeError ausgelöst."""

        mock_get_free_port.side_effect = [
            8000,  # Web
            8001,  # API
            8000,  # API 2 Versuch 1
            8001,  # API 2 Versuch 2
            8000,  # API 2 Versuch 3
        ]

        with self.assertRaises(RuntimeError) as context:
            _reserve_ports(ssh='fake_ssh', max_attempts=3)

        self.assertEqual(
            str(context.exception),
            'Kein freier Port für API 2 gefunden, nach 3 Versuchen.'
        )

        self.assertEqual(mock_get_free_port.call_count, 5)

    @patch('paas.tasks._get_free_port')
    def test_api_port_succeeds_on_last_allowed_attempt(self, mock_get_free_port):
        """Der letzte erlaubte Versuch darf noch erfolgreich sein."""

        mock_get_free_port.side_effect = [
            8000,  # Web
            8000,  # API Versuch 1: ungültig
            8000,  # API Versuch 2: ungültig
            8001,  # API Versuch 3: gültig
            8002,  # API 2
        ]

        result = _reserve_ports(
            ssh='fake_ssh',
            max_attempts=3
        )

        self.assertEqual(result, (8000, 8001, 8002))

    @patch('paas.tasks._get_free_port')
    def test_max_attempts_one(self, mock_get_free_port):
        """Mit max_attempts=1 ist genau ein Versuch pro API-Port erlaubt."""

        mock_get_free_port.side_effect = [
            8000,  # Web
            8001,  # API
            8002,  # API 2
        ]

        result = _reserve_ports(
            ssh='fake_ssh',
            max_attempts=1
        )

        self.assertEqual(result, (8000, 8001, 8002))
        self.assertEqual(mock_get_free_port.call_count, 3)

    @patch('paas.tasks._get_free_port')
    def test_max_attempts_none_allows_unlimited_retries(self, mock_get_free_port):
        """None erlaubt beliebig viele Wiederholungen."""

        mock_get_free_port.side_effect = [
            8000,  # Web
            8000,  # API: ungültig
            8000,  # API: ungültig
            8001,  # API: gültig
            8000,  # API 2: ungültig
            8001,  # API 2: ungültig
            8002,  # API 2: gültig
        ]

        result = _reserve_ports(
            ssh='fake_ssh',
            max_attempts=None
        )

        self.assertEqual(result, (8000, 8001, 8002))
        self.assertEqual(mock_get_free_port.call_count, 7)

    @patch('paas.tasks._get_free_port')
    def test_ssh_parameter_is_forwarded(self, mock_get_free_port):
        """Bei jedem Aufruf wird dasselbe SSH-Objekt übergeben."""

        ssh = object()

        mock_get_free_port.side_effect = [8000, 8001, 8002]

        _reserve_ports(ssh=ssh)

        self.assertEqual(
            mock_get_free_port.call_args_list,
            [
                call(ssh),
                call(ssh),
                call(ssh),
            ]
        )

    @patch('paas.tasks._get_free_port')
    def test_web_port_is_requested_before_api_ports(self, mock_get_free_port):
        """Die Portabfrage erfolgt in der Reihenfolge Web, API, API 2."""

        mock_get_free_port.side_effect = [
            8080,
            8081,
            8082,
        ]

        result = _reserve_ports(ssh='fake_ssh')

        self.assertEqual(result[0], 8080)
        self.assertEqual(result[1], 8081)
        self.assertEqual(result[2], 8082)