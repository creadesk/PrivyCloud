
from unittest import TestCase
from unittest.mock import MagicMock, Mock, patch

from paas.docker_ssh_adapter import PrivyCloudSSHAdapter


class PrivyCloudSSHAdapterTests(TestCase):

    def setUp(self):
        self.base_url = "ssh://deploy@server.example.com"
        self.ssh_client = Mock()
        self.ssh_context = MagicMock()

    @patch("paas.docker_ssh_adapter.SSHHTTPAdapter.__init__")
    def test_initialization(self, mock_super_init):
        adapter = PrivyCloudSSHAdapter(
            base_url=self.base_url,
            ssh_client=self.ssh_client,
            ssh_context=self.ssh_context,
            timeout=30,
        )

        mock_super_init.assert_called_once_with(
            self.base_url,
            timeout=30,
            shell_out=True,
        )

        self.assertIs(adapter.ssh_client, self.ssh_client)
        self.assertIs(adapter.ssh_context, self.ssh_context)
        self.assertEqual(adapter.ssh_params, {})

    @patch("paas.docker_ssh_adapter.SSHHTTPAdapter.__init__")
    def test_initialization_without_context(self, mock_super_init):
        adapter = PrivyCloudSSHAdapter(
            base_url=self.base_url,
            ssh_client=self.ssh_client,
        )

        self.assertIs(adapter.ssh_client, self.ssh_client)
        self.assertIsNone(adapter.ssh_context)
        self.assertEqual(adapter.ssh_params, {})

        mock_super_init.assert_called_once_with(
            self.base_url,
            timeout=60,
            shell_out=True,
        )

    # ---------------------------------------------------------
    # SSH-Verbindung prüfen
    # ---------------------------------------------------------

    @patch("paas.docker_ssh_adapter.SSHHTTPAdapter.__init__")
    @patch("paas.docker_ssh_adapter.SSHHTTPAdapter.get_connection")
    def test_get_connection_success(
        self,
        mock_super_get_connection,
        mock_super_init,
    ):
        transport = Mock()
        transport.is_active.return_value = True
        self.ssh_client.get_transport.return_value = transport

        expected_connection = Mock()
        mock_super_get_connection.return_value = expected_connection

        adapter = PrivyCloudSSHAdapter(
            self.base_url,
            self.ssh_client,
        )

        result = adapter.get_connection(
            "http://localhost:2375/version"
        )

        self.assertIs(result, expected_connection)

        self.ssh_client.get_transport.assert_called_once()
        transport.is_active.assert_called_once()

        mock_super_get_connection.assert_called_once_with(
            "http://localhost:2375/version",
            None,
        )

    @patch("paas.docker_ssh_adapter.SSHHTTPAdapter.__init__")
    @patch("paas.docker_ssh_adapter.SSHHTTPAdapter.get_connection")
    def test_get_connection_with_proxies(
        self,
        mock_super_get_connection,
        mock_super_init,
    ):
        transport = Mock()
        transport.is_active.return_value = True
        self.ssh_client.get_transport.return_value = transport

        proxies = {"https": "http://proxy.example.com:8080"}

        adapter = PrivyCloudSSHAdapter(
            self.base_url,
            self.ssh_client,
        )

        adapter.get_connection(
            "http://localhost:2375/version",
            proxies=proxies,
        )

        mock_super_get_connection.assert_called_once_with(
            "http://localhost:2375/version",
            proxies,
        )

    @patch("paas.docker_ssh_adapter.SSHHTTPAdapter.__init__")
    @patch("paas.docker_ssh_adapter.SSHHTTPAdapter.get_connection")
    def test_get_connection_without_transport(
        self,
        mock_super_get_connection,
        mock_super_init,
    ):
        self.ssh_client.get_transport.return_value = None

        adapter = PrivyCloudSSHAdapter(
            self.base_url,
            self.ssh_client,
        )

        with self.assertRaisesRegex(
            RuntimeError,
            "SSH-Transport ist nicht aktiv",
        ):
            adapter.get_connection(
                "http://localhost:2375/version"
            )

        mock_super_get_connection.assert_not_called()

    @patch("paas.docker_ssh_adapter.SSHHTTPAdapter.__init__")
    @patch("paas.docker_ssh_adapter.SSHHTTPAdapter.get_connection")
    def test_get_connection_with_inactive_transport(
        self,
        mock_super_get_connection,
        mock_super_init,
    ):
        transport = Mock()
        transport.is_active.return_value = False
        self.ssh_client.get_transport.return_value = transport

        adapter = PrivyCloudSSHAdapter(
            self.base_url,
            self.ssh_client,
        )

        with self.assertRaisesRegex(
            RuntimeError,
            "SSH-Transport ist nicht aktiv",
        ):
            adapter.get_connection(
                "http://localhost:2375/version"
            )

        transport.is_active.assert_called_once()
        mock_super_get_connection.assert_not_called()

    # ---------------------------------------------------------
    # Schließen mit SSH-Kontext
    # ---------------------------------------------------------

    @patch("paas.docker_ssh_adapter.SSHHTTPAdapter.__init__")
    @patch("paas.docker_ssh_adapter.SSHHTTPAdapter.close")
    def test_close_with_context(
        self,
        mock_super_close,
        mock_super_init,
    ):
        adapter = PrivyCloudSSHAdapter(
            self.base_url,
            self.ssh_client,
            ssh_context=self.ssh_context,
        )

        adapter.close()

        mock_super_close.assert_called_once()
        self.ssh_context.__exit__.assert_called_once_with(
            None,
            None,
            None,
        )

        self.assertIsNone(adapter.ssh_client)
        self.assertIsNone(adapter.ssh_context)

        # Der Client wird über den Kontext geschlossen,
        # nicht zusätzlich direkt.
        self.ssh_client.close.assert_not_called()

    @patch("paas.docker_ssh_adapter.SSHHTTPAdapter.__init__")
    @patch("paas.docker_ssh_adapter.SSHHTTPAdapter.close")
    def test_close_without_context(
        self,
        mock_super_close,
        mock_super_init,
    ):
        adapter = PrivyCloudSSHAdapter(
            self.base_url,
            self.ssh_client,
        )

        adapter.close()

        mock_super_close.assert_called_once()
        self.ssh_client.close.assert_called_once()

        self.assertIsNone(adapter.ssh_client)

    @patch("paas.docker_ssh_adapter.SSHHTTPAdapter.__init__")
    @patch("paas.docker_ssh_adapter.SSHHTTPAdapter.close")
    def test_close_without_client_or_context(
        self,
        mock_super_close,
        mock_super_init,
    ):
        adapter = PrivyCloudSSHAdapter(
            self.base_url,
            None,
        )

        adapter.close()

        mock_super_close.assert_called_once()

    # ---------------------------------------------------------
    # Fehler beim Schließen
    # ---------------------------------------------------------

    @patch("paas.docker_ssh_adapter.SSHHTTPAdapter.__init__")
    @patch(
        "paas.docker_ssh_adapter.SSHHTTPAdapter.close",
        side_effect=RuntimeError("Docker close failed"),
    )
    def test_close_context_even_if_super_close_fails(
        self,
        mock_super_close,
        mock_super_init,
    ):
        adapter = PrivyCloudSSHAdapter(
            self.base_url,
            self.ssh_client,
            ssh_context=self.ssh_context,
        )

        with self.assertRaisesRegex(
            RuntimeError,
            "Docker close failed",
        ):
            adapter.close()

        # Der finally-Block muss den Kontext trotzdem schließen.
        self.ssh_context.__exit__.assert_called_once_with(
            None,
            None,
            None,
        )

        self.assertIsNone(adapter.ssh_client)
        self.assertIsNone(adapter.ssh_context)

    @patch("paas.docker_ssh_adapter.SSHHTTPAdapter.__init__")
    @patch(
        "paas.docker_ssh_adapter.SSHHTTPAdapter.close",
        side_effect=RuntimeError("Docker close failed"),
    )
    def test_close_client_even_if_super_close_fails(
        self,
        mock_super_close,
        mock_super_init,
    ):
        adapter = PrivyCloudSSHAdapter(
            self.base_url,
            self.ssh_client,
        )

        with self.assertRaisesRegex(
            RuntimeError,
            "Docker close failed",
        ):
            adapter.close()

        self.ssh_client.close.assert_called_once()
        self.assertIsNone(adapter.ssh_client)


if __name__ == "__main__":
    import unittest
    unittest.main()
