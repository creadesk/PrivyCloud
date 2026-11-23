
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests

from paas.openbao_client import OpenBaoClient, OpenBaoError


class OpenBaoClientTests(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base_path = Path(self.temp_dir.name)

        self.credentials_dir = self.base_path / "credentials"
        self.credentials_dir.mkdir()

        self.secret_id_file = (
            self.credentials_dir / "openbao_secret_id"
        )
        self.secret_id_file.write_text(
            "test-secret-id\n",
            encoding="utf-8",
        )

        self.ca_file = self.base_path / "openbao-ca.pem"
        self.ca_file.write_text(
            "test-ca",
            encoding="utf-8",
        )

        self.env = {
            "OPENBAO_URL": "https://openbao.example.com/",
            "OPENBAO_ROLE_ID": "test-role-id",
            "OPENBAO_CA_CERT": str(self.ca_file),
            "OPENBAO_KV_MOUNT": "secret",
            "OPENBAO_KV_VERSION": "2",
            "CREDENTIALS_DIRECTORY": str(self.credentials_dir),
        }

        self.env_patcher = patch.dict(os.environ, self.env, clear=True)
        self.env_patcher.start()

        self.client = OpenBaoClient()

    def tearDown(self):
        self.client.close()
        self.env_patcher.stop()
        self.temp_dir.cleanup()

    # ---------------------------------------------------------
    # Initialisierung und Konfiguration
    # ---------------------------------------------------------

    def test_initialization_success(self):
        self.assertEqual(
            self.client.url,
            "https://openbao.example.com",
        )
        self.assertEqual(self.client.role_id, "test-role-id")
        self.assertEqual(self.client.kv_mount, "secret")
        self.assertEqual(self.client.kv_version, "2")
        self.assertEqual(
            self.client.secret_id_file,
            self.secret_id_file,
        )
        self.assertEqual(
            self.client.session.verify,
            str(self.ca_file),
        )
        self.assertIsNone(self.client.token)

    def test_missing_url(self):
        with patch.dict(os.environ, {"OPENBAO_URL": ""}):
            with self.assertRaisesRegex(
                OpenBaoError,
                "OPENBAO_URL",
            ):
                OpenBaoClient()

    def test_missing_role_id(self):
        with patch.dict(os.environ, {"OPENBAO_ROLE_ID": ""}):
            with self.assertRaisesRegex(
                OpenBaoError,
                "OPENBAO_ROLE_ID",
            ):
                OpenBaoClient()

    def test_missing_ca_file(self):
        with patch.dict(
            os.environ,
            {"OPENBAO_CA_CERT": "/does/not/exist.pem"},
        ):
            with self.assertRaisesRegex(
                OpenBaoError,
                "OPENBAO_CA_CERT",
            ):
                OpenBaoClient()

    def test_unsupported_kv_version(self):
        with patch.dict(
            os.environ,
            {"OPENBAO_KV_VERSION": "1"},
        ):
            with self.assertRaisesRegex(
                OpenBaoError,
                "KV-Version 2",
            ):
                OpenBaoClient()

    def test_missing_systemd_secret_id_file(self):
        with patch.dict(
            os.environ,
            {"CREDENTIALS_DIRECTORY": str(self.base_path / "missing")},
        ):
            with self.assertRaisesRegex(
                OpenBaoError,
                "SecretID-Datei wurde nicht gefunden",
            ):
                OpenBaoClient()

    def test_manual_secret_id_path(self):
        # Ohne CREDENTIALS_DIRECTORY wird der feste Pfad verwendet.
        env = dict(self.env)
        env.pop("CREDENTIALS_DIRECTORY")

        with patch.dict(os.environ, env, clear=True):
            with patch(
                "paas.openbao_client.Path.is_file",
                return_value=True,
            ), patch(
                "paas.openbao_client.os.access",
                return_value=True,
            ):
                client = OpenBaoClient()

        try:
            self.assertEqual(
                client.secret_id_file,
                Path("/etc/privycloud/openbao_secret_id"),
            )
        finally:
            client.close()

    # ---------------------------------------------------------
    # AppRole-Authentifizierung
    # ---------------------------------------------------------

    def test_login_success(self):
        response = Mock()
        response.json.return_value = {
            "auth": {
                "client_token": "test-token",
                "lease_duration": 900,
            }
        }

        with patch.object(
            self.client.session,
            "post",
            return_value=response,
        ) as mock_post:
            result = self.client.login()

        self.assertTrue(result)
        self.assertEqual(self.client.token, "test-token")
        self.assertEqual(
            self.client.session.headers["X-Vault-Token"],
            "test-token",
        )

        mock_post.assert_called_once_with(
            "https://openbao.example.com/v1/auth/approle/login",
            json={
                "role_id": "test-role-id",
                "secret_id": "test-secret-id",
            },
            timeout=10,
        )

        response.raise_for_status.assert_called_once()

    def test_login_strips_secret_id_whitespace(self):
        self.secret_id_file.write_text(
            "  secret-with-whitespace  \n",
            encoding="utf-8",
        )

        response = Mock()
        response.json.return_value = {
            "auth": {
                "client_token": "test-token",
                "lease_duration": 900,
            }
        }

        with patch.object(
            self.client.session,
            "post",
            return_value=response,
        ) as mock_post:
            self.client.login()

        self.assertEqual(
            mock_post.call_args.kwargs["json"]["secret_id"],
            "secret-with-whitespace",
        )

    def test_login_with_empty_secret_id(self):
        self.secret_id_file.write_text("", encoding="utf-8")

        with self.assertRaisesRegex(
            OpenBaoError,
            "SecretID-Credential ist leer",
        ):
            self.client.login()

    def test_login_http_error(self):
        with patch.object(
            self.client.session,
            "post",
            side_effect=requests.ConnectionError("Verbindung fehlgeschlagen"),
        ):
            with self.assertRaisesRegex(
                OpenBaoError,
                "AppRole-Anmeldung",
            ):
                self.client.login()

    def test_login_invalid_response(self):
        response = Mock()
        response.json.return_value = []

        with patch.object(
            self.client.session,
            "post",
            return_value=response,
        ):
            with self.assertRaisesRegex(
                OpenBaoError,
                "Ungültige Antwort",
            ):
                self.client.login()

    def test_login_without_token(self):
        response = Mock()
        response.json.return_value = {
            "auth": {
                "lease_duration": 900,
            }
        }

        with patch.object(
            self.client.session,
            "post",
            return_value=response,
        ):
            with self.assertRaisesRegex(
                OpenBaoError,
                "keinen gültigen Token",
            ):
                self.client.login()

    def test_login_invalid_lease_duration(self):
        response = Mock()
        response.json.return_value = {
            "auth": {
                "client_token": "test-token",
                "lease_duration": "invalid",
            }
        }

        with patch.object(
            self.client.session,
            "post",
            return_value=response,
        ):
            with self.assertRaisesRegex(
                OpenBaoError,
                "Ungültige Antwort",
            ):
                self.client.login()

    def test_login_sets_token_expiration_with_safety_buffer(self):
        response = Mock()
        response.json.return_value = {
            "auth": {
                "client_token": "test-token",
                "lease_duration": 900,
            }
        }

        with patch(
            "paas.openbao_client.time.monotonic",
            return_value=100,
        ), patch.object(
            self.client.session,
            "post",
            return_value=response,
        ):
            self.client.login()

        self.assertEqual(
            self.client.token_expires_at,
            940,
        )

    def test_login_short_lease_does_not_create_negative_expiration(self):
        response = Mock()
        response.json.return_value = {
            "auth": {
                "client_token": "test-token",
                "lease_duration": 30,
            }
        }

        with patch(
            "paas.openbao_client.time.monotonic",
            return_value=100,
        ), patch.object(
            self.client.session,
            "post",
            return_value=response,
        ):
            self.client.login()

        self.assertEqual(self.client.token_expires_at, 100)

    # ---------------------------------------------------------
    # Token-Verwaltung
    # ---------------------------------------------------------

    def test_ensure_token_logs_in_without_token(self):
        with patch.object(self.client, "login") as mock_login:
            self.client._ensure_token()

        mock_login.assert_called_once()

    def test_ensure_token_renews_expired_token(self):
        self.client.token = "old-token"
        self.client.token_expires_at = 100

        with patch(
            "paas.openbao_client.time.monotonic",
            return_value=100,
        ), patch.object(self.client, "login") as mock_login:
            self.client._ensure_token()

        mock_login.assert_called_once()

    def test_ensure_token_does_not_renew_valid_token(self):
        self.client.token = "valid-token"
        self.client.token_expires_at = 200

        with patch(
            "paas.openbao_client.time.monotonic",
            return_value=100,
        ), patch.object(self.client, "login") as mock_login:
            self.client._ensure_token()

        mock_login.assert_not_called()

    # ---------------------------------------------------------
    # KV-v2 Secret-Zugriff
    # ---------------------------------------------------------

    def test_read_secret_success(self):
        expected_data = {
            "private_key": "test-private-key",
            "username": "deploy",
            "port": 22,
        }

        response = Mock()
        response.json.return_value = {
            "data": {
                "data": expected_data,
            }
        }

        with patch.object(
            self.client,
            "_ensure_token",
        ) as mock_ensure_token, patch.object(
            self.client.session,
            "get",
            return_value=response,
        ) as mock_get:
            result = self.client.read_secret(
                "privycloud/ssh/test-server"
            )

        self.assertEqual(result, expected_data)
        mock_ensure_token.assert_called_once()

        mock_get.assert_called_once_with(
            "https://openbao.example.com/v1/secret/data/privycloud/ssh/test-server",
            timeout=10,
        )

        response.raise_for_status.assert_called_once()

    def test_read_secret_strips_outer_slashes(self):
        response = Mock()
        response.json.return_value = {
            "data": {
                "data": {"username": "deploy"},
            }
        }

        with patch.object(
            self.client,
            "_ensure_token",
        ), patch.object(
            self.client.session,
            "get",
            return_value=response,
        ) as mock_get:
            self.client.read_secret(
                "/privycloud/ssh/test-server/"
            )

        self.assertIn(
            "/secret/data/privycloud/ssh/test-server",
            mock_get.call_args.args[0],
        )

    def test_read_secret_encodes_special_characters(self):
        response = Mock()
        response.json.return_value = {
            "data": {
                "data": {"username": "deploy"},
            }
        }

        with patch.object(
            self.client,
            "_ensure_token",
        ), patch.object(
            self.client.session,
            "get",
            return_value=response,
        ) as mock_get:
            self.client.read_secret(
                "privycloud/ssh/server 01"
            )

        self.assertIn(
            "server%2001",
            mock_get.call_args.args[0],
        )

    def test_read_secret_rejects_path_outside_allowed_directory(self):
        invalid_paths = [
            "other/ssh/server01",
            "privycloud/database/password",
            "privycloud/ssh",
            "privycloud/sshx/server01",
        ]

        for path in invalid_paths:
            with self.subTest(path=path):
                with self.assertRaisesRegex(
                    OpenBaoError,
                    "muss unter privycloud/ssh/",
                ):
                    self.client.read_secret(path)

    def test_read_secret_rejects_path_traversal(self):
        invalid_paths = [
            "privycloud/ssh/../database",
            "privycloud/ssh/server01/../server02",
            "privycloud/ssh//server01",
            "privycloud/ssh/./server01",
        ]

        for path in invalid_paths:
            with self.subTest(path=path):
                with self.assertRaisesRegex(
                    OpenBaoError,
                    "Ungültiger Secret-Pfad",
                ):
                    self.client.read_secret(path)

    def test_read_secret_http_error(self):
        response = Mock()
        response.status_code = 403

        http_error = requests.HTTPError(
            "Forbidden",
            response=response,
        )

        with patch.object(
            self.client,
            "_ensure_token",
        ), patch.object(
            self.client.session,
            "get",
            side_effect=http_error,
        ):
            with self.assertRaisesRegex(
                OpenBaoError,
                "HTTP 403",
            ):
                self.client.read_secret(
                    "privycloud/ssh/test-server"
                )

    def test_read_secret_connection_error_without_response(self):
        with patch.object(
            self.client,
            "_ensure_token",
        ), patch.object(
            self.client.session,
            "get",
            side_effect=requests.ConnectionError("Offline"),
        ):
            with self.assertRaisesRegex(
                OpenBaoError,
                "HTTP unbekannt",
            ):
                self.client.read_secret(
                    "privycloud/ssh/test-server"
                )

    def test_read_secret_invalid_response_format(self):
        response = Mock()
        response.json.return_value = {
            "data": {}
        }

        with patch.object(
            self.client,
            "_ensure_token",
        ), patch.object(
            self.client.session,
            "get",
            return_value=response,
        ):
            with self.assertRaisesRegex(
                OpenBaoError,
                "KV-v2-Antwortformat",
            ):
                self.client.read_secret(
                    "privycloud/ssh/test-server"
                )

    # ---------------------------------------------------------
    # Aufräumen
    # ---------------------------------------------------------

    def test_close_clears_token_and_closes_session(self):
        self.client.token = "test-token"
        self.client.token_expires_at = 12345
        self.client.session.headers["X-Vault-Token"] = "test-token"

        with patch.object(
            self.client.session,
            "close",
        ) as mock_close:
            self.client.close()

        self.assertIsNone(self.client.token)
        self.assertEqual(self.client.token_expires_at, 0)
        self.assertNotIn(
            "X-Vault-Token",
            self.client.session.headers,
        )
        mock_close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
