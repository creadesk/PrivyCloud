'''
Unauthentifizierter Benutzer: Weiterleitung zur Login-Seite.
GET-Request: Weiterleitung zu paas_my_apps, ohne die App zu verändern.
POST mit Status stopped: Status wird auf starting gesetzt und start_container() wird aufgerufen.
POST mit Status running: Kein Start, keine Statusänderung.
POST mit Status starting: Kein erneuter Start.
POST mit Status error, deleted oder pending: Kein Start.
Fremde App: Ein Benutzer darf keine App eines anderen Benutzers starten (404).
Nicht existierende App: 404.
Superuser: Darf ebenfalls nur eigene Apps starten.
'''
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from paas.models import (
    AppDefinition,
    ProvisionedApp,
    RemoteHost,
)


User = get_user_model()


class StartAppViewTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            username="testuser",
            password="testpassword",
        )

        cls.other_user = User.objects.create_user(
            username="otheruser",
            password="testpassword",
        )

        cls.superuser = User.objects.create_superuser(
            username="admin",
            password="adminpassword",
            email="admin@example.com",
        )

        cls.app = AppDefinition.objects.create(
            name="test-app",
            display_name="Test App",
            docker_image="test/image",
        )

        cls.host = RemoteHost.objects.create(
            hostname="test-host",
            ip_address="192.168.1.100",
        )

    def create_provision(self, user=None, status="stopped"):
        """Erstellt eine bereitgestellte App für einen Benutzer."""
        return ProvisionedApp.objects.create(
            user=user or self.user,
            app=self.app,
            host=self.host,
            container_id="test-container-id",
            container_name="test-container",
            status=status,
            log="",
        )

    def get_url(self, provision):
        return reverse(
            "paas_start_app",
            kwargs={"pk": provision.pk},
        )

    # ---------------------------------------------------------
    # Authentifizierung
    # ---------------------------------------------------------

    def test_anonymous_user_is_redirected_to_login(self):
        provision = self.create_provision()

        response = self.client.post(self.get_url(provision))

        self.assertEqual(response.status_code, 302)
        self.assertIn("/user-login", response.url)

        provision.refresh_from_db()
        self.assertEqual(provision.status, "stopped")

    # ---------------------------------------------------------
    # GET
    # ---------------------------------------------------------

    @patch.object(ProvisionedApp, "start_container")
    def test_get_request_redirects_without_starting_container(
        self, mock_start
    ):
        provision = self.create_provision()

        self.client.force_login(self.user)

        response = self.client.get(self.get_url(provision))

        self.assertRedirects(
            response,
            reverse("paas_my_apps"),
            fetch_redirect_response=False,
        )

        mock_start.assert_not_called()

        provision.refresh_from_db()
        self.assertEqual(provision.status, "stopped")

    # ---------------------------------------------------------
    # Erfolgreicher Start
    # ---------------------------------------------------------

    @patch.object(ProvisionedApp, "start_container", return_value=True)
    def test_post_stopped_app_starts_container(self, mock_start):
        provision = self.create_provision(status="stopped")

        self.client.force_login(self.user)

        response = self.client.post(self.get_url(provision))

        self.assertRedirects(
            response,
            reverse("paas_my_apps"),
            fetch_redirect_response=False,
        )

        mock_start.assert_called_once()

        provision.refresh_from_db()
        self.assertEqual(provision.status, "starting")

    # ---------------------------------------------------------
    # Ungültige Status
    # ---------------------------------------------------------

    @patch.object(ProvisionedApp, "start_container")
    def test_post_running_app_does_not_start_container(
        self, mock_start
    ):
        provision = self.create_provision(status="running")

        self.client.force_login(self.user)

        response = self.client.post(self.get_url(provision))

        self.assertRedirects(
            response,
            reverse("paas_my_apps"),
            fetch_redirect_response=False,
        )

        mock_start.assert_not_called()

        provision.refresh_from_db()
        self.assertEqual(provision.status, "running")

    @patch.object(ProvisionedApp, "start_container")
    def test_post_starting_app_does_not_start_container(
        self, mock_start
    ):
        provision = self.create_provision(status="starting")

        self.client.force_login(self.user)

        response = self.client.post(self.get_url(provision))

        self.assertEqual(response.status_code, 302)
        mock_start.assert_not_called()

        provision.refresh_from_db()
        self.assertEqual(provision.status, "starting")

    @patch.object(ProvisionedApp, "start_container")
    def test_post_pending_app_does_not_start_container(
        self, mock_start
    ):
        provision = self.create_provision(status="pending")

        self.client.force_login(self.user)

        response = self.client.post(self.get_url(provision))

        self.assertEqual(response.status_code, 302)
        mock_start.assert_not_called()

        provision.refresh_from_db()
        self.assertEqual(provision.status, "pending")

    @patch.object(ProvisionedApp, "start_container")
    def test_post_error_app_does_not_start_container(
        self, mock_start
    ):
        provision = self.create_provision(status="error")

        self.client.force_login(self.user)

        response = self.client.post(self.get_url(provision))

        self.assertEqual(response.status_code, 302)
        mock_start.assert_not_called()

        provision.refresh_from_db()
        self.assertEqual(provision.status, "error")

    @patch.object(ProvisionedApp, "start_container")
    def test_post_deleted_app_does_not_start_container(
        self, mock_start
    ):
        provision = self.create_provision(status="deleted")

        self.client.force_login(self.user)

        response = self.client.post(self.get_url(provision))

        self.assertEqual(response.status_code, 302)
        mock_start.assert_not_called()

        provision.refresh_from_db()
        self.assertEqual(provision.status, "deleted")

    # ---------------------------------------------------------
    # Zugriffsschutz
    # ---------------------------------------------------------

    @patch.object(ProvisionedApp, "start_container")
    def test_user_cannot_start_another_users_app(self, mock_start):
        provision = self.create_provision(
            user=self.other_user,
            status="stopped",
        )

        self.client.force_login(self.user)

        response = self.client.post(self.get_url(provision))

        self.assertEqual(response.status_code, 404)
        mock_start.assert_not_called()

        provision.refresh_from_db()
        self.assertEqual(provision.status, "stopped")

    @patch.object(ProvisionedApp, "start_container")
    def test_superuser_cannot_start_another_users_app(
        self, mock_start
    ):
        provision = self.create_provision(
            user=self.other_user,
            status="stopped",
        )

        self.client.force_login(self.superuser)

        response = self.client.post(self.get_url(provision))

        self.assertEqual(response.status_code, 404)
        mock_start.assert_not_called()

        provision.refresh_from_db()
        self.assertEqual(provision.status, "stopped")

    @patch.object(ProvisionedApp, "start_container")
    def test_user_can_start_own_app(self, mock_start):
        provision = self.create_provision(
            user=self.user,
            status="stopped",
        )

        self.client.force_login(self.user)

        response = self.client.post(self.get_url(provision))

        self.assertEqual(response.status_code, 302)
        mock_start.assert_called_once()

    # ---------------------------------------------------------
    # Nicht existierende App
    # ---------------------------------------------------------

    @patch.object(ProvisionedApp, "start_container")
    def test_nonexistent_provision_returns_404(self, mock_start):
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("paas_start_app", kwargs={"pk": 999999})
        )

        self.assertEqual(response.status_code, 404)
        mock_start.assert_not_called()
