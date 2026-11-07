'''
Superuser kann das Cleanup-Log erfolgreich abrufen.
Nicht angemeldete Benutzer werden zum Login weitergeleitet.
Normale angemeldete Benutzer erhalten keinen Zugriff.
Nicht existierender Host liefert HTTP 404.
Ungültiger Log-Pfad liefert HTTP 400.
SSH-Verbindungsfehler liefert HTTP 500.
Fehler beim Lesen der Logdatei (Exit-Code ungleich 0) liefert HTTP 500.
Fehlerhafte UTF-8-Daten in stderr werden korrekt ersetzt.
Es wird ausschließlich die erwartete Logdatei des jeweiligen Hosts gelesen.
POST-Anfragen werden abgelehnt.
'''

from unittest.mock import patch, MagicMock
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from paas.models import RemoteHost

User = get_user_model()

"""Tests für die View paas_log_cleanup."""
class PaasLogCleanupViewTests(TestCase):
    def setUp(self):
        self.superuser = User.objects.create_superuser(
            username="admin",
            email="admin@example.com",
            password="test-password",
        )

        self.normal_user = User.objects.create_user(
            username="user",
            email="user@example.com",
            password="test-password",
        )

        self.host = RemoteHost.objects.create(
            hostname="test-host.example.com",
            ip_address="192.0.2.10",
            ssh_user="deploy",
        )

        self.url = reverse(
            "paas_log_cleanup",
            kwargs={"host_id": self.host.pk},
        )

    def test_superuser_can_read_cleanup_log(self):
        """Superuser kann das Cleanup-Log erfolgreich abrufen."""

        log_content = b"Cleanup started\nCleanup finished\n"

        self.client.force_login(self.superuser)

        mock_ssh = MagicMock()

        with (
            patch("paas.views._validate_log_path", return_value=True) as validate,
            patch("paas.views._ssh_client") as ssh_client,
            patch(
                "paas.views._run_cmd_bytes",
                return_value=(0, log_content, b""),
            ) as run_cmd,
        ):
            ssh_client.return_value.__enter__.return_value = mock_ssh

            response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, log_content)
        self.assertEqual(response["Content-Type"], "text/plain")
        self.assertEqual(
            response["Content-Disposition"],
            'inline; filename="cleanup_data_corpses_test-host.example.com.log"',
        )

        validate.assert_called_once_with(
            "/home/deploy/cleanup_data_corpses.log"
        )

        ssh_client.assert_called_once_with(self.host)

        run_cmd.assert_called_once_with(
            mock_ssh,
            "cat /home/deploy/cleanup_data_corpses.log",
        )

    def test_anonymous_user_is_redirected_to_login(self):
        """Nicht angemeldete Benutzer werden zum Login weitergeleitet."""

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 302)
        self.assertIn(
            reverse("login"),
            response["Location"],
        )

    def test_normal_user_is_denied(self):
        """Normale Benutzer werden abgewiesen und zum Login geleitet."""

        self.client.force_login(self.normal_user)

        with (
            patch("paas.views._ssh_client") as ssh_client,
            patch("paas.views._run_cmd_bytes") as run_cmd,
        ):
            response = self.client.get(self.url)

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response["Location"])

        ssh_client.assert_not_called()
        run_cmd.assert_not_called()

    def test_nonexistent_host_returns_404(self):
        """Ein nicht existierender Host liefert HTTP 404."""

        self.client.force_login(self.superuser)

        url = reverse(
            "paas_log_cleanup",
            kwargs={"host_id": 999999},
        )

        with patch("paas.views._ssh_client") as ssh_client:
            response = self.client.get(url)

        self.assertEqual(response.status_code, 404)
        ssh_client.assert_not_called()

    def test_invalid_log_path_returns_400(self):
        """Ein ungültiger Log-Pfad wird abgelehnt."""

        self.client.force_login(self.superuser)

        with (
            patch("paas.views._validate_log_path", return_value=False),
            patch("paas.views._ssh_client") as ssh_client,
        ):
            response = self.client.get(self.url)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.content, "Ungültiger Log-Pfad.".encode())

        ssh_client.assert_not_called()

    def test_ssh_connection_error_returns_500(self):
        """SSH-Verbindungsfehler werden als HTTP 500 zurückgegeben."""

        self.client.force_login(self.superuser)

        with (
            patch("paas.views._validate_log_path", return_value=True),
            patch(
                "paas.views._ssh_client",
                side_effect=Exception("Connection refused"),
            ),
        ):
            response = self.client.get(self.url)

        self.assertEqual(response.status_code, 500)
        self.assertIn(
            "SSH-Verbindung fehlgeschlagen",
            response.content.decode(),
        )
        self.assertIn(
            "Connection refused",
            response.content.decode(),
        )

    def test_ssh_command_error_returns_500(self):
        """Ein fehlgeschlagener cat-Befehl liefert HTTP 500."""

        self.client.force_login(self.superuser)

        mock_ssh = MagicMock()

        with (
            patch("paas.views._validate_log_path", return_value=True),
            patch("paas.views._ssh_client") as ssh_client,
            patch(
                "paas.views._run_cmd_bytes",
                return_value=(1, b"", b"File not found"),
            ),
        ):
            ssh_client.return_value.__enter__.return_value = mock_ssh

            response = self.client.get(self.url)

        self.assertEqual(response.status_code, 500)

        content = response.content.decode()

        self.assertIn(
            "Fehler beim Lesen der Logdatei",
            content,
        )
        self.assertIn("File not found", content)

    def test_invalid_utf8_in_stderr_is_replaced(self):
        """Ungültige UTF-8-Zeichen in stderr verursachen keinen Fehler."""

        self.client.force_login(self.superuser)

        mock_ssh = MagicMock()

        with (
            patch("paas.views._validate_log_path", return_value=True),
            patch("paas.views._ssh_client") as ssh_client,
            patch(
                "paas.views._run_cmd_bytes",
                return_value=(1, b"", b"Error: \xff"),
            ),
        ):
            ssh_client.return_value.__enter__.return_value = mock_ssh

            response = self.client.get(self.url)

        self.assertEqual(response.status_code, 500)

        content = response.content.decode("utf-8")

        self.assertIn("Error:", content)
        self.assertIn("\ufffd", content)

    def test_post_request_is_not_allowed(self):
        """Die View akzeptiert ausschließlich GET-Anfragen."""

        self.client.force_login(self.superuser)

        with patch("paas.views._ssh_client") as ssh_client:
            response = self.client.post(self.url)

        self.assertEqual(response.status_code, 405)
        ssh_client.assert_not_called()

    def test_empty_log_file_returns_empty_response(self):
        """Eine leere Logdatei ist ein gültiges Ergebnis."""

        self.client.force_login(self.superuser)

        mock_ssh = MagicMock()

        with (
            patch("paas.views._validate_log_path", return_value=True),
            patch("paas.views._ssh_client") as ssh_client,
            patch(
                "paas.views._run_cmd_bytes",
                return_value=(0, b"", b""),
            ),
        ):
            ssh_client.return_value.__enter__.return_value = mock_ssh

            response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"")
        self.assertEqual(response["Content-Type"], "text/plain")