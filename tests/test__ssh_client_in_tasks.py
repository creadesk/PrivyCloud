'''
Test --> Erwartetes Verhalten
Config-Benutzer und mehrere Keys --> Config-Benutzer und erster Key werden verwendet
Benutzer-Fallback --> host.ssh_user wird verwendet
Kein IdentityFile --> key_filename=None
Leere Key-Liste --> key_filename=None
Exception im with-Block --> Verbindung wird geschlossen
SSH-Verbindungsfehler --> Verbindung wird geschlossen
SSH-Config fehlt --> Fehler wird weitergegeben, kein Client wird erzeugt
'''

from unittest.mock import MagicMock, patch, call

from django.test import SimpleTestCase

from paas.tasks import _ssh_client

""" Tests für den SSH-Context-Manager _ssh_client()."""
class SSHClientTests(SimpleTestCase):
    """Es werden keine echten SSH - Verbindungen aufgebaut. Paramiko und load_ssh_config werden vollständig gemockt. """

    def setUp(self):
        self.host = MagicMock()
        self.host.hostname = "example.com"
        self.host.ssh_user = "fallback_user"

        self.client = MagicMock()

    @patch("paas.tasks.paramiko.AutoAddPolicy")
    @patch("paas.tasks.paramiko.SSHClient")
    @patch("paas.tasks.load_ssh_config")
    def test_ssh_client_uses_config_user_and_first_key(
        self,
        mock_load_config,
        mock_ssh_client,
        mock_auto_add_policy,
    ):
        mock_load_config.return_value = {
            "user": "config_user",
            "identityfile": [
                "/home/test/.ssh/deploy_key",
                "/home/test/.ssh/second_key",
            ],
        }

        mock_ssh_client.return_value = self.client
        policy = MagicMock()
        mock_auto_add_policy.return_value = policy

        with _ssh_client(self.host) as ssh:
            self.assertIs(ssh, self.client)

        mock_load_config.assert_called_once_with("example.com")
        mock_ssh_client.assert_called_once_with()

        self.client.set_missing_host_key_policy.assert_called_once_with(
            policy
        )

        self.client.connect.assert_called_once_with(
            hostname="example.com",
            username="config_user",
            key_filename="/home/test/.ssh/deploy_key",
            allow_agent=False,
            look_for_keys=False,
            timeout=10,
        )

        self.client.close.assert_called_once_with()

    @patch("paas.tasks.paramiko.AutoAddPolicy")
    @patch("paas.tasks.paramiko.SSHClient")
    @patch("paas.tasks.load_ssh_config")
    def test_ssh_client_uses_model_user_as_fallback(
        self,
        mock_load_config,
        mock_ssh_client,
        mock_auto_add_policy,
    ):
        mock_load_config.return_value = {
            "identityfile": ["/home/test/.ssh/id_rsa"],
        }
        mock_ssh_client.return_value = self.client

        with _ssh_client(self.host):
            pass

        self.client.connect.assert_called_once_with(
            hostname="example.com",
            username="fallback_user",
            key_filename="/home/test/.ssh/id_rsa",
            allow_agent=False,
            look_for_keys=False,
            timeout=10,
        )

        self.client.close.assert_called_once_with()

    @patch("paas.tasks.paramiko.AutoAddPolicy")
    @patch("paas.tasks.paramiko.SSHClient")
    @patch("paas.tasks.load_ssh_config")
    def test_ssh_client_without_identity_file(
        self,
        mock_load_config,
        mock_ssh_client,
        mock_auto_add_policy,
    ):
        mock_load_config.return_value = {
            "user": "config_user",
        }
        mock_ssh_client.return_value = self.client

        with _ssh_client(self.host):
            pass

        self.client.connect.assert_called_once_with(
            hostname="example.com",
            username="config_user",
            key_filename=None,
            allow_agent=False,
            look_for_keys=False,
            timeout=10,
        )

        self.client.close.assert_called_once_with()

    @patch("paas.tasks.paramiko.AutoAddPolicy")
    @patch("paas.tasks.paramiko.SSHClient")
    @patch("paas.tasks.load_ssh_config")
    def test_ssh_client_with_empty_identity_file_list(
        self,
        mock_load_config,
        mock_ssh_client,
        mock_auto_add_policy,
    ):
        mock_load_config.return_value = {
            "user": "config_user",
            "identityfile": [],
        }
        mock_ssh_client.return_value = self.client

        with _ssh_client(self.host):
            pass

        self.assertIsNone(
            self.client.connect.call_args.kwargs["key_filename"]
        )

    @patch("paas.tasks.paramiko.AutoAddPolicy")
    @patch("paas.tasks.paramiko.SSHClient")
    @patch("paas.tasks.load_ssh_config")
    def test_ssh_client_closes_connection_after_exception_in_context(
        self,
        mock_load_config,
        mock_ssh_client,
        mock_auto_add_policy,
    ):
        mock_load_config.return_value = {
            "user": "config_user",
            "identityfile": ["/home/test/.ssh/id_rsa"],
        }
        mock_ssh_client.return_value = self.client

        with self.assertRaises(RuntimeError):
            with _ssh_client(self.host):
                raise RuntimeError("Test exception")

        self.client.close.assert_called_once_with()

    @patch("paas.tasks.paramiko.AutoAddPolicy")
    @patch("paas.tasks.paramiko.SSHClient")
    @patch("paas.tasks.load_ssh_config")
    def test_ssh_client_closes_connection_after_connect_exception(
        self,
        mock_load_config,
        mock_ssh_client,
        mock_auto_add_policy,
    ):
        """Auch bei einem Verbindungsfehler wird close() aufgerufen."""

        mock_load_config.return_value = {
            "user": "config_user",
            "identityfile": ["/home/test/.ssh/id_rsa"],
        }
        mock_ssh_client.return_value = self.client
        self.client.connect.side_effect = Exception("SSH connection failed")

        with self.assertRaises(Exception):
            with _ssh_client(self.host):
                pass

        self.client.close.assert_called_once_with()

    @patch("paas.tasks.paramiko.AutoAddPolicy")
    @patch("paas.tasks.paramiko.SSHClient")
    @patch("paas.tasks.load_ssh_config")
    def test_ssh_client_does_not_connect_if_config_loading_fails(
        self,
        mock_load_config,
        mock_ssh_client,
        mock_auto_add_policy,
    ):
        """Bei einem Fehler beim Laden der SSH-Konfiguration wird abgebrochen."""

        mock_load_config.side_effect = FileNotFoundError(
            "SSH config not found"
        )

        with self.assertRaises(FileNotFoundError):
            with _ssh_client(self.host):
                pass

        mock_ssh_client.assert_not_called()
        self.client.connect.assert_not_called()
        self.client.close.assert_not_called()