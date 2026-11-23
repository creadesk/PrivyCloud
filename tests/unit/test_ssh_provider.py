
import io
import os
from contextlib import nullcontext
from unittest import TestCase
from unittest.mock import MagicMock, Mock, patch

import paramiko

from paas.openbao_client import OpenBaoError
from paas.ssh_provider import (
    ssh_config_lookup,
    _load_private_key,
    _openbao_ssh_client,
    ssh_client,
)


class SSHConfigLookupTests(TestCase):

    @patch("paas.ssh_provider.paramiko.SSHConfig")
    @patch("paas.ssh_provider.os.path.exists")
    @patch("builtins.open", create=True)
    def test_lookup_success(
        self,
        mock_open,
        mock_exists,
        mock_ssh_config,
    ):
        mock_exists.return_value = True

        expected = {
            "user": "deploy",
            "identityfile": ["/home/user/.ssh/id_ed25519"],
        }

        mock_ssh_config.return_value.lookup.return_value = expected

        result = ssh_config_lookup("server.example.com")

        self.assertEqual(result, expected)
        mock_exists.assert_called_once()
        mock_open.assert_called_once()
        mock_ssh_config.return_value.parse.assert_called_once()
        mock_ssh_config.return_value.lookup.assert_called_once_with(
            "server.example.com"
        )

    @patch("paas.ssh_provider.os.path.exists")
    def test_lookup_missing_config_file(self, mock_exists):
        mock_exists.return_value = False

        with self.assertRaises(FileNotFoundError):
            ssh_config_lookup("server.example.com")


class LoadPrivateKeyTests(TestCase):

    @patch("paas.ssh_provider.paramiko.Ed25519Key.from_private_key")
    def test_load_ed25519_key_success(self, mock_load):
        expected_key = Mock()
        mock_load.return_value = expected_key

        result = _load_private_key("test-private-key")

        self.assertIs(result, expected_key)
        mock_load.assert_called_once()

        args, kwargs = mock_load.call_args
        self.assertIsInstance(args[0], io.StringIO)
        self.assertEqual(args[0].getvalue(), "test-private-key")
        self.assertIsNone(kwargs["password"])

    @patch("paas.ssh_provider.paramiko.Ed25519Key.from_private_key")
    def test_load_private_key_with_passphrase(self, mock_load):
        expected_key = Mock()
        mock_load.return_value = expected_key

        result = _load_private_key(
            "test-private-key",
            passphrase="test-passphrase",
        )

        self.assertIs(result, expected_key)
        self.assertEqual(
            mock_load.call_args.kwargs["password"],
            "test-passphrase",
        )

    @patch("paas.ssh_provider.paramiko.Ed25519Key.from_private_key")
    @patch("paas.ssh_provider.paramiko.ECDSAKey.from_private_key")
    def test_load_ecdsa_key_after_ed25519_failure(
        self,
        mock_ecdsa,
        mock_ed25519,
    ):
        mock_ed25519.side_effect = paramiko.SSHException(
            "Kein Ed25519-Schlüssel"
        )

        expected_key = Mock()
        mock_ecdsa.return_value = expected_key

        result = _load_private_key("test-private-key")

        self.assertIs(result, expected_key)
        mock_ed25519.assert_called_once()
        mock_ecdsa.assert_called_once()

    @patch("paas.ssh_provider.paramiko.Ed25519Key.from_private_key")
    @patch("paas.ssh_provider.paramiko.ECDSAKey.from_private_key")
    @patch("paas.ssh_provider.paramiko.RSAKey.from_private_key")
    def test_load_rsa_key_after_other_formats_fail(
        self,
        mock_rsa,
        mock_ecdsa,
        mock_ed25519,
    ):
        mock_ed25519.side_effect = paramiko.SSHException("Ed25519 failed")
        mock_ecdsa.side_effect = paramiko.SSHException("ECDSA failed")

        expected_key = Mock()
        mock_rsa.return_value = expected_key

        result = _load_private_key("test-private-key")

        self.assertIs(result, expected_key)
        mock_ed25519.assert_called_once()
        mock_ecdsa.assert_called_once()
        mock_rsa.assert_called_once()

    @patch("paas.ssh_provider.paramiko.Ed25519Key.from_private_key")
    @patch("paas.ssh_provider.paramiko.ECDSAKey.from_private_key")
    @patch("paas.ssh_provider.paramiko.RSAKey.from_private_key")
    def test_load_private_key_all_formats_fail(
        self,
        mock_rsa,
        mock_ecdsa,
        mock_ed25519,
    ):
        mock_ed25519.side_effect = paramiko.SSHException("Ed25519 failed")
        mock_ecdsa.side_effect = paramiko.SSHException("ECDSA failed")
        mock_rsa.side_effect = paramiko.SSHException("RSA failed")

        with self.assertRaisesRegex(
            OpenBaoError,
            "private SSH-Schlüssel konnte nicht geladen werden",
        ):
            _load_private_key("invalid-private-key")

        mock_ed25519.assert_called_once()
        mock_ecdsa.assert_called_once()
        mock_rsa.assert_called_once()


class OpenBaoSSHClientTests(TestCase):

    def setUp(self):
        self.host = Mock()
        self.host.hostname = "192.168.1.113"
        self.host.ssh_user = "deploy"
        self.host.openbao_secret_path = "privycloud/ssh/test-server"

    @patch("paas.ssh_provider._load_private_key")
    @patch("paas.ssh_provider.paramiko.SSHClient")
    @patch("paas.ssh_provider.OpenBaoClient")
    def test_openbao_ssh_connection_success(
        self,
        mock_openbao_class,
        mock_ssh_class,
        mock_load_key,
    ):
        openbao = mock_openbao_class.return_value
        ssh = mock_ssh_class.return_value

        openbao.read_secret.return_value = {
            "private_key": "test-private-key",
            "username": "deploy",
            "port": 2222,
            "passphrase": "test-passphrase",
        }

        expected_key = Mock()
        mock_load_key.return_value = expected_key

        with _openbao_ssh_client(self.host) as client:
            self.assertIs(client, ssh)

        openbao.read_secret.assert_called_once_with(
            "privycloud/ssh/test-server"
        )

        mock_load_key.assert_called_once_with(
            "test-private-key",
            "test-passphrase",
        )

        ssh.connect.assert_called_once_with(
            hostname="192.168.1.113",
            port=2222,
            username="deploy",
            pkey=expected_key,
            timeout=10,
            allow_agent=False,
            look_for_keys=False,
        )

        ssh.close.assert_called_once()
        openbao.close.assert_called_once()

    @patch("paas.ssh_provider._load_private_key")
    @patch("paas.ssh_provider.paramiko.SSHClient")
    @patch("paas.ssh_provider.OpenBaoClient")
    def test_openbao_uses_host_username_and_default_port(
        self,
        mock_openbao_class,
        mock_ssh_class,
        mock_load_key,
    ):
        openbao = mock_openbao_class.return_value
        ssh = mock_ssh_class.return_value

        openbao.read_secret.return_value = {
            "private_key": "test-private-key",
        }

        with _openbao_ssh_client(self.host):
            pass

        mock_load_key.assert_called_once_with(
            "test-private-key",
            None,
        )

        ssh.connect.assert_called_once_with(
            hostname="192.168.1.113",
            port=22,
            username="deploy",
            pkey=mock_load_key.return_value,
            timeout=10,
            allow_agent=False,
            look_for_keys=False,
        )

        ssh.close.assert_called_once()
        openbao.close.assert_called_once()

    @patch("paas.ssh_provider.paramiko.SSHClient")
    @patch("paas.ssh_provider.OpenBaoClient")
    def test_openbao_missing_private_key(
        self,
        mock_openbao_class,
        mock_ssh_class,
    ):
        openbao = mock_openbao_class.return_value
        ssh = mock_ssh_class.return_value

        openbao.read_secret.return_value = {
            "username": "deploy",
        }

        with self.assertRaisesRegex(
            OpenBaoError,
            "fehlt private_key",
        ):
            with _openbao_ssh_client(self.host):
                pass

        ssh.close.assert_called_once()
        openbao.close.assert_called_once()

    @patch("paas.ssh_provider._load_private_key")
    @patch("paas.ssh_provider.paramiko.SSHClient")
    @patch("paas.ssh_provider.OpenBaoClient")
    def test_openbao_ssh_connection_error_closes_clients(
        self,
        mock_openbao_class,
        mock_ssh_class,
        mock_load_key,
    ):
        openbao = mock_openbao_class.return_value
        ssh = mock_ssh_class.return_value

        openbao.read_secret.return_value = {
            "private_key": "test-private-key",
        }

        ssh.connect.side_effect = paramiko.SSHException(
            "SSH-Verbindung fehlgeschlagen"
        )

        with self.assertRaises(paramiko.SSHException):
            with _openbao_ssh_client(self.host):
                pass

        ssh.close.assert_called_once()
        openbao.close.assert_called_once()


class SSHClientTests(TestCase):

    def setUp(self):
        self.host = Mock()
        self.host.hostname = "server.example.com"
        self.host.ssh_user = "deploy"
        self.host.ssh_auth_method = "ssh_config"
        self.host.openbao_secret_path = ""

    # ---------------------------------------------------------
    # ssh_config
    # ---------------------------------------------------------

    @patch("paas.ssh_provider.paramiko.SSHClient")
    @patch("paas.ssh_provider.ssh_config_lookup")
    def test_ssh_config_connection_success(
        self,
        mock_lookup,
        mock_ssh_class,
    ):
        ssh = mock_ssh_class.return_value

        mock_lookup.return_value = {
            "user": "admin",
            "identityfile": ["/home/user/.ssh/id_ed25519"],
        }

        with ssh_client(self.host) as client:
            self.assertIs(client, ssh)

        mock_lookup.assert_called_once_with(
            "server.example.com"
        )

        ssh.connect.assert_called_once_with(
            hostname="server.example.com",
            username="admin",
            key_filename="/home/user/.ssh/id_ed25519",
            timeout=10,
            allow_agent=False,
            look_for_keys=False,
        )

        ssh.close.assert_called_once()

    @patch("paas.ssh_provider.paramiko.SSHClient")
    @patch("paas.ssh_provider.ssh_config_lookup")
    def test_ssh_config_username_fallback(
        self,
        mock_lookup,
        mock_ssh_class,
    ):
        ssh = mock_ssh_class.return_value
        mock_lookup.return_value = {}

        with ssh_client(self.host):
            pass

        self.assertEqual(
            ssh.connect.call_args.kwargs["username"],
            "deploy",
        )

    @patch("paas.ssh_provider.paramiko.SSHClient")
    @patch("paas.ssh_provider.ssh_config_lookup")
    def test_ssh_config_without_identity_file(
        self,
        mock_lookup,
        mock_ssh_class,
    ):
        ssh = mock_ssh_class.return_value

        mock_lookup.return_value = {
            "user": "deploy",
            "identityfile": [],
        }

        with ssh_client(self.host):
            pass

        self.assertIsNone(
            ssh.connect.call_args.kwargs["key_filename"]
        )

    @patch("paas.ssh_provider.paramiko.SSHClient")
    @patch("paas.ssh_provider.ssh_config_lookup")
    def test_ssh_config_connection_error_closes_client(
        self,
        mock_lookup,
        mock_ssh_class,
    ):
        ssh = mock_ssh_class.return_value
        mock_lookup.return_value = {}

        ssh.connect.side_effect = paramiko.SSHException(
            "Verbindung fehlgeschlagen"
        )

        with self.assertRaises(paramiko.SSHException):
            with ssh_client(self.host):
                pass

        ssh.close.assert_called_once()

    # ---------------------------------------------------------
    # OpenBAO
    # ---------------------------------------------------------

    @patch("paas.ssh_provider._openbao_ssh_client")
    @patch("paas.ssh_provider.settings.OPENBAO_ENABLED", True)
    def test_openbao_connection_success(
        self,
        mock_openbao_context,
    ):
        self.host.ssh_auth_method = "openbao"
        self.host.openbao_secret_path = "privycloud/ssh/test-server"

        expected_client = Mock()
        mock_openbao_context.return_value = nullcontext(
            expected_client
        )

        with ssh_client(self.host) as client:
            self.assertIs(client, expected_client)

        mock_openbao_context.assert_called_once_with(self.host)

    @patch("paas.ssh_provider._openbao_ssh_client")
    @patch("paas.ssh_provider.settings.OPENBAO_ENABLED", "False")
    def test_openbao_disabled(self, mock_openbao_context):
        self.host.ssh_auth_method = "openbao"
        self.host.openbao_secret_path = "privycloud/ssh/test-server"

        with self.assertRaisesRegex(
            OpenBaoError,
            "OpenBAO ist in den PrivyCloud-Einstellungen deaktiviert",
        ):
            with ssh_client(self.host):
                pass

        mock_openbao_context.assert_not_called()

    @patch("paas.ssh_provider._openbao_ssh_client")
    @patch("paas.ssh_provider.settings.OPENBAO_ENABLED", " yes ")
    def test_openbao_enabled_with_string_value(
        self,
        mock_openbao_context,
    ):
        self.host.ssh_auth_method = "openbao"
        self.host.openbao_secret_path = "privycloud/ssh/test-server"

        mock_openbao_context.return_value = nullcontext(Mock())

        with ssh_client(self.host):
            pass

        mock_openbao_context.assert_called_once_with(self.host)

    @patch("paas.ssh_provider._openbao_ssh_client")
    @patch("paas.ssh_provider.settings.OPENBAO_ENABLED", True)
    def test_openbao_missing_secret_path(
        self,
        mock_openbao_context,
    ):
        self.host.ssh_auth_method = "openbao"
        self.host.openbao_secret_path = ""

        with self.assertRaisesRegex(
            OpenBaoError,
            "kein OpenBAO-Secret-Pfad",
        ):
            with ssh_client(self.host):
                pass

        mock_openbao_context.assert_not_called()

    # ---------------------------------------------------------
    # Ungültige Authentifizierungsmethode
    # ---------------------------------------------------------

    def test_unknown_authentication_method(self):
        self.host.ssh_auth_method = "unknown"

        with self.assertRaisesRegex(
            ValueError,
            "Unbekannte SSH-Authentifizierungsmethode",
        ):
            with ssh_client(self.host):
                pass


if __name__ == "__main__":
    import unittest
    unittest.main()
