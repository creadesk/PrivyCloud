import os
import time
import paramiko
from pathlib import Path
from django.utils import timezone
from datetime import datetime
from django.db import transaction
from django.conf import settings
from tornado.gen import sleep

from .models import ProvisionedApp, RemoteHost, AppDefinition, AppEnvVarPerApp, AppVolumePerApp, ConfigPatch
from celery import shared_task # celery framework
from celery import app # celery app datei
import logging
import re
from django.core.exceptions import ValidationError
from django.db.models import Q

import subprocess
from typing import Tuple
import contextlib
from typing import Generator

logger = logging.getLogger(__name__)


# ----------------------------------------------------
# Lokaler SSH‑Client (für localhost)
# ----------------------------------------------------

class LocalSFTP:
    """Minimaler Ersatz für Paramiko SFTP – nutzt einfach Python‑I/O."""
    def open(self, path: str, mode: str):
        # Im lokalen Fall muss das Verzeichnis existieren
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return open(path, mode)

class LocalSSH:
    """Paramiko‑ähnlicher Client für die lokale Maschine"""

    def __init__(self, host: "RemoteHost"):
        self.host = host
        self.host_obj = host          # damit get_transport() etwas weiß

    # ------------------------------------------------------------------
    # Dummy‑Transport‑Interface (nur get_username() wird benötigt)
    # ------------------------------------------------------------------
    def get_transport(self):
        class DummyTransport:
            def __init__(self, username):
                self._user = username

            def get_username(self):
                return self._user

        username = (
            getattr(self.host_obj, "ssh_user", None)
            or os.getenv("USER", "root")
        )
        return DummyTransport(username)

    # ------------------------------------------------------------------
    # Dummy SFTP‑Interface – **context‑manager‑fähig** + stat()
    # ------------------------------------------------------------------
    def open_sftp(self):
        """
        Liefert ein Objekt, das Paramiko’s SFTP‑Client simuliert
        und als Kontext‑Manager verwendet werden kann.
        """

        class DummySFTP:
            """Minimale SFTP‑Simulation für lokale Dateien"""

            def __init__(self, base_dir: Path):
                self.base_dir = base_dir

            def open(self, remote_path, mode="r"):
                local_path = Path(remote_path).expanduser()
                if "w" in mode:
                    local_path.parent.mkdir(parents=True, exist_ok=True)
                return open(local_path, mode, encoding="utf-8")

            # ---- Kontext‑Manager‑API -----------------------------------
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc_val, exc_tb):
                # Dummy – nichts zu tun
                pass

            # --------------------- stat() --------------------------------
            def stat(self, remote_path):
                """Prüft, ob die Datei existiert – wirft FileNotFoundError,
                wenn sie nicht vorhanden ist."""
                path = Path(remote_path).expanduser()
                if not path.exists():
                    raise FileNotFoundError(f"File {remote_path} not found")
                # Bei Paramiko würde hier ein SFTPAttributes‑Objekt zurück
                # kommen; das konkreten Objekt braucht unser Code nicht,
                # daher einfach Path.stat() zurückgeben.
                return path.stat()

            def close(self):
                pass

        # Basis: Home‑Verzeichnis
        return DummySFTP(Path.home())

    # ---- exec_command --------------------------------------------------
    def exec_command(self, cmd):
        res = subprocess.run(
            cmd, shell=True, text=True, capture_output=True
        )
        return res.returncode, res.stdout.strip(), res.stderr.strip()

    # ---- Kontext‑Manager‑API ------------------------------------------
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


# ----------------------------------------------------------------------
# SSH‑Hilfsfunktionen
# ----------------------------------------------------------------------
@contextlib.contextmanager
def _ssh_client(host: "RemoteHost") -> Generator:
    """
    Liefert einen SSH‑Client (Paramiko‑Client für echte Remote‑Hosts
    oder LocalSSH für localhost/127.0.0.1) als Context‑Manager.
    """
    # Lokaler Host
    if getattr(host, "hostname", None) in ("localhost", "127.0.0.1", "0.0.0.0") \
       or getattr(host, "ip_address", None) in ("127.0.0.1", "localhost", "0.0.0.0"):
        ssh = LocalSSH(host)
        try:
            yield ssh
        finally:
            pass
        return

    # Remote‑Host
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(
        hostname=host.hostname,
        username=host.ssh_user,
        key_filename=str(Path(host.ssh_key_path).expanduser()),
        timeout=15,
    )
    try:
        yield ssh
    finally:
        ssh.close()


def _run_cmd(ssh, cmd: str):
    """
    Unified wrapper that works with both a Paramiko SSHClient
    *and* the LocalSSH helper used for localhost.
    Returns: (exit_code: int, stdout: str, stderr: str)
    """
    # Execute the command once
    result = ssh.exec_command(cmd)

    # ----------------- Detect local return (int, str, str) -----------------
    # Paramiko returns a tuple of ChannelFile objects → first element is NOT int
    if isinstance(result, tuple) and len(result) == 3:
        # local:  (int, str, str)
        if isinstance(result[0], int):
            exit_code, out, err = result
            # Ensure strings (no trailing newlines)
            if not isinstance(out, str):
                out = out.decode().strip()
            if not isinstance(err, str):
                err = err.decode().strip()
            return exit_code, out, err

    # ----------------- Paramiko case ------------------------------------
    stdin, stdout, stderr = result
    exit_code = stdout.channel.recv_exit_status()
    out = stdout.read().decode().strip()
    err = stderr.read().decode().strip()
    return exit_code, out, err


def _get_user_id_uid(ssh, username: str = "deploy") -> tuple[str, str]:
    """
    Gibt die UID des angegebenen Users zurück.
    """
    exit_status, out, err = _run_cmd(ssh, f"id -u {username}")
    if exit_status != 0:
        raise RuntimeError(f"Kann UID für '{username}' nicht ermitteln: {err}")

    # Ausgabe ist "UID" z.B. "1002"
    uid= out
    return uid


def _get_user_id_gid(ssh, username: str = "deploy") -> tuple[str, str]:
    """
    Gibt die GID des angegebenen Users zurück.
    """
    exit_status, out, err = _run_cmd(ssh, f"id -g {username}")
    if exit_status != 0:
        raise RuntimeError(f"Kann GID für '{username}' nicht ermitteln: {err}")

    # Ausgabe ist "GID" z.B. "1002"
    gid= out
    return gid


def _wait_for_file(ssh, file_path: str, timeout: int = 60):
    """Warten, bis eine Datei existiert – gibt True/False zurück."""
    end = time.time() + timeout
    while time.time() < end:
        try:
            ssh.open_sftp().stat(file_path)
            return True
        except FileNotFoundError:
            time.sleep(1)
    return False


def _get_free_port(ssh):
    """Ruft auf dem Remote‑Host einen zufälligen freien Port ab."""
    cmd = (
        "python3 - <<'PY'\n"
        "import socket\n"
        "s=socket.socket(); s.bind(('0.0.0.0',0)); print(s.getsockname()[1]); s.close()\n"
        "PY"
    )
    _, out, err = _run_cmd(ssh, cmd)
    if err:
        raise RuntimeError(f"Port‑Scanner fehlgeschlagen: {err}")
    return int(out)


def _is_port_in_use(ssh, port: int):
    """Prüft, ob ein Port von einem Prozess (z.B. Tor) belegt ist."""
    cmd = f"ss -tlnp 2>/dev/null | grep -c :{port}"
    _, out, _ = _run_cmd(ssh, cmd)
    return bool(int(out or "0"))


def _write_systemd_unit(ssh, unit_name, unit_content):
    """
    Schreibt die unit_datei in ~/.config/systemd/user/ und lädt sie neu.
    Funktioniert sowohl für einen echten Paramiko‑SSHClient (remote)
    als auch für die LocalSSH‑Klasse (lokal).
    """
    # ---------------------------------------------------------
    # 2.1  Determine username & target directory
    # ---------------------------------------------------------
    if hasattr(ssh, "get_transport"):
        # Remote / Paramiko client
        user = ssh.get_transport().get_username()
        unit_dir = f"/home/{user}/.config/systemd/user/"
        # Remote: ensure dir via SSH (no local os.makedirs)
        _run_cmd(ssh, f"mkdir -p {unit_dir}")
        unit_path = f"{unit_dir}{unit_name}"          # remote path
    else:
        # Local execution – use the host_obj if available
        host_obj = getattr(ssh, "host_obj", None)
        user = getattr(host_obj, "ssh_user", os.getenv("USER", "root"))
        unit_dir = Path.home() / ".config" / "systemd" / "user"
        os.makedirs(unit_dir, exist_ok=True)
        unit_path = unit_dir / unit_name

    # ---------------------------------------------------------
    # 2.2  Write the unit file
    # ---------------------------------------------------------
    try:
        # Remote: use SFTP; Local: write directly to the filesystem
        with ssh.open_sftp() as sftp:
            with sftp.open(str(unit_path), "w") as f:
                f.write(unit_content)
    except AttributeError:
        # local fallback – open the file directly
        with open(unit_path, "w", encoding="utf-8") as f:
            f.write(unit_content)

    # ---------------------------------------------------------
    # 3. Reload systemd, enable & start the unit
    # ---------------------------------------------------------
    if hasattr(ssh, "get_transport"):
        # Remote commands – executed via the SSH client
        _run_cmd(ssh, f"systemctl --user daemon-reload")
        _run_cmd(ssh, f"systemctl --user enable {unit_name}")
        _run_cmd(ssh, f"systemctl --user start {unit_name}")
    else:
        # Local execution – run the commands on the host directly
        subprocess.run(f"systemctl --user daemon-reload", check=False)
        subprocess.run(f"systemctl --user enable {unit_name}", check=False)
        subprocess.run(f"systemctl --user start {unit_name}", check=False)



def _reserve_ports(ssh, max_attempts=10):
    """
    Reserviert zwei freie Ports – einen für Web und einen für API – und stellt sicher,
    dass sie verschieden sind.

    :param ssh:  SSH‑Connection‑Object (oder beliebiger Kontext, den _get_free_port nutzt)
    :param max_attempts:  Optionaler Höchstwert für die Versuche (None = unbegrenzt)
    :return:  Tuple (free_port_web, free_port_api)
    """
    # 1. Web‑Port holen – das ist ein einmaliger Vorgang
    free_port_web = _get_free_port(ssh)

    # 2. API‑Port holen – bis er verschieden vom Web‑Port ist
    attempts = 0
    while True:
        if max_attempts is not None and attempts >= max_attempts:
            raise RuntimeError(
                f"Kein freier Port für API gefunden, nach {attempts} Versuchen."
            )
        free_port_api = _get_free_port(ssh)
        if free_port_api != free_port_web:
            break  # Bedingung erfüllt – wir können weitermachen
        attempts += 1

    return free_port_web, free_port_api


def _build_torrc(app_def: AppDefinition,
                socks_port: int,
                hidden_dir: str,
                free_port_web: int,
                free_port_api: int) -> str:
    """
    Baut die Tor‑Konfigurations‑Datei (torrc) als String.

    Parameter:
        app_def        – Objekt mit den internen Ports
        socks_port     – Port, auf dem der Tor‑Socks‑Server lauscht
        hidden_dir     – Pfad zum Hidden‑Service‑Verzeichnis
        free_port_web  – Port, auf dem die Web‑App läuft (intern)
        free_port_api  – Port, auf dem die API läuft (intern)
    """
    # Basis‑Zeilen – immer vorhanden
    lines = [
        f"SocksPort {socks_port}",
        f"HiddenServiceDir {hidden_dir}",
    ]

    # Bedingte Zeilen hinzufügen
    if app_def.app_port_intern_web != 1:
        lines.append(f"HiddenServicePort {app_def.hiddenservice_port_web} 127.0.0.1:{free_port_web}")
    if app_def.app_port_intern_api != 1:
        lines.append(f"HiddenServicePort {app_def.hiddenservice_port_api} 127.0.0.1:{free_port_api}")

    # Alle Zeilen zu einem String mit Zeilenumbrüchen zusammenfügen
    torrc = "\n".join(lines) + "\n"   # Letztes \n für POSIX‑kompatibel
    return torrc


# ----------------------------------------------------------------------
# Gemeinsame Lösch‑Logik
# ----------------------------------------------------------------------
def _cleanup_provision(provision: ProvisionedApp):
    """Stopp, Löschung von Container und Tor‑Hidden‑Service."""
    host = provision.host
    with _ssh_client(host) as ssh:
        # 1. Container entfernen
        if provision.container_id:
            _run_cmd(ssh, f"docker rm -f {provision.container_id}")
            provision.log += f"\nContainer {provision.container_id} removed."
            provision.container_id = None

        # 1.1 Unbenutzte Docker‑Volumes entfernen
        # Achtung: Prüfen, ob Docker überhaupt läuft!
        _run_cmd(ssh, "docker volume prune -f")
        provision.log += "\nUnused Docker volumes pruned."

        # 2. Tor‑Hidden‑Service entfernen
        if provision.container_name:
            tor_data_dir = f"/home/{host.ssh_user}/{provision.container_name}/"
            hidden = f"{tor_data_dir}.tor_hidden_{provision.container_name}"
            provision.log += f"\nTor Hidden‑Service {hidden} removed."
            provision.onion_address = None

            #systemd dienst entfernen
            if hasattr(ssh, "get_transport"):
                # Remote commands – executed via the SSH client
                _run_cmd(ssh, f"systemctl --user stop tor-hidden-service@{provision.container_name}.service")
                _run_cmd(ssh, f"systemctl --user disable tor-hidden-service@{provision.container_name}.service")
                _run_cmd(ssh, f"rm ~/.config/systemd/user/tor-hidden-service@{provision.container_name}.service")
            else:
                # Local execution – run the commands on the host directly
                subprocess.run(f"systemctl --user stop tor-hidden-service@{provision.container_name}.service", shell=True, check=False)
                subprocess.run(f"systemctl --user disable tor-hidden-service@{provision.container_name}.service", shell=True, check=False)
                subprocess.run(f"rm ~/.config/systemd/user/tor-hidden-service@{provision.container_name}.service", shell=True, check=False)

            # tor datenverzeichnis löschen
            _run_cmd(ssh, f"rm -rf {tor_data_dir} || true")

        # 3. DB-Eintrag löschen
        provision.delete()

        # alternativ: 3.1 DB-Eintrag auf Status "deleted" setzen
        #provision.status = "deleted"
        #provision.last_modified = timezone.now()
        #provision.save(update_fields=[
        #    "container_id", "onion_address", "status",
        #    "log", "last_modified"
        #])


# ----------------------------------------------------------------------
# Anpassung Config eines Containers
# ----------------------------------------------------------------------
def _apply_patches(ssh, app_def: AppDefinition, host: RemoteHost, provision: ProvisionedApp):
    """
    Führt alle Config‑Patch‑Anweisungen aus, die für die App definiert sind.
    """
    # 1. Alle Patches holen
    patches = list(app_def.config_patches.all())

    # 2. Für jedes Patch
    for patch in patches:
        # Pfad relativ zum Deploy‑User (z.B. /home/deploy/<user-containername>simplex/smp/config/smp-server.ini)
        file_path = os.path.join(
            f"/home/{host.ssh_user}/{provision.container_name}",
            patch.target_file.lstrip('/')  # falls der Pfad mit / beginnt
        )

        # ------------------------------------------------------------------
        # Warten, bis die Datei auf dem Remote‑Host existiert
        # ------------------------------------------------------------------
        if not _file_exists(ssh, file_path, timeout=120, interval=2.0):
            raise FileNotFoundError(
                f"Target file {file_path} not found on remote host after waiting."
            )

        # 3. Aktion ausführen
        if patch.action == ConfigPatch.ACTION_COMMENT:
            # Zeilen auskommentieren:  sed -i 's/^\(https\|cert\|key\):/#\1:/'
            # Wir benutzen `grep -nE` um die Zeilennummern zu holen und `sed -i` zum Einfügen des #.
            cmd = (
                f"sed -i \"s/{patch.pattern}/#&/\" {file_path}"
            )
            #Bsp: sed -i 's/^\(https\|cert\|key\):/#&/' /home/deploy/testuser16-simplex-smp-1763212099/simplex/smp/config/smp-server.ini
            print(cmd)

            exit_code, out, err = _run_cmd(ssh, cmd)
            if exit_code:
                raise RuntimeError(f"Patch comment failed: {err}")

        elif patch.action == ConfigPatch.ACTION_REPLACE:
            # z.B.  replace  ^https:.*  WITH https://example.com
            # Wir nutzen sed -i 's/^https:.*/https://example.com/'
            # Um den Platzhalter <onion_address> etc. zu ersetzen, können wir
            # vorher `envsubst` benutzen oder `sed` selbst.
            replacement = patch.replacement or ''
            # Escape slashes in replacement
            replacement_escaped = replacement.replace('/', r'\/')
            cmd = (
                f"sed -i \"s/^{patch.pattern}/{replacement_escaped}/\" {file_path}"
            )
            exit_code, out, err = _run_cmd(ssh, cmd)
            if exit_code:
                raise RuntimeError(f"Patch replace failed: {err}")

        elif patch.action == ConfigPatch.ACTION_DELETE:
            # delete lines that match the pattern
            cmd = f'sed -i "/^{patch.pattern}/d" {file_path}'
            exit_code, out, err = _run_cmd(ssh, cmd)
            if exit_code:
                raise RuntimeError(f"Patch delete failed: {err}")

        else:
            raise ValueError(f"Unbekannte Patch‑Aktion: {patch.action}")


# ----------------------------------------------------------------------
# Hilfsfunktion: Prüfen, ob eine Datei auf dem Remote-Server existiert
# ----------------------------------------------------------------------
def _file_exists(ssh, path: str, timeout: int = 60, interval: float = 1.0) -> bool:
    """
    Prüft, ob die Datei ``path`` auf dem Remote‑Host existiert.
    Warten bis maximal ``timeout`` Sekunden (standard 60s).
    Bei Erfolg wird True zurückgegeben, sonst False.
    """
    end_time = time.time() + timeout
    while time.time() < end_time:
        exit_code, _, _ = _run_cmd(ssh, f"test -f {path}")
        if exit_code == 0:          # 0 == erfolgreich → Datei existiert
            return True
        time.sleep(interval)        # kurze Pause bevor erneut geprüft wird
    return False


# ----------------------------------------------------------------------
# Celery‑Tasks
# ----------------------------------------------------------------------
@shared_task
def add(x, y):
    """Test‑Task zum Addieren zweier Zahlen."""
    time.sleep(2)
    return x + y


@shared_task
def simple_task():
    return "Test successful"


@shared_task(bind=True, max_retries=5, default_retry_delay=60)
def deploy_app_task(self, provision_id: int, env_vars=None,**kwargs):
    """Deploy einer App als Docker‑Container + Tor‑Hidden‑Service."""
    provision = None
    try:
        provision = ProvisionedApp.objects.select_related("app", "host").get(pk=provision_id)
        app_def = provision.app
        host = provision.host

        #####################################
        # Environment‑Variables zusammenbauen
        #####################################
        default_env_qs = AppEnvVarPerApp.objects.filter(app=app_def) # Vordefinierte Variablen aus DB holen
        # Werte aus dem Form‑Input übernehmen
        env_from_user = env_vars or {} # Falls env_vars None, dann leeres dict
        # Endgültiges dict zusammenführen (User‑Werte überschreiben Defaults)
        final_env = {}
        for env in default_env_qs:
            key = env.key
            # Wert: User‑Eintrag, falls vorhanden, sonst Default
            final_env[key] = env_from_user.get(key, env.value)

        # SSH / Local‑Verbindung
        with _ssh_client(host) as ssh:
            # Freien Port ermitteln
            try:
                free_port_web, free_port_api = _reserve_ports(ssh)
                print(f"Web‑Port:  {free_port_web}")
                print(f"API‑Port:  {free_port_api}")
            except RuntimeError as e:
                print(f"Fehler: {e}")

            # Container‑Name setzen, falls noch nicht vorhanden
            if not provision.container_name:
                provision.container_name = f"{provision.user.username}-{app_def.name}-{int(time.time())}"
                provision.save(update_fields=["container_name"])

            # Tor‑Hidden‑Service
            tor_data_dir = f"/home/{host.ssh_user}/{provision.container_name}/"
            hidden_dir = f"{tor_data_dir}.tor_hidden_{provision.container_name}"
            _run_cmd(ssh, f"mkdir -p {hidden_dir} && chmod 700 {hidden_dir}")

            DEFAULT_SOCKS_PORT = 9050
            socks_port = DEFAULT_SOCKS_PORT if not _is_port_in_use(ssh, DEFAULT_SOCKS_PORT) else _get_free_port(ssh)

            torrc_path = f"{tor_data_dir}.torrc_{provision.container_name}"
            torrc_content = _build_torrc(app_def, socks_port, hidden_dir, free_port_web, free_port_api)
            print("=== erzeugte torrc ===")
            print(torrc_content)
            with ssh.open_sftp().open(torrc_path, "w") as f:
                f.write(torrc_content)

            # user‑systemd‑Unit für tor-instanz erzeugen
            unit_name = f"tor-hidden-service@{provision.container_name}.service"
            unit_content = f"""\
[Unit]
Description=Tor Hidden Service for {provision.container_name}
After=network.target

[Service]
ExecStart=/usr/bin/tor --torrc-file {torrc_path} --DataDirectory {tor_data_dir}
Restart=on-failure
RestartSec=10s
StandardOutput=journal
StandardError=inherit

[Install]
WantedBy=default.target
"""

            _write_systemd_unit(ssh, unit_name, unit_content)

            if not _wait_for_file(ssh, f"{hidden_dir}/hostname", timeout=120):
                raise RuntimeError("Tor hat hostname nicht erzeugt")

            _, onion_addr, _ = _run_cmd(ssh, f"cat {hidden_dir}/hostname")
            if not onion_addr:
                raise RuntimeError("Keine Onion‑Adresse gefunden")


            # Docker‑Run

            # UID / GID vom Remote‑User holen
            uid = _get_user_id_uid(ssh, host.ssh_user)
            gid = _get_user_id_gid(ssh, host.ssh_user)

            cmd_parts = [
                f"docker run -d --restart unless-stopped",
                f"--name {provision.container_name}",
            ]
            if app_def.use_deploy_user:
                cmd_parts.insert(1, f"--user {uid}:{gid}")  # wird nur hinzugefügt, wenn true
            if app_def.app_port_intern_web != 1:
                cmd_parts.append(f"-p {free_port_web}:{app_def.app_port_intern_web}")
            if app_def.app_port_intern_api != 1:
                cmd_parts.append(f"-p {free_port_api}:{app_def.app_port_intern_api}")
            env_flag_parts = [
                f"-e {k}={v.replace('<onion_address>', onion_addr) if '<onion_address>' in v else v}"
                for k, v in final_env.items()
            ]
            cmd_parts.extend(env_flag_parts)

            # Docker‑Volumes
            vol_qs = AppVolumePerApp.objects.filter(app=app_def)
            vol_flag_parts = []
            for vol in vol_qs:
                # kompletter Host‑Pfad (relativ zu tor_data_dir)
                full_host_path = os.path.join(tor_data_dir, vol.host_path)

                # Optional: Verzeichnis auf dem Host anlegen (falls noch nicht vorhanden)
                _run_cmd(ssh, f"mkdir -p {full_host_path}")

                # Docker‑Flag: -v <host_path>:<container_path>
                vol_flag_parts.append(
                    f"-v {full_host_path}:{vol.container_path}"
                )
            # Volumes vor den Umgebungsvariablen anfügen (Reihenfolge ist für Docker irrelevant)
            cmd_parts.extend(vol_flag_parts)

            cmd_parts.append(app_def.docker_image)

            docker_cmd = " ".join(cmd_parts)
            print(f"[deploy_app_task] Docker‑Cmd: {docker_cmd}")  # Debug‑Ausgabe

            exit_code, _, err = _run_cmd(ssh, docker_cmd)
            if exit_code:
                raise RuntimeError(f"Docker‑Run fehlgeschlagen: {err}")

            # Container‑ID abfragen
            ps_cmd = f"docker ps -q -f name={provision.container_name}"
            _, container_id, _ = _run_cmd(ssh, ps_cmd)
            if not container_id:
                raise RuntimeError("Kein Container‑ID zurückgegeben")

            # Jetzt die Config‑Patches anwenden
            _apply_patches(ssh, app_def, host, provision)

            # Basis‑Daten persistieren
            provision.container_id = container_id
            provision.port = free_port_web
            provision.status = "running"
            provision.log = f"Container {container_id} läuft auf Port {free_port_web}"
            provision.save(update_fields=["container_id", "port", "status", "log"])

            provision.onion_address = onion_addr
            provision.log += f"\nOnion‑Service erstellt: http://{onion_addr}:80"
            provision.save(update_fields=["onion_address", "log"])

    except Exception as exc:
        if provision:
            provision.status = "error"
            provision.log = f"{provision.log or ''}\n{exc}"
            provision.save(update_fields=["status", "log"])
        logger.exception("[deploy_app_task] Fehler")
        raise  # Celery kennzeichnet Task als fehlgeschlagen


@shared_task
def delete_container_by_id(provision_id: int, *_, **__):
    """Idempotenter Löscht‑Task – wird von Sweep oder Countdown aufgerufen."""
    try:
        #provision = ProvisionedApp.objects.select_related("host").get(pk=provision_id)
        provision = ProvisionedApp.objects.get(pk=provision_id)
    except ProvisionedApp.DoesNotExist:
        logger.info(f"[delete] ProvisionedApp {provision_id} nicht mehr vorhanden.")
        return

    if provision.status in ("deleted", "deleting"):
        logger.info(f"[delete] {provision} bereits gelöscht.")
        return

    _cleanup_provision(provision)



@shared_task(bind=True, name='paas.tasks.sweep_expired_containers')
def sweep_expired_containers(self):
    """
    Findet alle abgelaufenen ProvisionedApps (running/deleting) und löscht sie.
    Wird minütlich von Celery Beat aufgerufen.
    """
    now = timezone.now()
    logger.info("sweep_expired_containers gestartet – jetzt: %s", now)

    # WICHTIG: nur aware DateTimes! Falls expires_at naive ist → mach es aware
    expired_qs = ProvisionedApp.objects.filter(
        Q(status__in=("running", "deleting")) &
        Q(expires_at__lt=now)
    )

    # Falls expires_at naive in der DB gespeichert ist (sehr häufig!)
    # → alternativ diesen Filter benutzen:
    # expired_qs = ProvisionedApp.objects.filter(
    #     Q(status__in=("running", "deleting")) &
    #     Q(expires_at__lt=now.replace(tzinfo=None))  # naive Vergleich
    # )

    count = expired_qs.count()
    logger.info("Gefundene abgelaufene ProvisionedApps: %d", count)

    if count == 0:
        logger.info("Keine abgelaufenen Container → nichts zu tun")
        return

    deleted = 0
    failed = 0

    # iterator() spart RAM bei vielen Treffern + umgeht Cache-Probleme
    for prov in expired_qs.iterator(chunk_size=100):
        try:
            with transaction.atomic():
                # Optional: nochmal prüfen, ob wirklich abgelaufen (Race-Condition-Schutz)
                obj = ProvisionedApp.objects.select_for_update(skip_locked=True).get(pk=prov.pk)
                if obj.expires_at >= now:
                    continue  # wurde inzwischen verlängert
                if obj.status not in ("running", "deleting"):
                    continue

                logger.info("Lösche abgelaufene ProvisionedApp ID=%s (expires_at=%s)", obj.id, obj.expires_at)

                # Hier deine eigentliche Delete-Task aufrufen
                delete_container_by_id(obj.id)  # oder .apply_async(countdown=5)

                deleted += 1

        except Exception as exc:
            logger.error("Fehler beim Löschen von ProvisionedApp ID=%s: %s", prov.id, exc)
            failed += 1
            # Optional: retry mit Celery
            # raise self.retry(exc=exc, countdown=60)

    logger.info("Sweep fertig → %d gelöscht, %d fehlgeschlagen", deleted, failed)


@shared_task
def delete_container_task(provision_id: int):
    """Stoppt einen Container und entfernt den Tor‑Hidden‑Service, danach wird der DB‑Eintrag gelöscht."""
    try:
        provision = ProvisionedApp.objects.get(pk=provision_id)
        _cleanup_provision(provision)
    except Exception as exc:
        logger.exception("[delete_container_task] Fehler")
        raise

# --------------------------------------------------------------------------- #
#  Parse CPU‑Last aus „uptime“ oder „cat /proc/loadavg“
# --------------------------------------------------------------------------- #
def _parse_loadavg(output: str) -> float:
    """
    Erwartet Output einer Zeile wie:
        12:34:56 up 1 day,  3:45,  1 user,  load average: 0.25, 0.45, 0.32
    Gibt den ersten Load‑Average‑Wert (last 1 min) als Float zurück.
    """
    # Finde alle Float‑Werte am Ende
    match = re.match(r'\s*([0-9]+(?:\.[0-9]+)?)', output)
    # match = re.search(r'load average:\s*([0-9.,]+)', output)
    if not match:
        raise ValueError(f"Ungültige loadavg‑Zeile: {output!r}")
    return float(match.group(1))

    # In Debian/Ubuntu ist das Trennzeichen „,“ – deshalb ersetzen wir Komma durch Punkt
    load_str = match.group(1).replace(',', '.')
    # Der erste Wert ist der 1‑Min‑Durchschnitt
    return float(load_str.split()[0])


@shared_task(bind=True, name='paas.tasks.update_remote_loads')
def update_remote_loads(self):
    """
    Wird regelmäßig (Beat) ausgeführt und aktualisiert die CPU‑Last aller RemoteHost‑Instanzen.
    """
    logger.info("Update CPU‑Load aller RemoteHosts gestartet")
    successes = 0
    failures = 0

    for host in RemoteHost.objects.all():
        try:
            # Methode: uptime
            with _ssh_client(host) as ssh:
                _, out, _ = _run_cmd(ssh, 'uptime')
            load = _parse_loadavg(out)
            '''
            Falls `uptime` nicht verfügbar ist (z.B. auf minimalistischen Docker‑Hosts), kann man stattdessen `cat /proc/loadavg` ausführen:            
            out = _run_ssh_command(host, 'cat /proc/loadavg')
            load = float(out.split()[0])                        
            Das liefert denselben ersten Load‑Average‑Wert.
            '''

            # Für ein Feld, das 0–10 (100 %) bedeutet, normalisieren:
            load_normalized = min(max(load, 0.0), 10.0)

            # Validierung mit Django‑Validators (falls ein Fehler auftreten sollte)
            host.current_load = load_normalized
            host.full_clean()          # Validiert Min/Max
            host.save(update_fields=['current_load'])
            logger.debug(f"Host {host.hostname} ({host.ip_address}): load={load_normalized}")
            successes += 1

        except Exception as exc:
            logger.error(
                f"Fehler beim Abruf von {host.hostname} ({host.ip_address}): {exc}",
                exc_info=True,
            )
            failures += 1
            # Optional: host.current_load auf None setzen, falls Fehlgeschlagen
            # host.current_load = None
            # host.save(update_fields=['current_load'])

    logger.info(
        f"CPU‑Load Update beendet – {successes} erfolgreich, {failures} fehlgeschlagen."
    )