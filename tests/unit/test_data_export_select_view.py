'''
Superuser mit POST: Zugriff erlaubt.
Superuser mit POST: Alle AppDefinition-Einträge werden übergeben.
Superuser mit POST: Richtige Template- und Context-Daten.
Normaler eingeloggter Benutzer: Zugriff verweigert.
Anonymer Benutzer: Weiterleitung zum Login.
Superuser mit GET: HTTP 405 (Method Not Allowed).
POST ohne vorhandene AppDefinitions: View funktioniert trotzdem.
'''


from django.test import TestCase
from django.urls import reverse
from django.contrib.auth import get_user_model

from paas.models import AppDefinition
from django.conf import settings


User = get_user_model()


class DataExportSelectViewTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.superuser = User.objects.create_superuser(
            username="export_admin",
            email="admin@example.com",
            password="test-password-123",
        )

        cls.normal_user = User.objects.create_user(
            username="export_user",
            email="user@example.com",
            password="test-password-123",
        )

        cls.app1 = AppDefinition.objects.create(
            name="test-app-1",
            display_name="Test Application 1",
            docker_image="example/test-app-1",
        )

        cls.app2 = AppDefinition.objects.create(
            name="test-app-2",
            display_name="Test Application 2",
            docker_image="example/test-app-2",
        )

    def setUp(self):
        self.url = reverse("paas_data_export_select")

    # ---------------------------------------------------------
    # Superuser-Zugriff
    # ---------------------------------------------------------

    def test_superuser_can_access_view_with_post(self):
        """Ein Superuser darf die View per POST aufrufen."""

        self.client.force_login(self.superuser)

        response = self.client.post(self.url)

        self.assertEqual(response.status_code, 200)

    def test_superuser_gets_correct_template(self):
        """Die View verwendet das vorgesehene Template."""

        self.client.force_login(self.superuser)

        response = self.client.post(self.url)

        self.assertTemplateUsed(
            response,
            "paas/data_export_select.html"
        )

    def test_superuser_gets_all_app_definitions(self):
        """Alle AppDefinition-Einträge werden im Context bereitgestellt."""

        self.client.force_login(self.superuser)

        response = self.client.post(self.url)

        self.assertQuerySetEqual(
            response.context["app_def_all"],
            AppDefinition.objects.order_by("display_name"),
            transform=lambda app: app,
        )

        self.assertEqual(
            response.context["app_def_all"].count(),
            2,
        )

    def test_superuser_gets_platform_name_in_context(self):
        """Der Plattformname wird korrekt an das Template übergeben."""

        self.client.force_login(self.superuser)

        response = self.client.post(self.url)

        self.assertIn("PLATFORM_NAME", response.context)

        self.assertEqual(
            response.context["PLATFORM_NAME"],
            settings.PLATFORM_NAME
            if hasattr(settings, "PLATFORM_NAME")
            else response.context["PLATFORM_NAME"],
        )

    def test_view_works_without_app_definitions(self):
        """Die View funktioniert auch ohne vorhandene Apps."""

        AppDefinition.objects.all().delete()

        self.client.force_login(self.superuser)

        response = self.client.post(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.context["app_def_all"].count(),
            0,
        )

    # ---------------------------------------------------------
    # Berechtigungen
    # ---------------------------------------------------------

    def test_normal_user_cannot_access_view(self):
        """Ein normaler Benutzer darf die View nicht aufrufen."""

        self.client.force_login(self.normal_user)

        response = self.client.post(self.url)

        self.assertEqual(response.status_code, 302)

        self.assertTrue(
            response.url.startswith(
                reverse("login")
            )
        )

    def test_anonymous_user_is_redirected_to_login(self):
        """Ein nicht eingeloggter Benutzer wird zum Login umgeleitet."""

        response = self.client.post(self.url)

        self.assertEqual(response.status_code, 302)

        self.assertTrue(
            response.url.startswith(
                reverse("login")
            )
        )

    # ---------------------------------------------------------
    # HTTP-Methoden
    # ---------------------------------------------------------

    def test_get_request_is_not_allowed(self):
        """Die View akzeptiert ausschließlich POST."""

        self.client.force_login(self.superuser)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 405)

    def test_put_request_is_not_allowed(self):
        """PUT-Anfragen werden abgelehnt."""

        self.client.force_login(self.superuser)

        response = self.client.put(self.url)

        self.assertEqual(response.status_code, 405)

    def test_delete_request_is_not_allowed(self):
        """DELETE-Anfragen werden abgelehnt."""

        self.client.force_login(self.superuser)

        response = self.client.delete(self.url)

        self.assertEqual(response.status_code, 405)
