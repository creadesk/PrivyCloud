'''
1. Zugriffsschutz
    Nicht angemeldeter Benutzer wird zum Login weitergeleitet.
    Normaler angemeldeter Benutzer erhält keinen Zugriff.
    Superuser darf die View per POST aufrufen.
    GET wird nicht akzeptiert.

2. Fehlerbehandlung
    Keine übermittelten App-IDs.
    Ungültige App-IDs.
    Bei Fehlern wird kein Export in der Session gespeichert.

3. Erfolgreicher Export
    Alle ausgewählten Apps werden exportiert.
    Alle relevanten App-Felder werden berücksichtigt.
    Image-Tags, Environment-Variablen, Volumes und Config-Patches werden exportiert.
    Umlaute und UTF-8-Zeichen bleiben erhalten.
    Primärschlüssel und interne Datenbank-IDs werden nicht exportiert.
    Nicht ausgewählte Apps erscheinen nicht im Export.
    Der JSON-String wird korrekt in der Session gespeichert.
'''

import json

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from paas.models import (
    AppDefinition,
    AppImageTag,
    AppEnvVarPerApp,
    AppVolumePerApp,
    ConfigPatch,
)


User = get_user_model()


class DataExportAppDefViewTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        # Benutzer
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

        # Erste App mit möglichst vielen unterschiedlichen Daten
        cls.app1 = AppDefinition.objects.create(
            name="test-app",
            display_name="Test App äöü",
            docker_image="docker.io/example/test-app",
            command="python manage.py runserver",
            description="Eine Beschreibung mit Umlauten: äöü ß",
            default_duration=12,
            app_port_intern_web=8080,
            app_port_intern_api=8081,
            app_port_intern_api_2=8082,
            hiddenservice_port_web=80,
            hiddenservice_port_api=81,
            hiddenservice_port_api_2=82,
            use_deploy_user=True,
            no_hidden_service=False,
            use_hostpid_namespace=True,
            super_user_only=True,
            tor_auth_type="cert",
        )

        # Zweite App zum Testen der Mehrfachauswahl
        cls.app2 = AppDefinition.objects.create(
            name="second-app",
            display_name="Second App",
            docker_image="docker.io/example/second-app",
            command="",
            description="Zweite App",
        )

        # Abhängige Datensätze für App 1
        cls.image_tag1 = AppImageTag.objects.create(
            app_definition=cls.app1,
            tag="1.0.0",
        )

        cls.image_tag2 = AppImageTag.objects.create(
            app_definition=cls.app1,
            tag="latest",
        )

        cls.env_var1 = AppEnvVarPerApp.objects.create(
            app=cls.app1,
            key="DATABASE_URL",
            value="postgres://localhost/test",
            optional=True,
            editable=True,
        )

        cls.env_var2 = AppEnvVarPerApp.objects.create(
            app=cls.app1,
            key="DEBUG",
            value="false",
            optional=False,
            editable=False,
        )

        cls.volume1 = AppVolumePerApp.objects.create(
            app=cls.app1,
            host_path="/srv/test/data",
            container_path="/app/data",
        )

        cls.patch1 = ConfigPatch.objects.create(
            app=cls.app1,
            target_file="config.ini",
            pattern="^debug=",
            action=ConfigPatch.ACTION_REPLACE,
            replacement="debug=false",
        )

        # Abhängigkeit für App 2
        cls.image_tag3 = AppImageTag.objects.create(
            app_definition=cls.app2,
            tag="2.0.0",
        )

    def setUp(self):
        self.url = reverse("paas_data_export_app_def")

        self.client.force_login(self.superuser)

    # ---------------------------------------------------------
    # Zugriffsschutz
    # ---------------------------------------------------------

    def test_anonymous_user_is_redirected_to_login(self):
        """Nicht angemeldete Benutzer dürfen die View nicht aufrufen."""

        self.client.logout()

        response = self.client.post(
            self.url,
            {"app_ids": str(self.app1.pk)},
        )

        self.assertEqual(response.status_code, 302)
        self.assertIn(
            reverse("login"),
            response.url,
        )

    def test_normal_user_is_not_allowed(self):
        """Normale Benutzer dürfen keinen Export durchführen."""

        self.client.force_login(self.normal_user)

        response = self.client.post(
            self.url,
            {"app_ids": str(self.app1.pk)},
        )

        self.assertEqual(response.status_code, 302)

        self.assertNotIn(
            "app_export_payload",
            self.client.session,
        )

    def test_get_request_is_not_allowed(self):
        """Die View akzeptiert ausschließlich POST."""

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 405)

    # ---------------------------------------------------------
    # Fehlerbehandlung
    # ---------------------------------------------------------

    def test_empty_app_ids_show_selection_page(self):
        """Ohne App-Auswahl wird die Auswahlseite angezeigt."""

        response = self.client.post(self.url, {})

        self.assertEqual(response.status_code, 200)

        self.assertTemplateUsed(
            response,
            "paas/data_export_select.html",
        )

        self.assertNotIn(
            "app_export_payload",
            self.client.session,
        )

        self.assertContains(
            response,
            "Bitte wählen Sie mindestens eine App aus.",
        )

    def test_invalid_app_ids_show_selection_page(self):
        """Ungültige IDs führen zu einer Fehlermeldung."""

        response = self.client.post(
            self.url,
            {"app_ids": "99999999"},
        )

        self.assertEqual(response.status_code, 200)

        self.assertTemplateUsed(
            response,
            "paas/data_export_select.html",
        )

        self.assertNotIn(
            "app_export_payload",
            self.client.session,
        )

        self.assertContains(
            response,
            "Keine gültigen App",
        )

    # ---------------------------------------------------------
    # Erfolgreicher Export
    # ---------------------------------------------------------

    def test_export_single_app(self):
        """Eine ausgewählte App wird vollständig exportiert."""

        response = self.client.post(
            self.url,
            {"app_ids": str(self.app1.pk)},
        )

        self.assertEqual(response.status_code, 200)

        self.assertTemplateUsed(
            response,
            "paas/data_export_success.html",
        )

        self.assertIn(
            "download_filename",
            response.context,
        )

        filename = response.context["download_filename"]

        self.assertRegex(
            filename,
            r"^app_export_\d{14}\.json$",
        )

        self.assertIn(
            "app_export_payload",
            self.client.session,
        )

        payload = self.client.session["app_export_payload"]
        export_data = json.loads(payload)

        self.assertEqual(len(export_data), 1)

        app = export_data[0]

        self.assertEqual(app["name"], "test-app")
        self.assertEqual(app["display_name"], "Test App äöü")
        self.assertEqual(
            app["docker_image"],
            "docker.io/example/test-app",
        )
        self.assertEqual(
            app["command"],
            "python manage.py runserver",
        )
        self.assertEqual(
            app["description"],
            "Eine Beschreibung mit Umlauten: äöü ß",
        )

        self.assertEqual(app["default_duration"], 12)

        self.assertEqual(app["app_port_intern_web"], 8080)
        self.assertEqual(app["app_port_intern_api"], 8081)
        self.assertEqual(app["app_port_intern_api_2"], 8082)

        self.assertEqual(app["hiddenservice_port_web"], 80)
        self.assertEqual(app["hiddenservice_port_api"], 81)
        self.assertEqual(app["hiddenservice_port_api_2"], 82)

        self.assertTrue(app["use_deploy_user"])
        self.assertFalse(app["no_hidden_service"])
        self.assertTrue(app["use_hostpid_namespace"])
        self.assertTrue(app["super_user_only"])

        self.assertEqual(app["tor_auth_type"], "cert")

    def test_export_contains_all_dependencies(self):
        """Alle abhängigen Datensätze werden korrekt exportiert."""

        self.client.post(
            self.url,
            {"app_ids": str(self.app1.pk)},
        )

        export_data = json.loads(
            self.client.session["app_export_payload"]
        )

        app = export_data[0]

        # Image-Tags
        self.assertEqual(
            app["image_tags"],
            [
                {"tag": "1.0.0"},
                {"tag": "latest"},
            ],
        )

        # Environment-Variablen
        self.assertEqual(
            app["env_vars"],
            [
                {
                    "key": "DATABASE_URL",
                    "value": "postgres://localhost/test",
                    "optional": True,
                    "editable": True,
                },
                {
                    "key": "DEBUG",
                    "value": "false",
                    "optional": False,
                    "editable": False,
                },
            ],
        )

        # Volumes
        self.assertEqual(
            app["volumes"],
            [
                {
                    "host_path": "/srv/test/data",
                    "container_path": "/app/data",
                },
            ],
        )

        # Config-Patches
        self.assertEqual(
            app["config_patches"],
            [
                {
                    "target_file": "config.ini",
                    "pattern": "^debug=",
                    "action": ConfigPatch.ACTION_REPLACE,
                    "replacement": "debug=false",
                },
            ],
        )

    def test_export_multiple_apps(self):
        """Mehrere ausgewählte Apps werden gemeinsam exportiert."""

        response = self.client.post(
            self.url,
            {
                "app_ids": [
                    str(self.app1.pk),
                    str(self.app2.pk),
                ]
            },
        )

        self.assertEqual(response.status_code, 200)

        export_data = json.loads(
            self.client.session["app_export_payload"]
        )

        self.assertEqual(len(export_data), 2)

        exported_names = {
            app["name"] for app in export_data
        }

        self.assertEqual(
            exported_names,
            {"test-app", "second-app"},
        )

    def test_unselected_apps_are_not_exported(self):
        """Nicht ausgewählte Apps dürfen nicht im Export erscheinen."""

        self.client.post(
            self.url,
            {"app_ids": str(self.app1.pk)},
        )

        export_data = json.loads(
            self.client.session["app_export_payload"]
        )

        exported_names = [
            app["name"] for app in export_data
        ]

        self.assertEqual(exported_names, ["test-app"])

    def test_export_contains_no_database_primary_keys(self):
        """Datenbank-IDs werden nicht in den JSON-Export übernommen."""

        self.client.post(
            self.url,
            {"app_ids": str(self.app1.pk)},
        )

        payload = self.client.session["app_export_payload"]

        export_data = json.loads(payload)

        app = export_data[0]

        self.assertNotIn("id", app)
        self.assertNotIn("pk", app)
        self.assertNotIn("app_definition_id", app)

        for tag in app["image_tags"]:
            self.assertEqual(set(tag.keys()), {"tag"})

        for env in app["env_vars"]:
            self.assertEqual(
                set(env.keys()),
                {"key", "value", "optional", "editable"},
            )

        for volume in app["volumes"]:
            self.assertEqual(
                set(volume.keys()),
                {"host_path", "container_path"},
            )

        for patch in app["config_patches"]:
            self.assertEqual(
                set(patch.keys()),
                {
                    "target_file",
                    "pattern",
                    "action",
                    "replacement",
                },
            )

    def test_export_payload_is_valid_utf8_json(self):
        """Der JSON-Export enthält echte Unicode-Zeichen."""

        self.client.post(
            self.url,
            {"app_ids": str(self.app1.pk)},
        )

        payload = self.client.session["app_export_payload"]

        self.assertIn("äöü", payload)
        self.assertIn("ß", payload)

        # JSON muss erneut dekodierbar sein
        decoded = json.loads(payload)

        self.assertEqual(
            decoded[0]["display_name"],
            "Test App äöü",
        )

    def test_empty_dependencies_are_exported_as_empty_lists(self):
        """Apps ohne Abhängigkeiten erhalten leere Listen."""

        self.client.post(
            self.url,
            {"app_ids": str(self.app2.pk)},
        )

        export_data = json.loads(
            self.client.session["app_export_payload"]
        )

        app = export_data[0]

        self.assertEqual(
            app["image_tags"],
            [{"tag": "2.0.0"}],
        )

        self.assertEqual(app["env_vars"], [])
        self.assertEqual(app["volumes"], [])
        self.assertEqual(app["config_patches"], [])
