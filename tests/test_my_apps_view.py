'''
Nicht angemeldete Benutzer werden zum Login weitergeleitet.

Angemeldete Benutzer sehen nur ihre eigenen ProvisionedApps.

Die ProvisionedApps werden nach started_at absteigend sortiert.

refresh_status() wird für jede ProvisionedApp aufgerufen.

Der aktualisierte Status wird tatsächlich in der Datenbank gespeichert.

Die verfügbaren Docker-Images werden korrekt zusammengestellt.

ProvisionedApps ohne Image-Tags funktionieren ebenfalls.

Die Template-Variablen werden korrekt übergeben.

Ein Superuser kann die View ebenfalls aufrufen.
'''

from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from paas.models import (
    AppDefinition,
    AppImageTag,
    ProvisionedApp,
    RemoteHost,
)

User = get_user_model()


class MyAppsViewTest(TestCase):

    @classmethod
    def setUpTestData(cls):
        # Benutzer
        cls.user = User.objects.create_user(
            username="testuser",
            password="testpassword",
        )

        cls.other_user = User.objects.create_user(
            username="otheruser",
            password="otherpassword",
        )

        cls.superuser = User.objects.create_superuser(
            username="admin",
            password="adminpassword",
            email="admin@example.com",
        )

        # Remote Host
        cls.host = RemoteHost.objects.create(
            hostname="test-host",
            ip_address="192.168.1.100",
        )

        # App Definition
        cls.app = AppDefinition.objects.create(
            name="test-app",
            display_name="Test App",
            docker_image="docker.io/example/test-app",
        )

        # Image Tags
        cls.tag1 = AppImageTag.objects.create(
            app_definition=cls.app,
            tag="1.0",
        )

        cls.tag2 = AppImageTag.objects.create(
            app_definition=cls.app,
            tag="2.0",
        )

    def setUp(self):
        self.url = reverse("paas_my_apps")

    def create_provision(
        self,
        user=None,
        status="pending",
        started_at=None,
        container_id=None,
    ):
        """
        Hilfsmethode zum Erstellen einer ProvisionedApp.
        """
        if user is None:
            user = self.user

        if started_at is None:
            started_at = timezone.now()

        provision = ProvisionedApp.objects.create(
            user=user,
            app=self.app,
            host=self.host,
            status=status,
            started_at=started_at,
            container_id=container_id,
        )

        return provision

    # ---------------------------------------------------------
    # Authentifizierung
    # ---------------------------------------------------------

    def test_anonymous_user_is_redirected_to_login(self):
        """
        Nicht angemeldete Benutzer müssen zum Login weitergeleitet
        werden.
        """
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 302)
        self.assertIn("/user-login", response.url)

    def test_authenticated_user_can_access_view(self):
        """
        Angemeldete Benutzer dürfen die View aufrufen.
        """
        self.client.force_login(self.user)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "paas/my_apps.html")

    def test_superuser_can_access_view(self):
        """
        Superuser dürfen ebenfalls auf die View zugreifen.
        """
        self.client.force_login(self.superuser)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)

    # ---------------------------------------------------------
    # Benutzerbezogene ProvisionedApps
    # ---------------------------------------------------------

    def test_only_own_provisions_are_displayed(self):
        """
        Ein Benutzer darf ausschließlich seine eigenen
        ProvisionedApps sehen.
        """
        own_provision = self.create_provision(user=self.user)
        other_provision = self.create_provision(user=self.other_user)

        self.client.force_login(self.user)

        with patch.object(
            ProvisionedApp,
            "refresh_status",
            return_value=None,
        ):
            response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)

        provisions = list(response.context["provisions"])

        self.assertEqual(len(provisions), 1)
        self.assertEqual(provisions[0].pk, own_provision.pk)

        self.assertNotIn(
            other_provision.pk,
            [p.pk for p in provisions],
        )

    # ---------------------------------------------------------
    # Sortierung
    # ---------------------------------------------------------

    def test_provisions_are_ordered_by_started_at_descending(self):
        """
        Neuere ProvisionedApps müssen zuerst erscheinen.
        """
        now = timezone.now()

        older = self.create_provision(
            started_at=now - timedelta(hours=2),
        )

        newer = self.create_provision(
            started_at=now - timedelta(hours=1),
        )

        newest = self.create_provision(
            started_at=now,
        )

        self.client.force_login(self.user)

        with patch.object(
            ProvisionedApp,
            "refresh_status",
            return_value=None,
        ):
            response = self.client.get(self.url)

        provisions = list(response.context["provisions"])

        self.assertEqual(
            [p.pk for p in provisions],
            [newest.pk, newer.pk, older.pk],
        )

    # ---------------------------------------------------------
    # Statusaktualisierung
    # ---------------------------------------------------------

    def test_refresh_status_is_called_for_every_provision(self):
        """
        refresh_status muss für jede ProvisionedApp aufgerufen
        werden.
        """
        provision1 = self.create_provision()
        provision2 = self.create_provision()

        self.client.force_login(self.user)

        with patch.object(
            ProvisionedApp,
            "refresh_status",
            autospec=True,
        ) as mock_refresh:

            response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)

        self.assertEqual(mock_refresh.call_count, 2)

        called_ids = {
            call.args[0].pk
            for call in mock_refresh.call_args_list
        }

        self.assertEqual(
            called_ids,
            {provision1.pk, provision2.pk},
        )

    def test_updated_status_is_saved_to_database(self):
        """
        Ein von refresh_status geänderter Status muss
        anschließend gespeichert werden.
        """
        provision = self.create_provision(status="pending")

        self.client.force_login(self.user)

        def update_status(instance):
            instance.status = "running"

        with patch.object(
            ProvisionedApp,
            "refresh_status",
            autospec=True,
            side_effect=update_status,
        ):
            response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)

        provision.refresh_from_db()

        self.assertEqual(provision.status, "running")

    def test_status_is_saved_even_if_refresh_does_not_change_it(self):
        """
        Die View ruft save(update_fields=['status']) auch dann auf,
        wenn refresh_status den Status nicht verändert.
        """
        provision = self.create_provision(status="pending")

        self.client.force_login(self.user)

        with patch.object(
            ProvisionedApp,
            "refresh_status",
            return_value=None,
        ), patch.object(
            ProvisionedApp,
            "save",
            autospec=True,
        ) as mock_save:

            response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)

        self.assertTrue(
            any(
                call.args[0].pk == provision.pk
                and call.kwargs.get("update_fields") == ["status"]
                for call in mock_save.call_args_list
            )
        )

    # ---------------------------------------------------------
    # Docker-Images
    # ---------------------------------------------------------

    def test_available_images_are_generated_correctly(self):
        """
        Die verfügbaren Images müssen aus Basis-Image und
        sämtlichen Tags zusammengesetzt werden.
        """
        self.create_provision()

        self.client.force_login(self.user)

        with patch.object(
            ProvisionedApp,
            "refresh_status",
            return_value=None,
        ):
            response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)

        provisions = response.context["provisions"]

        self.assertEqual(
            provisions[0].available_images,
            [
                "docker.io/example/test-app:1.0",
                "docker.io/example/test-app:2.0",
            ],
        )

    def test_available_images_is_empty_without_tags(self):
        """
        Eine App ohne Image-Tags muss eine leere Image-Liste
        erhalten.
        """
        app_without_tags = AppDefinition.objects.create(
            name="no-tags-app",
            display_name="App ohne Tags",
            docker_image="docker.io/example/no-tags",
        )

        ProvisionedApp.objects.create(
            user=self.user,
            app=app_without_tags,
            host=self.host,
            status="pending",
        )

        self.client.force_login(self.user)

        with patch.object(
            ProvisionedApp,
            "refresh_status",
            return_value=None,
        ):
            response = self.client.get(self.url)

        provisions = response.context["provisions"]

        self.assertEqual(len(provisions), 1)
        self.assertEqual(provisions[0].available_images, [])

    # ---------------------------------------------------------
    # Template-Kontext
    # ---------------------------------------------------------

    def test_template_context_contains_required_variables(self):
        """
        Die für das Template benötigten Variablen müssen
        vorhanden sein.
        """
        self.client.force_login(self.user)

        with patch.object(
            ProvisionedApp,
            "refresh_status",
            return_value=None,
        ):
            response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)

        self.assertIn("provisions", response.context)
        self.assertIn("PLATFORM_NAME", response.context)
        self.assertIn("STRING_TO_ADMIN_PATH", response.context)

    def test_empty_provisions_list(self):
        """
        Benutzer ohne ProvisionedApps müssen eine leere Liste
        erhalten.
        """
        self.client.force_login(self.user)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            list(response.context["provisions"]),
            [],
        )
