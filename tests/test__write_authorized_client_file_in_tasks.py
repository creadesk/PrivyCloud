'''
Verzeichnis authorized_clients wird korrekt angelegt.
Verzeichnisberechtigungen werden auf 700 gesetzt.
Die Datei client.auth wird im richtigen Verzeichnis angelegt.
Der Dateiinhalt enthält exakt descriptor:x25519:<pub_b32>\n.
Der korrekte Dateipfad wird zurückgegeben.
_run_cmd()-Fehler werden weitergereicht.
Fehler beim Öffnen oder Schreiben der Datei werden weitergereicht.
Unterschiedliche Hidden-Service-Verzeichnisse und Public Keys funktionieren.
'''


from unittest import TestCase
from unittest.mock import MagicMock, patch

from paas.tasks import _write_authorized_client_file


class WriteAuthorizedClientFileTests(TestCase):

    def setUp(self):
        self.hidden_dir = "/var/lib/tor/my_hidden_service"
        self.pub_b32 = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"

        self.expected_auth_dir = (
            f"{self.hidden_dir}/authorized_clients"
        )
        self.expected_auth_path = (
            f"{self.expected_auth_dir}/client.auth"
        )
        self.expected_content = (
            f"descriptor:x25519:{self.pub_b32}\n"
        )

        self.ssh = MagicMock()
        self.sftp = MagicMock()
        self.remote_file = MagicMock()

        self.ssh.open_sftp.return_value = self.sftp
        self.sftp.open.return_value.__enter__.return_value = (
            self.remote_file
        )

    @patch("paas.tasks._run_cmd")
    def test_creates_authorized_clients_directory(self, mock_run_cmd):
        """
        Das authorized_clients-Verzeichnis muss angelegt
        und mit chmod 700 geschützt werden.
        """
        _write_authorized_client_file(
            self.ssh,
            self.hidden_dir,
            self.pub_b32,
        )

        mock_run_cmd.assert_called_once_with(
            self.ssh,
            (
                f"mkdir -p {self.expected_auth_dir} "
                f"&& chmod 700 {self.expected_auth_dir}"
            ),
        )

    @patch("paas.tasks._run_cmd")
    def test_opens_file_with_correct_path(self, mock_run_cmd):
        """
        Die Datei muss unter dem erwarteten Pfad geöffnet werden.
        """
        _write_authorized_client_file(
            self.ssh,
            self.hidden_dir,
            self.pub_b32,
        )

        self.ssh.open_sftp.assert_called_once_with()

        self.sftp.open.assert_called_once_with(
            self.expected_auth_path,
            "w",
        )

    @patch("paas.tasks._run_cmd")
    def test_writes_correct_authorized_client_content(
        self,
        mock_run_cmd,
    ):
        """
        Der Dateiinhalt muss exakt dem Tor-Format entsprechen.
        """
        _write_authorized_client_file(
            self.ssh,
            self.hidden_dir,
            self.pub_b32,
        )

        self.remote_file.write.assert_called_once_with(
            self.expected_content
        )

    @patch("paas.tasks._run_cmd")
    def test_returns_authorized_client_file_path(
        self,
        mock_run_cmd,
    ):
        """
        Die Funktion muss den vollständigen Dateipfad zurückgeben.
        """
        result = _write_authorized_client_file(
            self.ssh,
            self.hidden_dir,
            self.pub_b32,
        )

        self.assertEqual(
            result,
            self.expected_auth_path,
        )

    @patch("paas.tasks._run_cmd")
    def test_uses_different_hidden_service_directories(
        self,
        mock_run_cmd,
    ):
        """
        Unterschiedliche Hidden-Service-Verzeichnisse
        müssen korrekt verarbeitet werden.
        """
        hidden_dir = "/srv/tor/another_service"

        result = _write_authorized_client_file(
            self.ssh,
            hidden_dir,
            self.pub_b32,
        )

        expected_path = (
            f"{hidden_dir}/authorized_clients/client.auth"
        )

        self.assertEqual(result, expected_path)

        mock_run_cmd.assert_called_once_with(
            self.ssh,
            (
                f"mkdir -p {hidden_dir}/authorized_clients "
                f"&& chmod 700 {hidden_dir}/authorized_clients"
            ),
        )

    @patch("paas.tasks._run_cmd")
    def test_uses_different_public_keys(self, mock_run_cmd):
        """
        Der übergebene Public Key muss unverändert
        in die Datei geschrieben werden.
        """
        another_pub_key = "MZXW6YTBOI======".rstrip("=")

        _write_authorized_client_file(
            self.ssh,
            self.hidden_dir,
            another_pub_key,
        )

        self.remote_file.write.assert_called_once_with(
            f"descriptor:x25519:{another_pub_key}\n"
        )

    @patch("paas.tasks._run_cmd")
    def test_propagates_directory_creation_error(
        self,
        mock_run_cmd,
    ):
        """
        Fehler beim Erstellen des Verzeichnisses
        müssen weitergereicht werden.
        """
        mock_run_cmd.side_effect = RuntimeError(
            "Directory creation failed"
        )

        with self.assertRaisesRegex(
            RuntimeError,
            "Directory creation failed",
        ):
            _write_authorized_client_file(
                self.ssh,
                self.hidden_dir,
                self.pub_b32,
            )

        # Bei einem Fehler darf keine Datei geöffnet werden.
        self.ssh.open_sftp.assert_not_called()

    @patch("paas.tasks._run_cmd")
    def test_propagates_sftp_open_error(self, mock_run_cmd):
        """
        Fehler beim Öffnen der Remote-Datei
        müssen weitergereicht werden.
        """
        self.sftp.open.side_effect = OSError(
            "SFTP open failed"
        )

        with self.assertRaisesRegex(
            OSError,
            "SFTP open failed",
        ):
            _write_authorized_client_file(
                self.ssh,
                self.hidden_dir,
                self.pub_b32,
            )

    @patch("paas.tasks._run_cmd")
    def test_propagates_file_write_error(self, mock_run_cmd):
        """
        Fehler beim Schreiben der Remote-Datei
        müssen weitergereicht werden.
        """
        self.remote_file.write.side_effect = OSError(
            "SFTP write failed"
        )

        with self.assertRaisesRegex(
            OSError,
            "SFTP write failed",
        ):
            _write_authorized_client_file(
                self.ssh,
                self.hidden_dir,
                self.pub_b32,
            )


