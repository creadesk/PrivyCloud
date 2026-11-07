'''
SSH-Konfiguration wird korrekt eingelesen.
Richtiger Host wird über lookup() ausgewählt.
Mehrere Host-Einträge werden korrekt unterschieden.
Globale Einstellungen (Host *) werden berücksichtigt.
Fehlende SSH-Konfigurationsdatei löst FileNotFoundError aus.
Der Pfad wird über expanduser() ermittelt.
Ein unbekannter Host liefert das Ergebnis von Paramiko zurück (normalerweise ein leeres Dictionary).
'''


import os
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.test import SimpleTestCase

from paas.tasks import load_ssh_config


class LoadSSHConfigTests(SimpleTestCase):

    def setUp(self):
        # Temporäres Home-Verzeichnis erzeugen
        self.temp_dir = tempfile.TemporaryDirectory()
        self.home_dir = Path(self.temp_dir.name)

        # SSH-Verzeichnis erzeugen
        self.ssh_dir = self.home_dir / ".ssh"
        self.ssh_dir.mkdir()

        self.config_file = self.ssh_dir / "config"

        # HOME für diesen Test auf das temporäre Verzeichnis setzen
        self.home_patch = patch.dict(
            os.environ,
            {"HOME": str(self.home_dir)}
        )
        self.home_patch.start()

    def tearDown(self):
        self.home_patch.stop()
        self.temp_dir.cleanup()

    def write_config(self, content):
        self.config_file.write_text(content, encoding="utf-8")

    def test_load_config_returns_host_options(self):
        """
        Eine vorhandene SSH-Konfiguration wird korrekt eingelesen.
        """
        self.write_config("""
Host server1
    HostName 192.168.1.10
    User deploy
    Port 2222
    IdentityFile ~/.ssh/deploy_key
""")

        result = load_ssh_config("server1")

        self.assertEqual(result["hostname"], "192.168.1.10")
        self.assertEqual(result["user"], "deploy")
        self.assertEqual(result["port"], "2222")
        self.assertIn("identityfile", result)

    def test_load_config_selects_correct_host(self):
        """
        Bei mehreren Host-Einträgen wird der richtige Host ausgewählt.
        """
        self.write_config("""
Host server1
    HostName 192.168.1.10
    User deploy1

Host server2
    HostName 192.168.1.20
    User deploy2
""")

        result = load_ssh_config("server2")

        self.assertEqual(result["hostname"], "192.168.1.20")
        self.assertEqual(result["user"], "deploy2")

    def test_load_config_uses_global_host_settings(self):
        """
        Globale Einstellungen werden als Standardwerte verwendet.
        Spezifische Host-Einstellungen haben Vorrang, wenn sie
        vor dem globalen Host * definiert sind.
        """
        self.write_config("""
Host server1
    HostName 192.168.1.10
    User deploy

Host *
    User defaultuser
    ServerAliveInterval 60
""")

        result = load_ssh_config("server1")

        self.assertEqual(result["hostname"], "192.168.1.10")
        self.assertEqual(result["user"], "deploy")
        self.assertEqual(result["serveraliveinterval"], "60")

    def test_load_config_missing_file_raises_file_not_found(self):
        """
        Fehlt ~/.ssh/config, wird FileNotFoundError ausgelöst.
        """
        with self.assertRaises(FileNotFoundError) as context:
            load_ssh_config("server1")

        self.assertIn(
            str(self.config_file),
            str(context.exception)
        )

    def test_load_config_unknown_host_returns_hostname(self):
        """
        Paramiko liefert bei einem unbekannten Host mindestens
        den angefragten Hostnamen zurück.
        """
        self.write_config("""
Host server1
    HostName 192.168.1.10
    User deploy
""")

        result = load_ssh_config("unknown-server")

        self.assertEqual(
            result,
            {"hostname": "unknown-server"}
        )

    def test_load_config_supports_multiple_identity_files(self):
        """
        Mehrere IdentityFile-Einträge werden als Liste zurückgegeben.
        """
        self.write_config("""
Host server1
    HostName 192.168.1.10
    User deploy
    IdentityFile ~/.ssh/key1
    IdentityFile ~/.ssh/key2
""")

        result = load_ssh_config("server1")

        expected_keys = [
            str(self.ssh_dir / "key1"),
            str(self.ssh_dir / "key2"),
        ]

        self.assertEqual(
            result["identityfile"],
            expected_keys
        )

    def test_load_config_uses_expanded_config_path(self):
        """
        Die Konfigurationsdatei wird im Home-Verzeichnis gesucht.
        """
        self.write_config("""
Host server1
    User deploy
""")

        expected_path = self.home_dir / ".ssh" / "config"

        self.assertTrue(expected_path.exists())

        result = load_ssh_config("server1")

        self.assertEqual(result["user"], "deploy")