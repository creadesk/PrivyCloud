# PrivyCloud – Self‑hosted PaaS

> Eine Django‑basierte Plattform, die es Nutzern ermöglicht,
> Container‑Apps zu deployen, zu skalieren und zu verwalten.
> Die bereitgestellten Apps werden als TOR-Hidden-Service angeboten. 

# Inhaltsverzeichnis

- [Features](#features)
- [Voraussetzungen](#voraussetzungen)
  - [Redis](#redis)
- [Einrichtung](#einrichtung)
- [Remotehost vorbereiten](#remotehost-vorbereiten)
- [Web‑Zugriff](#web-zugriff)
- [Dienste einrichten](#dienste-einrichten)
- [Docker](#docker)
- [Lizenz](#lizenz)

## Features

- Docker‑basierte App‑Deployments welche als TOR-Hidden-Service veröffentlicht werden

## Voraussetzungen

### 1. Redis 
- muss bereits installiert bzw. verfügbar sein
- falls nicht wäre die einfachste Möglichkeit REDIS per Docker bereitzustellen
- REDIS Server-Only:
    ```bash
    docker run -d --name redis-stack-server --restart unless-stopped -p 6379:6379 redis/redis-stack-server:latest
    ```
- REDIS Server + REDIS-Insight(mit Weboberfläche)
    ```bash
    docker run -d --name redis-stack --restart unless-stopped -p 6379:6379 -p 8001:8001 redis/redis-stack:latest
    ```
    - Web-Zugriff: http://<dein_redis_host>:8001 

## Einrichtung

### 1. Klone das Repository
```bash
git clone https://github.com/creadesk/PrivyCloud.git
cd prj_PrivyCloud
```

### 2. Virtuelle Umgebung erstellen
```bash
python -m venv .venv

source .venv/bin/activate   # Linux/macOS 

.\.venv\Scripts\activate  # Windows
```

### 3. Abhängigkeiten installieren
```bash
pip install -r requirements.txt
```

### 4. Datenbank migrieren
```bash
python manage.py migrate
```

### 5. Superuser anlegen
```bash
python manage.py createsuperuser
```

### 6. .env-Datei anlegen:
```dotenv
SECRET_KEY='django-insecure-<lange_zufällige_zeichenkette>'
DEBUG=True
ALLOWED_HOSTS=127.0.0.1,localhost
STRING_TO_ADMIN_PAGE=<beliebige_individuelle_zeichenkette>/

#DB_ENGINE=django.db.backends.postgresql
DB_ENGINE=django.db.backends.sqlite3
DB_NAME=db/db.sqlite3
DB_USER=
DB_PASSWORD=
DB_HOST=
DB_PORT=

REDIS_SERVER_IP=<ip_redis_server>
REDIS_SERVER_PORT=<port_redis_server>
REDIS_SERVER_DB=<db_nummer>
```

### 7. Server+Celery starten
```bash
python manage.py runserver_plus --addrport 0.0.0.0:8000 --loglevel debug
```


## Datenank-Import Startkonfiguration
- mindestens benötigte Datensätze in sqlite db einfügen
```bash
python manage.py db_start_config
```



## Remotehost vorbereiten

#### Update & Docker installieren
```bash
sudo apt-get update

sudo apt-get install -y docker.io   # oder: docker-ce für die offizielle Docker‑Repo
```

#### Neuen Benutzer "deploy" erstellen
```bash
sudo adduser --disabled-password --gecos "" deploy
sudo passwd deploy
```

#### Benutzer zur Docker Gruppe hinzufügen
```bash
sudo usermod -aG docker deploy
```

#### Auf dem Celery‑Host (z.B. dein lokaler Entwicklungsrechner)
```bash
ssh-keygen -t ed25519 -C "celery-deploy-key" -f ~/.ssh/deploy_key
```
Du bekommst ~/.ssh/deploy_key (privat) und ~/.ssh/deploy_key.pub (öffentlich)

#### Rechte auf PrivateKey
```bash
chmod 600 ~/.ssh/deploy_key
```

#### Public Key von lokalem System auf den Zielserver kopieren
```bash
ssh-copy-id -i ~/.ssh/deploy_key.pub deploy@<ZIEL_IP>
```

#### ssh Konfiguration auf Zielserver
```bash
sudo nano /etc/ssh/sshd_config
-->Folgende Zeilen sicherstellen (oder hinzufügen):
PubkeyAuthentication yes
PasswordAuthentication no        # optional: Passwort‑Login komplett deaktivieren
PermitRootLogin no                # Root‑Login sperren
UsePAM yes
```

#### Neustart ssh Server auf Zielserver
```bash
sudo systemctl restart ssh.service
```

#### Ggf. Firewall freischalten auf Zielserver
```bash
sudo ufw allow OpenSSH
sudo ufw enable
```

#### Testlogin vom lokalen System
```bash
ssh -i ~/.ssh/deploy_key deploy@<ZIEL_IP>
```

#### Sicherstelen, dass `deploy` nur `sudo docker …` ausführen kann, keine anderen Root‑Kommandos
```bash
sudo nano /etc/sudoers.d/99_deploy_docker
deploy ALL=(root) NOPASSWD: /usr/bin/docker
```

Um systemd-Units nach einem *Boot* ohne User‑Login automatisch zu starten, muss der User **lingering** aktiviert haben:
```bash
sudo loginctl enable-linger deploy
```

#### Auf dem lokalen System: Sicherstellen, dass Python/Celery den Private Key lesen kann
```bash
sudo chown <app_user>:<app_user> ~/.ssh/deploy_key
sudo chmod 600 ~/.ssh/deploy_key
```

#### Hostname und IP in der Datenbanktabelle "paas_remotehost" setzen

sqlite3 /<pfad_zu_deinem_Projekt>/db.sqlite3

```sql
UPDATE "main"."paas_remotehost" SET "hostname" = <hostname_zielserver>, "ip_address" = <ip_zielserver>, "ssh_key_path"='<pfad_in_dein_homeverzeichnis>/.ssh/deploy_key' WHERE "id"=1; 
```

Die Pflege der Zielserver kann auch über die Admin-Oberfläche --> Rubrik "PAAS" --> "Remote Hosts" durchgeführt werden.


## Web-Zugriff
User: 
http://127.0.0.1:8000

Admin (siehe env "STRING_TO_ADMIN_PAGE"):
http://127.0.0.1:8000/<wie_in_env_datei_gesetzt>

Flower/Celery:
http://127.0.0.1:5555


## Dienste einrichten
Falls die Anwendung permanent laufen soll. Also z.B. für Testsysteme.

### Celery
```bash
sudo nano /etc/systemd/system/celery_privycld.service
```
Inhalt:
```systemd
[Unit]
Description=Celery Worker for PrivyCloud
After=network.target

[Service]
Type=simple
User=<app_user>
Group=<app_group>
Environment="DJANGO_SETTINGS_MODULE=core.settings"
WorkingDirectory=<pfad_zum_projekt>
ExecStart=<pfad_zum_projekt>/venv/bin/celery -A core worker --beat --loglevel debug --concurrency 2 --without-gossip --without-mingle --logfile <pfad_zum_projekt>/logs/celery.log
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```
Dienst aktivieren und starten:
```bash
sudo systemctl enable celery_privycld.service
sudo systemctl daemon-reload
sudo systemctl restart celery_privycld.service
```
Prüfung logs:
```bash
sudo journalctl -xe -u celery_privycld.service
```

### Gunicorn
```bash
sudo nano /etc/systemd/system/gunicorn_privycld.service
```
Inhalt:
```systemd
[Unit]
Description=gunicorn daemon for PrivyCloud
After=network.target celery_privycld.service

[Service]
User=<app_user>
Group=<app_group>
WorkingDirectory=<pfad_zum_projekt>
ExecStart=<pfad_zum_projekt>/venv/bin/gunicorn --timeout 550 --workers 3 --bind 0.0.0.0:8000 core.wsgi:application

[Install]
WantedBy=multi-user.target
```
Dienst aktivieren und starten:
```bash
sudo systemctl enable gunicorn_privycld.service
sudo systemctl daemon-reload
sudo systemctl restart gunicorn_privycld.service
```
Prüfung logs:
```bash
sudo journalctl -xe -u gunicorn_privycld.service
```


## Docker
### Image lokal erstellen 
```bash
git clone https://github.com/creadesk/PrivyCloud.git
cd prj_PrivyCloud
docker build -t prj_privycloud .
```
### (Alternativ)Image herunterladen
```bash
....
```
### Verzeichnisse anlegen
```bash
mkdir -p ./zdockerdata/media
mkdir -p ./zdockerdata/logs
mkdir -p ./zdockerdata/db
mkdir -p ./zdockerdata/keys
```
### Container starten
```bash
docker run -d \
  --name privycloud \
  --user "$(id -u):$(id -g)" \
  -p 8000:8000 \
  -p 5555:5555 \
  -v "$(pwd)/zdockerdata/media:/app/media" \
  -v "$(pwd)/zdockerdata/logs:/app/logs" \
  -v "$(pwd)/zdockerdata/db:/app/db" \
  -v "$(pwd)/zdockerdata/keys:/app/keys" \
  -e DJANGO_SUPERUSER_USERNAME=<geheim> \
  -e DJANGO_SUPERUSER_PASSWORD=<geheim> \
  -e SECRET_KEY=django-insecure-<geheime_lange_zeichenkette> \
  -e STRING_TO_ADMIN_PAGE=<geheime_lange_zeichenkette> \
  -e DEBUG=true \
  -e ALLOWED_HOSTS=127.0.0.1,localhost \
  -e DB_ENGINE=django.db.backends.sqlite3 \
  -e DB_NAME=db/db.sqlite3 \
  -e REDIS_SERVER_IP=<ip_redis_server> \
  -e REDIS_SERVER_PORT=<port_redis_server> \
  -e REDIS_SERVER_DB=<db_redis_server> \
  prj_privycloud:latest
```
### Keys für Zielserver bereitstellen
```bash
cp ~/.ssh/deploy_key ./keys/
cp ~/.ssh/deploy_key.pub ./keys/
```
### Keys für Zielserver verknüpfen
- am Admin Panel anmelden
- Rubrik "PAAS" --> Tabelle "Remote Hosts" --> Datensatz anlegen
- am entsprechenden Datensatz in das Feld "Ssh key path" folgendes eintragen: "/app/keys/deploy_key" 

## Lizenz

MIT