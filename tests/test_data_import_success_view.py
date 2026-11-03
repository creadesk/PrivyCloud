'''
Superuser: Darf die Seite aufrufen (HTTP 200).
Normaler eingeloggter Benutzer: Wird abgewiesen und zur Login-Seite bzw. gemäß Django-Konfiguration weitergeleitet.
Nicht eingeloggter Benutzer: Wird zur Login-Seite weitergeleitet.
Template: Es wird paas/data_import_success.html verwendet.
Context: PLATFORM_NAME wird korrekt an das Template übergeben.
'''


from django.test import TestCase
from django.urls import reverse
from django.contrib.auth import get_user_model

from paas.views import PLATFORM_NAME


User = get_user_model()


class DataImportSuccessViewTests(TestCase):
    """
    Tests für die View data_import_success.
    """

    def setUp(self):
        self.url = reverse('paas_data_import_app_def_success')

        self.superuser = User.objects.create_superuser(
            username='test_superuser',
            email='superuser@example.com',
            password='testpassword123',
        )

        self.normal_user = User.objects.create_user(
            username='test_user',
            email='user@example.com',
            password='testpassword123',
        )

    def test_superuser_can_access_view(self):
        """
        Ein angemeldeter Superuser darf die Seite aufrufen.
        """
        self.client.force_login(self.superuser)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)

    def test_normal_user_cannot_access_view(self):
        """
        Ein normaler Benutzer darf die Seite nicht aufrufen.
        """
        self.client.force_login(self.normal_user)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 302)

        # Weiterleitung zur Login-Seite
        self.assertRedirects(
            response,
            f"{reverse('login')}?next={self.url}",
            fetch_redirect_response=False,
        )

    def test_anonymous_user_is_redirected_to_login(self):
        """
        Ein nicht angemeldeter Benutzer wird zur Login-Seite weitergeleitet.
        """
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 302)

        self.assertRedirects(
            response,
            f"{reverse('login')}?next={self.url}",
            fetch_redirect_response=False,
        )

    def test_correct_template_is_used(self):
        """
        Die erwartete HTML-Template-Datei wird verwendet.
        """
        self.client.force_login(self.superuser)

        response = self.client.get(self.url)

        self.assertTemplateUsed(
            response,
            'paas/data_import_success.html'
        )

    def test_platform_name_is_available_in_template_context(self):
        """
        PLATFORM_NAME wird korrekt an das Template übergeben.
        """
        self.client.force_login(self.superuser)

        response = self.client.get(self.url)

        self.assertEqual(response.context['PLATFORM_NAME'], PLATFORM_NAME)
