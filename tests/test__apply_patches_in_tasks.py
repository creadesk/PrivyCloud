'''
Test --> Erwartetes Verhalten
COMMENT --> Korrektes sed-Kommando
REPLACE --> Replacement und Escaping von /
DELETE --> Korrektes Löschkommando
ADD --> Platzhalter <onion_address> wird ersetzt
ADD --> Container wird anschließend neu gestartet
ADD --> Fehlendes Replacement führt zu ValueError
Unbekannte Aktion --> ValueError
Datei nicht vorhanden --> FileNotFoundError
SSH-Kommando schlägt fehl --> RuntimeError
Mehrere Patches --> Alle Patches werden nacheinander ausgeführt
Dateipfad --> Pfad wird korrekt zusammengesetzt
'''
import shlex
from unittest.mock import patch, call

from django.test import TestCase

from paas.models import ( AppDefinition, RemoteHost, ConfigPatch, )

from paas.tasks import _apply_patches

class ApplyPatchesTest(TestCase):
    def setUp(self):
        self.app = AppDefinition.objects.create(
            name="test-app",
            display_name="Test App",
            docker_image="test/image",
        )

        self.host = RemoteHost.objects.create(
            hostname="test.example.com",
            ip_address="192.0.2.10",
            ssh_user="deploy",
        )

        self.provision = type(
            "Provision",
            (),
            {"container_name": "test-container"}
        )()

        self.ssh = object()

        self.onion_addr = "abcdefghijklmnop.onion"

        self.base_path = "/home/deploy/test-container"

        self.file_path = (
            f"{self.base_path}/config/test.ini"
        )

    def create_patch(
            self,
            action,
            pattern="^https:",
            replacement=None,
            target_file="/config/test.ini",
    ):
        return ConfigPatch.objects.create(
            app=self.app,
            target_file=target_file,
            pattern=pattern,
            action=action,
            replacement=replacement,
        )

    def apply_patches(self):
        return _apply_patches(
            self.ssh,
            self.app,
            self.host,
            self.provision,
            self.onion_addr,
        )

    # ==========================================================
    # COMMENT
    # ==========================================================

    @patch("paas.tasks._run_cmd")
    @patch("paas.tasks._file_exists", return_value=True)
    def test_comment_patch(self, mock_exists, mock_run):
        """Passende Zeilen werden auskommentiert."""

        self.create_patch(ConfigPatch.ACTION_COMMENT)

        mock_run.return_value = (0, "", "")

        self.apply_patches()

        expression = "s/^https:/#&/"

        expected_cmd = (
            f"sed -E -i {shlex.quote(expression)} "
            f"{shlex.quote(self.file_path)}"
        )

        mock_exists.assert_called_once_with(
            self.ssh,
            self.file_path,
            timeout=60,
            interval=6.0,
        )

        mock_run.assert_called_once_with(
            self.ssh,
            expected_cmd,
        )

    @patch("paas.tasks._run_cmd")
    @patch("paas.tasks._file_exists", return_value=True)
    def test_comment_pattern_with_slash(
            self, mock_exists, mock_run
    ):
        """Slashes im COMMENT-Pattern werden escaped."""

        self.create_patch(
            ConfigPatch.ACTION_COMMENT,
            pattern="^https://",
        )

        mock_run.return_value = (0, "", "")

        self.apply_patches()

        expression = r"s/^https:\/\//#&/"

        expected_cmd = (
            f"sed -E -i {shlex.quote(expression)} "
            f"{shlex.quote(self.file_path)}"
        )

        mock_run.assert_called_once_with(
            self.ssh,
            expected_cmd,
        )

    # ==========================================================
    # REPLACE
    # ==========================================================

    @patch("paas.tasks._run_cmd")
    @patch("paas.tasks._file_exists", return_value=True)
    def test_replace_patch(self, mock_exists, mock_run):
        """Replacement wird korrekt ausgeführt."""

        self.create_patch(
            ConfigPatch.ACTION_REPLACE,
            pattern="^https:.*",
            replacement="https://example.com/test",
        )

        mock_run.return_value = (0, "", "")

        self.apply_patches()

        expression = (
            r"s/^https:.*/https:\/\/example.com\/test/"
        )

        expected_cmd = (
            f"sed -E -i {shlex.quote(expression)} "
            f"{shlex.quote(self.file_path)}"
        )

        mock_run.assert_called_once_with(
            self.ssh,
            expected_cmd,
        )

    @patch("paas.tasks._run_cmd")
    @patch("paas.tasks._file_exists", return_value=True)
    def test_replace_escapes_ampersand(
            self, mock_exists, mock_run
    ):
        """& wird als normales Zeichen behandelt."""

        self.create_patch(
            ConfigPatch.ACTION_REPLACE,
            pattern="^url:.*",
            replacement="https://example.com/?a=1&b=2",
        )

        mock_run.return_value = (0, "", "")

        self.apply_patches()

        expression = (
            r"s/^url:.*/https:\/\/example.com\/?a=1\&b=2/"
        )

        expected_cmd = (
            f"sed -E -i {shlex.quote(expression)} "
            f"{shlex.quote(self.file_path)}"
        )

        mock_run.assert_called_once_with(
            self.ssh,
            expected_cmd,
        )

    @patch("paas.tasks._run_cmd")
    @patch("paas.tasks._file_exists", return_value=True)
    def test_replace_escapes_backslash(
            self, mock_exists, mock_run
    ):
        """Backslashes im Replacement werden escaped."""

        self.create_patch(
            ConfigPatch.ACTION_REPLACE,
            pattern="^path:.*",
            replacement=r"C:\test\config",
        )

        mock_run.return_value = (0, "", "")

        self.apply_patches()

        expression = (
            r"s/^path:.*/C:\\test\\config/"
        )

        expected_cmd = (
            f"sed -E -i {shlex.quote(expression)} "
            f"{shlex.quote(self.file_path)}"
        )

        mock_run.assert_called_once_with(
            self.ssh,
            expected_cmd,
        )

    @patch("paas.tasks._run_cmd")
    @patch("paas.tasks._file_exists", return_value=True)
    def test_replace_with_none_uses_empty_string(
            self, mock_exists, mock_run
    ):
        """Ein fehlendes Replacement wird als leerer String behandelt."""

        self.create_patch(
            ConfigPatch.ACTION_REPLACE,
            pattern="^https:.*",
            replacement=None,
        )

        mock_run.return_value = (0, "", "")

        self.apply_patches()

        expression = "s/^https:.*/" + "/"

        expected_cmd = (
            f"sed -E -i {shlex.quote(expression)} "
            f"{shlex.quote(self.file_path)}"
        )

        mock_run.assert_called_once_with(
            self.ssh,
            expected_cmd,
        )

    @patch("paas.tasks._run_cmd")
    @patch("paas.tasks._file_exists", return_value=True)
    def test_replace_shell_metacharacters_are_quoted(
            self, mock_exists, mock_run
    ):
        """Shell-Metazeichen werden nicht als Shell-Befehle interpretiert."""

        replacement = (
            "https://example.com/$(touch /tmp/pwned); test"
        )

        self.create_patch(
            ConfigPatch.ACTION_REPLACE,
            pattern="^url:.*",
            replacement=replacement,
        )

        mock_run.return_value = (0, "", "")

        self.apply_patches()

        expression = (
            r"s/^url:.*/https:\/\/example.com\/$(touch \/tmp\/pwned); test/"
        )

        expected_cmd = (
            f"sed -E -i {shlex.quote(expression)} "
            f"{shlex.quote(self.file_path)}"
        )

        mock_run.assert_called_once_with(
            self.ssh,
            expected_cmd,
        )

    # ==========================================================
    # DELETE
    # ==========================================================

    @patch("paas.tasks._run_cmd")
    @patch("paas.tasks._file_exists", return_value=True)
    def test_delete_patch(self, mock_exists, mock_run):
        """Passende Zeilen werden gelöscht."""

        self.create_patch(
            ConfigPatch.ACTION_DELETE,
            pattern="^https:",
        )

        mock_run.return_value = (0, "", "")

        self.apply_patches()

        expression = "/^https:/d"

        expected_cmd = (
            f"sed -E -i {shlex.quote(expression)} "
            f"{shlex.quote(self.file_path)}"
        )

        mock_run.assert_called_once_with(
            self.ssh,
            expected_cmd,
        )

    @patch("paas.tasks._run_cmd")
    @patch("paas.tasks._file_exists", return_value=True)
    def test_delete_pattern_without_anchor(
            self, mock_exists, mock_run
    ):
        """DELETE akzeptiert auch Patterns ohne ^."""

        self.create_patch(
            ConfigPatch.ACTION_DELETE,
            pattern="https:",
        )

        mock_run.return_value = (0, "", "")

        self.apply_patches()

        expression = "/https:/d"

        expected_cmd = (
            f"sed -E -i {shlex.quote(expression)} "
            f"{shlex.quote(self.file_path)}"
        )

        mock_run.assert_called_once_with(
            self.ssh,
            expected_cmd,
        )

    # ==========================================================
    # ADD
    # ==========================================================

    @patch("paas.tasks._run_cmd")
    @patch("paas.tasks._file_exists", return_value=True)
    def test_add_patch(self, mock_exists, mock_run):
        """Zeile wird angehängt und Container neu gestartet."""

        self.create_patch(
            ConfigPatch.ACTION_ADD,
            replacement="https://<onion_address>/api",
        )

        mock_run.return_value = (0, "", "")

        self.apply_patches()

        replacement = (
            f"https://{self.onion_addr}/api"
        )

        expected_add = (
            f"printf '%s\\n' {shlex.quote(replacement)} "
            f">> {shlex.quote(self.file_path)}"
        )

        expected_restart = (
            f"docker restart {shlex.quote('test-container')}"
        )

        self.assertEqual(
            mock_run.call_args_list,
            [
                call(self.ssh, expected_add),
                call(self.ssh, expected_restart),
            ],
        )

    @patch("paas.tasks._run_cmd")
    @patch("paas.tasks._file_exists", return_value=True)
    def test_add_replacement_with_special_characters(
            self, mock_exists, mock_run
    ):
        """Sonderzeichen im ADD-Text werden sicher gequotet."""

        replacement = (
            'url="https://example.com/?a=1&b=2"; test'
        )

        self.create_patch(
            ConfigPatch.ACTION_ADD,
            replacement=replacement,
        )

        mock_run.return_value = (0, "", "")

        self.apply_patches()

        expected_add = (
            f"printf '%s\\n' {shlex.quote(replacement)} "
            f">> {shlex.quote(self.file_path)}"
        )

        self.assertEqual(
            mock_run.call_args_list[0],
            call(self.ssh, expected_add),
        )

    @patch("paas.tasks._run_cmd")
    @patch("paas.tasks._file_exists", return_value=True)
    def test_add_replacement_with_percent(
            self, mock_exists, mock_run
    ):
        """Prozentzeichen werden von printf nicht interpretiert."""

        replacement = "timeout=100%"

        self.create_patch(
            ConfigPatch.ACTION_ADD,
            replacement=replacement,
        )

        mock_run.return_value = (0, "", "")

        self.apply_patches()

        expected_add = (
            f"printf '%s\\n' {shlex.quote(replacement)} "
            f">> {shlex.quote(self.file_path)}"
        )

        self.assertEqual(
            mock_run.call_args_list[0],
            call(self.ssh, expected_add),
        )

    @patch("paas.tasks._run_cmd")
    @patch("paas.tasks._file_exists", return_value=True)
    def test_add_without_onion_address(
            self, mock_exists, mock_run
    ):
        """None als Onion-Adresse wird durch einen leeren String ersetzt."""

        self.create_patch(
            ConfigPatch.ACTION_ADD,
            replacement="hiddenservice=<onion_address>",
        )

        mock_run.return_value = (0, "", "")

        _apply_patches(
            self.ssh,
            self.app,
            self.host,
            self.provision,
            None,
        )

        expected_add = (
            f"printf '%s\\n' "
            f"{shlex.quote('hiddenservice=')} "
            f">> {shlex.quote(self.file_path)}"
        )

        self.assertEqual(
            mock_run.call_args_list[0],
            call(self.ssh, expected_add),
        )

    @patch("paas.tasks._run_cmd")
    @patch("paas.tasks._file_exists", return_value=True)
    def test_add_without_replacement_raises_value_error(
            self, mock_exists, mock_run
    ):
        """ADD ohne Replacement wird abgelehnt."""

        self.create_patch(
            ConfigPatch.ACTION_ADD,
            replacement=None,
        )

        with self.assertRaisesRegex(
                ValueError,
                "replacement-Feld",
        ):
            self.apply_patches()

        mock_run.assert_not_called()

    @patch("paas.tasks._run_cmd")
    @patch("paas.tasks._file_exists", return_value=True)
    def test_add_with_empty_replacement_raises_value_error(
            self, mock_exists, mock_run
    ):
        """ADD mit leerem Replacement wird abgelehnt."""

        self.create_patch(
            ConfigPatch.ACTION_ADD,
            replacement="",
        )

        with self.assertRaises(ValueError):
            self.apply_patches()

        mock_run.assert_not_called()

    # ==========================================================
    # Container-Neustart
    # ==========================================================

    @patch("paas.tasks._run_cmd")
    @patch("paas.tasks._file_exists", return_value=True)
    def test_add_failure_does_not_restart_container(
            self, mock_exists, mock_run
    ):
        """Bei fehlgeschlagenem ADD wird nicht neu gestartet."""

        self.create_patch(
            ConfigPatch.ACTION_ADD,
            replacement="some configuration",
        )

        mock_run.return_value = (
            1,
            "",
            "append failed",
        )

        with self.assertRaisesRegex(
                RuntimeError,
                "Patch add failed",
        ):
            self.apply_patches()

        mock_run.assert_called_once()

    @patch("paas.tasks._run_cmd")
    @patch("paas.tasks._file_exists", return_value=True)
    def test_restart_failure_raises_runtime_error(
            self, mock_exists, mock_run
    ):
        """Ein fehlgeschlagener Container-Neustart wird erkannt."""

        self.create_patch(
            ConfigPatch.ACTION_ADD,
            replacement="some configuration",
        )

        mock_run.side_effect = [
            (0, "", ""),
            (1, "", "docker restart failed"),
        ]

        with self.assertRaisesRegex(
                RuntimeError,
                "Patch add failed",
        ):
            self.apply_patches()

        self.assertEqual(mock_run.call_count, 2)

    # ==========================================================
    # Fehlerbehandlung
    # ==========================================================

    @patch("paas.tasks._run_cmd")
    @patch("paas.tasks._file_exists", return_value=True)
    def test_unknown_action_raises_value_error(
            self, mock_exists, mock_run
    ):
        """Unbekannte Patch-Aktionen werden vor SSH abgelehnt."""

        self.create_patch("unknown")

        with self.assertRaisesRegex(
                ValueError,
                "Unbekannte Patch-Aktion",
        ):
            self.apply_patches()

        mock_exists.assert_not_called()
        mock_run.assert_not_called()

    @patch("paas.tasks._run_cmd")
    @patch("paas.tasks._file_exists", return_value=False)
    def test_missing_target_file_raises_file_not_found(
            self, mock_exists, mock_run
    ):
        """Eine nicht vorhandene Remote-Datei führt zu FileNotFoundError."""

        self.create_patch(ConfigPatch.ACTION_COMMENT)

        with self.assertRaises(FileNotFoundError):
            self.apply_patches()

        mock_exists.assert_called_once_with(
            self.ssh,
            self.file_path,
            timeout=60,
            interval=6.0,
        )

        mock_run.assert_not_called()

    @patch("paas.tasks._run_cmd")
    @patch("paas.tasks._file_exists", return_value=True)
    def test_comment_command_failure(
            self, mock_exists, mock_run
    ):
        mock_run.return_value = (
            1,
            "",
            "permission denied",
        )

        self.create_patch(ConfigPatch.ACTION_COMMENT)

        with self.assertRaisesRegex(
                RuntimeError,
                "Patch comment failed",
        ):
            self.apply_patches()

    @patch("paas.tasks._run_cmd")
    @patch("paas.tasks._file_exists", return_value=True)
    def test_replace_command_failure(
            self, mock_exists, mock_run
    ):
        mock_run.return_value = (
            1,
            "",
            "replace failed",
        )

        self.create_patch(
            ConfigPatch.ACTION_REPLACE,
            replacement="https://example.com",
        )

        with self.assertRaisesRegex(
                RuntimeError,
                "Patch replace failed",
        ):
            self.apply_patches()

    @patch("paas.tasks._run_cmd")
    @patch("paas.tasks._file_exists", return_value=True)
    def test_delete_command_failure(
            self, mock_exists, mock_run
    ):
        mock_run.return_value = (
            1,
            "",
            "delete failed",
        )

        self.create_patch(ConfigPatch.ACTION_DELETE)

        with self.assertRaisesRegex(
                RuntimeError,
                "Patch delete failed",
        ):
            self.apply_patches()

    # ==========================================================
    # Dateipfade
    # ==========================================================

    @patch("paas.tasks._run_cmd")
    @patch("paas.tasks._file_exists", return_value=True)
    def test_target_file_without_leading_slash(
            self, mock_exists, mock_run
    ):
        """Relative Pfade ohne führenden Slash funktionieren."""

        self.create_patch(
            ConfigPatch.ACTION_COMMENT,
            target_file="config/test.ini",
        )

        mock_run.return_value = (0, "", "")

        self.apply_patches()

        mock_exists.assert_called_once_with(
            self.ssh,
            self.file_path,
            timeout=60,
            interval=6.0,
        )

    @patch("paas.tasks._run_cmd")
    @patch("paas.tasks._file_exists", return_value=True)
    def test_target_file_with_spaces(
            self, mock_exists, mock_run
    ):
        """Leerzeichen im Dateipfad werden korrekt gequotet."""

        self.create_patch(
            ConfigPatch.ACTION_COMMENT,
            target_file="/config/my test.ini",
        )

        mock_run.return_value = (0, "", "")

        self.apply_patches()

        expected_path = (
            f"{self.base_path}/config/my test.ini"
        )

        mock_exists.assert_called_once_with(
            self.ssh,
            expected_path,
            timeout=60,
            interval=6.0,
        )

        command = mock_run.call_args.args[1]

        self.assertIn(
            shlex.quote(expected_path),
            command,
        )

    @patch("paas.tasks._run_cmd")
    @patch("paas.tasks._file_exists")
    def test_target_file_cannot_escape_base_directory(
            self, mock_exists, mock_run
    ):
        """Path Traversal über ../../ wird verhindert."""

        self.create_patch(
            ConfigPatch.ACTION_COMMENT,
            target_file="../../etc/passwd",
        )

        with self.assertRaisesRegex(
                ValueError,
                "Ungültiger Patch-Dateipfad",
        ):
            self.apply_patches()

        mock_exists.assert_not_called()
        mock_run.assert_not_called()

    @patch("paas.tasks._run_cmd")
    @patch("paas.tasks._file_exists")
    def test_absolute_target_file_cannot_escape_base_directory(
            self, mock_exists, mock_run
    ):
        """Auch absolute Pfadangaben mit Path Traversal werden verhindert."""

        self.create_patch(
            ConfigPatch.ACTION_COMMENT,
            target_file="/../../etc/passwd",
        )

        with self.assertRaises(ValueError):
            self.apply_patches()

        mock_exists.assert_not_called()
        mock_run.assert_not_called()

    # ==========================================================
    # Mehrere Patches
    # ==========================================================

    @patch("paas.tasks._run_cmd")
    @patch("paas.tasks._file_exists", return_value=True)
    def test_multiple_patches_are_executed_in_order(
            self, mock_exists, mock_run
    ):
        """Mehrere Patches werden nach ihrer ID ausgeführt."""

        self.create_patch(
            ConfigPatch.ACTION_COMMENT,
            pattern="^https:",
        )

        self.create_patch(
            ConfigPatch.ACTION_REPLACE,
            pattern="^cert:.*",
            replacement="/etc/ssl/cert.pem",
        )

        self.create_patch(
            ConfigPatch.ACTION_DELETE,
            pattern="^key:",
        )

        mock_run.return_value = (0, "", "")

        self.apply_patches()

        self.assertEqual(mock_run.call_count, 3)

        commands = [
            item.args[1]
            for item in mock_run.call_args_list
        ]

        self.assertIn(
            shlex.quote("s/^https:/#&/"),
            commands[0],
        )

        self.assertIn(
            shlex.quote(r"s/^cert:.*/\/etc\/ssl\/cert.pem/"),
            commands[1],
        )

        self.assertIn(
            shlex.quote("/^key:/d"),
            commands[2],
        )

        self.assertEqual(mock_exists.call_count, 3)

    # ==========================================================
    # Keine Patches
    # ==========================================================

    @patch("paas.tasks._run_cmd")
    @patch("paas.tasks._file_exists")
    def test_no_patches_executes_no_commands(
            self, mock_exists, mock_run
    ):
        """Ohne Patches erfolgen keine Remote-Operationen."""

        self.apply_patches()

        mock_exists.assert_not_called()
        mock_run.assert_not_called()
