'''
Authentifizierung: Nicht angemeldete Benutzer werden zum Login weitergeleitet.
GET-Request: Die View leitet zu paas_my_apps weiter, ohne die App zu stoppen.
POST bei erlaubten Statuswerten: Der Status wird zunächst auf stopping gesetzt und stop_container() wird aufgerufen.
POST bei nicht erlaubten Statuswerten: Es erfolgt keine Änderung und kein Aufruf von stop_container().
Fremde App: Ein Benutzer darf keine App eines anderen Benutzers stoppen (404).
Nicht existierende App: Ebenfalls 404.
Alle erlaubten Statuswerte: Jeder der sechs erlaubten Statuswerte wird einzeln geprüft.
'''

from django.test import TestCase
from django.urls import reverse
from django.contrib.auth import get_user_model
from unittest.mock import patch

from paas.models import (
    AppDefinition,
    RemoteHost,
    ProvisionedApp,
)


User = get_user_model()


class StopAppViewTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            username='testuser',
            password='testpassword123',
        )

        cls.other_user = User.objects.create_user(
            username='otheruser',
            password='testpassword123',
        )

        cls.app = AppDefinition.objects.create(
            name='test-app',
            display_name='Test App',
            docker_image='test/image',
        )

        cls.host = RemoteHost.objects.create(
            hostname='test.example.com',
            ip_address='192.0.2.1',
        )

    def setUp(self):
        self.client.force_login(self.user)

        self.provision = ProvisionedApp.objects.create(
            user=self.user,
            app=self.app,
            host=self.host,
            container_id='test-container-id',
            container_name='test-container',
            status='running',
        )

        self.url = reverse(
            'paas_stop_app',
            kwargs={'pk': self.provision.pk},
        )

    # ---------------------------------------------------------
    # Authentifizierung
    # ---------------------------------------------------------

    def test_anonymous_user_is_redirected_to_login(self):
        self.client.logout()

        response = self.client.post(self.url)

        self.assertEqual(response.status_code, 302)
        self.assertIn(
            reverse('login'),
            response.url,
        )

    # ---------------------------------------------------------
    # GET-Request
    # ---------------------------------------------------------

    @patch.object(ProvisionedApp, 'stop_container')
    def test_get_redirects_without_stopping_app(self, mock_stop):
        response = self.client.get(self.url)

        self.assertRedirects(
            response,
            reverse('paas_my_apps'),
        )

        mock_stop.assert_not_called()

        self.provision.refresh_from_db()
        self.assertEqual(self.provision.status, 'running')

    # ---------------------------------------------------------
    # Erfolgreiches Stoppen
    # ---------------------------------------------------------

    @patch.object(ProvisionedApp, 'stop_container')
    def test_post_stops_running_app(self, mock_stop):
        response = self.client.post(self.url)

        self.assertRedirects(
            response,
            reverse('paas_my_apps'),
        )

        mock_stop.assert_called_once_with()

        self.provision.refresh_from_db()
        self.assertEqual(self.provision.status, 'stopped')

    def test_all_allowed_statuses_can_be_stopped(self):
        allowed_statuses = [
            'running',
            'deleting',
            'stopped',
            'error',
            'restarting',
            'pending',
        ]

        for status in allowed_statuses:
            with self.subTest(status=status):
                self.provision.status = status
                self.provision.save()

                with patch.object(
                    ProvisionedApp,
                    'stop_container',
                ) as mock_stop:

                    response = self.client.post(self.url)

                    self.assertEqual(response.status_code, 302)
                    self.assertRedirects(
                        response,
                        reverse('paas_my_apps'),
                    )

                    mock_stop.assert_called_once_with()

                self.provision.refresh_from_db()
                self.assertEqual(
                    self.provision.status,
                    'stopped',
                )

    # ---------------------------------------------------------
    # Ungültige Statuswerte
    # ---------------------------------------------------------

    def test_invalid_status_does_not_stop_app(self):
        invalid_statuses = [
            'finished',
            'deleted',
            'stopping',
            'unknown',
            '',
        ]

        for status in invalid_statuses:
            with self.subTest(status=status):
                self.provision.status = status
                self.provision.save()

                with patch.object(
                    ProvisionedApp,
                    'stop_container',
                ) as mock_stop:

                    response = self.client.post(self.url)

                    self.assertEqual(response.status_code, 302)

                    self.assertRedirects(
                        response,
                        reverse('paas_my_apps'),
                    )

                    mock_stop.assert_not_called()

                self.provision.refresh_from_db()
                self.assertEqual(
                    self.provision.status,
                    status,
                )

    # ---------------------------------------------------------
    # Zugriffsschutz
    # ---------------------------------------------------------

    def test_user_cannot_stop_another_users_app(self):
        other_provision = ProvisionedApp.objects.create(
            user=self.other_user,
            app=self.app,
            host=self.host,
            container_id='other-container-id',
            status='running',
        )

        url = reverse(
            'paas_stop_app',
            kwargs={'pk': other_provision.pk},
        )

        with patch.object(
            ProvisionedApp,
            'stop_container',
        ) as mock_stop:

            response = self.client.post(url)

            self.assertEqual(response.status_code, 404)
            mock_stop.assert_not_called()

        other_provision.refresh_from_db()
        self.assertEqual(other_provision.status, 'running')

    def test_nonexistent_provision_returns_404(self):
        url = reverse(
            'paas_stop_app',
            kwargs={'pk': 999999},
        )

        response = self.client.post(url)

        self.assertEqual(response.status_code, 404)

    # ---------------------------------------------------------
    # Andere HTTP-Methoden
    # ---------------------------------------------------------

    @patch.object(ProvisionedApp, 'stop_container')
    def test_put_request_does_not_stop_app(self, mock_stop):
        response = self.client.put(self.url)

        self.assertRedirects(
            response,
            reverse('paas_my_apps'),
        )

        mock_stop.assert_not_called()

        self.provision.refresh_from_db()
        self.assertEqual(self.provision.status, 'running')

    # Erfolgreiches Stoppen
    @patch.object(ProvisionedApp, 'stop_container', return_value=True)
    def test_successful_stop_sets_status_stopped(self, mock_stop):
        response = self.client.post(self.url)

        self.assertEqual(response.status_code, 302)

        self.provision.refresh_from_db()

        self.assertEqual(self.provision.status, 'stopped')
        mock_stop.assert_called_once()

    # Fehler beim Stoppen
    @patch.object(ProvisionedApp, 'stop_container', return_value=False)
    def test_failed_stop_sets_status_error(self, mock_stop):
        response = self.client.post(self.url)

        self.assertEqual(response.status_code, 302)

        self.provision.refresh_from_db()

        self.assertEqual(self.provision.status, 'error')
        mock_stop.assert_called_once()
