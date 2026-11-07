'''
Abgelaufene Container mit Status running werden gelöscht.
Abgelaufene Container mit Status deleting werden gelöscht.
Nicht abgelaufene Container werden nicht gelöscht.
Container ohne Ablaufdatum (expires_at=None) werden nicht gelöscht.
Container mit anderen Status werden nicht gelöscht.
Es wird nichts gelöscht, wenn keine abgelaufenen Container existieren.
Mehrere abgelaufene Container werden verarbeitet.
Ein Fehler beim Löschen eines Containers verhindert nicht die Verarbeitung weiterer Container.
Ein zwischenzeitlich verlängertes Ablaufdatum wird berücksichtigt.
Ein zwischenzeitlich geänderter Status wird berücksichtigt.
'''

from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from paas.models import (
    AppDefinition,
    ProvisionedApp,
    RemoteHost,
)
from paas.tasks import sweep_expired_containers


User = get_user_model()


class SweepExpiredContainersTaskTests(TestCase):
    """
    Tests für den Celery-Task sweep_expired_containers.

    Die eigentliche Container-Löschung wird gemockt, damit
    keine SSH- oder Docker-Verbindungen aufgebaut werden.
    """

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            username="testuser",
            password="testpassword",
        )

        cls.app = AppDefinition.objects.create(
            name="test-app",
            display_name="Test App",
            docker_image="test/image",
        )

        cls.host = RemoteHost.objects.create(
            hostname="test.example.com",
            ip_address="192.0.2.10",
            ssh_user="root",
        )

    def create_provision(
        self,
        status="running",
        expires_at=None,
    ):
        """
        Erstellt eine ProvisionedApp mit den gewünschten Eigenschaften.
        """

        return ProvisionedApp.objects.create(
            user=self.user,
            app=self.app,
            host=self.host,
            container_id="test-container",
            container_name="test-container",
            status=status,
            expires_at=expires_at,
        )

    @patch("paas.tasks.delete_container_by_id")
    def test_expired_running_container_is_deleted(self, mock_delete):
        """
        Ein abgelaufener Container mit Status running
        muss gelöscht werden.
        """

        expired = self.create_provision(
            status="running",
            expires_at=timezone.now() - timedelta(hours=1),
        )

        sweep_expired_containers()

        mock_delete.assert_called_once_with(expired.id)

    @patch("paas.tasks.delete_container_by_id")
    def test_expired_deleting_container_is_deleted(self, mock_delete):
        """
        Auch Container mit Status deleting müssen berücksichtigt werden.
        """

        expired = self.create_provision(
            status="deleting",
            expires_at=timezone.now() - timedelta(hours=1),
        )

        sweep_expired_containers()

        mock_delete.assert_called_once_with(expired.id)

    @patch("paas.tasks.delete_container_by_id")
    def test_not_expired_container_is_not_deleted(self, mock_delete):
        """
        Container mit Ablaufdatum in der Zukunft dürfen
        nicht gelöscht werden.
        """

        self.create_provision(
            status="running",
            expires_at=timezone.now() + timedelta(hours=1),
        )

        sweep_expired_containers()

        mock_delete.assert_not_called()

    @patch("paas.tasks.delete_container_by_id")
    def test_container_without_expiration_is_not_deleted(self, mock_delete):
        """
        expires_at=None bedeutet unbegrenzte Laufzeit.
        """

        self.create_provision(
            status="running",
            expires_at=None,
        )

        sweep_expired_containers()

        mock_delete.assert_not_called()

    @patch("paas.tasks.delete_container_by_id")
    def test_other_statuses_are_not_deleted(self, mock_delete):
        """
        Nur running und deleting dürfen gelöscht werden.
        """

        statuses = [
            "pending",
            "finished",
            "error",
            "deleted",
            "stopped",
        ]

        for status in statuses:
            self.create_provision(
                status=status,
                expires_at=timezone.now() - timedelta(hours=1),
            )

        sweep_expired_containers()

        mock_delete.assert_not_called()

    @patch("paas.tasks.delete_container_by_id")
    def test_multiple_expired_containers_are_deleted(self, mock_delete):
        """
        Alle abgelaufenen Container müssen verarbeitet werden.
        """

        provisions = [
            self.create_provision(
                status="running",
                expires_at=timezone.now() - timedelta(hours=1),
            ),
            self.create_provision(
                status="deleting",
                expires_at=timezone.now() - timedelta(hours=2),
            ),
            self.create_provision(
                status="running",
                expires_at=timezone.now() - timedelta(days=1),
            ),
        ]

        sweep_expired_containers()

        self.assertEqual(mock_delete.call_count, 3)

        called_ids = {
            call.args[0]
            for call in mock_delete.call_args_list
        }

        expected_ids = {
            provision.id
            for provision in provisions
        }

        self.assertEqual(called_ids, expected_ids)

    @patch("paas.tasks.delete_container_by_id")
    def test_no_expired_containers(self, mock_delete):
        """
        Wenn keine abgelaufenen Container vorhanden sind,
        darf keine Löschung erfolgen.
        """

        self.create_provision(
            status="running",
            expires_at=timezone.now() + timedelta(hours=1),
        )

        self.create_provision(
            status="running",
            expires_at=None,
        )

        sweep_expired_containers()

        mock_delete.assert_not_called()

    @patch("paas.tasks.delete_container_by_id")
    def test_deletion_exception_does_not_stop_sweep(self, mock_delete):
        """
        Ein Fehler beim Löschen eines Containers darf
        die Verarbeitung weiterer Container nicht verhindern.
        """

        first = self.create_provision(
            status="running",
            expires_at=timezone.now() - timedelta(hours=2),
        )

        second = self.create_provision(
            status="running",
            expires_at=timezone.now() - timedelta(hours=1),
        )

        def delete_side_effect(provision_id):
            if provision_id == first.id:
                raise Exception("Simulierter Löschfehler")

        mock_delete.side_effect = delete_side_effect

        sweep_expired_containers()

        self.assertEqual(mock_delete.call_count, 2)

        called_ids = {
            call.args[0]
            for call in mock_delete.call_args_list
        }

        self.assertEqual(
            called_ids,
            {first.id, second.id},
        )

    @patch("paas.tasks.delete_container_by_id")
    @patch("paas.tasks.ProvisionedApp.objects.select_for_update")
    def test_extended_container_is_not_deleted(
            self,
            mock_select_for_update,
            mock_delete,
    ):
        """
        Wenn das Ablaufdatum zwischen Query und Verarbeitung
        verlängert wird, darf der Container nicht gelöscht werden.
        """

        expired = self.create_provision(
            status="running",
            expires_at=timezone.now() - timedelta(hours=1),
        )

        def get_updated_object(**kwargs):
            # Simuliert die zwischenzeitliche Verlängerung.
            ProvisionedApp.objects.filter(pk=expired.pk).update(
                expires_at=timezone.now() + timedelta(hours=2)
            )

            return ProvisionedApp.objects.get(pk=kwargs["pk"])

        mock_select_for_update.return_value.get.side_effect = (
            get_updated_object
        )

        sweep_expired_containers()

        mock_delete.assert_not_called()

    @patch("paas.tasks.delete_container_by_id")
    @patch("paas.tasks.ProvisionedApp.objects.select_for_update")
    def test_changed_status_is_not_deleted(
            self,
            mock_select_for_update,
            mock_delete,
    ):
        """
        Wenn sich der Status zwischen Query und Verarbeitung
        auf einen nicht löschbaren Status ändert,
        darf der Container nicht gelöscht werden.
        """

        expired = self.create_provision(
            status="running",
            expires_at=timezone.now() - timedelta(hours=1),
        )

        def get_updated_object(**kwargs):
            # Simuliert eine parallele Statusänderung.
            ProvisionedApp.objects.filter(pk=expired.pk).update(
                status="finished"
            )

            return ProvisionedApp.objects.get(pk=kwargs["pk"])

        mock_select_for_update.return_value.get.side_effect = (
            get_updated_object
        )

        sweep_expired_containers()

        mock_delete.assert_not_called()
