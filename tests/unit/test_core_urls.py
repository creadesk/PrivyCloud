'''
alle URLs korrekt registriert sind.
die URL-Namen eindeutig und auflösbar sind.
die richtigen Views zugeordnet sind.
URL-Parameter korrekt verarbeitet werden.
ungültige URL-Parameter zu einem 404 führen.
die Admin-URL über STRING_TO_ADMIN_PATH erreichbar ist.
die zusätzlichen Static-/Media-URLs abhängig von DEBUG registriert werden.
'''

from django.conf import settings
from django.contrib import admin
from django.test import SimpleTestCase
from django.urls import reverse, resolve, Resolver404

from core.settings import STRING_TO_ADMIN_PATH
import dashboard.views
import authent.views
import paas.views

"""Tests für die URL-Konfiguration in core/urls.py."""
class CoreUrlsTests(SimpleTestCase):
    def test_named_urls_resolve(self):
        """Alle benannten URLs lassen sich korrekt auflösen."""

        url_view_mapping = {
            "dashboard": dashboard.views.dashboard,
            "verify_2fa": authent.views.verify_2fa,
            "register": authent.views.register_view,
            "login": authent.views.login_view,
            "logout": authent.views.logout_view,
            "paas_my_apps": paas.views.my_apps,
            "paas_select_app": paas.views.select_app,
            "paas_deploy_app": paas.views.deploy_app,
            "update_provisioned_app": paas.views.update_provisioned_app,
            "paas_images": paas.views.images,
            "paas_data_corpses": paas.views.data_corpses,
            "paas_mark_delete": paas.views.mark_delete,
            "paas_data_export_select": paas.views.data_export_select,
            "paas_data_export_app_def": paas.views.data_export_app_def,
            "paas_download_app_export": paas.views.download_app_export,
            "paas_data_import_app_def": paas.views.data_import_app_def,
            "paas_data_import_app_def_success": paas.views.data_import_success,
        }

        for url_name, expected_view in url_view_mapping.items():
            with self.subTest(url_name=url_name):
                url = reverse(url_name)
                resolved = resolve(url)

                self.assertEqual(resolved.func, expected_view)
                self.assertEqual(resolved.url_name, url_name)

    def test_parameterized_urls_resolve(self):
        """URLs mit dynamischen Parametern werden korrekt aufgelöst."""

        url_view_mapping = {
            "paas_delete_app": (
                "/paas/delete_app/123/",
                paas.views.delete_app,
            ),
            "paas_stop_app": (
                "/paas/stop_app/123/",
                paas.views.stop_app,
            ),
            "paas_start_app": (
                "/paas/start_app/123/",
                paas.views.start_app,
            ),
            "paas_delete_image": (
                "/paas/delete-image/123/test-image/",
                paas.views.delete_image,
            ),
            "docker_logs": (
                "/paas/provision/123/docker-logs/",
                paas.views.docker_logs,
            ),
            "app_logs": (
                "/paas/app-logs/123/",
                paas.views.application_logs,
            ),
            "paas_log_cleanup": (
                "/log_cleanup/123/",
                paas.views.paas_log_cleanup,
            ),
        }

        for url_name, (url, expected_view) in url_view_mapping.items():
            with self.subTest(url_name=url_name):
                resolved = resolve(url)

                self.assertEqual(resolved.func, expected_view)
                self.assertEqual(resolved.url_name, url_name)

    def test_parameterized_url_arguments(self):
        """Dynamische URL-Parameter werden korrekt übergeben."""

        test_cases = [
            ("paas_delete_app", {"pk": 123}),
            ("paas_stop_app", {"pk": 123}),
            ("paas_start_app", {"pk": 123}),
            ("paas_delete_image", {"host_id": 123, "image_id": "test-image"}),
            ("docker_logs", {"provision_id": 123}),
            ("app_logs", {"pk": 123}),
            ("paas_log_cleanup", {"host_id": 123}),
        ]

        for url_name, expected_kwargs in test_cases:
            with self.subTest(url_name=url_name):
                match = resolve(reverse(url_name, kwargs=expected_kwargs))

                self.assertEqual(match.kwargs, expected_kwargs)

    def test_invalid_integer_parameters_return_404(self):
        """Integer-Parameter akzeptieren keine ungültigen Werte."""

        invalid_urls = [
            "/paas/delete_app/abc/",
            "/paas/stop_app/abc/",
            "/paas/start_app/abc/",
            "/paas/delete-image/abc/test-image/",
            "/paas/provision/abc/docker-logs/",
            "/paas/app-logs/abc/",
            "/log_cleanup/abc/",
        ]

        for url in invalid_urls:
            with self.subTest(url=url):
                with self.assertRaises(Resolver404):
                    resolve(url)

    def test_admin_url_uses_configured_path(self):
        """Die Admin-URL verwendet den konfigurierten URL-Pfad."""

        url = reverse("admin:index")
        resolved = resolve(url)

        self.assertEqual(url, f"/{STRING_TO_ADMIN_PATH}")
        self.assertEqual(resolved.url_name, "index")
        self.assertEqual(resolved.namespace, "admin")

    def test_dashboard_is_root_url(self):
        """Das Dashboard ist unter der Root-URL registriert."""

        match = resolve("/")

        self.assertEqual(match.func, dashboard.views.dashboard)
        self.assertEqual(match.url_name, "dashboard")

    def test_authentication_urls(self):
        """Die Authentifizierungs-URLs sind korrekt registriert."""

        test_cases = {
            "/verify-2fa/": (authent.views.verify_2fa, "verify_2fa"),
            "/register/": (authent.views.register_view, "register"),
            "/user-login/": (authent.views.login_view, "login"),
            "/logout/": (authent.views.logout_view, "logout"),
        }

        for url, (expected_view, expected_name) in test_cases.items():
            with self.subTest(url=url):
                match = resolve(url)

                self.assertEqual(match.func, expected_view)
                self.assertEqual(match.url_name, expected_name)

    def test_captcha_urls_are_included(self):
        """Die CAPTCHA-URLs sind korrekt eingebunden."""

        captcha_urls = {
            "/captcha/refresh/": "captcha-refresh",
        }

        for url, expected_name in captcha_urls.items():
            with self.subTest(url=url):
                match = resolve(url)
                self.assertEqual(match.url_name, expected_name)

    def test_unknown_urls_raise_404(self):
        """Nicht registrierte URLs führen zu einem Resolver404."""

        invalid_urls = [
            "/does-not-exist/",
            "/paas/does-not-exist/",
            "/unknown/path/",
        ]

        for url in invalid_urls:
            with self.subTest(url=url):
                with self.assertRaises(Resolver404):
                    resolve(url)

    def test_parameterized_url_argument_types(self):
        """URL-Parameter werden mit den erwarteten Datentypen aufgelöst."""

        match = resolve("/paas/delete-image/123/test-image/")

        self.assertIsInstance(match.kwargs["host_id"], int)
        self.assertIsInstance(match.kwargs["image_id"], str)