'''
Erfolgreiches Update:
    Provision wird geladen.
    Docker-Image wird korrekt ersetzt.
    Container wird gestoppt und entfernt.
    Neuer Container wird gestartet.
    Modell wird korrekt aktualisiert.
    Task liefert True zurück.

Es wird nur eine SSH-Session verwendet.
Die Docker-Kommandos werden in der richtigen Reihenfolge ausgeführt.
Fehler beim Stoppen führen zu einem Celery-Retry.
Fehler beim Entfernen führen zu einem Celery-Retry.
Fehler beim Starten führen zu einem Celery-Retry.
Eine nicht vorhandene Provision führt ebenfalls zu einem Retry.
Bei einem Fehler werden die Modelldaten nicht aktualisiert.
'''

from unittest.mock import MagicMock, patch, call

from django.contrib.auth import get_user_model
from django.test import TestCase

from paas.models import (
    AppDefinition,
    RemoteHost,
    ProvisionedApp,
)
from paas.tasks import update_app_task


User = get_user_model()


class UpdateAppTaskTests(TestCase):

    def setUp(self):
        self.user = User.objects.create_user(
            username="testuser",
            password="testpassword",
        )

        self.app = AppDefinition.objects.create(
            name="test-app",
            display_name="Test App",
            docker_image="example/test-app",
        )

        self.host = RemoteHost.objects.create(
            hostname="test-host.example.com",
            ip_address="192.0.2.10",
            ssh_user="root",
        )

        self.provision = ProvisionedApp.objects.create(
            user=self.user,
            app=self.app,
            host=self.host,
            container_id="old-container-id",
            container_name="test-container",
            status="running",
            docker_run_cmd=(
                "docker run -d "
                "--name test-container "
                "-p 8080:80 "
                "example/test-app:1.0"
            ),
            image="example/test-app:1.0",
        )

    # ---------------------------------------------------------
    # Erfolgreiches Update
    # ---------------------------------------------------------

    @patch("paas.tasks._run_cmd")
    @patch("paas.tasks._ssh_client")
    def test_successful_update(self, mock_ssh_client, mock_run_cmd):
        """
        Ein erfolgreiches Update muss:
        - alten Container stoppen
        - alten Container entfernen
        - neuen Container starten
        - Datenbank aktualisieren
        - True zurückgeben
        """

        ssh = MagicMock()

        ssh_context = MagicMock()
        ssh_context.__enter__.return_value = ssh
        ssh_context.__exit__.return_value = False

        mock_ssh_client.return_value = ssh_context

        mock_run_cmd.side_effect = [
            (0, "old-container-id", ""),  # docker inspect
            (0, "test-container", ""),  # docker stop
            (0, "test-container", ""),  # docker rm
            (0, "new-container-id", ""),  # docker run
        ]

        result = update_app_task.run(
            self.provision.pk,
            "example/test-app:2.0",
        )

        self.assertTrue(result)

        self.provision.refresh_from_db()

        self.assertEqual(
            self.provision.docker_run_cmd,
            (
                "docker run -d "
                "--name test-container "
                "-p 8080:80 "
                "example/test-app:2.0"
            ),
        )

        self.assertEqual(
            self.provision.image,
            "example/test-app:2.0",
        )

        self.assertEqual(
            self.provision.container_id,
            "new-container-id",
        )

        self.assertEqual(mock_run_cmd.call_count, 4)

        self.assertEqual(
            mock_run_cmd.call_args_list,
            [
                call(ssh, "docker inspect --type container old-container-id"),
                call(ssh, "docker stop old-container-id"),
                call(ssh, "docker rm old-container-id"),
                call(
                    ssh,
                    (
                        "docker run -d "
                        "--name test-container "
                        "-p 8080:80 "
                        "example/test-app:2.0"
                    ),
                ),
            ],
        )

        mock_ssh_client.assert_called_once_with(self.host)

    # ---------------------------------------------------------
    # Nur eine SSH-Session
    # ---------------------------------------------------------

    @patch("paas.tasks._run_cmd")
    @patch("paas.tasks._ssh_client")
    def test_only_one_ssh_session_is_used(
        self,
        mock_ssh_client,
        mock_run_cmd,
    ):
        ssh = MagicMock()

        ssh_context = MagicMock()
        ssh_context.__enter__.return_value = ssh
        ssh_context.__exit__.return_value = False

        mock_ssh_client.return_value = ssh_context

        mock_run_cmd.side_effect = [
            (0, "old-container-id", ""),
            (0, "stopped", ""),
            (0, "removed", ""),
            (0, "new-container-id", ""),
        ]

        update_app_task.run(
            self.provision.pk,
            "example/test-app:2.0",
        )

        mock_ssh_client.assert_called_once()
        ssh_context.__enter__.assert_called_once()
        ssh_context.__exit__.assert_called_once()

    # ---------------------------------------------------------
    # Docker-Image korrekt ersetzen
    # ---------------------------------------------------------

    @patch("paas.tasks._run_cmd")
    @patch("paas.tasks._ssh_client")
    def test_only_last_image_token_is_replaced(
        self,
        mock_ssh_client,
        mock_run_cmd,
    ):
        """
        Alle Argumente des Docker-Run-Befehls müssen erhalten bleiben.
        Nur das letzte Token (das Image) wird ersetzt.
        """

        self.provision.docker_run_cmd = (
            "docker run -d "
            "--restart unless-stopped "
            "--name my-app "
            "-e TEST=123 "
            "registry.example.com/my-app:1.0"
        )
        self.provision.save()

        ssh = MagicMock()

        ssh_context = MagicMock()
        ssh_context.__enter__.return_value = ssh
        ssh_context.__exit__.return_value = False

        mock_ssh_client.return_value = ssh_context

        mock_run_cmd.side_effect = [
            (0, "old-container-id", ""),
            (0, "", ""),
            (0, "", ""),
            (0, "new-container-id", ""),
        ]

        update_app_task.run(
            self.provision.pk,
            "registry.example.com/my-app:2.0",
        )

        self.provision.refresh_from_db()

        expected_cmd = (
            "docker run -d "
            "--restart unless-stopped "
            "--name my-app "
            "-e TEST=123 "
            "registry.example.com/my-app:2.0"
        )

        self.assertEqual(
            self.provision.docker_run_cmd,
            expected_cmd,
        )

        self.assertEqual(
            mock_run_cmd.call_args_list[3],
            call(ssh, expected_cmd),
        )

    # ---------------------------------------------------------
    # Retry bei Fehlern
    # ---------------------------------------------------------

    @patch("paas.tasks._run_cmd")
    @patch("paas.tasks._ssh_client")
    def test_retry_when_docker_stop_fails(
        self,
        mock_ssh_client,
        mock_run_cmd,
    ):
        """
        Ein Fehler beim Stoppen muss einen Celery-Retry auslösen.
        """

        ssh = MagicMock()

        ssh_context = MagicMock()
        ssh_context.__enter__.return_value = ssh
        ssh_context.__exit__.return_value = False

        mock_ssh_client.return_value = ssh_context

        error = RuntimeError("Docker stop failed")

        mock_run_cmd.side_effect = error

        with patch.object(
            update_app_task,
            "retry",
            side_effect=Exception("Retry triggered"),
        ) as mock_retry:

            with self.assertRaisesRegex(
                Exception,
                "Retry triggered",
            ):
                update_app_task.run(
                    self.provision.pk,
                    "example/test-app:2.0",
                )

        mock_retry.assert_called_once()

        self.assertEqual(
            mock_retry.call_args.kwargs["countdown"],
            60,
        )

        self.provision.refresh_from_db()

        self.assertEqual(
            self.provision.container_id,
            "old-container-id",
        )

    @patch("paas.tasks._run_cmd")
    @patch("paas.tasks._ssh_client")
    def test_retry_when_docker_remove_fails(
        self,
        mock_ssh_client,
        mock_run_cmd,
    ):
        ssh = MagicMock()

        ssh_context = MagicMock()
        ssh_context.__enter__.return_value = ssh
        ssh_context.__exit__.return_value = False

        mock_ssh_client.return_value = ssh_context

        mock_run_cmd.side_effect = [
            (None, "stopped", None),
            RuntimeError("Docker remove failed"),
        ]

        with patch.object(
            update_app_task,
            "retry",
            side_effect=Exception("Retry triggered"),
        ) as mock_retry:

            with self.assertRaisesRegex(
                Exception,
                "Retry triggered",
            ):
                update_app_task.run(
                    self.provision.pk,
                    "example/test-app:2.0",
                )

        mock_retry.assert_called_once()

        self.provision.refresh_from_db()

        self.assertEqual(
            self.provision.container_id,
            "old-container-id",
        )

    @patch("paas.tasks._run_cmd")
    @patch("paas.tasks._ssh_client")
    def test_retry_when_docker_start_fails(
        self,
        mock_ssh_client,
        mock_run_cmd,
    ):
        ssh = MagicMock()

        ssh_context = MagicMock()
        ssh_context.__enter__.return_value = ssh
        ssh_context.__exit__.return_value = False

        mock_ssh_client.return_value = ssh_context

        mock_run_cmd.side_effect = [
            (None, "stopped", None),
            (None, "removed", None),
            RuntimeError("Docker start failed"),
        ]

        with patch.object(
            update_app_task,
            "retry",
            side_effect=Exception("Retry triggered"),
        ) as mock_retry:

            with self.assertRaisesRegex(
                Exception,
                "Retry triggered",
            ):
                update_app_task.run(
                    self.provision.pk,
                    "example/test-app:2.0",
                )

        mock_retry.assert_called_once()

        self.provision.refresh_from_db()

        self.assertEqual(
            self.provision.container_id,
            "old-container-id",
        )

        self.assertEqual(
            self.provision.image,
            "example/test-app:1.0",
        )

    # ---------------------------------------------------------
    # Provision nicht vorhanden
    # ---------------------------------------------------------

    def test_retry_when_provision_does_not_exist(self):

        with patch.object(
            update_app_task,
            "retry",
            side_effect=Exception("Retry triggered"),
        ) as mock_retry:

            with self.assertRaisesRegex(
                Exception,
                "Retry triggered",
            ):
                update_app_task.run(
                    999999,
                    "example/test-app:2.0",
                )

        mock_retry.assert_called_once()

        self.assertEqual(
            mock_retry.call_args.kwargs["countdown"],
            60,
        )

    # ---------------------------------------------------------
    # Keine Datenbankänderung bei Fehler
    # ---------------------------------------------------------

    @patch("paas.tasks._run_cmd")
    @patch("paas.tasks._ssh_client")
    def test_database_is_not_updated_when_ssh_fails(
        self,
        mock_ssh_client,
        mock_run_cmd,
    ):

        mock_ssh_client.side_effect = RuntimeError(
            "SSH connection failed"
        )

        with patch.object(
            update_app_task,
            "retry",
            side_effect=Exception("Retry triggered"),
        ):

            with self.assertRaisesRegex(
                Exception,
                "Retry triggered",
            ):
                update_app_task.run(
                    self.provision.pk,
                    "example/test-app:2.0",
                )

        self.provision.refresh_from_db()

        self.assertEqual(
            self.provision.docker_run_cmd,
            (
                "docker run -d "
                "--name test-container "
                "-p 8080:80 "
                "example/test-app:1.0"
            ),
        )

        self.assertEqual(
            self.provision.image,
            "example/test-app:1.0",
        )

        self.assertEqual(
            self.provision.container_id,
            "old-container-id",
        )

        mock_run_cmd.assert_not_called()


    # ---------------------------------------------------------
    # Alter Container existiert nicht mehr
    # ---------------------------------------------------------

    @patch("paas.tasks._run_cmd")
    @patch("paas.tasks._ssh_client")
    def test_update_when_old_container_does_not_exist(
        self,
        mock_ssh_client,
        mock_run_cmd,
    ):
        ssh = MagicMock()

        ssh_context = MagicMock()
        ssh_context.__enter__.return_value = ssh
        ssh_context.__exit__.return_value = False

        mock_ssh_client.return_value = ssh_context

        mock_run_cmd.side_effect = [
            (1, "", "No such object"),  # inspect
            (0, "new-container-id", ""),  # docker run
        ]

        result = update_app_task.run(
            self.provision.pk,
            "example/test-app:2.0",
        )

        self.assertTrue(result)

        self.provision.refresh_from_db()

        self.assertEqual(
            self.provision.container_id,
            "new-container-id",
        )

        self.assertEqual(mock_run_cmd.call_count, 2)

        commands = [
            c.args[1]
            for c in mock_run_cmd.call_args_list
        ]

        self.assertTrue(commands[0].startswith("docker inspect"))
        self.assertTrue(commands[1].startswith("docker run"))

        self.assertFalse(
            any(cmd.startswith("docker stop") for cmd in commands)
        )

        self.assertFalse(
            any(cmd.startswith("docker rm") for cmd in commands)
        )

    # ---------------------------------------------------------
    # Docker Stop liefert einen Fehlercode
    # ---------------------------------------------------------

    @patch("paas.tasks._run_cmd")
    @patch("paas.tasks._ssh_client")
    def test_retry_when_docker_stop_returns_error_code(
        self,
        mock_ssh_client,
        mock_run_cmd,
    ):
        ssh = MagicMock()

        ssh_context = MagicMock()
        ssh_context.__enter__.return_value = ssh
        ssh_context.__exit__.return_value = False

        mock_ssh_client.return_value = ssh_context

        mock_run_cmd.side_effect = [
            (0, "old-container-id", ""),  # inspect
            (1, "", "Stop failed"),       # stop
        ]

        with patch.object(
            update_app_task,
            "retry",
            side_effect=Exception("Retry triggered"),
        ) as mock_retry:

            with self.assertRaisesRegex(
                Exception,
                "Retry triggered",
            ):
                update_app_task.run(
                    self.provision.pk,
                    "example/test-app:2.0",
                )

        mock_retry.assert_called_once()

        # Remove und Start dürfen nicht ausgeführt werden.
        self.assertEqual(mock_run_cmd.call_count, 2)

        self.provision.refresh_from_db()

        self.assertEqual(
            self.provision.container_id,
            "old-container-id",
        )

    # ---------------------------------------------------------
    # Docker Run liefert einen Fehlercode
    # ---------------------------------------------------------

    @patch("paas.tasks._run_cmd")
    @patch("paas.tasks._ssh_client")
    def test_retry_when_docker_run_returns_error_code(
        self,
        mock_ssh_client,
        mock_run_cmd,
    ):
        ssh = MagicMock()

        ssh_context = MagicMock()
        ssh_context.__enter__.return_value = ssh
        ssh_context.__exit__.return_value = False

        mock_ssh_client.return_value = ssh_context

        mock_run_cmd.side_effect = [
            (0, "old-container-id", ""),  # inspect
            (0, "old-container-id", ""),  # stop
            (0, "old-container-id", ""),  # remove
            (1, "", "Docker run failed"),  # run
        ]

        with patch.object(
            update_app_task,
            "retry",
            side_effect=Exception("Retry triggered"),
        ) as mock_retry:

            with self.assertRaisesRegex(
                Exception,
                "Retry triggered",
            ):
                update_app_task.run(
                    self.provision.pk,
                    "example/test-app:2.0",
                )

        mock_retry.assert_called_once()

        self.provision.refresh_from_db()

        self.assertEqual(
            self.provision.container_id,
            "old-container-id",
        )

        self.assertEqual(
            self.provision.image,
            "example/test-app:1.0",
        )
