'''
Erfolgreiches Umbenennen eines Verzeichnisses.
Verzeichnis mit ssh_user="root" (korrektes Home-Verzeichnis /root).
Verzeichnis existiert bereits unter dem Namen delete_me_....
Fehler beim mv-Kommando.
SSH-Verbindungsfehler.
Fehlende POST-Parameter.
Ungültige Verzeichnisnamen (Slash, ..).
Nicht vorhandener Host.
GET statt POST.
Nicht angemeldeter Benutzer.
Angemeldeter Benutzer ohne Superuser-Rechte.
'''

from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.test import TestCase
from django.urls import reverse
from django.http import HttpResponse

from paas.models import RemoteHost


User = get_user_model()


class MarkDeleteViewTests(TestCase):

    def setUp(self):
        self.url = reverse("paas_mark_delete")
        self.redirect_url = reverse("paas_data_corpses")

        self.admin = User.objects.create_superuser(
            username="admin",
            email="admin@example.com",
            password="test-password",
        )

        self.normal_user = User.objects.create_user(
            username="normal",
            email="normal@example.com",
            password="test-password",
        )

        self.host = RemoteHost.objects.create(
            hostname="test.example.com",
            ip_address="192.0.2.10",
            ssh_user="deploy",
        )

        self.client.force_login(self.admin)

    def assertRedirects(self, response, expected_url, **kwargs):
        """
        Redirect prüfen, ohne die Ziel-View tatsächlich aufzurufen.
        """
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, expected_url)

    def post_mark_delete(self, host_id=None, dirname="my_directory"):
        data = {"dirname": dirname}

        if host_id is not None:
            data["host_id"] = host_id

        return self.client.post(self.url, data)

    def get_messages_list(self, response):
        return [str(message) for message in get_messages(response.wsgi_request)]

    @patch("paas.views._run_cmd")
    @patch("paas.views._ssh_client")
    def test_successful_rename(self, mock_ssh_client, mock_run_cmd):
        """Ein Verzeichnis wird erfolgreich zur Löschung vorgemerkt."""

        ssh = MagicMock()
        mock_ssh_client.return_value.__enter__.return_value = ssh

        mock_run_cmd.side_effect = [
            (1, "", ""),  # Ziel existiert nicht
            (0, "", ""),  # mv erfolgreich
            (0, "other_dir\n", ""),  # find: Verzeichnisliste
        ]

        response = self.post_mark_delete(
            self.host.pk,
            "my_directory",
        )

        self.assertRedirects(response, self.redirect_url)

        calls = mock_run_cmd.call_args_list

        self.assertGreaterEqual(len(calls), 2)

        self.assertEqual(
            calls[0].args,
            (ssh, "test -e /home/deploy/delete_me_my_directory"),
        )

        self.assertEqual(
            calls[1].args,
            (
                ssh,
                "mv -- /home/deploy/my_directory "
                "/home/deploy/delete_me_my_directory",
            ),
        )

        messages = self.get_messages_list(response)

        self.assertTrue(
            any(
                "wurde zu 'delete_me_my_directory' umbenannt" in m
                for m in messages
            )
        )

        mock_ssh_client.assert_called_once_with(self.host)

    @patch("paas.views._run_cmd")
    @patch("paas.views._ssh_client")
    def test_root_user_uses_root_home_directory(
            self, mock_ssh_client, mock_run_cmd
    ):
        """Bei ssh_user=root muss /root verwendet werden."""

        host = RemoteHost.objects.create(
            hostname="root.example.com",
            ip_address="192.0.2.11",
            ssh_user="root",
        )

        ssh = MagicMock()
        mock_ssh_client.return_value.__enter__.return_value = ssh

        mock_run_cmd.side_effect = [
            (1, "", ""),
            (0, "", ""),
            (0, "", ""),
        ]

        response = self.post_mark_delete(host.pk, "backup")

        self.assertRedirects(response, self.redirect_url)

        calls = mock_run_cmd.call_args_list

        self.assertEqual(
            calls[0].args[1],
            "test -e /root/delete_me_backup",
        )

        self.assertEqual(
            calls[1].args[1],
            "mv -- /root/backup /root/delete_me_backup",
        )

    @patch("paas.views._run_cmd")
    @patch("paas.views._ssh_client")
    def test_existing_target_directory_aborts_rename(
            self, mock_ssh_client, mock_run_cmd
    ):
        """Existiert das Ziel bereits, darf kein mv ausgeführt werden."""

        ssh = MagicMock()
        mock_ssh_client.return_value.__enter__.return_value = ssh

        mock_run_cmd.side_effect = [
            (0, "", ""),  # Ziel existiert bereits
            (0, "", ""),  # zusätzliche Verzeichnisabfrage
        ]

        response = self.post_mark_delete(
            self.host.pk,
            "my_directory",
        )

        self.assertRedirects(response, self.redirect_url)

        commands = [
            call.args[1]
            for call in mock_run_cmd.call_args_list
        ]

        self.assertIn(
            "test -e /home/deploy/delete_me_my_directory",
            commands,
        )

        self.assertFalse(
            any(command.startswith("mv ") for command in commands)
        )

        messages = list(get_messages(response.wsgi_request))

        self.assertTrue(
            any("existiert bereits" in str(m) for m in messages)
        )

        self.assertTrue(
            any(m.level_tag == "warning" for m in messages)
        )

    @patch("paas.views._run_cmd")
    @patch("paas.views._ssh_client")
    def test_rename_command_failure(
            self, mock_ssh_client, mock_run_cmd
    ):
        """Ein Fehler beim mv-Kommando erzeugt eine Fehlermeldung."""

        ssh = MagicMock()
        mock_ssh_client.return_value.__enter__.return_value = ssh

        mock_run_cmd.side_effect = [
            (1, "", ""),  # Ziel existiert nicht
            (1, "", "Permission denied"),  # mv fehlgeschlagen
            (0, "", ""),  # zusätzliche Verzeichnisabfrage
        ]

        response = self.post_mark_delete(
            self.host.pk,
            "my_directory",
        )

        self.assertRedirects(response, self.redirect_url)

        messages = self.get_messages_list(response)

        self.assertTrue(
            any("Permission denied" in m for m in messages)
        )

        commands = [
            call.args[1]
            for call in mock_run_cmd.call_args_list
        ]

        self.assertTrue(
            any(command.startswith("mv ") for command in commands)
        )

    @patch("paas.views._ssh_client")
    def test_ssh_exception(self, mock_ssh_client):
        """Eine SSH-Ausnahme wird abgefangen."""

        mock_ssh_client.side_effect = Exception("Connection refused")

        response = self.post_mark_delete(self.host.pk, "my_directory")

        self.assertRedirects(response, self.redirect_url)

        messages = self.get_messages_list(response)

        self.assertTrue(
            any("SSH-Fehler: Connection refused" in m for m in messages)
        )

    def test_missing_host_id(self):
        """Fehlende host_id wird abgewiesen."""

        response = self.post_mark_delete(dirname="my_directory")

        self.assertRedirects(response, self.redirect_url)

        messages = self.get_messages_list(response)

        self.assertIn("Ungültige Eingabe.", messages)

    def test_missing_dirname(self):
        """Fehlender dirname wird abgewiesen."""

        response = self.client.post(
            self.url,
            {"host_id": self.host.pk},
        )

        self.assertRedirects(response, self.redirect_url)

        messages = self.get_messages_list(response)

        self.assertIn("Ungültige Eingabe.", messages)

    def test_empty_dirname(self):
        """Ein leerer Verzeichnisname wird abgewiesen."""

        response = self.post_mark_delete(self.host.pk, "")

        self.assertRedirects(response, self.redirect_url)

        self.assertIn(
            "Ungültige Eingabe.",
            self.get_messages_list(response),
        )

    def test_directory_name_with_slash_is_rejected(self):
        """Ein Slash im Verzeichnisnamen ist nicht erlaubt."""

        for dirname in (
            "../etc",
            "foo/bar",
            "/etc",
        ):
            with self.subTest(dirname=dirname):
                response = self.post_mark_delete(
                    self.host.pk,
                    dirname,
                )

                self.assertRedirects(response, self.redirect_url)

                self.assertIn(
                    "Ungültige Eingabe.",
                    self.get_messages_list(response),
                )

    def test_directory_name_with_double_dot_is_rejected(self):
        """Jeder Verzeichnisname mit .. wird abgewiesen."""

        for dirname in (
            "..",
            "foo..bar",
            "backup..old",
        ):
            with self.subTest(dirname=dirname):
                response = self.post_mark_delete(
                    self.host.pk,
                    dirname,
                )

                self.assertRedirects(response, self.redirect_url)

                self.assertIn(
                    "Ungültige Eingabe.",
                    self.get_messages_list(response),
                )

    def test_nonexistent_host_returns_404(self):
        """Ein unbekannter Host führt zu HTTP 404."""

        response = self.post_mark_delete(
            999999,
            "my_directory",
        )

        self.assertEqual(response.status_code, 404)

    def test_get_request_not_allowed(self):
        """Die View akzeptiert ausschließlich POST."""

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 405)

    def test_anonymous_user_is_redirected_to_login(self):
        """Nicht angemeldete Benutzer werden zum Login umgeleitet."""

        self.client.logout()

        response = self.post_mark_delete(
            self.host.pk,
            "my_directory",
        )

        self.assertEqual(response.status_code, 302)

        self.assertIn(
            reverse("login"),
            response.url,
        )

    def test_normal_user_is_forbidden(self):
        """Normale Benutzer dürfen die View nicht aufrufen."""

        self.client.force_login(self.normal_user)

        response = self.post_mark_delete(
            self.host.pk,
            "my_directory",
        )

        self.assertEqual(response.status_code, 302)

        self.assertIn(
            reverse("login"),
            response.url,
        )
