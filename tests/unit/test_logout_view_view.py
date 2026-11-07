'''
Ein Aufruf der View den Benutzer abmeldet.
Anschließend auf die URL login weitergeleitet wird.
Die Session nach dem Logout nicht mehr authentifiziert ist.
'''


from django.test import TestCase
from django.urls import reverse
from django.contrib.auth import get_user_model


User = get_user_model()


class LogoutViewTests(TestCase):

    def setUp(self):
        self.user = User.objects.create_user(
            username="testuser",
            password="TestPassword123!"
        )

        self.logout_url = reverse("logout")
        self.login_url = reverse("login")

    def test_logout_redirects_to_login(self):
        """
        Nach dem Logout wird auf die Login-Seite weitergeleitet.
        """
        self.client.force_login(self.user)

        response = self.client.get(self.logout_url)

        self.assertRedirects(response, self.login_url)

    def test_logout_user_is_no_longer_authenticated(self):
        """
        Der Benutzer ist nach dem Logout nicht mehr authentifiziert.
        """
        self.client.force_login(self.user)

        self.client.get(self.logout_url)

        response = self.client.get(self.logout_url)

        self.assertFalse(response.wsgi_request.user.is_authenticated)

    def test_logout_works_without_authenticated_user(self):
        """
        Auch ein nicht angemeldeter Benutzer kann die Logout-URL aufrufen.
        """
        response = self.client.get(self.logout_url)

        self.assertRedirects(response, self.login_url)
