'''
Zugriffsschutz: Nicht angemeldet, normaler Benutzer und Superuser.
Erfolgreiches Abrufen der Docker-Logs.
Übergabe des Parameters lines an Docker.
Fehlende, ungültige und nicht positive lines-Werte.
Fehlende Container-ID.
Nicht initialisierbarer Docker-Client.
Fehler beim Abrufen des Containers bzw. seiner Logs.
Korrekte HTTP-Header und Content-Type.
UTF-8-Dekodierung einschließlich ungültiger Bytes.
'''


from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from paas.models import (
    AppDefinition,
    ProvisionedApp,
    RemoteHost,
)


User = get_user_model()


class DockerLogsViewTests(TestCase):
    """Tests für die docker_logs-View."""

    @classmethod
    def setUpTestData(cls):
        cls.superuser = User.objects.create_superuser(
            username='admin',
            email='admin@example.com',
            password='testpassword',
        )

        cls.normal_user = User.objects.create_user(
            username='user',
            email='user@example.com',
            password='testpassword',
        )

        cls.app = AppDefinition.objects.create(
            name='test-app',
            display_name='Test App',
            docker_image='test/image',
        )

        cls.host = RemoteHost.objects.create(
            hostname='docker.example.com',
            ip_address='192.0.2.10',
            ssh_user='root',
        )

        cls.provision = ProvisionedApp.objects.create(
            user=cls.normal_user,
            app=cls.app,
            host=cls.host,
            container_id='container123',
            container_name='test-container',
        )

    def setUp(self):
        self.url = reverse(
            'docker_logs',
            args=[self.provision.id],
        )

    # ---------------------------------------------------------
    # Hilfsmethoden
    # ---------------------------------------------------------

    def login_superuser(self):
        self.client.force_login(self.superuser)

    def create_docker_client(self, logs=b'Test Docker logs'):
        """Erzeugt einen vollständig gemockten Docker-Client."""

        docker_client = MagicMock()
        container = MagicMock()

        container.logs.return_value = logs
        docker_client.containers.get.return_value = container

        return docker_client, container

    # ---------------------------------------------------------
    # Zugriffsschutz
    # ---------------------------------------------------------

    def test_anonymous_user_redirected_to_login(self):
        """Nicht angemeldete Benutzer müssen zum Login umgeleitet werden."""

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 302)
        self.assertIn('/user-login/', response.url)

    def test_normal_user_forbidden(self):
        """Normale Benutzer dürfen Docker-Logs nicht ansehen."""

        self.client.force_login(self.normal_user)

        with patch.object(
            ProvisionedApp,
            '_docker_client',
        ) as docker_mock:
            response = self.client.get(self.url)

        self.assertEqual(response.status_code, 302)
        docker_mock.assert_not_called()

    def test_superuser_has_access(self):
        """Superuser dürfen Docker-Logs abrufen."""

        self.login_superuser()

        docker_client, container = self.create_docker_client()

        with patch.object(
            ProvisionedApp,
            '_docker_client',
            return_value=docker_client,
        ):
            response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b'Test Docker logs')

        docker_client.containers.get.assert_called_once_with(
            'container123'
        )

    # ---------------------------------------------------------
    # Erfolgreicher Abruf
    # ---------------------------------------------------------

    def test_logs_are_returned_as_plain_text(self):
        """Logs werden als Plain-Text zurückgegeben."""

        self.login_superuser()

        log_content = b'Line 1\nLine 2\nLine 3\n'

        docker_client, container = self.create_docker_client(
            logs=log_content
        )

        with patch.object(
            ProvisionedApp,
            '_docker_client',
            return_value=docker_client,
        ):
            response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, log_content)
        self.assertTrue(
            response['Content-Type'].startswith('text/plain')
        )

    def test_content_disposition_contains_provision_id(self):
        """Der Dateiname enthält die ID der Provision."""

        self.login_superuser()

        docker_client, _ = self.create_docker_client()

        with patch.object(
            ProvisionedApp,
            '_docker_client',
            return_value=docker_client,
        ):
            response = self.client.get(self.url)

        self.assertEqual(
            response['Content-Disposition'],
            f'inline; filename="docker-logs-{self.provision.id}.txt"',
        )

    # ---------------------------------------------------------
    # Parameter lines
    # ---------------------------------------------------------

    def test_default_lines_parameter_is_none(self):
        """Ohne lines-Parameter wird tail=None übergeben."""

        self.login_superuser()

        docker_client, container = self.create_docker_client()

        with patch.object(
            ProvisionedApp,
            '_docker_client',
            return_value=docker_client,
        ):
            response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)

        container.logs.assert_called_once_with(
            tail=None,
            stream=False,
        )

    def test_valid_lines_parameter(self):
        """Ein gültiger lines-Parameter wird übernommen."""

        self.login_superuser()

        docker_client, container = self.create_docker_client()

        with patch.object(
            ProvisionedApp,
            '_docker_client',
            return_value=docker_client,
        ):
            response = self.client.get(
                self.url,
                {'lines': '250'},
            )

        self.assertEqual(response.status_code, 200)

        container.logs.assert_called_once_with(
            tail=250,
            stream=False,
        )

    def test_invalid_lines_parameter_falls_back_to_100(self):
        """Ungültige Werte führen zum Fallback auf 100."""

        self.login_superuser()

        docker_client, container = self.create_docker_client()

        invalid_values = [
            'abc',
            '1.5',
            '-1',
            '0',
        ]

        for value in invalid_values:
            with self.subTest(lines=value):
                container.logs.reset_mock()

                with patch.object(
                    ProvisionedApp,
                    '_docker_client',
                    return_value=docker_client,
                ):
                    response = self.client.get(
                        self.url,
                        {'lines': value},
                    )

                self.assertEqual(response.status_code, 200)

                container.logs.assert_called_once_with(
                    tail=100,
                    stream=False,
                )

    # ---------------------------------------------------------
    # Fehlende Container-ID
    # ---------------------------------------------------------

    def test_missing_container_id_returns_message(self):
        """Ohne Container-ID wird eine entsprechende Meldung ausgegeben."""

        self.login_superuser()

        self.provision.container_id = ''
        self.provision.save(update_fields=['container_id'])

        docker_client, _ = self.create_docker_client()

        with patch.object(
            ProvisionedApp,
            '_docker_client',
            return_value=docker_client,
        ):
            response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.content.decode('utf-8'),
            'Kein Container-ID vorhanden.',
        )

        docker_client.containers.get.assert_not_called()

    # ---------------------------------------------------------
    # Docker-Client
    # ---------------------------------------------------------

    def test_docker_client_initialization_failure_returns_500(self):
        """Wenn kein Docker-Client erzeugt werden kann, kommt HTTP 500."""

        self.login_superuser()

        with patch.object(
            ProvisionedApp,
            '_docker_client',
            return_value=None,
        ):
            response = self.client.get(self.url)

        self.assertEqual(response.status_code, 500)
        self.assertEqual(
            response.content.decode('utf-8'),
            'Docker-Client konnte nicht initialisiert werden.',
        )

        self.assertTrue(
            response['Content-Type'].startswith('text/plain')
        )

    # ---------------------------------------------------------
    # Docker-Fehler
    # ---------------------------------------------------------

    def test_container_not_found_returns_error_message(self):
        """Fehler beim Auffinden des Containers werden abgefangen."""

        self.login_superuser()

        docker_client = MagicMock()
        docker_client.containers.get.side_effect = Exception(
            'Container not found'
        )

        with patch.object(
            ProvisionedApp,
            '_docker_client',
            return_value=docker_client,
        ):
            response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.content.decode('utf-8'),
            'Fehler beim Auslesen der Logs: Container not found',
        )

    def test_logs_exception_returns_error_message(self):
        """Fehler beim Lesen der Logs werden abgefangen."""

        self.login_superuser()

        docker_client, container = self.create_docker_client()

        container.logs.side_effect = Exception('Docker API error')

        with patch.object(
            ProvisionedApp,
            '_docker_client',
            return_value=docker_client,
        ):
            response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.content.decode('utf-8'),
            'Fehler beim Auslesen der Logs: Docker API error',
        )

    # ---------------------------------------------------------
    # UTF-8
    # ---------------------------------------------------------

    def test_utf8_logs_are_decoded_correctly(self):
        """UTF-8-Zeichen werden korrekt ausgegeben."""

        self.login_superuser()

        log_content = 'Grüße aus dem Container – äöüß'.encode('utf-8')

        docker_client, _ = self.create_docker_client(
            logs=log_content
        )

        with patch.object(
            ProvisionedApp,
            '_docker_client',
            return_value=docker_client,
        ):
            response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.content.decode('utf-8'),
            'Grüße aus dem Container – äöüß',
        )

    def test_invalid_utf8_bytes_are_replaced(self):
        """Ungültige UTF-8-Bytes werden durch Ersatzzeichen ersetzt."""

        self.login_superuser()

        docker_client, _ = self.create_docker_client(
            logs=b'Hello \xff World'
        )

        with patch.object(
            ProvisionedApp,
            '_docker_client',
            return_value=docker_client,
        ):
            response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.content.decode('utf-8'),
            'Hello \ufffd World',
        )

    # ---------------------------------------------------------
    # Nicht existierende Provision
    # ---------------------------------------------------------

    def test_nonexistent_provision_returns_404(self):
        """Eine nicht vorhandene Provision liefert HTTP 404."""

        self.login_superuser()

        url = reverse(
            'docker_logs',
            args=[999999],
        )

        with patch.object(
            ProvisionedApp,
            '_docker_client',
        ) as docker_mock:
            response = self.client.get(url)

        self.assertEqual(response.status_code, 404)
        docker_mock.assert_not_called()

    def test_lines_parameter_is_limited_to_10000(self):
        """Mehr als 10000 Zeilen werden auf 10000 begrenzt."""

        self.login_superuser()

        docker_client, container = self.create_docker_client()

        with patch.object(
                ProvisionedApp,
                '_docker_client',
                return_value=docker_client,
        ):
            response = self.client.get(
                self.url,
                {'lines': '50000'},
            )

        self.assertEqual(response.status_code, 200)

        container.logs.assert_called_once_with(
            tail=10000,
            stream=False,
        )

    def test_docker_client_is_closed(self):
        """Der Docker-Client wird nach dem Log-Abruf geschlossen."""

        self.login_superuser()

        docker_client, _ = self.create_docker_client()

        with patch.object(
                ProvisionedApp,
                '_docker_client',
                return_value=docker_client,
        ):
            response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        docker_client.close.assert_called_once_with()
