#!/usr/bin/env python
# -*- coding: utf-8 -*-

# ──────────────────────────────────────────────────────────────────────────────
#  prj_PrivyCloud/paas/management/commands/db_start_config.py
# ──────────────────────────────────────────────────────────────────────────────
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
# ──────────────────────────────────────────────────────────────────────────────

import sys
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction

# ──────────────────────────────────────────────────────────────────────────────
#  Hilfsfunktion: SQL aus einer Liste ausführen
# ──────────────────────────────────────────────────────────────────────────────
def execute_sql_statements(sql_blocks):
    """
    Führt eine Liste von SQL‑Blöcken in einer einzigen Transaktion aus.
    Jeder Block kann mehrere Statements enthalten (durch ';' getrennt).
    Fehler werden als CommandError ausgelöst, damit die
    Management‑Command‑Ausführung abbricht.
    """
    with transaction.atomic():
        with connection.cursor() as cursor:
            for block in sql_blocks:
                # split by ';' – entfernt leere Einträge (z.B. durch letztes ';')
                for stmt in filter(None, (s.strip() for s in block.split(";"))):
                    try:
                        cursor.execute(stmt)
                    except Exception as exc:
                        raise CommandError(
                            f"Fehler beim Ausführen von SQL:\n{stmt}\n{exc}"
                        ) from exc


# ──────────────────────────────────────────────────────────────────────────────
#  Hauptcommand‑Klasse
# ──────────────────────────────────────────────────────────────────────────────
class Command(BaseCommand):
    help = (
        "Initialisiert die SQLite‑Datenbank mit den Minimal‑Datensätzen, "
        "die für den Start des privaten Cloud‑Setups benötigt werden."
    )

    def handle(self, *args, **options):
        self.stdout.write(self.style.NOTICE("Starte Datenbank‑Initialisierung …"))

        # ──────────────────────────────────────────────────────────────────────
        #  1. Paas_AppDefinition
        # ──────────────────────────────────────────────────────────────────────
        paas_appdefinition_sql = [
            """
            INSERT INTO "main"."paas_appdefinition"
                ("id","name","display_name","docker_image","description",
                 "default_duration","app_port_intern_web","app_port_intern_api",
                 "hiddenservice_port_web","hiddenservice_port_api",
                 "use_deploy_user","no_hidden_service")
            VALUES
                ('1','it-tools','it-tools','ghcr.io/corentinth/it-tools:latest',
                 'Nützliche Werkzeuge für Entwickler und Personen, die in der IT arbeiten.',
                 '1','80','1','80','1','0','0'),

                ('2','uptime-kuma','uptime-kuma',
                 'louislam/uptime-kuma:latest',
                 'Ein einfaches und nützliches Monitoring‑Tool.','1',
                 '3001','1','3001','1','0','0'),

                ('3','redis','redis',
                 'redis/redis-stack:latest',
                 'In-Memory‑Datenbank für schnelle Lese‑/Schreibzugriffe. Initiales Passwort: mypassword',
                 '1','8001','6379','8001','6379','0','0'),

                ('4','simplex-smp','simplex-smp',
                 'simplexchat/smp-server:latest',
                 'Ein SimpleX Messaging Protocol-Server.','1',
                 '1','5223','1','5223','1','0'),

                ('5','simplex-xftp','simplex-xftp',
                 'simplexchat/xftp-server:latest',
                 'SimpleX XFTP Server ist ein Dateiübertragungsprotokoll zum Schutz von Metadaten, das auf den Prinzipien des SimpleX Messaging Protocol (SMP) basiert.',
                 '1','1','443','1','443','1','0'),

                ('6','owncloud','owncloud',
                 'owncloud/server:latest',
                 'Datunschutzorientiertes Kooperationstool. Initiale Anmeldedaten: User: admin / PW: admin',
                 '1','8080','1','8080','1','0','0'),

                ('10','postgres_pgadmin4','postgres_pgadmin4',
                 'rchaput/postgres_pgadmin4:latest',
                 'Postgres Datenbank mit pgadmin4 Frontend','1','5050','1','5050','1','0','0'),

                ('11','jellyfin','jellyfin',
                 'jellyfin/jellyfin',
                 'Ein freies Mediensystem, für die Verwaltung und das Streamen von Medien.','1',
                 '8096','7359','8096','7359','1','0'),

                ('13','i2p','i2p',
                 'geti2p/i2p',
                 'Ein anonymes Overlay-Netzwerk – ein Netzwerk innerhalb eines Netzwerks.','1',
                 '7657','4444','7657','4444','1','0'),

                ('14','nostr-rs-relay','nostr-rs-relay',
                 'scsibug/nostr-rs-relay:latest',
                 'Ein Nostr-Server („Notes and Other Stuff Transmitted by Relays“), der Daten für Benutzer speichert und weiterleitet.',
                 '1','8080','1','8080','1','1','0'),

                ('16','snort','snort',
                 'docker.io/dockurr/snort',
                 'Eine Benutzeroberfläche für das Nostr-Protokoll.','1',
                 '8080','1','80','1','0','0'),

                ('17','glances','glances',
                 'nicolargo/glances:latest-full',
                 'Monitoring Tool für Host und Container Überwachung.','1',
                 '61208','1','61208','1','0','0'),

                ('18','signal-cli-rest-api','signal-cli-rest-api',
                 'bbernhard/signal-cli-rest-api',
                 'Eine kleiner Wrapper für das Befehlszeilentool „signal-cli“. Link zur Registrierung als zweites Gerät: http://<onion-adresse>:<port>/v1/qrcodelink?device_name=signal-api','1','8080','1','8080','1','0','0'),

                ('20','wordpress','wordpress',
                 'wordpress',
                 'Ein leistungsfähiges Content-Management-System.','1','80','1','80','1','1','0'),

                ('21','mysql','mysql',
                 'mysql:latest',
                 'Eine open source Datenbank.','1','1','3306','1','1','1','1'),

                ('22','phpmyadmin','phpmyadmin',
                 'phpmyadmin',
                 'Ein kostenloses, in PHP geschriebenes Software-Tool, das für die Verwaltung von MySQL über das Internet gedacht ist.',
                 '1','80','1','80','1','0','0')
            ;
            """
        ]

        # ──────────────────────────────────────────────────────────────────────
        #  2. Paas_AppEnvVarPerApp
        # ──────────────────────────────────────────────────────────────────────
        paas_appenvvarperapp_sql = [
            """
            INSERT INTO "main"."paas_appenvvarperapp"
                ("id","key","value","optional","editable","app_id")
            VALUES
                ('1','ADDR','<onion_address>','0','0','4'),
                ('2','ADDR','<onion_address>','0','0','5'),
                ('3','QUOTA','1gb','0','0','5'),
                ('4','REDIS_ARGS','"--requirepass mypassword"','0','0','3'),
                ('5','OWNCLOUD_TRUSTED_DOMAINS','<onion_address>','0','0','6'),
                ('9','JVM_XMX','512m','1','1','13'),
                ('11','TZ','"${TZ}"','0','0','17'),
                ('12','GLANCES_OPT','"-w"','0','0','17'),
                ('13','MODE','native','0','0','18'),
                ('22','MYSQL_ROOT_PASSWORD','<db_root_password>','0','1','21'),
                ('23','MYSQL_DATABASE','<db_name>','1','1','21'),
                ('24','MYSQL_USER','<db_user>','1','1','21'),
                ('25','MYSQL_PASSWORD','<db_user_password>','1','1','21'),
                ('26','PMA_HOST','<db_host_ip>','1','1','22'),
                ('27','PMA_PORT','<db_port>','1','1','22')
            ;
            """
        ]

        # ──────────────────────────────────────────────────────────────────────
        #  3. Paas_AppVolumePerApp
        # ──────────────────────────────────────────────────────────────────────
        paas_appvolumeperapp_sql = [
            """
            INSERT INTO "main"."paas_appvolumeperapp"
                ("id","host_path","container_path","app_id")
            VALUES
                ('1','simplex/smp/config','/etc/opt/simplex:z','4'),
                ('2','simplex/smp/logs','/var/opt/simplex:z','4'),
                ('3','simplex/xftp/config','/etc/opt/simplex-xftp:z','5'),
                ('4','simplex/xftp/logs','/var/opt/simplex-xftp:z','5'),
                ('5','simplex/xftp/files','/srv/xftp:z','5'),
                ('8','postgres-pgadmin4-data','/root/data','10'),
                ('9','jellyfin-config','/config','11'),
                ('10','jellyfin-cache','/cache','11'),
                ('11','jellyfin-media','/media','11'),
                ('13','i2pconfig','/i2p/.i2p','13'),
                ('14','i2ptorrents','/i2psnark','13'),
                ('15','nostr_rs_relay/config.toml','/usr/src/app/config.toml','14'),
                ('16','nostr_rs_relay/data','/usr/src/app/db','14'),
                ('17','signal-api-data','/home/.local/share/signal-cli','18'),
                ('19','wordpress-data','/var/www/html','20'),
                ('20','mysql-data','/var/lib/mysql','21')
            ;
            """
        ]

        # ──────────────────────────────────────────────────────────────────────
        #  4. Paas_ConfigPatch
        # ──────────────────────────────────────────────────────────────────────
        paas_configpatch_sql = [
            """
            INSERT INTO "main"."paas_configpatch"
                ("id","target_file","pattern","action","replacement","app_id","volume_id")
            VALUES
                ('1','simplex/smp/config/smp-server.ini','^\\(https\\|cert\\|key\\):','comment','','4','1'),
                ('2','i2pconfig/router.config','^routerconsole','add','routerconsole.allowedHosts=<onion_address>','13','13')
            ;
            """
        ]

        # ──────────────────────────────────────────────────────────────────────
        #  5. Paas_RemoteHost
        # ──────────────────────────────────────────────────────────────────────
        paas_remotehost_sql = [
            """
            INSERT INTO "main"."paas_remotehost"
                ("id","hostname","ip_address","ssh_user","ssh_key_path",
                 "current_load","nur_superuser")
            VALUES
                ('1','192.168.1.113','192.168.1.113','deploy','/home/user/.ssh/deploy_key','10.0','0'),
                ('2','127.0.0.1','127.0.0.1','deploy','/home/user/.ssh/deploy_key_local','10.0','1')
            ;
            """
        ]

        # ──────────────────────────────────────────────────────────────────────
        #  Alle Statements in einer Liste zusammenführen
        # ──────────────────────────────────────────────────────────────────────
        all_sql_blocks = (
            paas_appdefinition_sql
            + paas_appenvvarperapp_sql
            + paas_appvolumeperapp_sql
            + paas_configpatch_sql
            + paas_remotehost_sql
        )

        # ──────────────────────────────────────────────────────────────────────
        #  Ausführen
        # ──────────────────────────────────────────────────────────────────────
        try:
            execute_sql_statements(all_sql_blocks)
        except CommandError as e:
            self.stderr.write(self.style.ERROR(str(e)))
            sys.exit(1)

        self.stdout.write(
            self.style.SUCCESS("Datenbank‑Initialisierung erfolgreich abgeschlossen!")
        )
