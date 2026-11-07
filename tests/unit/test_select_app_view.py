'''
Test --> Erwartung
GET als nicht angemeldeter Benutzer --> Redirect zum Login
GET als normaler Benutzer --> paas/select_app.html
GET als Superuser --> paas/select_app.html
POST als normaler Benutzer mit gültigen Daten --> paas/deploy_app.html
POST als Superuser mit gültigen Daten --> paas/deploy_app.html
POST mit ungültiger App-ID --> Formular bleibt auf der Auswahlseite
POST ohne App --> Formular bleibt auf der Auswahlseite
POST mit ungültiger Laufzeit --> Formular bleibt auf der Auswahlseite
POST bei überschrittenem Limit --> Fehlermeldung, kein Deployment
POST mit normalem Benutzer und super_user_only=True --> App wird abgelehnt
POST als Superuser mit super_user_only=True --> App wird akzeptiert
POST als normaler Benutzer --> DeployForm wird verwendet
POST als Superuser --> DeployFormAdmin wird verwendet
POST bei gültigen Daten --> Ausgewählte App bleibt im initialisierten Formular erhalten
'''

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from paas.models import AppDefinition, RemoteHost
from paas.forms import DeployForm, DeployFormAdmin


User = get_user_model()


class SelectAppViewTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            username="testuser",
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
            default_duration=1,
            tor_auth_type="none",
        )

        cls.admin_app = AppDefinition.objects.create(
            name="admin-app",
            display_name="Admin App",
            docker_image="test/admin-image",
            super_user_only=True,
            tor_auth_type="cert",
        )

        cls.host = RemoteHost.objects.create(
            hostname="test-host",
            ip_address="127.0.0.1",
        )

    def setUp(self):
        self.url = reverse("paas_select_app")

    # ---------------------------------------------------------
    # GET
    # ---------------------------------------------------------

    def test_anonymous_user_is_redirected_to_login(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 302)
        self.assertIn("/user-login", response.url)

    def test_get_as_normal_user(self):
        self.client.force_login(self.user)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "paas/select_app.html")

        self.assertIsInstance(response.context["form"], DeployForm)

        self.assertIn("PLATFORM_NAME", response.context)

    def test_get_as_superuser(self):
        self.client.force_login(self.superuser)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "paas/select_app.html")

        self.assertIsInstance(response.context["form"], DeployFormAdmin)

    # ---------------------------------------------------------
    # POST: erfolgreiche Formularverarbeitung
    # ---------------------------------------------------------

    @patch("paas.views._check_user_limits", return_value=True)
    def test_valid_post_as_normal_user(self, mock_limits):
        self.client.force_login(self.user)

        response = self.client.post(self.url, {
            "app": self.app.pk,
            "duration": "1h",
        })

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "paas/deploy_app.html")

        self.assertIsInstance(response.context["form"], DeployForm)

        self.assertEqual(
            response.context["form"].initial["app"],
            self.app,
        )

        self.assertEqual(
            response.context["tor_auth_type"],
            self.app.tor_auth_type,
        )

        mock_limits.assert_called_once()

    @patch("paas.views._check_user_limits", return_value=True)
    def test_valid_post_as_superuser(self, mock_limits):
        self.client.force_login(self.superuser)

        response = self.client.post(self.url, {
            "app": self.admin_app.pk,
            "duration": "1h",
            "target_host": self.host.pk,
        })

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "paas/deploy_app.html")

        self.assertIsInstance(response.context["form"], DeployFormAdmin)

        self.assertEqual(
            response.context["form"].initial["app"],
            self.admin_app,
        )

        mock_limits.assert_called_once()

    # ---------------------------------------------------------
    # POST: Formularvalidierung
    # ---------------------------------------------------------

    def test_post_without_app(self):
        self.client.force_login(self.user)

        response = self.client.post(self.url, {
            "duration": "1h",
        })

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "paas/select_app.html")

        self.assertFalse(response.context["form"].is_valid())
        self.assertIn("app", response.context["form"].errors)

    def test_post_with_invalid_app_id(self):
        self.client.force_login(self.user)

        response = self.client.post(self.url, {
            "app": 999999,
            "duration": "1h",
        })

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "paas/select_app.html")

        self.assertFalse(response.context["form"].is_valid())

    def test_post_with_invalid_duration(self):
        self.client.force_login(self.user)

        response = self.client.post(self.url, {
            "app": self.app.pk,
            "duration": "invalid-duration",
        })

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "paas/select_app.html")

        self.assertFalse(response.context["form"].is_valid())

    # ---------------------------------------------------------
    # POST: Berechtigungen
    # ---------------------------------------------------------

    def test_normal_user_cannot_select_admin_only_app(self):
        self.client.force_login(self.user)

        response = self.client.post(self.url, {
            "app": self.admin_app.pk,
            "duration": "1h",
        })

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "paas/select_app.html")

        self.assertFalse(response.context["form"].is_valid())

        self.assertIn("app", response.context["form"].errors)

    @patch("paas.views._check_user_limits", return_value=True)
    def test_superuser_can_select_admin_only_app(self, mock_limits):
        self.client.force_login(self.superuser)

        response = self.client.post(self.url, {
            "app": self.admin_app.pk,
            "duration": "1h",
            "target_host": self.host.pk,
        })

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "paas/deploy_app.html")

        self.assertEqual(
            response.context["form"].initial["app"],
            self.admin_app,
        )

    # ---------------------------------------------------------
    # POST: Benutzerlimits
    # ---------------------------------------------------------

    @patch("paas.views._check_user_limits", return_value=False)
    def test_post_when_user_limits_are_exceeded(self, mock_limits):
        self.client.force_login(self.user)

        response = self.client.post(self.url, {
            "app": self.app.pk,
            "duration": "1h",
        })

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "paas/select_app.html")

        self.assertIn("error", response.context)

        self.assertEqual(
            str(response.context["error"]),
            "Ihre Limits wurden überschritten.",
        )

        self.assertIsInstance(response.context["form"], DeployForm)

        mock_limits.assert_called_once()

    @patch("paas.views._check_user_limits", return_value=False)
    def test_superuser_bypasses_rate_limit_but_still_checks_user_limits(
        self, mock_limits
    ):
        self.client.force_login(self.superuser)

        response = self.client.post(self.url, {
            "app": self.app.pk,
            "duration": "1h",
            "target_host": self.host.pk,
        })

        # Der Superuser überspringt nur den Rate-Limit-Decorator.
        # _check_user_limits wird weiterhin aufgerufen.
        mock_limits.assert_called_once()

        self.assertEqual(response.status_code, 200)

    # ---------------------------------------------------------
    # POST: Fehlende Admin-Angaben
    # ---------------------------------------------------------

    def test_superuser_post_without_target_host_is_invalid(self):
        self.client.force_login(self.superuser)

        response = self.client.post(self.url, {
            "app": self.app.pk,
            "duration": "1h",
        })

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "paas/select_app.html")

        self.assertFalse(response.context["form"].is_valid())
        self.assertIn("target_host", response.context["form"].errors)
