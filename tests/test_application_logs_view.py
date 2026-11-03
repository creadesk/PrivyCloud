'''
1. Zugriffsschutz
    Nicht angemeldeter Benutzer wird zum Login weitergeleitet.
    Normaler Benutzer erhält HTTP 302 bzw. wird abgewiesen (abhängig von der Decorator-Konfiguration).
    Superuser darf die View aufrufen.
2. Log-Pfad
    Kein definierter Log-Pfad.
    Ungültiger bzw. unsicherer Log-Pfad.
    Provisionierte App existiert nicht.
3. Parameter lines
    Standardwert 200.
    Gültiger Wert wird übernommen.
    Ungültiger Wert wird auf 200 zurückgesetzt.
    Wert 0 und negative Werte werden auf 200 zurückgesetzt.
4. SSH und Docker
    Log-Datei existiert nicht.
    SSH-Verbindung schlägt fehl.
    tail schlägt fehl.
    tail liefert Bytes.
    tail liefert bereits einen String.
    Ungültiges UTF-8 wird mit Replacement-Zeichen ausgegeben.
'''


from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from paas.models import (
    AppDefinition,
    RemoteHost,
    ProvisionedApp,
)


User = get_user_model()


class ApplicationLogsViewTests(TestCase):

    def setUp(self):
        self.superuser = User.objects.create_superuser(
            username='admin',
            email='admin@example.com',
            password='test-password',
        )

        self.normal_user = User.objects.create_user(
            username='user',
            email='user@example.com',
            password='test-password',
        )

        self.app = AppDefinition.objects.create(
            name='test-app',
            display_name='Test App',
            docker_image='test/image',
        )

        self.host = RemoteHost.objects.create(
            hostname='test-host',
            ip_address='192.0.2.10',
        )

        self.provision = ProvisionedApp.objects.create(
            user=self.superuser,
            app=self.app,
            host=self.host,
            container_id='test-container',
            container_name='test-container',
            application_log_path='/var/log/app.log',
            status='running',
        )

        self.url = reverse(
            'app_logs',
            kwargs={'pk': self.provision.pk},
        )

        self.client.force_login(self.superuser)

    # ---------------------------------------------------------
    # Hilfsmethoden
    # ---------------------------------------------------------

    def mock_ssh_success(self, stdout=b'test log output\n'):
        """
        Simuliert:
        1. Erfolgreiche Existenzprüfung
        2. Erfolgreiches tail-Kommando
        """

        ssh_context = MagicMock()
        ssh_context.__enter__.return_value = MagicMock()
        ssh_context.__exit__.return_value = False

        ssh_mock = MagicMock(return_value=ssh_context)

        run_bytes_mock = MagicMock(
            return_value=(0, b'', b'')
        )

        run_cmd_mock = MagicMock(
            return_value=(0, stdout, b'')
        )

        return ssh_mock, run_bytes_mock, run_cmd_mock

    # ---------------------------------------------------------
    # Zugriffsschutz
    # ---------------------------------------------------------

    def test_anonymous_user_is_redirected_to_login(self):
        self.client.logout()

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 302)
        self.assertIn(
            reverse('login'),
            response.url,
        )

    def test_normal_user_is_denied(self):
        self.client.force_login(self.normal_user)

        response = self.client.get(self.url)

        self.assertIn(response.status_code, (302, 403))

    def test_superuser_can_access_view(self):
        ssh, run_bytes, run_cmd = self.mock_ssh_success()

        with (
            patch('paas.views._ssh_client', ssh),
            patch('paas.views._run_cmd_bytes', run_bytes),
            patch('paas.views._run_cmd', run_cmd),
        ):
            response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)

    # ---------------------------------------------------------
    # ProvisionedApp
    # ---------------------------------------------------------

    def test_nonexistent_provision_returns_404(self):
        url = reverse(
            'app_logs',
            kwargs={'pk': 999999},
        )

        response = self.client.get(url)

        self.assertEqual(response.status_code, 404)

    # ---------------------------------------------------------
    # Log-Pfad
    # ---------------------------------------------------------

    def test_missing_log_path_returns_404(self):
        self.provision.application_log_path = ''
        self.provision.save()

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 404)
        self.assertIn(
            'Log',
            response.content.decode(),
        )

    def test_none_log_path_returns_404(self):
        self.provision.application_log_path = None
        self.provision.save()

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 404)

    def test_invalid_log_path_returns_404(self):
        self.provision.application_log_path = (
            '/var/log/../../etc/passwd'
        )
        self.provision.save()

        with patch('paas.views._ssh_client') as ssh:
            response = self.client.get(self.url)

        self.assertEqual(response.status_code, 404)
        ssh.assert_not_called()

    # ---------------------------------------------------------
    # lines-Parameter
    # ---------------------------------------------------------

    def test_default_lines_parameter_is_200(self):
        ssh, run_bytes, run_cmd = self.mock_ssh_success()

        with (
            patch('paas.views._ssh_client', ssh),
            patch('paas.views._run_cmd_bytes', run_bytes),
            patch('paas.views._run_cmd', run_cmd),
        ):
            response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)

        tail_command = run_cmd.call_args.args[1]

        self.assertIn('tail -n 200', tail_command)

    def test_custom_lines_parameter_is_used(self):
        ssh, run_bytes, run_cmd = self.mock_ssh_success()

        with (
            patch('paas.views._ssh_client', ssh),
            patch('paas.views._run_cmd_bytes', run_bytes),
            patch('paas.views._run_cmd', run_cmd),
        ):
            response = self.client.get(
                self.url,
                {'lines': '50'},
            )

        self.assertEqual(response.status_code, 200)

        tail_command = run_cmd.call_args.args[1]

        self.assertIn('tail -n 50', tail_command)

    def test_invalid_lines_parameter_defaults_to_200(self):
        invalid_values = [
            'abc',
            '0',
            '-1',
            '',
        ]

        for value in invalid_values:
            with self.subTest(lines=value):
                ssh, run_bytes, run_cmd = (
                    self.mock_ssh_success()
                )

                with (
                    patch('paas.views._ssh_client', ssh),
                    patch(
                        'paas.views._run_cmd_bytes',
                        run_bytes,
                    ),
                    patch(
                        'paas.views._run_cmd',
                        run_cmd,
                    ),
                ):
                    response = self.client.get(
                        self.url,
                        {'lines': value},
                    )

                self.assertEqual(response.status_code, 200)

                tail_command = run_cmd.call_args.args[1]

                self.assertIn(
                    'tail -n 200',
                    tail_command,
                )

    # ---------------------------------------------------------
    # Prüfung der Log-Datei
    # ---------------------------------------------------------

    def test_log_file_not_existing_returns_404(self):
        ssh_context = MagicMock()
        ssh_context.__enter__.return_value = MagicMock()
        ssh_context.__exit__.return_value = False

        with (
            patch(
                'paas.views._ssh_client',
                return_value=ssh_context,
            ),
            patch(
                'paas.views._run_cmd_bytes',
                return_value=(1, b'', b''),
            ),
            patch('paas.views._run_cmd') as run_cmd,
        ):
            response = self.client.get(self.url)

        self.assertEqual(response.status_code, 404)
        run_cmd.assert_not_called()

    def test_ssh_error_during_file_check_returns_404(self):
        with patch(
            'paas.views._ssh_client',
            side_effect=Exception('SSH connection failed'),
        ):
            response = self.client.get(self.url)

        self.assertEqual(response.status_code, 404)
        self.assertIn(
            'SSH connection failed',
            response.content.decode(),
        )

    # ---------------------------------------------------------
    # tail-Kommando
    # ---------------------------------------------------------

    def test_tail_error_returns_404(self):
        ssh_context = MagicMock()
        ssh_context.__enter__.return_value = MagicMock()
        ssh_context.__exit__.return_value = False

        with (
            patch(
                'paas.views._ssh_client',
                return_value=ssh_context,
            ),
            patch(
                'paas.views._run_cmd_bytes',
                return_value=(0, b'', b''),
            ),
            patch(
                'paas.views._run_cmd',
                return_value=(1, b'', b'tail failed'),
            ),
        ):
            response = self.client.get(self.url)

        self.assertEqual(response.status_code, 404)
        self.assertIn(
            'tail failed',
            response.content.decode(),
        )

    def test_ssh_error_during_tail_returns_404(self):
        ssh_context = MagicMock()
        ssh_context.__enter__.return_value = MagicMock()
        ssh_context.__exit__.return_value = False

        ssh_mock = MagicMock(
            side_effect=[
                ssh_context,
                Exception('SSH disconnected'),
            ]
        )

        with (
            patch('paas.views._ssh_client', ssh_mock),
            patch(
                'paas.views._run_cmd_bytes',
                return_value=(0, b'', b''),
            ),
        ):
            response = self.client.get(self.url)

        self.assertEqual(response.status_code, 404)
        self.assertIn(
            'SSH disconnected',
            response.content.decode(),
        )

    # ---------------------------------------------------------
    # Ausgabe der Logs
    # ---------------------------------------------------------

    def test_log_output_as_bytes(self):
        log_content = b'Line 1\nLine 2\nLine 3\n'

        ssh, run_bytes, run_cmd = self.mock_ssh_success(
            stdout=log_content,
        )

        with (
            patch('paas.views._ssh_client', ssh),
            patch('paas.views._run_cmd_bytes', run_bytes),
            patch('paas.views._run_cmd', run_cmd),
        ):
            response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, log_content)
        self.assertEqual(
            response['Content-Type'],
            'text/plain',
        )

    def test_log_output_as_string(self):
        log_content = 'Line 1\nLine 2\n'

        ssh, run_bytes, run_cmd = self.mock_ssh_success(
            stdout=log_content,
        )

        with (
            patch('paas.views._ssh_client', ssh),
            patch('paas.views._run_cmd_bytes', run_bytes),
            patch('paas.views._run_cmd', run_cmd),
        ):
            response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.content.decode(),
            log_content,
        )

    def test_invalid_utf8_is_replaced(self):
        log_content = b'Valid text \xff invalid byte\n'

        ssh, run_bytes, run_cmd = self.mock_ssh_success(
            stdout=log_content,
        )

        with (
            patch('paas.views._ssh_client', ssh),
            patch('paas.views._run_cmd_bytes', run_bytes),
            patch('paas.views._run_cmd', run_cmd),
        ):
            response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)

        self.assertIn(
            '\ufffd',
            response.content.decode('utf-8'),
        )
