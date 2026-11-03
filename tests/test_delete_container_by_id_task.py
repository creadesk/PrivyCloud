'''
Provision existiert nicht → Task beendet sich ohne Fehler.
Status deleted → keine Löschung und kein SSH-Aufruf.
Status deleting → keine Löschung und kein SSH-Aufruf.
Erfolgreiche Löschung mit Container und Tor Hidden Service.
Erfolgreiche Löschung ohne container_id.
Erfolgreiche Löschung ohne container_name.
Löschen des Tor-Verzeichnisses schlägt fehl → Verzeichnis wird umbenannt.
Löschen und Umbenennen schlagen fehl → entsprechender Logeintrag.
Docker-Volume-Prune wird ausgeführt.
Der Datenbankeintrag wird tatsächlich gelöscht.
'''


from contextlib import contextmanager
from unittest.mock import Mock, patch, call

from django.contrib.auth import get_user_model
from django.test import TestCase

from paas.models import (
    AppDefinition,
    RemoteHost,
    ProvisionedApp,
)
from paas.tasks import delete_container_by_id


class DeleteContainerByIdTaskTests(TestCase):

    def setUp(self):
        User = get_user_model()

        self.user = User.objects.create_user(
            username="testuser",
            password="testpassword",
        )

        self.app = AppDefinition.objects.create(
            name="test-app",
            display_name="Test App",
            docker_image="test/image",
        )

        self.host = RemoteHost.objects.create(
            hostname="test.example.com",
            ip_address="192.0.2.10",
            ssh_user="testuser",
        )

        self.provision = ProvisionedApp.objects.create(
            user=self.user,
            app=self.app,
            host=self.host,
            container_id="container123",
            container_name="test-container",
            status="running",
            onion_address="abcdefghijklmnop.onion",
            log="Initial log",
        )

    @contextmanager
    def mock_ssh(self):
        """
        Simuliert eine SSH-Verbindung.
        """
        ssh = Mock()

        with patch(
            "paas.tasks._ssh_client"
        ) as ssh_client:

            ssh_client.return_value.__enter__.return_value = ssh
            ssh_client.return_value.__exit__.return_value = False

            yield ssh

    # ---------------------------------------------------------
    # 1. Provision existiert nicht
    # ---------------------------------------------------------

    @patch("paas.tasks._ssh_client")
    def test_provision_does_not_exist(self, mock_ssh_client):
        """
        Ein nicht vorhandener Datensatz darf keinen Fehler auslösen.
        """

        delete_container_by_id(999999)

        mock_ssh_client.assert_not_called()

    # ---------------------------------------------------------
    # 2. Bereits gelöscht
    # ---------------------------------------------------------

    @patch("paas.tasks._ssh_client")
    def test_already_deleted(self, mock_ssh_client):
        """
        Status deleted: keine weitere Aktion.
        """

        self.provision.status = "deleted"
        self.provision.save()

        delete_container_by_id(self.provision.pk)

        mock_ssh_client.assert_not_called()

        self.assertTrue(
            ProvisionedApp.objects.filter(
                pk=self.provision.pk
            ).exists()
        )

    # ---------------------------------------------------------
    # 3. Wird bereits gelöscht
    # ---------------------------------------------------------

    @patch("paas.tasks._ssh_client")
    def test_already_deleting(self, mock_ssh_client):
        """
        Status deleting: keine weitere Aktion.
        """

        self.provision.status = "deleting"
        self.provision.save()

        delete_container_by_id(self.provision.pk)

        mock_ssh_client.assert_not_called()

        self.assertTrue(
            ProvisionedApp.objects.filter(
                pk=self.provision.pk
            ).exists()
        )

    # ---------------------------------------------------------
    # 4. Erfolgreiche vollständige Löschung
    # ---------------------------------------------------------

    def test_successful_deletion(self):
        """
        Container und Tor Hidden Service werden entfernt.
        Anschließend wird der DB-Eintrag gelöscht.
        """

        with self.mock_ssh() as ssh:
            with patch(
                "paas.tasks._run_cmd",
                return_value=(0, "", ""),
            ) as run_cmd:

                delete_container_by_id(self.provision.pk)

        self.assertFalse(
            ProvisionedApp.objects.filter(
                pk=self.provision.pk
            ).exists()
        )

        expected_commands = [
            "docker rm -f container123",
            "docker volume prune -f",
            "systemctl --user stop tor-hidden-service@test-container.service",
            "systemctl --user disable tor-hidden-service@test-container.service",
            "rm ~/.config/systemd/user/tor-hidden-service@test-container.service",
            "rm -rf /home/testuser/test-container",
        ]

        actual_commands = [
            c.args[1] for c in run_cmd.call_args_list
        ]

        self.assertEqual(
            actual_commands,
            expected_commands,
        )

    # ---------------------------------------------------------
    # 5. Kein Container vorhanden
    # ---------------------------------------------------------

    def test_deletion_without_container_id(self):
        """
        Ohne Container-ID darf kein docker rm ausgeführt werden.
        """

        self.provision.container_id = None
        self.provision.save()

        with self.mock_ssh():
            with patch(
                "paas.tasks._run_cmd",
                return_value=(0, "", ""),
            ) as run_cmd:

                delete_container_by_id(self.provision.pk)

        commands = [
            c.args[1] for c in run_cmd.call_args_list
        ]

        self.assertNotIn(
            "docker rm -f container123",
            commands,
        )

        self.assertIn(
            "docker volume prune -f",
            commands,
        )

        self.assertFalse(
            ProvisionedApp.objects.filter(
                pk=self.provision.pk
            ).exists()
        )

    # ---------------------------------------------------------
    # 6. Kein Containername vorhanden
    # ---------------------------------------------------------

    def test_deletion_without_container_name(self):
        """
        Ohne Containername darf keine Tor-Bereinigung erfolgen.
        """

        self.provision.container_name = None
        self.provision.save()

        with self.mock_ssh():
            with patch(
                "paas.tasks._run_cmd",
                return_value=(0, "", ""),
            ) as run_cmd:

                delete_container_by_id(self.provision.pk)

        commands = [
            c.args[1] for c in run_cmd.call_args_list
        ]

        self.assertIn(
            "docker rm -f container123",
            commands,
        )

        self.assertIn(
            "docker volume prune -f",
            commands,
        )

        self.assertFalse(
            any("tor-hidden-service" in cmd for cmd in commands)
        )

        self.assertFalse(
            ProvisionedApp.objects.filter(
                pk=self.provision.pk
            ).exists()
        )

    # ---------------------------------------------------------
    # 7. Tor-Verzeichnis kann nicht gelöscht werden
    #    -> Umbenennung erfolgreich
    # ---------------------------------------------------------

    def test_tor_directory_delete_fails_rename_succeeds(self):
        """
        Wenn rm -rf fehlschlägt, soll das Verzeichnis
        in delete_me_<name> umbenannt werden.
        """

        def run_cmd_side_effect(ssh, command):

            if command == "rm -rf /home/testuser/test-container":
                return (1, "", "Permission denied")

            if command == "mv /home/testuser/test-container /home/testuser/delete_me_test-container":
                return (0, "", "")

            return (0, "", "")

        with self.mock_ssh():
            with patch(
                "paas.tasks._run_cmd",
                side_effect=run_cmd_side_effect,
            ) as run_cmd:

                delete_container_by_id(self.provision.pk)

        self.assertFalse(
            ProvisionedApp.objects.filter(
                pk=self.provision.pk
            ).exists()
        )

        commands = [
            c.args[1] for c in run_cmd.call_args_list
        ]

        self.assertIn(
            "mv /home/testuser/test-container /home/testuser/delete_me_test-container",
            commands,
        )

    # ---------------------------------------------------------
    # 8. Tor-Verzeichnis löschen und Umbenennen schlägt fehl
    # ---------------------------------------------------------

    def test_tor_directory_delete_and_rename_fail(self):
        """
        Wenn sowohl rm -rf als auch mv fehlschlagen,
        bleibt der DB-Eintrag mit Status 'error' erhalten.
        """

        def run_cmd_side_effect(ssh, command):

            if command.startswith("rm -rf "):
                return (1, "", "Permission denied")

            if command.startswith("mv "):
                return (1, "", "Rename failed")

            return (0, "", "")

        original_container_id = self.provision.container_id
        original_onion_address = self.provision.onion_address

        with self.mock_ssh():
            with patch(
                    "paas.tasks._run_cmd",
                    side_effect=run_cmd_side_effect,
            ) as run_cmd:
                delete_container_by_id(self.provision.pk)

        # Datenbankeintrag muss weiterhin existieren
        provision = ProvisionedApp.objects.get(
            pk=self.provision.pk
        )

        # Status muss auf error gesetzt sein
        self.assertEqual(provision.status, "error")

        # Ursprüngliche Container-ID muss erhalten bleiben
        self.assertEqual(
            provision.container_id,
            original_container_id,
        )

        # Onion-Adresse muss erhalten bleiben
        self.assertEqual(
            provision.onion_address,
            original_onion_address,
        )

        # Fehler muss im Log dokumentiert sein
        self.assertIn(
            "KRITISCHER FEHLER",
            provision.log,
        )

        self.assertIn(
            "Permission denied",
            provision.log,
        )

        self.assertIn(
            "Rename failed",
            provision.log,
        )

        # Beide Dateioperationen müssen ausgeführt worden sein
        commands = [
            c.args[1] for c in run_cmd.call_args_list
        ]

        self.assertTrue(
            any(cmd.startswith("rm -rf ") for cmd in commands)
        )

        self.assertTrue(
            any(cmd.startswith("mv ") for cmd in commands)
        )

    # ---------------------------------------------------------
    # 9. Task ist idempotent
    # ---------------------------------------------------------

    @patch("paas.tasks._ssh_client")
    def test_second_execution_is_harmless(self, mock_ssh_client):
        """
        Ein zweiter Aufruf nach erfolgreicher Löschung
        darf keinen Fehler verursachen.
        """

        with self.mock_ssh():
            with patch(
                "paas.tasks._run_cmd",
                return_value=(0, "", ""),
            ):
                delete_container_by_id(self.provision.pk)

        mock_ssh_client.reset_mock()

        # Zweiter Aufruf: Datensatz existiert nicht mehr.
        delete_container_by_id(self.provision.pk)

        mock_ssh_client.assert_not_called()

    # ---------------------------------------------------------
    # 10. Zusätzliche Task-Argumente werden ignoriert
    # ---------------------------------------------------------

    @patch("paas.tasks._ssh_client")
    def test_extra_arguments_are_ignored(self, mock_ssh_client):
        """
        Der Task akzeptiert zusätzliche Positions- und
        Keyword-Argumente.
        """

        delete_container_by_id(
            999999,
            "additional",
            countdown=10,
        )

        mock_ssh_client.assert_not_called()

    def test_delete_and_rename_failure_keeps_provision(self):

        def run_cmd_side_effect(ssh, command):

            if command.startswith("rm -rf "):
                return (1, "", "Permission denied")

            if command.startswith("mv "):
                return (1, "", "Rename failed")

            return (0, "", "")

        with self.mock_ssh():
            with patch(
                    "paas.tasks._run_cmd",
                    side_effect=run_cmd_side_effect,
            ):
                delete_container_by_id(self.provision.pk)

        provision = ProvisionedApp.objects.get(
            pk=self.provision.pk
        )

        self.assertEqual(provision.status, "error")

        self.assertIsNotNone(provision.container_id)

        self.assertIsNotNone(provision.onion_address)

        self.assertIn(
            "KRITISCHER FEHLER",
            provision.log,
        )