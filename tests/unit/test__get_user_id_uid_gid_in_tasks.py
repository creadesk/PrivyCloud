'''
Erfolgreiche Ermittlung der UID.
Erfolgreiche Ermittlung der GID.
Verwendung des Standard-Benutzers deploy.
Übergabe eines alternativen Benutzernamens.
Korrekte Behandlung eines SSH-Kommandofehlers (exit_status != 0).
Übernahme der Fehlermeldung in die RuntimeError-Exception.
'''


from unittest.mock import patch

from django.test import SimpleTestCase

from paas.tasks import _get_user_id_uid, _get_user_id_gid


class GetUserIdUidTests(SimpleTestCase):
    """Tests für _get_user_id_uid()."""

    @patch("paas.tasks._run_cmd")
    def test_get_uid_success(self, mock_run_cmd):
        """UID wird erfolgreich ermittelt."""
        mock_run_cmd.return_value = (0, "1002", "")

        result = _get_user_id_uid(ssh="fake_ssh")

        self.assertEqual(result, "1002")
        mock_run_cmd.assert_called_once_with(
            "fake_ssh",
            "id -u deploy"
        )

    @patch("paas.tasks._run_cmd")
    def test_get_uid_with_custom_username(self, mock_run_cmd):
        """UID eines anderen Benutzers wird ermittelt."""
        mock_run_cmd.return_value = (0, "1005", "")

        result = _get_user_id_uid(
            ssh="fake_ssh",
            username="admin"
        )

        self.assertEqual(result, "1005")
        mock_run_cmd.assert_called_once_with(
            "fake_ssh",
            "id -u admin"
        )

    @patch("paas.tasks._run_cmd")
    def test_get_uid_command_error(self, mock_run_cmd):
        """Bei einem fehlgeschlagenen SSH-Kommando wird RuntimeError ausgelöst."""
        mock_run_cmd.return_value = (
            1,
            "",
            "user does not exist"
        )

        with self.assertRaisesRegex(
            RuntimeError,
            "Kann UID für 'deploy' nicht ermitteln"
        ):
            _get_user_id_uid(ssh="fake_ssh")

    @patch("paas.tasks._run_cmd")
    def test_get_uid_error_contains_stderr(self, mock_run_cmd):
        """Die Fehlermeldung enthält die SSH-Fehlerausgabe."""
        mock_run_cmd.return_value = (
            1,
            "",
            "permission denied"
        )

        with self.assertRaisesRegex(
            RuntimeError,
            "permission denied"
        ):
            _get_user_id_uid(ssh="fake_ssh")


class GetUserIdGidTests(SimpleTestCase):
    """Tests für _get_user_id_gid()."""

    @patch("paas.tasks._run_cmd")
    def test_get_gid_success(self, mock_run_cmd):
        """GID wird erfolgreich ermittelt."""
        mock_run_cmd.return_value = (0, "1002", "")

        result = _get_user_id_gid(ssh="fake_ssh")

        self.assertEqual(result, "1002")
        mock_run_cmd.assert_called_once_with(
            "fake_ssh",
            "id -g deploy"
        )

    @patch("paas.tasks._run_cmd")
    def test_get_gid_with_custom_username(self, mock_run_cmd):
        """GID eines anderen Benutzers wird ermittelt."""
        mock_run_cmd.return_value = (0, "1005", "")

        result = _get_user_id_gid(
            ssh="fake_ssh",
            username="admin"
        )

        self.assertEqual(result, "1005")
        mock_run_cmd.assert_called_once_with(
            "fake_ssh",
            "id -g admin"
        )

    @patch("paas.tasks._run_cmd")
    def test_get_gid_command_error(self, mock_run_cmd):
        """Bei einem fehlgeschlagenen SSH-Kommando wird RuntimeError ausgelöst."""
        mock_run_cmd.return_value = (
            1,
            "",
            "user does not exist"
        )

        with self.assertRaisesRegex(
            RuntimeError,
            "Kann GID für 'deploy' nicht ermitteln"
        ):
            _get_user_id_gid(ssh="fake_ssh")

    @patch("paas.tasks._run_cmd")
    def test_get_gid_error_contains_stderr(self, mock_run_cmd):
        """Die Fehlermeldung enthält die SSH-Fehlerausgabe."""
        mock_run_cmd.return_value = (
            1,
            "",
            "permission denied"
        )

        with self.assertRaisesRegex(
            RuntimeError,
            "permission denied"
        ):
            _get_user_id_gid(ssh="fake_ssh")
