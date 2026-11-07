'''
Test --> Erwartung
Nicht angemeldet --> Redirect zum Login
Normaler Benutzer --> HTTP 403
Superuser --> HTTP 200
Template --> paas/images.html
Mehrere Hosts --> Alle Hosts vorhanden
Docker-Abfrage --> Ein Aufruf je Host
Image-Daten --> Korrekt im Context
Leere Image-Liste --> Kein Fehler
Host ohne Images --> Andere Hosts bleiben erhalten
Keine Hosts --> Leere Liste, HTTP 200
'''

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from paas.models import RemoteHost


User = get_user_model()


class ImagesViewTest(TestCase):
    """
    Tests für die View paas.views.images.

    Ausführung:
        python manage.py test tests.test_images_view
    """

    @classmethod
    def setUpTestData(cls):
        cls.superuser = User.objects.create_superuser(
            username="admin",
            email="admin@example.com",
            password="test-password",
        )

        cls.normal_user = User.objects.create_user(
            username="user",
            email="user@example.com",
            password="test-password",
        )

        cls.host1 = RemoteHost.objects.create(
            hostname="host1.example.com",
            ip_address="192.168.1.10",
        )

        cls.host2 = RemoteHost.objects.create(
            hostname="host2.example.com",
            ip_address="192.168.1.11",
        )

    def setUp(self):
        self.url = reverse("paas_images")

    # ---------------------------------------------------------
    # Login / Berechtigungen
    # ---------------------------------------------------------

    def test_anonymous_user_is_redirected_to_login(self):
        """Nicht angemeldete Benutzer werden zum Login weitergeleitet."""

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 302)
        self.assertIn("/user-login/", response.url)

    @patch.object(RemoteHost, "docker_images")
    def test_normal_user_gets_forbidden(self, mock_docker_images):
        """Normale Benutzer dürfen die Image-Übersicht nicht aufrufen."""

        self.client.force_login(self.normal_user)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.content.decode(), "Keine Berechtigung.")

        # Es dürfen keine Docker-Abfragen stattfinden.
        mock_docker_images.assert_not_called()

    # ---------------------------------------------------------
    # Erfolgreicher Aufruf als Superuser
    # ---------------------------------------------------------

    @patch.object(RemoteHost, "docker_images")
    def test_superuser_can_access_images_view(self, mock_docker_images):
        """Superuser erhalten die Image-Übersicht."""

        mock_docker_images.return_value = []

        self.client.force_login(self.superuser)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "paas/images.html")

    @patch.object(RemoteHost, "docker_images")
    def test_all_hosts_are_included(self, mock_docker_images):
        """Alle vorhandenen Hosts werden in der Ausgabe berücksichtigt."""

        mock_docker_images.return_value = []

        self.client.force_login(self.superuser)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)

        images = response.context["images"]

        self.assertEqual(len(images), 2)

        returned_hosts = [entry["host"] for entry in images]

        self.assertIn(self.host1, returned_hosts)
        self.assertIn(self.host2, returned_hosts)

    @patch.object(RemoteHost, "docker_images")
    def test_docker_images_is_called_for_every_host(self, mock_docker_images):
        """Für jeden Host wird docker_images() genau einmal aufgerufen."""

        mock_docker_images.return_value = []

        self.client.force_login(self.superuser)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)

        self.assertEqual(mock_docker_images.call_count, 2)

    # ---------------------------------------------------------
    # Rückgabe der Images
    # ---------------------------------------------------------

    @patch.object(RemoteHost, "docker_images", autospec=True)
    def test_images_are_passed_to_template(self, mock_docker_images):
        """Die von docker_images() gelieferten Daten kommen im Context an."""

        host1_images = [
            {
                "repository": "nginx",
                "tag": "latest",
                "image_id": "abc123",
                "size": "150MB",
                "used": True,
            },
            {
                "repository": "redis",
                "tag": "7",
                "image_id": "def456",
                "size": "120MB",
                "used": False,
            },
        ]

        host2_images = [
            {
                "repository": "postgres",
                "tag": "16",
                "image_id": "ghi789",
                "size": "300MB",
                "used": True,
            },
        ]

        def docker_images_side_effect(host):
            if host.pk == self.host1.pk:
                return host1_images
            if host.pk == self.host2.pk:
                return host2_images
            return []

        mock_docker_images.side_effect = docker_images_side_effect

        self.client.force_login(self.superuser)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)

        images = response.context["images"]

        host1_entry = next(
            entry for entry in images
            if entry["host"] == self.host1
        )

        host2_entry = next(
            entry for entry in images
            if entry["host"] == self.host2
        )

        self.assertEqual(host1_entry["images"], host1_images)
        self.assertEqual(host2_entry["images"], host2_images)

    # ---------------------------------------------------------
    # Fehlerbehandlung
    # ---------------------------------------------------------

    @patch.object(RemoteHost, "docker_images")
    def test_empty_image_list_is_supported(self, mock_docker_images):
        """Hosts ohne Images werden korrekt dargestellt."""

        mock_docker_images.return_value = []

        self.client.force_login(self.superuser)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)

        for entry in response.context["images"]:
            self.assertEqual(entry["images"], [])

    @patch.object(RemoteHost, "docker_images", autospec=True)
    def test_one_host_failure_does_not_affect_other_hosts(
        self,
        mock_docker_images,
    ):
        """
        Wenn ein Host keine Images liefert, bleiben die anderen
        Host-Ergebnisse erhalten.
        """

        host1_images = [
            {
                "repository": "nginx",
                "tag": "latest",
                "image_id": "abc123",
                "size": "150MB",
                "used": True,
            }
        ]

        def docker_images_side_effect(host):
            if host.pk == self.host1.pk:
                return host1_images
            return []

        mock_docker_images.side_effect = docker_images_side_effect

        self.client.force_login(self.superuser)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)

        images = response.context["images"]

        host1_entry = next(
            entry for entry in images
            if entry["host"] == self.host1
        )

        host2_entry = next(
            entry for entry in images
            if entry["host"] == self.host2
        )

        self.assertEqual(host1_entry["images"], host1_images)
        self.assertEqual(host2_entry["images"], [])

    # ---------------------------------------------------------
    # Keine Hosts vorhanden
    # ---------------------------------------------------------

    @patch.object(RemoteHost, "docker_images")
    def test_view_works_without_hosts(self, mock_docker_images):
        """Auch ohne konfigurierte Hosts funktioniert die View."""

        RemoteHost.objects.all().delete()

        self.client.force_login(self.superuser)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["images"], [])

        mock_docker_images.assert_not_called()
