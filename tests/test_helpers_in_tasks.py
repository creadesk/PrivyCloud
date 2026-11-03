'''
_wait_for_file
Datei sofort vorhanden, Datei später vorhanden, Timeout, anderer SFTP-Fehler

_get_free_port
Erfolgreiche Port-Ermittlung, SSH-Fehler, ungültige Port-Ausgabe, Rückgabetyp

_is_port_in_use
Port belegt, Port frei, leere Ausgabe, ungültige Ausgabe

_write_systemd_unit
Schreiben der Unit, richtiger Benutzerpfad, Fehler beim Schreiben, Reihenfolge der systemd-Kommandos
'''

from django.test import SimpleTestCase
from unittest.mock import MagicMock, patch, call

from paas import tasks


class WaitForFileTests(SimpleTestCase):

    @patch("paas.tasks.time.sleep")
    @patch("paas.tasks.time.time")
    def test_file_exists_immediately(self, mock_time, mock_sleep):
        """Datei existiert sofort."""
        ssh = MagicMock()

        # Zeit konstant halten, damit der Timeout nicht erreicht wird.
        mock_time.return_value = 100

        result = tasks._wait_for_file(ssh, "/tmp/test.txt")

        self.assertTrue(result)
        ssh.open_sftp().stat.assert_called_once_with("/tmp/test.txt")
        mock_sleep.assert_not_called()

    @patch("paas.tasks.time.sleep")
    @patch("paas.tasks.time.time")
    def test_file_appears_after_retry(self, mock_time, mock_sleep):
        """Datei wird erst nach einem erneuten Versuch gefunden."""
        ssh = MagicMock()

        sftp = ssh.open_sftp()
        sftp.stat.side_effect = [
            FileNotFoundError,
            FileNotFoundError,
            None,
        ]

        mock_time.side_effect = [0, 0, 1, 2]

        result = tasks._wait_for_file(ssh, "/tmp/test.txt", timeout=10)

        self.assertTrue(result)
        self.assertEqual(sftp.stat.call_count, 3)
        self.assertEqual(mock_sleep.call_count, 2)

    @patch("paas.tasks.time.sleep")
    @patch("paas.tasks.time.time")
    def test_file_not_found_until_timeout(self, mock_time, mock_sleep):
        """Datei wird während des gesamten Timeouts nicht gefunden."""
        ssh = MagicMock()
        ssh.open_sftp().stat.side_effect = FileNotFoundError

        mock_time.side_effect = [0, 0, 1, 2, 3, 4]

        result = tasks._wait_for_file(ssh, "/tmp/missing.txt", timeout=3)

        self.assertFalse(result)

    def test_other_sftp_error_is_propagated(self):
        """Andere SSH/SFTP-Fehler werden nicht verschluckt."""
        ssh = MagicMock()
        ssh.open_sftp().stat.side_effect = PermissionError("Access denied")

        with self.assertRaises(PermissionError):
            tasks._wait_for_file(ssh, "/root/secret.txt")


class GetFreePortTests(SimpleTestCase):

    @patch("paas.tasks._run_cmd")
    def test_returns_free_port(self, mock_run_cmd):
        """Gibt den vom Remote-Host ermittelten Port zurück."""
        ssh = MagicMock()
        mock_run_cmd.return_value = (0, "49152", "")

        result = tasks._get_free_port(ssh)

        self.assertEqual(result, 49152)
        mock_run_cmd.assert_called_once()

        args = mock_run_cmd.call_args
        self.assertIs(args[0][0], ssh)
        self.assertIn("socket.socket()", args[0][1])
        self.assertIn("bind(('0.0.0.0',0))", args[0][1])

    @patch("paas.tasks._run_cmd")
    def test_raises_runtime_error_on_stderr(self, mock_run_cmd):
        """Ein Fehler auf stderr führt zu RuntimeError."""
        ssh = MagicMock()
        mock_run_cmd.return_value = (1, "", "Permission denied")

        with self.assertRaisesRegex(RuntimeError, "Permission denied"):
            tasks._get_free_port(ssh)

    @patch("paas.tasks._run_cmd")
    def test_raises_value_error_on_invalid_port(self, mock_run_cmd):
        """Ungültige Port-Ausgabe wird nicht akzeptiert."""
        ssh = MagicMock()
        mock_run_cmd.return_value = (0, "invalid", "")

        with self.assertRaises(ValueError):
            tasks._get_free_port(ssh)

    @patch("paas.tasks._run_cmd")
    def test_returns_integer_port(self, mock_run_cmd):
        """Port wird als Integer zurückgegeben, nicht als String."""
        ssh = MagicMock()
        mock_run_cmd.return_value = (0, "8080", "")

        result = tasks._get_free_port(ssh)

        self.assertIsInstance(result, int)
        self.assertEqual(result, 8080)


class IsPortInUseTests(SimpleTestCase):

    @patch("paas.tasks._run_cmd")
    def test_port_is_in_use(self, mock_run_cmd):
        """Port ist belegt."""
        ssh = MagicMock()
        mock_run_cmd.return_value = (0, "1", "")

        result = tasks._is_port_in_use(ssh, 9050)

        self.assertTrue(result)

        mock_run_cmd.assert_called_once_with(
            ssh,
            "ss -tlnp 2>/dev/null | grep -c :9050"
        )

    @patch("paas.tasks._run_cmd")
    def test_port_is_not_in_use(self, mock_run_cmd):
        """Port ist nicht belegt."""
        ssh = MagicMock()
        mock_run_cmd.return_value = (0, "0", "")

        result = tasks._is_port_in_use(ssh, 9050)

        self.assertFalse(result)

    @patch("paas.tasks._run_cmd")
    def test_empty_output_means_port_is_not_in_use(self, mock_run_cmd):
        """Leere Ausgabe wird als freier Port interpretiert."""
        ssh = MagicMock()
        mock_run_cmd.return_value = (1, "", "")

        result = tasks._is_port_in_use(ssh, 9050)

        self.assertFalse(result)

    @patch("paas.tasks._run_cmd")
    def test_invalid_output_raises_value_error(self, mock_run_cmd):
        """Ungültige Ausgabe führt zu ValueError."""
        ssh = MagicMock()
        mock_run_cmd.return_value = (0, "unknown", "")

        with self.assertRaises(ValueError):
            tasks._is_port_in_use(ssh, 9050)


class WriteSystemdUnitTests(SimpleTestCase):

    @patch("paas.tasks._run_cmd")
    def test_writes_systemd_unit_and_enables_service(self, mock_run_cmd):
        """Unit-Datei wird geschrieben und systemd wird aktualisiert."""
        ssh = MagicMock()
        ssh.get_transport().get_username.return_value = "deploy"

        mock_run_cmd.return_value = (0, "", "")

        unit_name = "myapp.service"
        unit_content = (
            "[Unit]\n"
            "Description=My Test Application\n\n"
            "[Service]\n"
            "ExecStart=/usr/bin/python3 /opt/myapp.py\n"
        )

        tasks._write_systemd_unit(ssh, unit_name, unit_content)

        unit_path = "/home/deploy/.config/systemd/user/myapp.service"

        ssh.get_transport().get_username.assert_called_once()

        mock_run_cmd.assert_any_call(
            ssh,
            "mkdir -p /home/deploy/.config/systemd/user/"
        )

        ssh.open_sftp.assert_called_once()

        sftp = ssh.open_sftp.return_value.__enter__.return_value
        sftp.open.assert_called_once_with(unit_path, "w")

        file_handle = sftp.open.return_value.__enter__.return_value
        file_handle.write.assert_called_once_with(unit_content)

        expected_commands = [
            "mkdir -p /home/deploy/.config/systemd/user/",
            "systemctl --user daemon-reload",
            "systemctl --user enable myapp.service",
            "systemctl --user start myapp.service",
        ]

        actual_commands = [
            c.args[1] for c in mock_run_cmd.call_args_list
        ]

        self.assertEqual(actual_commands, expected_commands)

    @patch("paas.tasks._run_cmd")
    def test_uses_correct_username_for_unit_directory(self, mock_run_cmd):
        """Unit-Verzeichnis wird anhand des SSH-Benutzers gebildet."""
        ssh = MagicMock()
        ssh.get_transport().get_username.return_value = "myuser"

        mock_run_cmd.return_value = (0, "", "")

        tasks._write_systemd_unit(
            ssh,
            "test.service",
            "test content"
        )

        mock_run_cmd.assert_any_call(
            ssh,
            "mkdir -p /home/myuser/.config/systemd/user/"
        )

    @patch("paas.tasks._run_cmd")
    def test_propagates_sftp_write_error(self, mock_run_cmd):
        """Fehler beim Schreiben der Datei wird weitergegeben."""
        ssh = MagicMock()
        ssh.get_transport().get_username.return_value = "deploy"

        mock_run_cmd.return_value = (0, "", "")

        file_handle = (
            ssh.open_sftp.return_value
            .__enter__.return_value
            .open.return_value
            .__enter__.return_value
        )

        file_handle.write.side_effect = IOError("Disk full")

        with self.assertRaisesRegex(IOError, "Disk full"):
            tasks._write_systemd_unit(
                ssh,
                "test.service",
                "test content"
            )

        # Nach dem Schreibfehler darf systemd nicht aktualisiert werden.
        self.assertEqual(mock_run_cmd.call_count, 1)

    @patch("paas.tasks._run_cmd")
    def test_systemd_commands_are_executed_in_correct_order(self, mock_run_cmd):
        """systemd wird erst nach dem Schreiben der Datei aktualisiert."""
        ssh = MagicMock()
        ssh.get_transport().get_username.return_value = "deploy"

        mock_run_cmd.return_value = (0, "", "")

        tasks._write_systemd_unit(
            ssh,
            "test.service",
            "[Service]\nExecStart=/bin/true\n"
        )

        commands = [
            c.args[1] for c in mock_run_cmd.call_args_list
        ]

        self.assertEqual(
            commands,
            [
                "mkdir -p /home/deploy/.config/systemd/user/",
                "systemctl --user daemon-reload",
                "systemctl --user enable test.service",
                "systemctl --user start test.service",
            ]
        )


class RunCmdCheckedTests(SimpleTestCase):

    @patch("paas.tasks._run_cmd")
    def test_successful_command(self, mock_run_cmd):
        ssh = MagicMock()
        mock_run_cmd.return_value = (0, "OK", "")

        result = tasks._run_cmd_checked(ssh, "test-command")

        self.assertEqual(result, (0, "OK", ""))

    @patch("paas.tasks._run_cmd")
    def test_failed_command_raises_runtime_error(self, mock_run_cmd):
        ssh = MagicMock()
        mock_run_cmd.return_value = (
            1,
            "",
            "Unit not found"
        )

        with self.assertRaises(RuntimeError) as context:
            tasks._run_cmd_checked(ssh, "systemctl start test.service")

        self.assertIn("Unit not found", str(context.exception))
        self.assertIn("Exit-Code 1", str(context.exception))

    @patch("paas.tasks._run_cmd")
    def test_systemd_start_failure_is_detected(self, mock_run_cmd):
        ssh = MagicMock()
        ssh.get_transport().get_username.return_value = "deploy"

        # mkdir und daemon-reload und enable erfolgreich,
        # start schlägt fehl.
        mock_run_cmd.side_effect = [
            (0, "", ""),
            (0, "", ""),
            (0, "", ""),
            (1, "", "Failed to start test.service"),
        ]

        with self.assertRaisesRegex(
                RuntimeError,
                "Failed to start test.service"
        ):
            tasks._write_systemd_unit(
                ssh,
                "test.service",
                "[Service]\nExecStart=/bin/true\n"
            )

        self.assertEqual(mock_run_cmd.call_count, 4)