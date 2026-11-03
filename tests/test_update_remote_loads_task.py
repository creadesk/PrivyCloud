'''
Erfolgreiches Auslesen und Speichern der CPU-Last.
Mehrere RemoteHosts werden unabhängig voneinander aktualisiert.
Load-Werte unter 0.0 werden auf 0.0 begrenzt.
Load-Werte über 10.0 werden auf 10.0 begrenzt.
Fehlerhafte SSH-Ausgabe wird abgefangen.
SSH-Verbindungsfehler werden abgefangen.
Ein Fehler bei einem Host verhindert nicht die Aktualisierung weiterer Hosts.
Bei einem Fehler bleibt die bisherige CPU-Last unverändert.
Ohne RemoteHosts läuft der Task fehlerfrei durch.
'''



from unittest.mock import MagicMock, patch

from django.test import TestCase

from paas.models import RemoteHost
from paas.tasks import update_remote_loads


class UpdateRemoteLoadsTaskTest(TestCase):

    def setUp(self):
        self.host1 = RemoteHost.objects.create(
            hostname="server1.example.com",
            ip_address="192.168.1.10",
            ssh_user="root",
            current_load=1.5,
        )

        self.host2 = RemoteHost.objects.create(
            hostname="server2.example.com",
            ip_address="192.168.1.11",
            ssh_user="root",
            current_load=2.5,
        )

    def create_ssh_context(self):
        """Erzeugt einen simulierten SSH-Kontext."""
        context = MagicMock()
        context.__enter__.return_value = MagicMock()
        context.__exit__.return_value = False
        return context

    @patch("paas.tasks._ssh_client")
    @patch("paas.tasks._run_cmd")
    def test_successful_load_update(
        self, mock_run_cmd, mock_ssh_client
    ):
        """CPU-Last wird erfolgreich ausgelesen und gespeichert."""

        mock_ssh_client.return_value = self.create_ssh_context()

        mock_run_cmd.return_value = (
            None,
            "3.75 2.10 1.50 2/100 1234",
            None,
        )

        result = update_remote_loads()

        self.host1.refresh_from_db()
        self.host2.refresh_from_db()

        self.assertEqual(self.host1.current_load, 3.75)
        self.assertEqual(self.host2.current_load, 3.75)

        self.assertEqual(result, {
            "successes": 2,
            "failures": 0,
        })

        self.assertEqual(mock_run_cmd.call_count, 2)

    @patch("paas.tasks._ssh_client")
    @patch("paas.tasks._run_cmd")
    def test_load_is_clamped_to_maximum(
        self, mock_run_cmd, mock_ssh_client
    ):
        """CPU-Last über 10.0 wird auf 10.0 begrenzt."""

        mock_ssh_client.return_value = self.create_ssh_context()

        mock_run_cmd.return_value = (
            None,
            "15.75 10.00 8.00 2/100 1234",
            None,
        )

        result = update_remote_loads()

        self.host1.refresh_from_db()
        self.host2.refresh_from_db()

        self.assertEqual(self.host1.current_load, 10.0)
        self.assertEqual(self.host2.current_load, 10.0)

        self.assertEqual(result["successes"], 2)
        self.assertEqual(result["failures"], 0)

    @patch("paas.tasks._ssh_client")
    @patch("paas.tasks._run_cmd")
    def test_negative_load_is_clamped_to_zero(
        self, mock_run_cmd, mock_ssh_client
    ):
        """Negative CPU-Last wird auf 0.0 begrenzt."""

        mock_ssh_client.return_value = self.create_ssh_context()

        mock_run_cmd.return_value = (
            None,
            "-2.5 0.00 0.00 1/100 1234",
            None,
        )

        result = update_remote_loads()

        self.host1.refresh_from_db()
        self.host2.refresh_from_db()

        self.assertEqual(self.host1.current_load, 0.0)
        self.assertEqual(self.host2.current_load, 0.0)

        self.assertEqual(result["successes"], 2)
        self.assertEqual(result["failures"], 0)

    @patch("paas.tasks._ssh_client")
    @patch("paas.tasks._run_cmd")
    def test_invalid_load_output_does_not_update_host(
        self, mock_run_cmd, mock_ssh_client
    ):
        """Ungültige SSH-Ausgabe wird als Fehler behandelt."""

        mock_ssh_client.return_value = self.create_ssh_context()

        mock_run_cmd.return_value = (
            None,
            "invalid load average",
            None,
        )

        result = update_remote_loads()

        self.host1.refresh_from_db()
        self.host2.refresh_from_db()

        self.assertEqual(self.host1.current_load, 1.5)
        self.assertEqual(self.host2.current_load, 2.5)

        self.assertEqual(result["successes"], 0)
        self.assertEqual(result["failures"], 2)

    @patch("paas.tasks._ssh_client")
    def test_ssh_connection_error_does_not_update_host(
        self, mock_ssh_client
    ):
        """SSH-Verbindungsfehler werden abgefangen."""

        mock_ssh_client.side_effect = Exception(
            "SSH connection failed"
        )

        result = update_remote_loads()

        self.host1.refresh_from_db()
        self.host2.refresh_from_db()

        self.assertEqual(self.host1.current_load, 1.5)
        self.assertEqual(self.host2.current_load, 2.5)

        self.assertEqual(result["successes"], 0)
        self.assertEqual(result["failures"], 2)

    @patch("paas.tasks._ssh_client")
    @patch("paas.tasks._run_cmd")
    def test_failure_on_one_host_does_not_stop_other_hosts(
        self, mock_run_cmd, mock_ssh_client
    ):
        """
        Ein fehlerhafter Host verhindert nicht die Aktualisierung
        der übrigen Hosts.
        """

        def ssh_client_side_effect(host):
            if host.hostname == self.host1.hostname:
                raise Exception("SSH connection failed")

            return self.create_ssh_context()

        mock_ssh_client.side_effect = ssh_client_side_effect

        mock_run_cmd.return_value = (
            None,
            "4.25 3.00 2.00 1/100 1234",
            None,
        )

        result = update_remote_loads()

        self.host1.refresh_from_db()
        self.host2.refresh_from_db()

        # Erster Host: Fehler, alter Wert bleibt erhalten.
        self.assertEqual(self.host1.current_load, 1.5)

        # Zweiter Host: Erfolgreich aktualisiert.
        self.assertEqual(self.host2.current_load, 4.25)

        self.assertEqual(result["successes"], 1)
        self.assertEqual(result["failures"], 1)

        self.assertEqual(mock_run_cmd.call_count, 1)

    @patch("paas.tasks._ssh_client")
    @patch("paas.tasks._run_cmd")
    def test_empty_remote_host_list(
        self, mock_run_cmd, mock_ssh_client
    ):
        """Ohne RemoteHosts läuft der Task fehlerfrei durch."""

        RemoteHost.objects.all().delete()

        result = update_remote_loads()

        mock_ssh_client.assert_not_called()
        mock_run_cmd.assert_not_called()

        self.assertEqual(result, {
            "successes": 0,
            "failures": 0,
        })

    @patch("paas.tasks._ssh_client")
    @patch("paas.tasks._run_cmd")
    def test_only_current_load_is_updated(
        self, mock_run_cmd, mock_ssh_client
    ):
        """Andere Host-Felder bleiben unverändert."""

        mock_ssh_client.return_value = self.create_ssh_context()

        mock_run_cmd.return_value = (
            None,
            "6.50 5.00 4.00 1/100 1234",
            None,
        )

        original_hostname = self.host1.hostname
        original_ip = self.host1.ip_address
        original_ssh_user = self.host1.ssh_user

        result = update_remote_loads()

        self.host1.refresh_from_db()

        self.assertEqual(self.host1.current_load, 6.5)
        self.assertEqual(self.host1.hostname, original_hostname)
        self.assertEqual(self.host1.ip_address, original_ip)
        self.assertEqual(self.host1.ssh_user, original_ssh_user)

        self.assertEqual(result["successes"], 2)
        self.assertEqual(result["failures"], 0)

    @patch("paas.tasks._ssh_client")
    @patch("paas.tasks._run_cmd")
    def test_command_is_cat_proc_loadavg(
        self, mock_run_cmd, mock_ssh_client
    ):
        """Es wird der erwartete SSH-Befehl ausgeführt."""

        mock_ssh_client.return_value = self.create_ssh_context()

        mock_run_cmd.return_value = (
            None,
            "1.25 0.80 0.60 1/100 1234",
            None,
        )

        update_remote_loads()

        self.assertEqual(mock_run_cmd.call_count, 2)

        for call in mock_run_cmd.call_args_list:
            self.assertEqual(
                call.args[1],
                "cat /proc/loadavg",
            )

