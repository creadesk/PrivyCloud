'''
Bereich --> Test
Login --> Anonymer Benutzer wird umgeleitet
Berechtigung --> Normaler Benutzer erhält HTTP 403
HTTP --> GET erhält HTTP 405
Host --> Nicht vorhandener Host erhält HTTP 404
Image --> Nicht vorhandenes Image wird nicht gelöscht
Image --> Benutztes Image wird nicht gelöscht
Erfolg --> Docker-Löschbefehl wird korrekt ausgeführt
Docker --> Docker-Fehler wird als Fehlermeldung ausgegeben
SSH --> Verbindungsfehler wird abgefangen
SSH --> Fehler beim Kommando wird abgefangen
Sicherheit --> Image-Prüfung erfolgt vor dem Löschen
'''

from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.test import TestCase
from django.urls import reverse

from paas.models import RemoteHost

User = get_user_model()

''' Tests für die View delete_image.
SSH-Verbindungen und Docker-Kommandos werden vollständig gemockt.
'''
class DeleteImageViewTests(TestCase):

    IMAGE_ID = "abc123def456"

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
            hostname="docker-host",
            ip_address="192.168.1.100",
            ssh_user="root",
        )

        self.url = reverse(
            "paas_delete_image",
            kwargs={
                "host_id": self.host.pk,
                "image_id": self.IMAGE_ID,
            },
        )

    def login_as_superuser(self):
        self.client.force_login(self.superuser)

    def get_messages(self, response):
        return [str(message) for message in get_messages(response.wsgi_request)]

    # ---------------------------------------------------------
    # Berechtigungen
    # ---------------------------------------------------------

    def test_anonymous_user_is_redirected_to_login(self):
        """Nicht angemeldete Benutzer werden zum Login weitergeleitet."""

        response = self.client.post(self.url)

        self.assertEqual(response.status_code, 302)
        self.assertIn("/user-login/", response.url)

    def test_normal_user_gets_forbidden(self):
        """Normale Benutzer dürfen keine Images löschen."""

        self.client.force_login(self.normal_user)

        with patch("paas.views._ssh_client") as ssh_mock:
            response = self.client.post(self.url)

        self.assertEqual(response.status_code, 403)
        self.assertContains(
            response,
            "Keine Berechtigung.",
            status_code=403,
        )
        ssh_mock.assert_not_called()

    # ---------------------------------------------------------
    # HTTP-Methode
    # ---------------------------------------------------------

    def test_get_request_is_not_allowed(self):
        """Die View akzeptiert ausschließlich POST."""

        self.login_as_superuser()

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 405)
        self.assertEqual(response.headers["Allow"], "POST")

    # ---------------------------------------------------------
    # Host und Image
    # ---------------------------------------------------------

    def test_nonexistent_host_returns_404(self):
        """Ein nicht vorhandener Host führt zu HTTP 404."""

        self.login_as_superuser()

        url = reverse(
            "paas_delete_image",
            kwargs={
                "host_id": 999999,
                "image_id": self.IMAGE_ID,
            },
        )

        with patch("paas.views._ssh_client") as ssh_mock:
            response = self.client.post(url)

        self.assertEqual(response.status_code, 404)
        ssh_mock.assert_not_called()

    @patch("paas.views._ssh_client")
    @patch.object(RemoteHost, "docker_images")
    def test_image_not_found(self, docker_images_mock, ssh_mock):
        """Ein nicht vorhandenes Image wird nicht gelöscht."""

        self.login_as_superuser()

        docker_images_mock.return_value = [
            {
                "repository": "nginx",
                "tag": "latest",
                "image_id": "other123",
                "size": "100MB",
                "used": False,
            }
        ]

        response = self.client.post(self.url)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("paas_images"))

        messages = self.get_messages(response)

        self.assertTrue(
            any("nicht gefunden" in message for message in messages)
        )

        ssh_mock.assert_not_called()

    @patch("paas.views._ssh_client")
    @patch.object(RemoteHost, "docker_images")
    def test_image_in_use_is_not_deleted(self, docker_images_mock, ssh_mock):
        """Ein Image, das von einem Container verwendet wird, darf nicht gelöscht werden."""

        self.login_as_superuser()

        docker_images_mock.return_value = [
            {
                "repository": "nginx",
                "tag": "latest",
                "image_id": self.IMAGE_ID,
                "size": "100MB",
                "used": True,
            }
        ]

        response = self.client.post(self.url)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("paas_images"))

        messages = self.get_messages(response)

        self.assertTrue(
            any("ist im Einsatz" in message for message in messages)
        )

        ssh_mock.assert_not_called()

    # ---------------------------------------------------------
    # Erfolgreiches Löschen
    # ---------------------------------------------------------

    @patch("paas.views._ssh_client")
    @patch.object(RemoteHost, "docker_images")
    def test_image_is_successfully_deleted(
        self,
        docker_images_mock,
        ssh_mock,
    ):
        """Ein nicht verwendetes Image wird per SSH gelöscht."""

        self.login_as_superuser()

        docker_images_mock.return_value = [
            {
                "repository": "nginx",
                "tag": "latest",
                "image_id": self.IMAGE_ID,
                "size": "100MB",
                "used": False,
            }
        ]

        ssh = MagicMock()
        ssh.__enter__.return_value = ssh
        ssh.__exit__.return_value = False

        stdout = MagicMock()
        stdout.read.return_value = b""

        stderr = MagicMock()
        stderr.read.return_value = b""

        ssh.exec_command.return_value = (None, stdout, stderr)
        ssh_mock.return_value = ssh

        response = self.client.post(self.url)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("paas_images"))

        ssh_mock.assert_called_once_with(self.host)

        ssh.exec_command.assert_called_once_with(
            f"docker rmi -f {self.IMAGE_ID}"
        )

        messages = self.get_messages(response)

        self.assertTrue(
            any("erfolgreich gelöscht" in message for message in messages)
        )

    # ---------------------------------------------------------
    # Fehler beim Löschen
    # ---------------------------------------------------------

    @patch("paas.views._ssh_client")
    @patch.object(RemoteHost, "docker_images")
    def test_docker_remove_returns_error(
        self,
        docker_images_mock,
        ssh_mock,
    ):
        """Eine Fehlermeldung von Docker wird als Error-Message ausgegeben."""

        self.login_as_superuser()

        docker_images_mock.return_value = [
            {
                "repository": "nginx",
                "tag": "latest",
                "image_id": self.IMAGE_ID,
                "size": "100MB",
                "used": False,
            }
        ]

        ssh = MagicMock()
        ssh.__enter__.return_value = ssh
        ssh.__exit__.return_value = False

        stdout = MagicMock()
        stdout.read.return_value = b""

        stderr = MagicMock()
        stderr.read.return_value = b"Error: image is being used"

        ssh.exec_command.return_value = (None, stdout, stderr)
        ssh_mock.return_value = ssh

        response = self.client.post(self.url)

        self.assertEqual(response.status_code, 302)

        messages = self.get_messages(response)

        self.assertTrue(
            any(
                "Löschen fehlgeschlagen" in message
                and "image is being used" in message
                for message in messages
            )
        )

    @patch("paas.views._ssh_client")
    @patch.object(RemoteHost, "docker_images")
    def test_ssh_connection_error(
        self,
        docker_images_mock,
        ssh_mock,
    ):
        """Ein SSH-Verbindungsfehler wird abgefangen."""

        self.login_as_superuser()

        docker_images_mock.return_value = [
            {
                "repository": "nginx",
                "tag": "latest",
                "image_id": self.IMAGE_ID,
                "size": "100MB",
                "used": False,
            }
        ]

        ssh_mock.side_effect = Exception("Connection refused")

        response = self.client.post(self.url)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("paas_images"))

        messages = self.get_messages(response)

        self.assertTrue(
            any(
                "SSH-Fehler" in message
                and "Connection refused" in message
                for message in messages
            )
        )

    @patch("paas.views._ssh_client")
    @patch.object(RemoteHost, "docker_images")
    def test_ssh_command_error(self, docker_images_mock, ssh_mock):
        """Ein Fehler bei exec_command wird ebenfalls abgefangen."""

        self.login_as_superuser()

        docker_images_mock.return_value = [
            {
                "repository": "nginx",
                "tag": "latest",
                "image_id": self.IMAGE_ID,
                "size": "100MB",
                "used": False,
            }
        ]

        ssh = MagicMock()
        ssh.__enter__.return_value = ssh
        ssh.__exit__.return_value = False

        ssh.exec_command.side_effect = Exception("SSH command failed")
        ssh_mock.return_value = ssh

        response = self.client.post(self.url)

        self.assertEqual(response.status_code, 302)

        messages = self.get_messages(response)

        self.assertTrue(
            any(
                "SSH-Fehler" in message
                and "SSH command failed" in message
                for message in messages
            )
        )

    # ---------------------------------------------------------
    # Zusätzliche Sicherheitsprüfung
    # ---------------------------------------------------------

    @patch("paas.views._ssh_client")
    @patch.object(RemoteHost, "docker_images")
    def test_image_check_happens_before_ssh_delete(
        self,
        docker_images_mock,
        ssh_mock,
    ):
        """Die Image-Prüfung findet vor dem SSH-Löschvorgang statt."""

        self.login_as_superuser()

        docker_images_mock.return_value = [
            {
                "repository": "nginx",
                "tag": "latest",
                "image_id": self.IMAGE_ID,
                "size": "100MB",
                "used": False,
            }
        ]

        ssh = MagicMock()
        ssh.__enter__.return_value = ssh
        ssh.__exit__.return_value = False

        stdout = MagicMock()
        stdout.read.return_value = b""

        stderr = MagicMock()
        stderr.read.return_value = b""

        ssh.exec_command.return_value = (None, stdout, stderr)
        ssh_mock.return_value = ssh

        response = self.client.post(self.url)

        self.assertEqual(response.status_code, 302)

        docker_images_mock.assert_called_once()

        ssh.exec_command.assert_called_once_with(
            f"docker rmi -f {self.IMAGE_ID}"
        )