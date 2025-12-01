# PrivyCloud – Self‑hosted PaaS

> Eine Django‑basierte Plattform, die es Nutzern ermöglicht,
> Container‑Apps zu deployen, zu skalieren und zu verwalten.
> Die bereitgestellten Apps werden als TOR-Hidden-Service angeboten. 

## Features

- Docker‑basierte App‑Deployments welche als TOR-Hidden-Service veröffentlicht werden


## Quick‑Start

# 1. Klone das Repository
git clone https://github.com/creadesk/PrivyCloud.git

cd prj_PrivyCloud

# 2. Virtuelle Umgebung erstellen
python -m venv .venv

source .venv/bin/activate   # Linux/macOS 

.\.venv\Scripts\activate  # Windows

# 3. Abhängigkeiten installieren
pip install -r requirements.txt

# 4. Datenbank migrieren
python manage.py migrate

# 5. Superuser anlegen
python manage.py createsuperuser

# 6. Server+Celery starten
python manage.py runserver_plus --addrport 0.0.0.0:8000 --loglevel debug



## Konfiguration

### .env - Datei notwendig:

SECRET_KEY='django-insecure-<lange_zufällige_zeichenkette>'

DEBUG=True

ALLOWED_HOSTS=127.0.0.1,localhost


#DB_ENGINE=django.db.backends.postgresql

DB_ENGINE=django.db.backends.sqlite3

DB_NAME=db.sqlite3

DB_USER=

DB_PASSWORD=

DB_HOST=

DB_PORT=


REDIS_SERVER_IP=<ip_redis_server>

REDIS_SERVER_PORT=<port_redis_server>

REDIS_SERVER_DB=<db_nummer> /z.B. 0

### Datenank-Import Startkonfiguration

- benötigte Datensätze in sqlite db einfügen
- 
    sudo apt install sqlite3
- 
    sqlite3 /<pfad_zu_deinem_Projekt>/db.sqlite3


    paas_appdefinition:
        
        INSERT INTO "main"."paas_appdefinition" ("id", "name", "display_name", "docker_image", "description", "default_duration", "app_port_intern_web", "app_port_intern_api", "hiddenservice_port_api", "hiddenservice_port_web", "use_deploy_user") VALUES ('1', 'it-tools', 'it-tools', 'ghcr.io/corentinth/it-tools:latest', 'Nützliche Werkzeuge für Entwickler und Personen, die in der IT arbeiten.', '1', '80', '1', '1', '80', '0');
        INSERT INTO "main"."paas_appdefinition" ("id", "name", "display_name", "docker_image", "description", "default_duration", "app_port_intern_web", "app_port_intern_api", "hiddenservice_port_api", "hiddenservice_port_web", "use_deploy_user") VALUES ('2', 'uptime-kuma', 'uptime-kuma', 'louislam/uptime-kuma:latest', 'Ein einfaches und nützliches Monitoring‑Tool.', '1', '3001', '1', '1', '3001', '0');
        INSERT INTO "main"."paas_appdefinition" ("id", "name", "display_name", "docker_image", "description", "default_duration", "app_port_intern_web", "app_port_intern_api", "hiddenservice_port_api", "hiddenservice_port_web", "use_deploy_user") VALUES ('3', 'redis', 'redis', 'redis/redis-stack:latest', 'In-Memory‑Datenbank für schnelle Lese‑/Schreibzugriffe. Initiales Passwort: mypassword', '1', '8001', '6379', '6379', '8001', '0');
        INSERT INTO "main"."paas_appdefinition" ("id", "name", "display_name", "docker_image", "description", "default_duration", "app_port_intern_web", "app_port_intern_api", "hiddenservice_port_api", "hiddenservice_port_web", "use_deploy_user") VALUES ('4', 'simplex-smp', 'simplex-smp', 'simplexchat/smp-server:latest', 'Ein SimpleX Messaging Protocol-Server.', '1', '1', '5223', '5223', '1', '1');
        INSERT INTO "main"."paas_appdefinition" ("id", "name", "display_name", "docker_image", "description", "default_duration", "app_port_intern_web", "app_port_intern_api", "hiddenservice_port_api", "hiddenservice_port_web", "use_deploy_user") VALUES ('5', 'simplex-xftp', 'simplex-xftp', 'simplexchat/xftp-server:latest', 'SimpleX XFTP Server ist ein Dateiübertragungsprotokoll zum Schutz von Metadaten, das auf den Prinzipien des SimpleX Messaging Protocol (SMP) basiert.', '1', '1', '443', '443', '1', '1');
        INSERT INTO "main"."paas_appdefinition" ("id", "name", "display_name", "docker_image", "description", "default_duration", "app_port_intern_web", "app_port_intern_api", "hiddenservice_port_api", "hiddenservice_port_web", "use_deploy_user") VALUES ('6', 'owncloud', 'owncloud', 'owncloud/server:latest', 'Datunschutzorientiertes Kooperationstool. Initiale Anmeldedaten: User: admin / PW: admin', '1', '8080', '1', '1', '8080', '0');    

    
    paas_appenvvarperapp:

        INSERT INTO "main"."paas_appenvvarperapp" ("id", "key", "value", "app_id", "optional", "editable") VALUES ('1', 'ADDR', '<onion_address>', '4', '0', '0');
        INSERT INTO "main"."paas_appenvvarperapp" ("id", "key", "value", "app_id", "optional", "editable") VALUES ('2', 'ADDR', '<onion_address>', '5', '0', '0');
        INSERT INTO "main"."paas_appenvvarperapp" ("id", "key", "value", "app_id", "optional", "editable") VALUES ('3', 'QUOTA', '1gb', '5', '0', '0');
        INSERT INTO "main"."paas_appenvvarperapp" ("id", "key", "value", "app_id", "optional", "editable") VALUES ('4', 'REDIS_ARGS', '"--requirepass mypassword"', '3', '0', '0');
        INSERT INTO "main"."paas_appenvvarperapp" ("id", "key", "value", "app_id", "optional", "editable") VALUES ('5', 'OWNCLOUD_TRUSTED_DOMAINS', '<onion_address>', '6', '0', '0');    

    
    paas_appvolumeperapp:

        INSERT INTO "main"."paas_appvolumeperapp" ("id", "host_path", "container_path", "app_id") VALUES ('1', 'simplex/smp/config', '/etc/opt/simplex:z', '4');
        INSERT INTO "main"."paas_appvolumeperapp" ("id", "host_path", "container_path", "app_id") VALUES ('2', 'simplex/smp/logs', '/var/opt/simplex:z', '4');
        INSERT INTO "main"."paas_appvolumeperapp" ("id", "host_path", "container_path", "app_id") VALUES ('3', 'simplex/xftp/config', '/etc/opt/simplex-xftp:z', '5');
        INSERT INTO "main"."paas_appvolumeperapp" ("id", "host_path", "container_path", "app_id") VALUES ('4', 'simplex/xftp/logs', '/var/opt/simplex-xftp:z', '5');
        INSERT INTO "main"."paas_appvolumeperapp" ("id", "host_path", "container_path", "app_id") VALUES ('5', 'simplex/xftp/files', '/srv/xftp:z', '5');


    paas_configpatch:

        INSERT INTO "main"."paas_configpatch" ("id", "target_file", "pattern", "action", "replacement", "app_id", "volume_id") VALUES ('1', 'simplex/smp/config/smp-server.ini', '^\(https\|cert\|key\):', 'comment', '', '4', '1');

    paas_remotehost:

        INSERT INTO "main"."paas_remotehost" ("id", "hostname", "ip_address", "ssh_user", "ssh_key_path") VALUES ('1', '<dein_zielserver_hostname>', '<deine_zielserver_ip>', 'deploy', '<pfad_in_dein_homeverzeichnis>/.ssh/deploy_key');
 


## Lizenz

MIT


## Remotehost vorbereiten

#### Update & Docker installieren
sudo apt-get update
sudo apt-get install -y docker.io   # oder: docker-ce für die offizielle Docker‑Repo

#### Neuen Benutzer "deploy" erstellen
sudo adduser --disabled-password --gecos "" deploy
sudo passwd deploy

#### Benutzer zur Docker Gruppe hinzufügen
sudo usermod -aG docker deploy

#### Auf dem Celery‑Host (z.B. dein lokaler Entwicklungsrechner)
ssh-keygen -t ed25519 -C "celery-deploy-key" -f ~/.ssh/deploy_key

Du bekommst ~/.ssh/deploy_key (privat) und ~/.ssh/deploy_key.pub (öffentlich)

#### Rechte auf PrivateKey
chmod 600 ~/.ssh/deploy_key

#### Public Key auf Zielserver kopieren
ssh-copy-id -i ~/.ssh/deploy_key.pub deploy@<ZIEL_IP>

#### ssh Konfiguration auf Zielserver
sudo nano /etc/ssh/sshd_config
-->Folgende Zeilen sicherstellen (oder hinzufügen):
PubkeyAuthentication yes
PasswordAuthentication no        # optional: Passwort‑Login komplett deaktivieren
PermitRootLogin no                # Root‑Login sperren
UsePAM yes

#### Neustart ssh Server auf Zielserver
sudo systemctl restart ssh.service

#### Ggf. Firewall freischalten auf Zielserver
sudo ufw allow OpenSSH
sudo ufw enable

#### Testlogin vom lokalen System
ssh -i ~/.ssh/deploy_key deploy@<ZIEL_IP>

#### Sicherstelen, dass `deploy` nur `sudo docker …` ausführen kann, keine anderen Root‑Kommandos
sudo nano /etc/sudoers.d/99_deploy_docker
deploy ALL=(root) NOPASSWD: /usr/bin/docker

Um systemd-Units nach einem *Boot* ohne User‑Login automatisch zu starten, muss der User **lingering** aktiviert haben:
sudo loginctl enable-linger deploy

#### Sicherstellen, dass Python/Celery den Private Key lesen kann
sudo chown <app_user>:<app_user> ~/.ssh/deploy_key
sudo chmod 600 ~/.ssh/deploy_key
