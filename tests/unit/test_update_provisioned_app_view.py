'''
Testfall --> Erwartetes Verhalten
Nicht angemeldeter Benutzer --> Redirect zum Login
GET statt POST --> Redirect zu paas_my_apps
Fehlende provision_id --> Redirect, Task wird nicht ausgeführt
Ungültige provision_id --> Redirect, Task wird nicht ausgeführt
Fehlendes new_image --> Redirect, Task wird nicht ausgeführt
Image ohne Tag --> Redirect, Task wird nicht ausgeführt
Unbekanntes Docker-Image --> Redirect, Task wird nicht ausgeführt
Nicht vorhandenes Image-Tag --> Redirect, Task wird nicht ausgeführt
Provision existiert nicht --> 404, Task wird nicht ausgeführt
Provision gehört anderem Benutzer --> 404, Task wird nicht ausgeführt
Gültige Anfrage --> Task wird genau einmal mit korrekten Parametern aufgerufen
Superuser --> Darf ebenfalls aktualisieren
'''


from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth import get_user_model
from unittest.mock import patch

from paas.models import (
    AppDefinition,
    AppImageTag,
    ProvisionedApp,
    RemoteHost,
)


User = get_user_model()


class UpdateProvisionedAppViewTests(TestCase):

    def setUp(self):
        self.client = Client()

        # Benutzer
        self.user = User.objects.create_user(
            username="testuser",
            password="testpassword123",
        )

        self.other_user = User.objects.create_user(
            username="otheruser",
            password="testpassword123",
        )

        # Superuser
        self.superuser = User.objects.create_superuser(
            username="admin",
            email="admin@example.com",
            password="adminpassword123",
        )

        # App-Definition
        self.app_definition = AppDefinition.objects.create(
            name="test-app",
            display_name="Test App",
            docker_image="example/test-app",
        )

        # Gültige Image-Tags
        self.image_tag = AppImageTag.objects.create(
            app_definition=self.app_definition,
            tag="1.0.0",
        )

        self.image_tag_latest = AppImageTag.objects.create(
            app_definition=self.app_definition,
            tag="latest",
        )

        # Remote Host
        self.host = RemoteHost.objects.create(
            hostname="test-host",
            ip_address="127.0.0.1",
        )

        # Provision für den Testbenutzer
        self.provision = ProvisionedApp.objects.create(
            user=self.user,
            app=self.app_definition,
            host=self.host,
            container_id="container123",
            container_name="test-container",
            status="running",
            image="example/test-app:1.0.0",
        )

        self.url = reverse("update_provisioned_app")

    # ---------------------------------------------------------
    # Hilfsmethode
    # ---------------------------------------------------------

    def post_update(self, data=None):
        if data is None:
            data = {
                "provision_id": self.provision.pk,
                "new_image": "example/test-app:1.0.0",
            }

        return self.client.post(self.url, data)

    # ---------------------------------------------------------
    # Authentifizierung
    # ---------------------------------------------------------

    def test_anonymous_user_is_redirected_to_login(self):
        """
        Nicht angemeldete Benutzer dürfen die View nicht aufrufen.
        """
        response = self.post_update()

        self.assertEqual(response.status_code, 302)
        self.assertIn(
            reverse("login"),
            response.url,
        )

        # Ohne Anmeldung darf keine Aktualisierung erfolgen.

    # ---------------------------------------------------------
    # HTTP-Methode
    # ---------------------------------------------------------

    def test_get_request_redirects_to_my_apps(self):
        """
        Die View akzeptiert ausschließlich POST.
        """
        self.client.force_login(self.user)

        response = self.client.get(self.url)

        self.assertRedirects(
            response,
            reverse("paas_my_apps"),
            fetch_redirect_response=False,
        )

    # ---------------------------------------------------------
    # Eingabevalidierung: Provision-ID
    # ---------------------------------------------------------

    @patch("paas.views.update_app_task")
    def test_missing_provision_id(self, mock_update_task):
        self.client.force_login(self.user)

        response = self.post_update({
            "new_image": "example/test-app:1.0.0",
        })

        self.assertRedirects(
            response,
            reverse("paas_my_apps"),
            fetch_redirect_response=False,
        )

        mock_update_task.assert_not_called()

    @patch("paas.views.update_app_task")
    def test_invalid_provision_id(self, mock_update_task):
        self.client.force_login(self.user)

        response = self.post_update({
            "provision_id": "abc",
            "new_image": "example/test-app:1.0.0",
        })

        self.assertRedirects(
            response,
            reverse("paas_my_apps"),
            fetch_redirect_response=False,
        )

        mock_update_task.assert_not_called()

    # ---------------------------------------------------------
    # Eingabevalidierung: Docker-Image
    # ---------------------------------------------------------

    @patch("paas.views.update_app_task")
    def test_missing_new_image(self, mock_update_task):
        self.client.force_login(self.user)

        response = self.post_update({
            "provision_id": self.provision.pk,
        })

        self.assertRedirects(
            response,
            reverse("paas_my_apps"),
            fetch_redirect_response=False,
        )

        mock_update_task.assert_not_called()

    @patch("paas.views.update_app_task")
    def test_empty_new_image(self, mock_update_task):
        self.client.force_login(self.user)

        response = self.post_update({
            "provision_id": self.provision.pk,
            "new_image": "",
        })

        self.assertRedirects(
            response,
            reverse("paas_my_apps"),
            fetch_redirect_response=False,
        )

        mock_update_task.assert_not_called()

    @patch("paas.views.update_app_task")
    def test_image_without_tag(self, mock_update_task):
        """
        Das Image muss das Format image:tag besitzen.
        """
        self.client.force_login(self.user)

        response = self.post_update({
            "provision_id": self.provision.pk,
            "new_image": "example/test-app",
        })

        self.assertRedirects(
            response,
            reverse("paas_my_apps"),
            fetch_redirect_response=False,
        )

        mock_update_task.assert_not_called()

    @patch("paas.views.update_app_task")
    def test_unknown_docker_image(self, mock_update_task):
        """
        Das Image muss in AppDefinition existieren.
        """
        self.client.force_login(self.user)

        response = self.post_update({
            "provision_id": self.provision.pk,
            "new_image": "unknown/image:1.0",
        })

        self.assertRedirects(
            response,
            reverse("paas_my_apps"),
            fetch_redirect_response=False,
        )

        mock_update_task.assert_not_called()

    @patch("paas.views.update_app_task")
    def test_unknown_image_tag(self, mock_update_task):
        """
        Ein vorhandenes Image mit einem nicht existierenden Tag
        darf nicht aktualisiert werden.
        """
        self.client.force_login(self.user)

        response = self.post_update({
            "provision_id": self.provision.pk,
            "new_image": "example/test-app:999.999",
        })

        self.assertRedirects(
            response,
            reverse("paas_my_apps"),
            fetch_redirect_response=False,
        )

        mock_update_task.assert_not_called()

    # ---------------------------------------------------------
    # Eigentümerprüfung
    # ---------------------------------------------------------

    @patch("paas.views.update_app_task")
    def test_nonexistent_provision_returns_404(self, mock_update_task):
        """
        Eine nicht existierende Provision darf nicht aktualisiert werden.
        """
        self.client.force_login(self.user)

        response = self.post_update({
            "provision_id": 999999,
            "new_image": "example/test-app:1.0.0",
        })

        self.assertEqual(response.status_code, 404)
        mock_update_task.assert_not_called()

    @patch("paas.views.update_app_task")
    def test_other_users_provision_returns_404(self, mock_update_task):
        """
        Benutzer dürfen nicht die Provision eines anderen Benutzers
        aktualisieren.
        """
        self.client.force_login(self.other_user)

        response = self.post_update({
            "provision_id": self.provision.pk,
            "new_image": "example/test-app:1.0.0",
        })

        self.assertEqual(response.status_code, 404)
        mock_update_task.assert_not_called()

    # ---------------------------------------------------------
    # Erfolgreiche Aktualisierung
    # ---------------------------------------------------------

    @patch("paas.views.update_app_task")
    def test_successful_update(self, mock_update_task):
        """
        Eine gültige Anfrage muss die Update-Task genau einmal
        mit der richtigen Provision-ID und dem neuen Image aufrufen.
        """
        self.client.force_login(self.user)

        new_image = "example/test-app:1.0.0"

        response = self.post_update({
            "provision_id": self.provision.pk,
            "new_image": new_image,
        })

        self.assertRedirects(
            response,
            reverse("paas_my_apps"),
            fetch_redirect_response=False,
        )

        mock_update_task.assert_called_once_with(
            self.provision.pk,
            new_image,
        )

    @patch("paas.views.update_app_task")
    def test_successful_update_with_latest_tag(self, mock_update_task):
        """
        Auch ein gültiger Tag wie 'latest' muss akzeptiert werden.
        """
        self.client.force_login(self.user)

        new_image = "example/test-app:latest"

        response = self.post_update({
            "provision_id": self.provision.pk,
            "new_image": new_image,
        })

        self.assertEqual(response.status_code, 302)

        mock_update_task.assert_called_once_with(
            self.provision.pk,
            new_image,
        )

    # ---------------------------------------------------------
    # Superuser
    # ---------------------------------------------------------

    @patch("paas.views.update_app_task")
    def test_superuser_can_update_provision(self, mock_update_task):
        """
        Auch ein Superuser darf eine Provision aktualisieren,
        sofern die Provision ihm selbst gehört.
        """
        admin_provision = ProvisionedApp.objects.create(
            user=self.superuser,
            app=self.app_definition,
            host=self.host,
            container_id="admin-container",
            container_name="admin-test-container",
            status="running",
        )

        self.client.force_login(self.superuser)

        new_image = "example/test-app:1.0.0"

        response = self.post_update({
            "provision_id": admin_provision.pk,
            "new_image": new_image,
        })

        self.assertEqual(response.status_code, 302)

        mock_update_task.assert_called_once_with(
            admin_provision.pk,
            new_image,
        )
