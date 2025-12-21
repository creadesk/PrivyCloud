# prj_PrivyCloud/paas/management/commands/db_start_config.py
"""
Django‑Management‑Command zur Initialisierung der SQLite‑Datenbank
mit den minimalen, für den Start benötigten Datensätzen.

Der Befehl führt eine Reihe von RAW‑SQL‑INSERT‑Statements aus und
speichert die Ergebnisse in der Datenbank, die von Django verwendet
wird (normalerweise <pfad_zu_deinem_Projekt>/db.sqlite3).

Hinweis:
- Das Skript arbeitet mit der Django‑Datenbankverbindung
  (connection.cursor()), sodass es auch mit einer anderen DB‑Engine
  funktionieren würde, sofern die SQL‑Statements kompatibel sind.
- Um Konflikte zu vermeiden, werden die INSERT‑Statements mit
  ``ON CONFLICT IGNORE`` versehen, sodass bereits vorhandene
  Einträge nicht erneut eingefügt werden.
"""

import os
import sys
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction

# ------------------------------------------------------------------
#  Hilfsfunktion: SQL aus einer Liste ausführen
# ------------------------------------------------------------------
def execute_sql_statements(sql_statements):
    """
    Führt eine Liste von SQL‑Statements in einer einzigen Transaktion
    aus.  Fehler werden als CommandError ausgelöst, damit die
    Management‑Command‑Ausführung abbricht.
    """
    with transaction.atomic():
        with connection.cursor() as cursor:
            for stmt in sql_statements:
                try:
                    cursor.execute(stmt)
                except Exception as exc:
                    raise CommandError(f"Fehler beim Ausführen von SQL:\n{stmt}\n{exc}") from exc


# ------------------------------------------------------------------
#  Hauptcommand‑Klasse
# ------------------------------------------------------------------
class Command(BaseCommand):
    help = (
        "Initialisiert die SQLite‑Datenbank mit den Minimal‑Datensätzen, "
        "die für den Start des privaten Cloud‑Setups benötigt werden."
    )

    def handle(self, *args, **options):
        self.stdout.write(self.style.NOTICE("Starte Datenbank‑Initialisierung …"))

        # ------------------------------------------------------------------
        #  1. Definitionen für die Paas‑App‑Definitionen
        # ------------------------------------------------------------------
        paas_appdefinition_sql = [
            """
            INSERT INTO "main"."paas_appdefinition"
            ("id", "name", "display_name", "docker_image", "description",
             "default_duration", "app_port_intern_web", "app_port_intern_api",
             "hiddenservice_port_api", "hiddenservice_port_web", "use_deploy_user")
            VALUES
            ('1', 'it-tools', 'it-tools',
             'ghcr.io/corentinth/it-tools:latest',
             'Nützliche Werkzeuge für Entwickler und Personen, die in der IT arbeiten.',
             '1', '80', '1', '1', '80', '0'),
            ('2', 'uptime-kuma', 'uptime-kuma',
             'louislam/uptime-kuma:latest',
             'Ein einfaches und nützliches Monitoring‑Tool.',
             '1', '3001', '1', '1', '3001', '0'),
            ('3', 'redis', 'redis',
             'redis/redis-stack:latest',
             'In-Memory‑Datenbank für schnelle Lese‑/Schreibzugriffe. Initiales Passwort: mypassword',
             '1', '8001', '6379', '6379', '8001', '0'),
            ('4', 'simplex-smp', 'simplex-smp',
             'simplexchat/smp-server:latest',
             'Ein SimpleX Messaging Protocol-Server.',
             '1', '1', '5223', '5223', '1', '1'),
            ('5', 'simplex-xftp', 'simplex-xftp',
             'simplexchat/xftp-server:latest',
             'SimpleX XFTP Server ist ein Dateiübertragungsprotokoll zum Schutz von Metadaten, das auf den Prinzipien des SimpleX Messaging Protocol (SMP) basiert.',
             '1', '1', '443', '443', '1', '1'),
            ('6', 'owncloud', 'owncloud',
             'owncloud/server:latest',
             'Datunschutzorientiertes Kooperationstool. Initiale Anmeldedaten: User: admin / PW: admin',
             '1', '8080', '1', '1', '8080', '0')
            ON CONFLICT ("id") DO NOTHING;
            """
        ]

        # ------------------------------------------------------------------
        #  2. Paas_AppEnvVarPerApp
        # ------------------------------------------------------------------
        paas_appenvvarperapp_sql = [
            """
            INSERT INTO "main"."paas_appenvvarperapp"
            ("id", "key", "value", "app_id", "optional", "editable")
            VALUES
            ('1', 'ADDR', '<onion_address>', '4', '0', '0'),
            ('2', 'ADDR', '<onion_address>', '5', '0', '0'),
            ('3', 'QUOTA', '1gb', '5', '0', '0'),
            ('4', 'REDIS_ARGS', '"--requirepass mypassword"', '3', '0', '0'),
            ('5', 'OWNCLOUD_TRUSTED_DOMAINS', '<onion_address>', '6', '0', '0')
            ON CONFLICT ("id") DO NOTHING;
            """
        ]

        # ------------------------------------------------------------------
        #  3. Paas_AppVolumePerApp
        # ------------------------------------------------------------------
        paas_appvolumeperapp_sql = [
            """
            INSERT INTO "main"."paas_appvolumeperapp"
            ("id", "host_path", "container_path", "app_id")
            VALUES
            ('1', 'simplex/smp/config', '/etc/opt/simplex:z', '4'),
            ('2', 'simplex/smp/logs', '/var/opt/simplex:z', '4'),
            ('3', 'simplex/xftp/config', '/etc/opt/simplex-xftp:z', '5'),
            ('4', 'simplex/xftp/logs', '/var/opt/simplex-xftp:z', '5'),
            ('5', 'simplex/xftp/files', '/srv/xftp:z', '5')
            ON CONFLICT ("id") DO NOTHING;
            """
        ]

        # ------------------------------------------------------------------
        #  4. Paas_ConfigPatch
        # ------------------------------------------------------------------
        paas_configpatch_sql = [
            """
            INSERT INTO "main"."paas_configpatch"
            ("id", "target_file", "pattern", "action", "replacement", "app_id", "volume_id")
            VALUES
            ('1', 'simplex/smp/config/smp-server.ini', '^(https|cert|key):', 'comment', '', '4', '1')
            ON CONFLICT ("id") DO NOTHING;
            """
        ]

        # ------------------------------------------------------------------
        #  5. Paas_RemoteHost
        # ------------------------------------------------------------------
        paas_remotehost_sql = [
            """
            INSERT INTO "main"."paas_remotehost"
            ("id", "hostname", "ip_address", "ssh_user", "ssh_key_path", "current_load", "nur_superuser")
            VALUES
            ('1', '<dein_zielserver_hostname>', '<deine_zielserver_ip>', 'deploy',
             '<pfad_in_dein_homeverzeichnis>/.ssh/deploy_key', '0.0', '0'),
            ('2', '127.0.0.1', '127.0.0.1', '<dein_lokaler_user>',
             '', '0.0', '1')
            ON CONFLICT ("id") DO NOTHING;
            """
        ]

        # ------------------------------------------------------------------
        #  Alle Statements in einer Liste zusammenführen
        # ------------------------------------------------------------------
        all_sql = (
            paas_appdefinition_sql
            + paas_appenvvarperapp_sql
            + paas_appvolumeperapp_sql
            + paas_configpatch_sql
            + paas_remotehost_sql
        )

        # ------------------------------------------------------------------
        #  Ausführen
        # ------------------------------------------------------------------
        try:
            execute_sql_statements(all_sql)
        except CommandError as e:
            self.stderr.write(self.style.ERROR(str(e)))
            sys.exit(1)

        self.stdout.write(self.style.SUCCESS("Datenbank‑Initialisierung erfolgreich abgeschlossen!"))