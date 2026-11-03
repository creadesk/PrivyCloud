'''
Die Funktion hat zwei unterschiedliche Ausführungspfade, die beide vollständig getestet werden sollten:
    LocalSSH: Rückgabe (exit_code, stdout, stderr).
    Paramiko: Rückgabe (stdin, stdout, stderr) mit channel.recv_exit_status().

Zusätzlich sollten folgende Sonderfälle abgedeckt werden:
    Bytes statt Strings bei LocalSSH.
    Entfernen von führenden und nachfolgenden Leerzeichen bzw. Zeilenumbrüchen.
    Leere Ausgaben.
    Fehlercodes ungleich 0.
    Sicherstellen, dass exec_command() genau einmal aufgerufen wird.
    Weiterreichen von Exceptions.
'''


from unittest import TestCase
from unittest.mock import MagicMock

from paas.tasks import _run_cmd


class TestRunCmd(TestCase):

    def setUp(self):
        self.cmd = "echo test"

    # ---------------------------------------------------------
    # LocalSSH: Rückgabe (exit_code, stdout, stderr)
    # ---------------------------------------------------------

    def test_local_ssh_returns_strings(self):
        """LocalSSH liefert bereits Strings zurück."""

        ssh = MagicMock()
        ssh.exec_command.return_value = (
            0,
            "hello world",
            "some warning",
        )

        result = _run_cmd(ssh, self.cmd)

        self.assertEqual(
            result,
            (0, "hello world", "some warning")
        )
        ssh.exec_command.assert_called_once_with(self.cmd)

    def test_local_ssh_strips_whitespace(self):
        """Ausgaben werden von Leerzeichen und Newlines bereinigt."""

        ssh = MagicMock()
        ssh.exec_command.return_value = (
            0,
            "  hello world  \n",
            "\n  warning  \n",
        )

        result = _run_cmd(ssh, self.cmd)

        self.assertEqual(
            result,
            (0, "hello world", "warning")
        )

    def test_local_ssh_decodes_bytes(self):
        """LocalSSH darf Bytes statt Strings zurückgeben."""

        ssh = MagicMock()
        ssh.exec_command.return_value = (
            0,
            b"hello world\n",
            b"some error\n",
        )

        result = _run_cmd(ssh, self.cmd)

        self.assertEqual(
            result,
            (0, "hello world", "some error")
        )

    def test_local_ssh_empty_output(self):
        """Leere Ausgaben werden korrekt verarbeitet."""

        ssh = MagicMock()
        ssh.exec_command.return_value = (
            0,
            "",
            "",
        )

        result = _run_cmd(ssh, self.cmd)

        self.assertEqual(result, (0, "", ""))

    def test_local_ssh_nonzero_exit_code(self):
        """Ein Fehlercode wird unverändert zurückgegeben."""

        ssh = MagicMock()
        ssh.exec_command.return_value = (
            127,
            "",
            "command not found",
        )

        result = _run_cmd(ssh, self.cmd)

        self.assertEqual(
            result,
            (127, "", "command not found")
        )

    # ---------------------------------------------------------
    # Paramiko: Rückgabe (stdin, stdout, stderr)
    # ---------------------------------------------------------

    def test_paramiko_returns_output(self):
        """Paramiko-typische Rückgabe wird korrekt verarbeitet."""

        ssh = MagicMock()

        stdin = MagicMock()
        stdout = MagicMock()
        stderr = MagicMock()

        stdout.channel.recv_exit_status.return_value = 0
        stdout.read.return_value = b"hello world\n"
        stderr.read.return_value = b""

        ssh.exec_command.return_value = (
            stdin,
            stdout,
            stderr,
        )

        result = _run_cmd(ssh, self.cmd)

        self.assertEqual(result, (0, "hello world", ""))

        stdout.channel.recv_exit_status.assert_called_once()
        stdout.read.assert_called_once()
        stderr.read.assert_called_once()

        ssh.exec_command.assert_called_once_with(self.cmd)

    def test_paramiko_nonzero_exit_code(self):
        """Paramiko-Fehlercodes werden korrekt übernommen."""

        ssh = MagicMock()

        stdout = MagicMock()
        stderr = MagicMock()

        stdout.channel.recv_exit_status.return_value = 1
        stdout.read.return_value = b""
        stderr.read.return_value = b"permission denied\n"

        ssh.exec_command.return_value = (
            MagicMock(),
            stdout,
            stderr,
        )

        result = _run_cmd(ssh, self.cmd)

        self.assertEqual(
            result,
            (1, "", "permission denied")
        )

    def test_paramiko_strips_whitespace(self):
        """Paramiko-Ausgaben werden bereinigt."""

        ssh = MagicMock()

        stdout = MagicMock()
        stderr = MagicMock()

        stdout.channel.recv_exit_status.return_value = 0
        stdout.read.return_value = b"  output  \n"
        stderr.read.return_value = b"  error  \n"

        ssh.exec_command.return_value = (
            MagicMock(),
            stdout,
            stderr,
        )

        result = _run_cmd(ssh, self.cmd)

        self.assertEqual(
            result,
            (0, "output", "error")
        )

    def test_paramiko_empty_output(self):
        """Leere Paramiko-Ausgaben werden korrekt verarbeitet."""

        ssh = MagicMock()

        stdout = MagicMock()
        stderr = MagicMock()

        stdout.channel.recv_exit_status.return_value = 0
        stdout.read.return_value = b""
        stderr.read.return_value = b""

        ssh.exec_command.return_value = (
            MagicMock(),
            stdout,
            stderr,
        )

        result = _run_cmd(ssh, self.cmd)

        self.assertEqual(result, (0, "", ""))

    # ---------------------------------------------------------
    # Allgemeine Fehlerbehandlung
    # ---------------------------------------------------------

    def test_exec_command_exception_is_propagated(self):
        """Exceptions von exec_command werden weitergereicht."""

        ssh = MagicMock()
        ssh.exec_command.side_effect = OSError("SSH connection failed")

        with self.assertRaises(OSError) as context:
            _run_cmd(ssh, self.cmd)

        self.assertEqual(
            str(context.exception),
            "SSH connection failed"
        )

        ssh.exec_command.assert_called_once_with(self.cmd)
