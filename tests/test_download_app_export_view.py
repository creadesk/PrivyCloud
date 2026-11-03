'''
Superuser mit Session-Payload: Download erfolgreich, HTTP 200.
Dateiinhalt: Der JSON-Payload wird unverändert ausgeliefert.
Content-Type: application/json.
Content-Disposition: Standard-Dateiname app_export.json.
Eigener Dateiname: Der übergebene GET-Parameter filename wird verwendet.
Einmaliger Download: Der Payload wird nach dem Download aus der Session entfernt.
Kein Payload vorhanden: HTTP 404.
Normaler angemeldeter Benutzer: HTTP 302 (Weiterleitung zur Login-Seite aufgrund von user_passes_test).
Nicht angemeldeter Benutzer: HTTP 302 zur Login-Seite.
'''

import json
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from paas.views import sanitize_export_filename

class DownloadAppExportViewTests(TestCase):
    def setUp(self):
        self.url = reverse("paas_download_app_export")

        User = get_user_model()

        self.superuser = User.objects.create_superuser(
            username="admin",
            email="admin@example.com",
            password="testpassword123",
        )

        self.normal_user = User.objects.create_user(
            username="normal",
            email="normal@example.com",
            password="testpassword123",
        )

        self.payload = json.dumps(
            {
                "apps": [
                    {
                        "name": "uptime-kuma",
                        "display_name": "Uptime Kuma",
                        "docker_image": "louislam/uptime-kuma",
                        "tags": ["1.23.0"],
                    }
                ]
            },
            ensure_ascii=False,
        )

    def test_superuser_can_download_export(self):
        """Ein Superuser kann den Export herunterladen."""

        self.client.force_login(self.superuser)
        session = self.client.session
        session["app_export_payload"] = self.payload
        session.save()

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content.decode("utf-8"), self.payload)

    def test_response_has_correct_content_type(self):
        """Der Download wird als JSON ausgeliefert."""

        self.client.force_login(self.superuser)
        session = self.client.session
        session["app_export_payload"] = self.payload
        session.save()

        response = self.client.get(self.url)

        self.assertEqual(
            response["Content-Type"],
            "application/json",
        )

    def test_response_has_default_filename(self):
        """Ohne filename-Parameter wird app_export.json verwendet."""

        self.client.force_login(self.superuser)
        session = self.client.session
        session["app_export_payload"] = self.payload
        session.save()

        response = self.client.get(self.url)

        self.assertEqual(
            response["Content-Disposition"],
            'attachment; filename="app_export.json"',
        )

    def test_response_uses_custom_filename(self):
        """Ein übergebener Dateiname wird verwendet."""

        self.client.force_login(self.superuser)
        session = self.client.session
        session["app_export_payload"] = self.payload
        session.save()

        response = self.client.get(
            self.url,
            {"filename": "my_app_export.json"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response["Content-Disposition"],
            'attachment; filename="my_app_export.json"',
        )

    def test_payload_is_removed_from_session_after_download(self):
        """Der Payload kann nur einmal heruntergeladen werden."""

        self.client.force_login(self.superuser)
        session = self.client.session
        session["app_export_payload"] = self.payload
        session.save()

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)

        # Session erneut laden, damit Änderungen des Views sichtbar sind.
        session = self.client.session

        self.assertNotIn("app_export_payload", session)

    def test_second_download_returns_404(self):
        """Ein zweiter Download ohne neuen Payload liefert HTTP 404."""

        self.client.force_login(self.superuser)
        session = self.client.session
        session["app_export_payload"] = self.payload
        session.save()

        first_response = self.client.get(self.url)

        self.assertEqual(first_response.status_code, 200)

        second_response = self.client.get(self.url)

        self.assertEqual(second_response.status_code, 404)

    def test_missing_payload_returns_404(self):
        """Ohne Payload in der Session wird HTTP 404 zurückgegeben."""

        self.client.force_login(self.superuser)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 404)

    def test_normal_user_is_redirected(self):
        """Ein normaler Benutzer darf den Export nicht herunterladen."""

        self.client.force_login(self.normal_user)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 302)
        self.assertIn(
            reverse("login"),
            response.url,
        )

    def test_anonymous_user_is_redirected(self):
        """Nicht angemeldete Benutzer werden zum Login weitergeleitet."""

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 302)
        self.assertIn(
            reverse("login"),
            response.url,
        )




class ExportFilenameSanitizationTests(TestCase):

    def test_normal_filename(self):
        self.assertEqual(
            sanitize_export_filename("my_export.json"),
            "my_export.json",
        )

    def test_filename_without_extension(self):
        self.assertEqual(
            sanitize_export_filename("my_export"),
            "my_export.json",
        )

    def test_path_traversal_is_removed(self):
        self.assertEqual(
            sanitize_export_filename("../../etc/passwd"),
            "passwd.json",
        )

    def test_windows_path_is_removed(self):
        self.assertEqual(
            sanitize_export_filename(
                r"C:\Users\Admin\export.json"
            ),
            "export.json",
        )

    def test_special_characters_are_replaced(self):
        self.assertEqual(
            sanitize_export_filename(
                'my export"; test.json'
            ),
            "my_export___test.json",
        )

    def test_empty_filename_uses_default(self):
        self.assertEqual(
            sanitize_export_filename(""),
            "app_export.json",
        )

    def test_newline_is_removed(self):
        self.assertEqual(
            sanitize_export_filename(
                "export\r\nX-Injected: true.json"
            ),
            "export__X-Injected__true.json",
        )

    def test_filename_length_is_limited(self):
        filename = "a" * 200 + ".json"

        result = sanitize_export_filename(filename)

        self.assertLessEqual(len(result), 100)
        self.assertTrue(result.endswith(".json"))

    def test_double_extension_is_preserved(self):
        self.assertEqual(
            sanitize_export_filename("export.tar.gz"),
            "export.tar.gz.json",
        )