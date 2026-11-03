'''
Nicht angemeldete Benutzer werden zur Login-Seite weitergeleitet.
GET zeigt keine Bestätigungsseite und löscht nichts.
Erstes POST zeigt die Bestätigungsseite.
Zweites POST mit confirmed setzt den Status auf deleting.
delete_container_task() wird genau einmal aufgerufen.
Benutzer können keine Apps anderer Benutzer löschen.
Nicht erlaubte Status führen zurück zur App-Übersicht.
Alle erlaubten Status werden akzeptiert.
POST ohne confirmed löscht noch nichts.
Andere HTTP-Methoden lösen keine Löschung aus.
'''


from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth import get_user_model

from unittest.mock import patch

from paas.models import (
    AppDefinition,
    RemoteHost,
    ProvisionedApp,
)


User = get_user_model()


class DeleteAppViewTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            username='testuser',
            password='testpassword'
        )

        cls.other_user = User.objects.create_user(
            username='otheruser',
            password='otherpassword'
        )

        cls.app = AppDefinition.objects.create(
            name='test-app',
            display_name='Test App',
            docker_image='test/image',
        )

        cls.host = RemoteHost.objects.create(
            hostname='test-host',
            ip_address='127.0.0.1',
        )

    def setUp(self):
        self.client = Client()
        self.client.login(
            username='testuser',
            password='testpassword'
        )

        self.provision = ProvisionedApp.objects.create(
            user=self.user,
            app=self.app,
            host=self.host,
            container_id='test-container-id',
            container_name='test-container',
            status='running',
        )

        self.url = reverse(
            'paas_delete_app',
            kwargs={'pk': self.provision.pk}
        )

    # ---------------------------------------------------------
    # Hilfsmethode
    # ---------------------------------------------------------

    def create_provision(self, user=None, status='running'):
        return ProvisionedApp.objects.create(
            user=user or self.user,
            app=self.app,
            host=self.host,
            container_id='another-container-id',
            status=status,
        )

    # ---------------------------------------------------------
    # Authentifizierung
    # ---------------------------------------------------------

    def test_anonymous_user_is_redirected_to_login(self):
        self.client.logout()

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 302)
        self.assertIn('/user-login', response.url)

    # ---------------------------------------------------------
    # GET
    # ---------------------------------------------------------

    @patch('paas.views.delete_container_task')
    def test_get_does_not_delete_app(self, mock_delete):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'paas/my_apps.html')

        self.provision.refresh_from_db()

        self.assertEqual(self.provision.status, 'running')
        mock_delete.assert_not_called()

    # ---------------------------------------------------------
    # Erster POST: Bestätigungsseite
    # ---------------------------------------------------------

    @patch('paas.views.delete_container_task')
    def test_first_post_shows_confirmation_page(self, mock_delete):
        response = self.client.post(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(
            response,
            'paas/confirm_delete_app.html'
        )

        self.assertEqual(
            response.context['provision'],
            self.provision
        )

        self.provision.refresh_from_db()

        self.assertEqual(self.provision.status, 'running')
        mock_delete.assert_not_called()

    # ---------------------------------------------------------
    # Zweiter POST: Tatsächliches Löschen
    # ---------------------------------------------------------

    @patch('paas.views.delete_container_task')
    def test_confirmed_post_deletes_app(self, mock_delete):
        response = self.client.post(
            self.url,
            {'confirmed': '1'}
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'paas/my_apps.html')

        self.provision.refresh_from_db()

        self.assertEqual(self.provision.status, 'deleting')

        mock_delete.assert_called_once_with(self.provision.id)

    @patch('paas.views.delete_container_task')
    def test_confirmed_post_calls_delete_task_only_once(self, mock_delete):
        self.client.post(
            self.url,
            {'confirmed': '1'}
        )

        mock_delete.assert_called_once()

    # ---------------------------------------------------------
    # Sicherheit: Fremde Apps
    # ---------------------------------------------------------

    @patch('paas.views.delete_container_task')
    def test_user_cannot_delete_another_users_app(self, mock_delete):
        foreign_provision = self.create_provision(
            user=self.other_user,
            status='running'
        )

        url = reverse(
            'paas_delete_app',
            kwargs={'pk': foreign_provision.pk}
        )

        response = self.client.post(
            url,
            {'confirmed': '1'}
        )

        self.assertEqual(response.status_code, 404)

        foreign_provision.refresh_from_db()

        self.assertEqual(foreign_provision.status, 'running')
        mock_delete.assert_not_called()

    # ---------------------------------------------------------
    # Nicht erlaubte Status
    # ---------------------------------------------------------

    @patch('paas.views.delete_container_task')
    def test_invalid_status_does_not_delete_app(self, mock_delete):
        self.provision.status = 'finished'
        self.provision.save()

        response = self.client.post(
            self.url,
            {'confirmed': '1'}
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'paas/my_apps.html')

        self.provision.refresh_from_db()

        self.assertEqual(self.provision.status, 'finished')
        mock_delete.assert_not_called()

    # ---------------------------------------------------------
    # Alle erlaubten Status
    # ---------------------------------------------------------

    @patch('paas.views.delete_container_task')
    def test_all_allowed_statuses_can_be_deleted(self, mock_delete):
        allowed_statuses = [
            'running',
            'deleting',
            'stopped',
            'stopping',
            'error',
            'restarting',
            'pending',
        ]

        for status in allowed_statuses:
            with self.subTest(status=status):
                provision = self.create_provision(status=status)

                url = reverse(
                    'paas_delete_app',
                    kwargs={'pk': provision.pk}
                )

                response = self.client.post(
                    url,
                    {'confirmed': '1'}
                )

                self.assertEqual(response.status_code, 200)

                provision.refresh_from_db()

                self.assertEqual(
                    provision.status,
                    'deleting'
                )

                mock_delete.assert_any_call(provision.id)

        self.assertEqual(
            mock_delete.call_count,
            len(allowed_statuses)
        )

    # ---------------------------------------------------------
    # POST ohne Bestätigung
    # ---------------------------------------------------------

    @patch('paas.views.delete_container_task')
    def test_post_without_confirmed_never_deletes(self, mock_delete):
        response = self.client.post(
            self.url,
            {'some_other_field': 'value'}
        )

        self.assertEqual(response.status_code, 200)

        self.assertTemplateUsed(
            response,
            'paas/confirm_delete_app.html'
        )

        self.provision.refresh_from_db()

        self.assertEqual(self.provision.status, 'running')
        mock_delete.assert_not_called()

    # ---------------------------------------------------------
    # Nicht existierende App
    # ---------------------------------------------------------

    @patch('paas.views.delete_container_task')
    def test_nonexistent_app_returns_404(self, mock_delete):
        url = reverse(
            'paas_delete_app',
            kwargs={'pk': 999999}
        )

        response = self.client.post(
            url,
            {'confirmed': '1'}
        )

        self.assertEqual(response.status_code, 404)
        mock_delete.assert_not_called()

    # ---------------------------------------------------------
    # Andere HTTP-Methode
    # ---------------------------------------------------------

    @patch('paas.views.delete_container_task')
    def test_put_does_not_delete_app(self, mock_delete):
        response = self.client.put(self.url)

        self.assertEqual(response.status_code, 200)

        self.provision.refresh_from_db()

        self.assertEqual(self.provision.status, 'running')
        mock_delete.assert_not_called()
