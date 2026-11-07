'''
Bereich --> Tests
Zugriffsschutz --> Login, Superuser, normale Benutzer
HTTP --> GET, POST, PUT
Formular --> Fehlende Eingabe, leere Eingabe
JSON --> Ungültiges JSON, falscher Datentyp
AppDefinition --> Erstellen, Standardwerte, Aktualisieren
Image-Tags --> Erstellen, keine Duplikate
Environment-Variablen --> Erstellen und Aktualisieren
Volumes --> Erstellen und Validierung
Config-Patches --> Erstellen und Validierung
Transaktionen --> Vollständiger Rollback bei Fehlern
Mehrfachimport --> Mehrere Apps in einem Import
'''

import json

from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.test import TestCase
from django.urls import reverse
from paas.models import ( AppDefinition, AppImageTag, AppEnvVarPerApp, AppVolumePerApp, ConfigPatch, )

User = get_user_model()

class DataImportAppDefViewTest(TestCase):
    def setUp(self):
        self.url = reverse("paas_data_import_app_def")

        self.superuser = User.objects.create_superuser(
            username="admin",
            email="admin@example.com",
            password="testpassword",
        )

        self.normal_user = User.objects.create_user(
            username="user",
            email="user@example.com",
            password="testpassword",
        )

    # ------------------------------------------------------------
    # Hilfsmethoden
    # ------------------------------------------------------------

    def login_superuser(self):
        self.client.force_login(self.superuser)

    def login_normal_user(self):
        self.client.force_login(self.normal_user)

    def post_json(self, data):
        return self.client.post(
            self.url,
            {"json_data": json.dumps(data)},
        )

    def get_messages_list(self, response):
        return [str(m.message) for m in get_messages(response.wsgi_request)]

    # ------------------------------------------------------------
    # GET
    # ------------------------------------------------------------

    def test_get_requires_login(self):
        """
        Nicht angemeldete Benutzer müssen zum Login weitergeleitet werden.
        """
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response.url)

    def test_get_rejects_normal_user(self):
        """
        Normale Benutzer dürfen die Import-Seite nicht aufrufen.
        """
        self.login_normal_user()

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 302)
        self.assertIn("/user-login", response.url)

    def test_get_superuser_displays_import_form(self):
        """
        Superuser erhalten das Import-Formular.
        """
        self.login_superuser()

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "paas/data_import_select.html")
        self.assertIn("form", response.context)

    # ------------------------------------------------------------
    # POST – Berechtigungen und HTTP-Methoden
    # ------------------------------------------------------------

    def test_post_requires_login(self):
        response = self.post_json([
            {"name": "test-app"}
        ])

        self.assertEqual(response.status_code, 302)
        self.assertEqual(AppDefinition.objects.count(), 0)

    def test_post_rejects_normal_user(self):
        self.login_normal_user()

        response = self.post_json([
            {"name": "test-app"}
        ])

        self.assertEqual(response.status_code, 302)
        self.assertEqual(AppDefinition.objects.count(), 0)

    def test_put_method_not_allowed(self):
        self.login_superuser()

        response = self.client.put(self.url)

        self.assertEqual(response.status_code, 405)

    # ------------------------------------------------------------
    # POST – JSON-Validierung
    # ------------------------------------------------------------

    def test_invalid_form(self):
        """
        Ein fehlendes JSON-Feld muss einen Formularfehler erzeugen.
        """
        self.login_superuser()

        response = self.client.post(self.url, {})

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "paas/data_import_select.html")
        self.assertEqual(AppDefinition.objects.count(), 0)

        messages = self.get_messages_list(response)
        self.assertTrue(
            any("Ungültiges Formular" in m for m in messages)
        )

    def test_empty_json(self):
        self.login_superuser()

        response = self.client.post(
            self.url,
            {"json_data": ""},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(AppDefinition.objects.count(), 0)

        messages = self.get_messages_list(response)

        self.assertTrue(
            any(
                "Ungültiges Formular" in m
                or "darf nicht leer sein" in m
                for m in messages
            ),
            messages,
        )

    def test_invalid_json(self):
        self.login_superuser()

        response = self.client.post(
            self.url,
            {"json_data": "{invalid json"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(AppDefinition.objects.count(), 0)

        messages = self.get_messages_list(response)
        self.assertTrue(
            any("JSON-Fehler" in m for m in messages)
        )

    def test_json_must_be_array(self):
        self.login_superuser()

        response = self.client.post(
            self.url,
            {"json_data": json.dumps({"name": "test-app"})},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(AppDefinition.objects.count(), 0)

        messages = self.get_messages_list(response)
        self.assertTrue(
            any("JSON muss ein Array sein" in m for m in messages)
        )

    def test_json_array_entries_must_be_objects(self):
        self.login_superuser()

        response = self.client.post(
            self.url,
            {"json_data": json.dumps([
                {"name": "valid-app"},
                "invalid-entry",
            ])},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(AppDefinition.objects.count(), 0)

        messages = self.get_messages_list(response)

        self.assertTrue(
            any("Eintrag 2 muss ein JSON-Objekt sein" in m for m in messages)
        )

    # ------------------------------------------------------------
    # POST – erfolgreicher Import
    # ------------------------------------------------------------

    def test_import_creates_app_definition(self):
        """
        Eine einfache AppDefinition wird korrekt angelegt.
        """
        self.login_superuser()

        data = [
            {
                "name": "test-app",
                "display_name": "Test Application",
                "docker_image": "example/test-app",
                "command": "python app.py",
                "description": "Eine Testanwendung",
                "default_duration": 12,
                "app_port_intern_web": 8080,
                "app_port_intern_api": 9000,
                "use_deploy_user": True,
                "no_hidden_service": True,
                "use_hostpid_namespace": False,
                "super_user_only": True,
                "tor_auth_type": "cert",
            }
        ]

        response = self.post_json(data)

        self.assertEqual(response.status_code, 302)
        self.assertRedirects(
            response,
            reverse("paas_data_import_app_def_success"),
        )

        app = AppDefinition.objects.get(name="test-app")

        self.assertEqual(app.display_name, "Test Application")
        self.assertEqual(app.docker_image, "example/test-app")
        self.assertEqual(app.command, "python app.py")
        self.assertEqual(app.description, "Eine Testanwendung")
        self.assertEqual(app.default_duration, 12)
        self.assertEqual(app.app_port_intern_web, 8080)
        self.assertEqual(app.app_port_intern_api, 9000)
        self.assertTrue(app.use_deploy_user)
        self.assertTrue(app.no_hidden_service)
        self.assertTrue(app.super_user_only)
        self.assertEqual(app.tor_auth_type, "cert")

        messages = self.get_messages_list(response)
        self.assertTrue(
            any("Import erfolgreich" in m for m in messages)
        )

    def test_import_uses_default_values(self):
        """
        Nicht angegebene Felder erhalten die im View definierten Defaults.
        """
        self.login_superuser()

        response = self.post_json([
            {"name": "minimal-app"}
        ])

        self.assertEqual(response.status_code, 302)

        app = AppDefinition.objects.get(name="minimal-app")

        self.assertEqual(app.display_name, "minimal-app")
        self.assertEqual(app.docker_image, "")
        self.assertEqual(app.command, "")
        self.assertEqual(app.default_duration, 1)
        self.assertEqual(app.app_port_intern_web, 80)
        self.assertEqual(app.app_port_intern_api, 1)
        self.assertEqual(app.hiddenservice_port_web, 80)
        self.assertFalse(app.use_deploy_user)
        self.assertFalse(app.no_hidden_service)
        self.assertFalse(app.super_user_only)
        self.assertEqual(app.tor_auth_type, "none")

    # ------------------------------------------------------------
    # POST – abhängige Objekte
    # ------------------------------------------------------------

    def test_import_creates_related_objects(self):
        """
        Image-Tags, Environment-Variablen, Volumes und Config-Patches
        werden korrekt angelegt.
        """
        self.login_superuser()

        data = [
            {
                "name": "complex-app",
                "display_name": "Complex App",
                "docker_image": "example/complex",
                "image_tags": [
                    {"tag": "1.0"},
                    {"tag": "2.0"},
                ],
                "env_vars": [
                    {
                        "key": "DATABASE_URL",
                        "value": "postgres://db",
                        "optional": False,
                        "editable": True,
                    },
                    {
                        "key": "DEBUG",
                        "value": "false",
                        "optional": True,
                        "editable": False,
                    },
                ],
                "volumes": [
                    {
                        "host_path": "/data/app",
                        "container_path": "/app/data",
                    }
                ],
                "config_patches": [
                    {
                        "target_file": "config.ini",
                        "pattern": "^debug=false$",
                        "action": "replace",
                        "replacement": "debug=true",
                    }
                ],
            }
        ]

        response = self.post_json(data)

        self.assertEqual(response.status_code, 302)

        app = AppDefinition.objects.get(name="complex-app")

        self.assertEqual(
            set(app.image_tags.values_list("tag", flat=True)),
            {"1.0", "2.0"},
        )

        env = AppEnvVarPerApp.objects.get(
            app=app,
            key="DATABASE_URL",
        )
        self.assertEqual(env.value, "postgres://db")
        self.assertFalse(env.optional)
        self.assertTrue(env.editable)

        volume = AppVolumePerApp.objects.get(app=app)

        self.assertEqual(volume.host_path, "/data/app")
        self.assertEqual(volume.container_path, "/app/data")

        patch = ConfigPatch.objects.get(app=app)

        self.assertEqual(patch.target_file, "config.ini")
        self.assertEqual(patch.pattern, "^debug=false$")
        self.assertEqual(patch.action, "replace")
        self.assertEqual(patch.replacement, "debug=true")
        self.assertIsNone(patch.volume)

    # ------------------------------------------------------------
    # POST – Update statt Duplikate
    # ------------------------------------------------------------

    def test_import_updates_existing_app_definition(self):
        """
        Eine vorhandene AppDefinition wird anhand ihres Namens aktualisiert.
        """
        self.login_superuser()

        app = AppDefinition.objects.create(
            name="existing-app",
            display_name="Old Name",
            docker_image="old/image",
        )

        response = self.post_json([
            {
                "name": "existing-app",
                "display_name": "New Name",
                "docker_image": "new/image",
                "default_duration": 24,
            }
        ])

        self.assertEqual(response.status_code, 302)

        self.assertEqual(AppDefinition.objects.count(), 1)

        app.refresh_from_db()

        self.assertEqual(app.display_name, "New Name")
        self.assertEqual(app.docker_image, "new/image")
        self.assertEqual(app.default_duration, 24)

    def test_import_updates_existing_env_var(self):
        """
        Environment-Variablen werden anhand app + key aktualisiert.
        """
        self.login_superuser()

        app = AppDefinition.objects.create(
            name="env-app",
            display_name="Environment App",
            docker_image="example/env",
        )

        AppEnvVarPerApp.objects.create(
            app=app,
            key="API_KEY",
            value="old-value",
        )

        response = self.post_json([
            {
                "name": "env-app",
                "env_vars": [
                    {
                        "key": "API_KEY",
                        "value": "new-value",
                        "optional": True,
                        "editable": True,
                    }
                ],
            }
        ])

        self.assertEqual(response.status_code, 302)

        self.assertEqual(
            AppEnvVarPerApp.objects.filter(app=app).count(),
            1,
        )

        env = AppEnvVarPerApp.objects.get(app=app, key="API_KEY")

        self.assertEqual(env.value, "new-value")
        self.assertTrue(env.optional)
        self.assertTrue(env.editable)

    def test_import_does_not_duplicate_image_tags(self):
        """
        Bereits vorhandene Image-Tags werden nicht dupliziert.
        """
        self.login_superuser()

        app = AppDefinition.objects.create(
            name="tag-app",
            display_name="Tag App",
            docker_image="example/tag",
        )

        AppImageTag.objects.create(
            app_definition=app,
            tag="latest",
        )

        response = self.post_json([
            {
                "name": "tag-app",
                "image_tags": [
                    {"tag": "latest"},
                    {"tag": "2.0"},
                ],
            }
        ])

        self.assertEqual(response.status_code, 302)

        self.assertEqual(
            AppImageTag.objects.filter(app_definition=app).count(),
            2,
        )

    # ------------------------------------------------------------
    # POST – Validierung einzelner Einträge
    # ------------------------------------------------------------

    def test_import_rejects_app_without_name(self):
        self.login_superuser()

        response = self.post_json([
            {"display_name": "Missing Name"}
        ])

        self.assertEqual(response.status_code, 200)
        self.assertEqual(AppDefinition.objects.count(), 0)

        messages = self.get_messages_list(response)
        self.assertTrue(
            any("name" in m for m in messages)
        )

    def test_import_rejects_env_var_without_key(self):
        self.login_superuser()

        response = self.post_json([
            {
                "name": "invalid-env-app",
                "env_vars": [
                    {"value": "some-value"}
                ],
            }
        ])

        self.assertEqual(response.status_code, 200)
        self.assertEqual(AppDefinition.objects.count(), 0)
        self.assertEqual(AppEnvVarPerApp.objects.count(), 0)

        messages = self.get_messages_list(response)
        self.assertTrue(
            any("env-Var" in m for m in messages)
        )

    def test_import_rejects_volume_without_paths(self):
        self.login_superuser()

        response = self.post_json([
            {
                "name": "invalid-volume-app",
                "volumes": [
                    {"host_path": "/data"}
                ],
            }
        ])

        self.assertEqual(response.status_code, 200)
        self.assertEqual(AppDefinition.objects.count(), 0)
        self.assertEqual(AppVolumePerApp.objects.count(), 0)

        messages = self.get_messages_list(response)
        self.assertTrue(
            any("Volume" in m for m in messages)
        )

    def test_import_rejects_config_patch_without_required_fields(self):
        self.login_superuser()

        response = self.post_json([
            {
                "name": "invalid-patch-app",
                "config_patches": [
                    {
                        "target_file": "config.ini"
                    }
                ],
            }
        ])

        self.assertEqual(response.status_code, 200)
        self.assertEqual(AppDefinition.objects.count(), 0)
        self.assertEqual(ConfigPatch.objects.count(), 0)

        messages = self.get_messages_list(response)
        self.assertTrue(
            any("ConfigPatch" in m for m in messages)
        )

    # ------------------------------------------------------------
    # POST – Transaktionssicherheit
    # ------------------------------------------------------------

    def test_import_rolls_back_all_changes_on_error(self):
        """
        Wenn ein späterer Eintrag fehlerhaft ist, müssen auch bereits
        angelegte Apps und abhängige Objekte zurückgerollt werden.
        """
        self.login_superuser()

        data = [
            {
                "name": "valid-app",
                "display_name": "Valid App",
                "image_tags": [
                    {"tag": "1.0"}
                ],
                "env_vars": [
                    {
                        "key": "TEST",
                        "value": "value",
                    }
                ],
            },
            {
                "name": "invalid-app",
                "volumes": [
                    {
                        "host_path": "/data",
                        # container_path fehlt absichtlich
                    }
                ],
            },
        ]

        response = self.post_json(data)

        self.assertEqual(response.status_code, 200)

        self.assertEqual(AppDefinition.objects.count(), 0)
        self.assertEqual(AppImageTag.objects.count(), 0)
        self.assertEqual(AppEnvVarPerApp.objects.count(), 0)
        self.assertEqual(AppVolumePerApp.objects.count(), 0)
        self.assertEqual(ConfigPatch.objects.count(), 0)

        messages = self.get_messages_list(response)
        self.assertTrue(
            any("Import fehlgeschlagen" in m for m in messages)
        )

    # ------------------------------------------------------------
    # POST – Mehrere Apps in einem Import
    # ------------------------------------------------------------

    def test_import_multiple_app_definitions(self):
        self.login_superuser()

        response = self.post_json([
            {
                "name": "app-one",
                "display_name": "Application One",
            },
            {
                "name": "app-two",
                "display_name": "Application Two",
            },
            {
                "name": "app-three",
                "display_name": "Application Three",
            },
        ])

        self.assertEqual(response.status_code, 302)
        self.assertEqual(AppDefinition.objects.count(), 3)

        self.assertTrue(AppDefinition.objects.filter(name="app-one").exists())
        self.assertTrue(AppDefinition.objects.filter(name="app-two").exists())
        self.assertTrue(AppDefinition.objects.filter(name="app-three").exists())