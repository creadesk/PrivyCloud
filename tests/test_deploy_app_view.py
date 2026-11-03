'''
Test --> Erwartung
Nicht angemeldet --> Weiterleitung zum Login
GET als normaler Benutzer --> Formularauswahl wird angezeigt
GET als Superuser --> Formularauswahl wird angezeigt
POST ungültiges Formular --> select_app.html, Formular enthält Fehler
POST gültig, normaler Benutzer --> Vorschau wird angezeigt
POST gültig, Superuser --> Vorschau mit ausgewähltem Target-Host
Normaler Benutzer wählt Superuser-App --> Formular ungültig
POST mit doIT --> _handle_deploy() wird aufgerufen
'''

from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.http import HttpResponse
from django.test import TestCase
from django.urls import reverse

from paas.models import (
    AppDefinition,
    AppImageTag,
    RemoteHost,
)


User = get_user_model()


class DeployAppViewTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        # Benutzer
        cls.user = User.objects.create_user(
            username="normal_user",
            password="test-password",
        )

        cls.admin = User.objects.create_superuser(
            username="admin_user",
            email="admin@example.com",
            password="test-password",
        )

        # Normale App
        cls.app = AppDefinition.objects.create(
            name="test-app",
            display_name="Test App",
            docker_image="example/test-app",
            description="Eine Test-Anwendung",
            default_duration=1,
            super_user_only=False,
        )

        # Nur für Superuser verfügbare App
        cls.admin_app = AppDefinition.objects.create(
            name="admin-app",
            display_name="Admin App",
            docker_image="example/admin-app",
            description="Admin-Anwendung",
            super_user_only=True,
        )

        # Image-Tags
        cls.tag = AppImageTag.objects.create(
            app_definition=cls.app,
            tag="1.0.0",
        )

        # Target-Host
        cls.host = RemoteHost.objects.create(
            hostname="test.example.com",
            ip_address="192.0.2.10",
            ssh_user="root",
        )

    def setUp(self):
        self.url = reverse("paas_deploy_app")

    # ---------------------------------------------------------
    # Hilfsfunktionen
    # ---------------------------------------------------------

    def login_user(self):
        self.client.force_login(self.user)

    def login_admin(self):
        self.client.force_login(self.admin)

    def valid_user_post_data(self):
        return {
            "app": str(self.app.pk),
            "duration": "1h",
        }

    def valid_admin_post_data(self):
        return {
            "app": str(self.app.pk),
            "duration": "1h",
            "target_host": str(self.host.pk),
        }

    # ---------------------------------------------------------
    # Authentifizierung
    # ---------------------------------------------------------

    def test_anonymous_user_is_redirected_to_login(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response.url)

    # ---------------------------------------------------------
    # GET
    # ---------------------------------------------------------

    def test_get_as_normal_user(self):
        self.login_user()

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "paas/select_app.html")
        self.assertIn("form", response.context)

        form = response.context["form"]

        # Normale Benutzer dürfen keine Superuser-Apps auswählen.
        available_apps = form.fields["app"].queryset

        self.assertIn(self.app, available_apps)
        self.assertNotIn(self.admin_app, available_apps)

    def test_get_as_superuser(self):
        self.login_admin()

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "paas/select_app.html")
        self.assertIn("form", response.context)

        form = response.context["form"]

        # Superuser dürfen alle Apps auswählen.
        available_apps = form.fields["app"].queryset

        self.assertIn(self.app, available_apps)
        self.assertIn(self.admin_app, available_apps)

        # Superuser erhalten zusätzlich das Target-Host-Feld.
        self.assertIn("target_host", form.fields)

    # ---------------------------------------------------------
    # POST: ungültiges Formular
    # ---------------------------------------------------------

    def test_invalid_post_as_normal_user(self):
        self.login_user()

        response = self.client.post(
            self.url,
            {
                "app": "",
                "duration": "1h",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "paas/select_app.html")

        form = response.context["form"]

        self.assertFalse(form.is_valid())
        self.assertIn("app", form.errors)

    def test_normal_user_cannot_select_superuser_app(self):
        self.login_user()

        response = self.client.post(
            self.url,
            {
                "app": str(self.admin_app.pk),
                "duration": "1h",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "paas/select_app.html")

        form = response.context["form"]

        self.assertFalse(form.is_valid())
        self.assertIn("app", form.errors)

    # ---------------------------------------------------------
    # POST: gültiges Formular -> Vorschau
    # ---------------------------------------------------------

    def test_valid_post_as_normal_user_shows_preview(self):
        self.login_user()

        response = self.client.post(
            self.url,
            self.valid_user_post_data(),
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "paas/deploy_app.html")

        self.assertEqual(
            response.context["app_selected"],
            self.app,
        )

        self.assertEqual(
            response.context["duration_selected"],
            '1h',
        )

        self.assertEqual(
            response.context["app_description"],
            self.app.description,
        )

        self.assertTrue(response.context["readonly"])

        self.assertEqual(
            response.context["images"],
            ["example/test-app:1.0.0"],
        )

        self.assertEqual(
            response.context["tor_auth_type"],
            self.app.tor_auth_type,
        )

    def test_valid_post_as_admin_shows_preview(self):
        self.login_admin()

        response = self.client.post(
            self.url,
            self.valid_admin_post_data(),
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "paas/deploy_app.html")

        self.assertEqual(
            response.context["app_selected"],
            self.app,
        )

        self.assertEqual(
            response.context["target_host_selected"],
            self.host,
        )

        self.assertEqual(
            response.context["images"],
            ["example/test-app:1.0.0"],
        )

    # ---------------------------------------------------------
    # POST: doIT -> tatsächliches Deployment
    # ---------------------------------------------------------

    @patch("paas.views._handle_deploy")
    def test_do_it_calls_handle_deploy(self, mock_handle_deploy):
        self.login_user()

        mock_handle_deploy.return_value = HttpResponse("Deployment-Test")

        post_data = self.valid_user_post_data()
        post_data["doIT"] = "1"

        response = self.client.post(
            self.url,
            post_data,
        )

        mock_handle_deploy.assert_called_once()

        args = mock_handle_deploy.call_args.args

        self.assertEqual(args[0].user, self.user)
        self.assertEqual(args[0].method, "POST")

        # Das bereits gebundene Formular wird weitergereicht.
        form = args[1]

        self.assertTrue(form.is_valid())
        self.assertEqual(form.cleaned_data["app"], self.app)

    @patch("paas.views._handle_deploy")
    def test_do_it_calls_handle_deploy_for_admin(self, mock_handle_deploy):
        self.login_admin()

        mock_handle_deploy.return_value = HttpResponse("Deployment-Test")

        post_data = self.valid_admin_post_data()
        post_data["doIT"] = "1"

        self.client.post(
            self.url,
            post_data,
        )

        mock_handle_deploy.assert_called_once()

        form = mock_handle_deploy.call_args.args[1]

        self.assertTrue(form.is_valid())
        self.assertEqual(form.cleaned_data["target_host"], self.host)
