'''
Test --> Erwartetes Verhalten
Erfolgreicher Deploy --> Status running, Container-ID und Ports werden gespeichert
Image-Auswahl --> Das übergebene selected_image wird verwendet und gespeichert
Container-Name --> Wird automatisch erzeugt, wenn er fehlt
Vorhandener Container-Name --> Wird nicht überschrieben
Environment-Variablen --> DB-Defaults werden übernommen
Environment-Variablen --> Benutzereingaben überschreiben Defaults
Onion-Adresse --> <onion_address> wird durch die tatsächliche Adresse ersetzt
Docker-Volumes --> Host- und Container-Pfade werden korrekt eingebunden
Deploy-User --> --user UID:GID wird nur bei entsprechender Konfiguration gesetzt
Host-PID --> --pid host wird nur bei entsprechender Konfiguration gesetzt
Ohne Hidden-Service --> Es wird keine Tor-Instanz eingerichtet
Cert-Authentifizierung --> Public Key wird hinterlegt
Fehlender Public Key --> Deployment schlägt fehl
Docker-Fehler --> Status wird auf error gesetzt und Fehler protokolliert
Keine Container-ID --> Deployment schlägt fehl
Patch-Fehler --> Container-Daten bleiben erhalten, Status wird error
Provision nicht vorhanden --> Fehler wird weitergegeben
'''


import os
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from paas.models import (
    AppDefinition,
    AppEnvVarPerApp,
    AppVolumePerApp,
    ProvisionedApp,
    RemoteHost,
)
from paas.tasks import deploy_app_task


class DeployAppTaskTestCase(TestCase):

    def setUp(self):
        User = get_user_model()

        self.user = User.objects.create_user(
            username="deploytest",
            password="test-password",
        )

        self.host = RemoteHost.objects.create(
            hostname="test-host",
            ip_address="192.0.2.10",
            ssh_user="deploy",
        )

        self.app = AppDefinition.objects.create(
            name="test-app",
            display_name="Test App",
            docker_image="example/test-app",
            command="",
            app_port_intern_web=8080,
            app_port_intern_api=1,
            app_port_intern_api_2=1,
            hiddenservice_port_web=80,
            no_hidden_service=True,
        )

        self.provision = ProvisionedApp.objects.create(
            user=self.user,
            app=self.app,
            host=self.host,
            status="pending",
        )

        # Mock-SSH-Verbindung
        self.ssh = MagicMock()

        self.sftp = MagicMock()
        self.ssh.open_sftp.return_value = self.sftp

        # Hilfsfunktionen für die erfolgreiche Bereitstellung
        self.patches = [
            patch(
                "paas.tasks._ssh_client"
            ),
            patch(
                "paas.tasks._reserve_ports",
                return_value=(18080, 18081, 18082),
            ),
            patch(
                "paas.tasks._run_cmd",
                side_effect=self.mock_run_cmd,
            ),
            patch(
                "paas.tasks._get_user_id_uid",
                return_value=1000,
            ),
            patch(
                "paas.tasks._get_user_id_gid",
                return_value=1000,
            ),
            patch(
                "paas.tasks._apply_patches",
            ),
        ]

        self.mocks = {}

        for p in self.patches:
            self.mocks[p.attribute] = p.start()

        self.mocks["_ssh_client"].return_value.__enter__.return_value = self.ssh

        self.addCleanup(patch.stopall)

    def mock_run_cmd(self, ssh, command):
        """
        Simuliert die Remote-Kommandos.
        """

        if command.startswith("docker run"):
            return 0, "", ""

        if command.startswith("docker ps"):
            return 0, "container123456", ""

        return 0, "", ""

    def reload_provision(self):
        self.provision.refresh_from_db()
        return self.provision

    # ---------------------------------------------------------
    # Erfolgreiches Deployment
    # ---------------------------------------------------------

    def test_successful_deployment(self):
        """
        Ein erfolgreicher Deploy speichert alle Basisdaten.
        """

        result = deploy_app_task.run(
            self.provision.id,
            "example/test-app:1.0",
        )

        self.assertEqual(result, self.provision.id)

        provision = self.reload_provision()

        self.assertEqual(provision.status, "running")
        self.assertEqual(provision.container_id, "container123456")
        self.assertEqual(provision.port, 18080)
        self.assertEqual(provision.port2, 18081)
        self.assertEqual(provision.port3, 18082)
        self.assertEqual(provision.image, "example/test-app:1.0")

        self.assertIsNotNone(provision.container_name)

        self.assertIn(
            "container123456",
            provision.log,
        )

        self.assertIn(
            "docker run",
            provision.docker_run_cmd,
        )

    def test_container_name_is_generated(self):
        """
        Wenn kein Container-Name existiert, wird einer erzeugt.
        """

        deploy_app_task.run(
            self.provision.id,
            "example/test-app:latest",
        )

        provision = self.reload_provision()

        self.assertTrue(provision.container_name)

        self.assertIn(
            self.user.username,
            provision.container_name,
        )

        self.assertIn(
            self.app.name,
            provision.container_name,
        )

    def test_existing_container_name_is_preserved(self):
        """
        Ein bereits vorhandener Container-Name bleibt erhalten.
        """

        self.provision.container_name = "my-existing-container"
        self.provision.save()

        deploy_app_task.run(
            self.provision.id,
            "example/test-app:latest",
        )

        provision = self.reload_provision()

        self.assertEqual(
            provision.container_name,
            "my-existing-container",
        )

    # ---------------------------------------------------------
    # Environment-Variablen
    # ---------------------------------------------------------

    def test_default_environment_variables_are_used(self):
        """
        DB-Defaults werden als Docker-Environment-Variablen verwendet.
        """

        AppEnvVarPerApp.objects.create(
            app=self.app,
            key="APP_MODE",
            value="production",
        )

        deploy_app_task.run(
            self.provision.id,
            "example/test-app:latest",
        )

        docker_cmd = self.reload_provision().docker_run_cmd

        self.assertIn(
            "-e APP_MODE=production",
            docker_cmd,
        )

    def test_user_environment_variables_override_defaults(self):
        """
        Benutzerwerte überschreiben die DB-Defaults.
        """

        AppEnvVarPerApp.objects.create(
            app=self.app,
            key="APP_MODE",
            value="production",
        )

        deploy_app_task.run(
            self.provision.id,
            "example/test-app:latest",
            env_vars={
                "APP_MODE": "development",
            },
        )

        docker_cmd = self.reload_provision().docker_run_cmd

        self.assertIn(
            "-e APP_MODE=development",
            docker_cmd,
        )

        self.assertNotIn(
            "-e APP_MODE=production",
            docker_cmd,
        )

    def test_unknown_user_environment_variables_are_ignored(self):
        """
        Nicht in AppEnvVarPerApp definierte Variablen werden ignoriert.
        """

        deploy_app_task.run(
            self.provision.id,
            "example/test-app:latest",
            env_vars={
                "UNKNOWN_VARIABLE": "test",
            },
        )

        docker_cmd = self.reload_provision().docker_run_cmd

        self.assertNotIn(
            "UNKNOWN_VARIABLE",
            docker_cmd,
        )

    def test_onion_address_is_replaced_in_environment_variables(self):
        """
        Der Platzhalter <onion_address> wird durch die tatsächliche
        Onion-Adresse ersetzt und an Docker übergeben.
        """

        onion_address = "abcdefghijklmnop.onion"

        # Hidden-Service aktivieren
        self.app.no_hidden_service = False
        self.app.save()

        # Environment-Variable mit Onion-Platzhalter
        AppEnvVarPerApp.objects.create(
            app=self.app,
            key="APP_URL",
            value="http://<onion_address>",
        )

        # Tor-Hilfsfunktionen simulieren
        with patch(
                "paas.tasks._is_port_in_use",
                return_value=False,
        ), patch(
            "paas.tasks._build_torrc",
            return_value="# Test torrc",
        ), patch(
            "paas.tasks._write_systemd_unit",
        ), patch(
            "paas.tasks._wait_for_file",
            return_value=True,
        ), patch(
            "paas.tasks._run_cmd",
            side_effect=lambda ssh, command: (
                    (0, onion_address, "")
                    if command.startswith("cat ")
                    else self.mock_run_cmd(ssh, command)
            ),
        ):
            result = deploy_app_task.run(
                self.provision.id,
                "example/test-app:latest",
            )

        # Rückgabewert überprüfen
        self.assertEqual(result, self.provision.id)

        provision = self.reload_provision()

        # Onion-Adresse wurde gespeichert
        self.assertEqual(
            provision.onion_address,
            onion_address,
        )

        # Docker-Kommando enthält die ersetzte Adresse
        self.assertIn(
            f"-e APP_URL=http://{onion_address}",
            provision.docker_run_cmd,
        )

        # Platzhalter darf nicht mehr vorhanden sein
        self.assertNotIn(
            "<onion_address>",
            provision.docker_run_cmd,
        )

    # ---------------------------------------------------------
    # Docker-Konfiguration
    # ---------------------------------------------------------

    def test_selected_image_is_used(self):
        """
        Es wird das ausgewählte Image und nicht das Standard-Image verwendet.
        """

        deploy_app_task.run(
            self.provision.id,
            "registry.example.com/custom/image:2.0",
        )

        provision = self.reload_provision()

        self.assertEqual(
            provision.image,
            "registry.example.com/custom/image:2.0",
        )

        self.assertIn(
            "registry.example.com/custom/image:2.0",
            provision.docker_run_cmd,
        )

    def test_custom_command_is_added_to_docker_command(self):
        """
        Ein definierter Startbefehl wird an docker run angehängt.
        """

        self.app.command = "python manage.py runserver"
        self.app.save()

        deploy_app_task.run(
            self.provision.id,
            "example/test-app:latest",
        )

        docker_cmd = self.reload_provision().docker_run_cmd

        self.assertTrue(
            docker_cmd.endswith("python manage.py runserver")
        )

    def test_deploy_user_is_added_when_enabled(self):
        """
        --user UID:GID wird bei use_deploy_user=True gesetzt.
        """

        self.app.use_deploy_user = True
        self.app.save()

        deploy_app_task.run(
            self.provision.id,
            "example/test-app:latest",
        )

        docker_cmd = self.reload_provision().docker_run_cmd

        self.assertIn(
            "--user 1000:1000",
            docker_cmd,
        )

    def test_deploy_user_is_not_added_when_disabled(self):
        """
        Ohne use_deploy_user wird kein --user-Flag gesetzt.
        """

        self.app.use_deploy_user = False
        self.app.save()

        deploy_app_task.run(
            self.provision.id,
            "example/test-app:latest",
        )

        docker_cmd = self.reload_provision().docker_run_cmd

        self.assertNotIn(
            "--user",
            docker_cmd,
        )

    def test_host_pid_namespace_is_enabled(self):
        """
        use_hostpid_namespace=True aktiviert --pid host.
        """

        self.app.use_hostpid_namespace = True
        self.app.save()

        deploy_app_task.run(
            self.provision.id,
            "example/test-app:latest",
        )

        docker_cmd = self.reload_provision().docker_run_cmd

        self.assertIn(
            "--pid host",
            docker_cmd,
        )

    def test_host_pid_namespace_is_disabled(self):
        """
        Ohne use_hostpid_namespace wird --pid host nicht gesetzt.
        """

        self.app.use_hostpid_namespace = False
        self.app.save()

        deploy_app_task.run(
            self.provision.id,
            "example/test-app:latest",
        )

        docker_cmd = self.reload_provision().docker_run_cmd

        self.assertNotIn(
            "--pid host",
            docker_cmd,
        )

    # ---------------------------------------------------------
    # Docker-Volumes
    # ---------------------------------------------------------

    def test_volume_is_added_to_docker_command(self):
        """
        Definierte Volumes werden korrekt eingebunden.
        """

        AppVolumePerApp.objects.create(
            app=self.app,
            host_path="data",
            container_path="/app/data",
        )

        deploy_app_task.run(
            self.provision.id,
            "example/test-app:latest",
        )

        provision = self.reload_provision()

        expected_path = os.path.join(
            f"/home/{self.host.ssh_user}/{provision.container_name}/",
            "data",
        )

        self.assertIn(
            f"-v {expected_path}:/app/data",
            provision.docker_run_cmd,
        )

        self.mocks["_run_cmd"].assert_any_call(
            self.ssh,
            f"mkdir -p {expected_path}",
        )

    # ---------------------------------------------------------
    # Hidden Service
    # ---------------------------------------------------------

    def test_no_hidden_service_does_not_start_tor(self):
        """
        Bei no_hidden_service=True werden keine Tor-Dateien erzeugt.
        """

        self.app.no_hidden_service = True
        self.app.save()

        deploy_app_task.run(
            self.provision.id,
            "example/test-app:latest",
        )

        provision = self.reload_provision()

        self.assertEqual(
            provision.onion_address,
            "",
        )

        for call in self.mocks["_run_cmd"].call_args_list:
            command = call.args[1]

            self.assertNotIn(
                "tor",
                command.lower(),
            )

        self.mocks["_apply_patches"].assert_called_once()

    def test_missing_public_key_fails_cert_deployment(self):
        """
        Cert-Authentifizierung ohne Public Key muss fehlschlagen.
        """

        self.app.no_hidden_service = False
        self.app.save()

        with patch(
            "paas.tasks._reserve_ports",
            return_value=(18080, 18081, 18082),
        ):
            with patch(
                "paas.tasks._is_port_in_use",
                return_value=False,
            ):
                with self.assertRaisesRegex(
                    RuntimeError,
                    "Public key fehlt",
                ):
                    deploy_app_task.run(
                        self.provision.id,
                        "example/test-app:latest",
                        tor_auth_type="cert",
                        tor_pub_key=None,
                    )

        provision = self.reload_provision()

        self.assertEqual(
            provision.status,
            "error",
        )

        self.assertIn(
            "Public key fehlt",
            provision.log,
        )

    # ---------------------------------------------------------
    # Fehlerbehandlung
    # ---------------------------------------------------------

    def test_docker_run_failure_sets_error_status(self):
        """
        Ein fehlgeschlagenes docker run setzt den Status auf error.
        """

        self.mocks["_run_cmd"].side_effect = [
            (0, "", ""),
            (1, "", "Docker daemon error"),
        ]

        with self.assertRaisesRegex(
            RuntimeError,
            "Kein Container-ID zurückgegeben",
        ):
            deploy_app_task.run(
                self.provision.id,
                "example/test-app:latest",
            )

        provision = self.reload_provision()

        self.assertEqual(
            provision.status,
            "error",
        )

        self.assertIn(
            "Kein Container-ID zurückgegeben",
            provision.log,
        )

    def test_missing_container_id_sets_error_status(self):
        """
        Wenn docker ps keine ID liefert, schlägt der Task fehl.
        """

        def mock_run_cmd(ssh, command):
            if command.startswith("docker run"):
                return 0, "", ""

            if command.startswith("docker ps"):
                return 0, "", ""

            return 0, "", ""

        self.mocks["_run_cmd"].side_effect = mock_run_cmd

        with self.assertRaisesRegex(
                RuntimeError,
                "Kein Container-ID",
        ):
            deploy_app_task.run(
                self.provision.id,
                "example/test-app:latest",
            )

        provision = self.reload_provision()

        self.assertEqual(provision.status, "error")

        self.assertIn(
            "Kein Container-ID",
            provision.log,
        )

    def test_patch_failure_keeps_container_data(self):
        """
        Bei Patch-Fehlern bleiben Container-ID und Ports erhalten.
        """

        self.mocks["_apply_patches"].side_effect = RuntimeError(
            "Patch konnte nicht angewendet werden"
        )

        with self.assertRaisesRegex(
            RuntimeError,
            "Patch konnte nicht angewendet werden",
        ):
            deploy_app_task.run(
                self.provision.id,
                "example/test-app:latest",
            )

        provision = self.reload_provision()

        self.assertEqual(
            provision.status,
            "error",
        )

        self.assertEqual(
            provision.container_id,
            "container123456",
        )

        self.assertEqual(
            provision.port,
            18080,
        )

        self.assertIn(
            "Patch-Fehler",
            provision.log,
        )

        self.assertEqual(
            provision.image,
            "example/test-app:latest",
        )

    def test_non_existing_provision_raises_exception(self):
        """
        Eine unbekannte Provision-ID wird nicht verschluckt.
        """

        with self.assertRaises(ProvisionedApp.DoesNotExist):
            deploy_app_task.run(
                999999,
                "example/test-app:latest",
            )
