'''
Nicht angemeldeter Benutzer: Wird auf die Login-Seite weitergeleitet?
Angemeldeter Benutzer: Wird das Dashboard mit HTTP 200 ausgeliefert?
Template: Wird dashboard/index.html verwendet?
Context: Werden PLATFORM_NAME und STRING_TO_ADMIN_PATH korrekt übergeben?
HTTP-Methode: Funktioniert der Aufruf per GET?
'''

from django.test import TestCase
from django.urls import reverse
from django.contrib.auth import get_user_model

from dashboard import views


User = get_user_model()


class DashboardViewTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="testuser",
            password="testpassword123",
        )
        self.url = reverse("dashboard")

    def test_dashboard_redirects_anonymous_user_to_login(self):
        """
        Nicht angemeldete Benutzer müssen zum Login weitergeleitet werden.
        """
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 302)
        self.assertRedirects(
            response,
            f"{reverse('login')}?next={self.url}",
            fetch_redirect_response=False,
        )

    def test_dashboard_returns_200_for_authenticated_user(self):
        """
        Angemeldete Benutzer dürfen das Dashboard aufrufen.
        """
        self.client.force_login(self.user)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)

    def test_dashboard_uses_correct_template(self):
        """
        Das Dashboard muss das erwartete Template verwenden.
        """
        self.client.force_login(self.user)

        response = self.client.get(self.url)

        self.assertTemplateUsed(response, "dashboard/index.html")

    def test_dashboard_contains_expected_context(self):
        """
        Die erwarteten Context-Variablen müssen vorhanden sein.
        """
        self.client.force_login(self.user)

        response = self.client.get(self.url)

        self.assertEqual(
            response.context["PLATFORM_NAME"],
            views.PLATFORM_NAME,
        )
        self.assertEqual(
            response.context["STRING_TO_ADMIN_PATH"],
            views.STRING_TO_ADMIN_PATH,
        )
