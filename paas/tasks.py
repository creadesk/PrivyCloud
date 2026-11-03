import hashlib
import base64
import os
import rsa
from cryptography.hazmat.primitives.asymmetric import x25519
from cryptography.hazmat.primitives import serialization
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
from typing import Any, Generator, Optional
import shlex


logger = logging.getLogger(__name__)


# ----------------------------------------------------
# Lokaler SSH‑Client (für localhost)
# ----------------------------------------------------
'''
class LocalSFTP:
    """Minimaler Ersatz für Paramiko SFTP – nutzt einfach Python‑I/O."""
    def open(self, path: str, mode: str):
        # Im lokalen Fall muss das Verzeichnis existieren
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return open(path, mode)
'''
'''
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
'''

# ----------------------------------------------------------------------
# SSH‑Hilfsfunktionen
# ----------------------------------------------------------------------
def load_ssh_config(hostname: str):
    """
    Liest ~/.ssh/config und gibt die Optionen für *hostname* zurück.
    """
    cfg = paramiko.SSHConfig()
    cfg_file = os.path.expanduser("~/.ssh/config")
    if not os.path.exists(cfg_file):
        raise FileNotFoundError(f"{cfg_file} nicht gefunden")

    with open(cfg_file) as f:
        cfg.parse(f)

    # Das lookup liefert ein dict, z.B. {'user': 'deploy', 'identityfile': ['/home/user/.ssh/deploy_key']}
    return cfg.lookup(hostname)

@contextlib.contextmanager
def _ssh_client(host: "RemoteHost") -> Generator:
    """
        Öffnet einen Paramiko‑Client, der ausschließlich die in ~/.ssh/config
        hinterlegten Keys verwendet.
        """
    # 1. Config‑Optionen holen
    opts = load_ssh_config(host.hostname)

    user = opts.get('user', host.ssh_user)  # fallback auf Model‑Feld
    key_files = opts.get('identityfile', [])  # Liste von Pfaden
    key_file = key_files[0] if key_files else None  # erster Key

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    try:
        client.connect(
            hostname=host.hostname,
            username=user,
            key_filename=key_file,  # expliziter Pfad
            allow_agent=False,  # Agent nicht nutzen
            look_for_keys=False,  # keine weiteren Keys suchen
            timeout=10,
        )
        yield client
    finally:
        client.close()


def _run_cmd(ssh, cmd: str):
    """
    Unified wrapper that works with both a Paramiko SSHClient
    *and* the LocalSSH helper used for localhost.
    Returns: (exit_code: int, stdout: str, stderr: str)
    """
    #print(f'cmd in _run_cmd: {cmd}')
    # Execute the command once
    result = ssh.exec_command(cmd)
    #print(f'result in _run_cmd: {result}')

    # ----------------- Detect local return (int, str, str) -----------------
    # Paramiko returns a tuple of ChannelFile objects → first element is NOT int
    if isinstance(result, tuple) and len(result) == 3:
        # local: (int, str, str)
        if isinstance(result[0], int):
            exit_code, out, err = result

            # Ensure strings and remove surrounding whitespace
            if not isinstance(out, str):
                out = out.decode()

            if not isinstance(err, str):
                err = err.decode()

            out = out.strip()
            err = err.strip()

            return exit_code, out, err

    # ----------------- Paramiko case ------------------------------------
    stdin, stdout, stderr = result
    exit_code = stdout.channel.recv_exit_status()
    out = stdout.read().decode().strip()
    err = stderr.read().decode().strip()
    return exit_code, out, err


def _get_user_id_uid(ssh, username: str = "deploy") -> str:
    """
    Gibt die UID des angegebenen Users zurück.
    """
    exit_status, out, err = _run_cmd(ssh, f"id -u {username}")
    if exit_status != 0:
        raise RuntimeError(f"Kann UID für '{username}' nicht ermitteln: {err}")

    # Ausgabe ist "UID" z.B. "1002"
    uid= out
    return uid


def _get_user_id_gid(ssh, username: str = "deploy") -> str:
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
    Schreibt die systemd-Unit-Datei in
    ~/.config/systemd/user/ und aktiviert/startet sie.

    Funktioniert sowohl mit Paramiko SSHClient als auch LocalSSH.
    Bei Fehlern wird eine RuntimeError-Exception ausgelöst.
    """
    # ---------------------------------------------------------
    # 1. Benutzer und Zielverzeichnis ermitteln
    # ---------------------------------------------------------
    user = ssh.get_transport().get_username()

    unit_dir = f"/home/{user}/.config/systemd/user/"
    unit_path = f"{unit_dir}{unit_name}"

    # Verzeichnis anlegen
    _run_cmd_checked(
        ssh,
        f"mkdir -p {unit_dir}"
    )

    # ---------------------------------------------------------
    # 2. Unit-Datei schreiben
    # ---------------------------------------------------------
    with ssh.open_sftp() as sftp:
        with sftp.open(str(unit_path), "w") as f:
            f.write(unit_content)

    # ---------------------------------------------------------
    # 3. systemd aktualisieren
    # ---------------------------------------------------------
    _run_cmd_checked(
        ssh,
        "systemctl --user daemon-reload"
    )

    # ---------------------------------------------------------
    # 4. Service aktivieren
    # ---------------------------------------------------------
    _run_cmd_checked(
        ssh,
        f"systemctl --user enable {unit_name}"
    )

    # ---------------------------------------------------------
    # 5. Service starten
    # ---------------------------------------------------------
    _run_cmd_checked(
        ssh,
        f"systemctl --user start {unit_name}"
    )



def _run_cmd_checked(ssh, cmd: str):
    """
    Führt einen SSH-Befehl aus und wirft bei einem Fehler
    (Exit-Code != 0) eine RuntimeError-Exception.

    Returns:
        (exit_code, stdout, stderr)
    """
    exit_code, stdout, stderr = _run_cmd(ssh, cmd)

    if exit_code != 0:
        raise RuntimeError(
            f"SSH-Befehl fehlgeschlagen "
            f"(Exit-Code {exit_code}): {cmd}\n"
            f"STDOUT: {stdout}\n"
            f"STDERR: {stderr}"
        )

    return exit_code, stdout, stderr


def _reserve_ports(ssh, max_attempts=10):
    """
    Reserviert zwei freie Ports – einen für Web und einen für API – und stellt sicher,
    dass sie verschieden sind.

    :param ssh:  SSH‑Connection‑Object (oder beliebiger Kontext, den _get_free_port nutzt)
    :param max_attempts:  Optionaler Höchstwert für die Versuche (None = unbegrenzt)
    :return:  Tuple (free_port_web, free_port_api, free_port_api_2)
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

    # 3. API‑Port 2 holen – bis er verschieden vom Web‑Port und API-Port 1 ist
    attempts = 0
    while True:
        if max_attempts is not None and attempts >= max_attempts:
            raise RuntimeError(
                f"Kein freier Port für API 2 gefunden, nach {attempts} Versuchen."
            )
        free_port_api_2 = _get_free_port(ssh)
        if free_port_api_2 != free_port_web and free_port_api_2 != free_port_api:
            break  # Bedingung erfüllt – wir können weitermachen
        attempts += 1

    return free_port_web, free_port_api, free_port_api_2


def _build_torrc(app_def: AppDefinition,
                socks_port: int,
                hidden_dir: str,
                free_port_web: int,
                free_port_api: int,
                free_port_api_2: int,
                tor_auth_type=None,) -> str:
    """
    Baut die Tor‑Konfigurations‑Datei (torrc) als String.

    Parameter:
        app_def        – Objekt mit den internen Ports
        socks_port     – Port, auf dem der Tor‑Socks‑Server lauscht
        hidden_dir     – Pfad zum Hidden‑Service‑Verzeichnis
        free_port_web  – Port, auf dem die Web‑App läuft (intern)
        free_port_api  – Port, auf dem die API läuft (intern)
        free_port_api_2  – Port, auf dem die API 2 läuft (intern)
        tor_auth_type  - 'password' | 'cert' | None
    """
    # Basis‑Zeilen – immer vorhanden
    lines = [
        f"SocksPort {socks_port}",
        f"HiddenServiceDir {hidden_dir}",
    ]

    # Bedingte Zeilen hinzufügen
    if app_def.app_port_intern_web != 1 and app_def.hiddenservice_port_web !=1:
        lines.append(f"HiddenServicePort {app_def.hiddenservice_port_web} 127.0.0.1:{free_port_web}")
    if app_def.app_port_intern_api != 1 and app_def.hiddenservice_port_api !=1:
        lines.append(f"HiddenServicePort {app_def.hiddenservice_port_api} 127.0.0.1:{free_port_api}")
    if app_def.app_port_intern_api_2 != 1 and app_def.hiddenservice_port_api_2 !=1:
        lines.append(f"HiddenServicePort {app_def.hiddenservice_port_api_2} 127.0.0.1:{free_port_api_2}")

    # Alle Zeilen zu einem String mit Zeilenumbrüchen zusammenfügen
    torrc = "\n".join(lines) + "\n"   # Letztes \n für POSIX‑kompatibel
    return torrc


# ----------------------------------------------------------------------
# Gemeinsame Lösch‑Logik
# ----------------------------------------------------------------------
def _cleanup_provision(provision: ProvisionedApp) -> bool:
    """
    Entfernt Container und Tor Hidden Service.

    Rückgabe:
        True: Bereinigung erfolgreich.
        False: Kritischer Fehler bei der Bereinigung.
    """

    host = provision.host

    # Ursprüngliche Werte sichern
    container_id = provision.container_id
    onion_address = provision.onion_address

    with _ssh_client(host) as ssh:

        # 1. Container entfernen
        if container_id:
            exit_code, out, err = _run_cmd(
                ssh,
                f"docker rm -f {container_id}",
            )

            if exit_code != 0:
                provision.status = "error"
                provision.log += (
                    f"\n✗ Container konnte nicht entfernt werden."
                    f"\nContainer: {container_id}"
                    f"\nFehler: {err}"
                )
                provision.save()
                return False

            provision.log += (
                f"\nContainer {container_id} removed."
            )

        # 1.1 Unbenutzte Docker-Volumes entfernen
        _run_cmd(ssh, "docker volume prune -f")

        provision.log += "\nUnused Docker volumes pruned."

        # 2. Tor Hidden Service entfernen
        if provision.container_name:

            tor_data_dir = (
                f"/home/{host.ssh_user}/{provision.container_name}"
            )

            provision.log += (
                f"\nTor Hidden-Service {tor_data_dir} removed."
            )

            service = (
                f"tor-hidden-service@{provision.container_name}.service"
            )

            _run_cmd(
                ssh,
                f"systemctl --user stop {service}",
            )

            _run_cmd(
                ssh,
                f"systemctl --user disable {service}",
            )

            _run_cmd(
                ssh,
                f"rm ~/.config/systemd/user/{service}",
            )

            # 2.1 Tor-Verzeichnis löschen
            exit_code, out, err = _run_cmd(
                ssh,
                f"rm -rf {tor_data_dir}",
            )

            if exit_code != 0:

                parent_dir = os.path.dirname(tor_data_dir)
                base_name = os.path.basename(tor_data_dir)

                new_path = os.path.join(
                    parent_dir,
                    f"delete_me_{base_name}",
                )

                cmd_mv = (
                    f"mv {shlex.quote(tor_data_dir)} "
                    f"{shlex.quote(new_path)}"
                )

                mv_exit, mv_out, mv_err = _run_cmd(
                    ssh,
                    cmd_mv,
                )

                if mv_exit != 0:

                    provision.status = "error"
                    provision.log += (
                        f"\n✗ KRITISCHER FEHLER: "
                        f"Tor-Verzeichnis konnte weder gelöscht "
                        f"noch umbenannt werden."
                        f"\nVerzeichnis: {tor_data_dir}"
                        f"\nrm-Fehler: {err}"
                        f"\nmv-Fehler: {mv_err}"
                    )

                    provision.save()
                    return False

                provision.log += (
                    f"\nTor-Verzeichnis konnte nicht gelöscht werden."
                    f"\nUmbenannt zu: {new_path}"
                )

            else:
                provision.log += (
                    "\nTor-Datenverzeichnis gelöscht."
                )

        # 3. Erst jetzt lokale Werte aktualisieren
        provision.container_id = None
        provision.onion_address = None

        # 4. DB-Eintrag löschen
        provision.delete()

        return True


'''
def append_line_via_sftp(
    ssh,
    file_path: str,
    line: str,
    *,
    encoding: str = "utf-8",
    max_retries: int = 3,
    retry_delay: float = 2.0,
    log_fn: Optional[callable] = None
) -> bool:
    """
    Append *line* to a file located at *file_path* on the remote host
    via SFTP. The function will:

        1. Ensure the file exists (create if missing).
        2. Append the line (adds a trailing newline if absent).
        3. Verify that the line is indeed present after writing.
        4. Retry the whole operation on transient failures.

    Parameters
    ----------
    ssh : paramiko.SSHClient
        An established SSH connection.
    file_path : str
        Remote path to the target file.
    line : str
        Text to append.
    encoding : str, optional
        Text encoding used for the remote file (default "utf-8").
    max_retries : int, optional
        How many times to retry on transient errors.
    retry_delay : float, optional
        Seconds to wait between retries.
    log_fn : callable, optional
        Custom logging function accepting a single string argument.
        Defaults to a simple `print`.

    Returns
    -------
    bool
        ``True`` if the line was appended and verified, otherwise an
        exception is raised.
    """
    if log_fn is None:
        log_fn = print

    # Make sure we always end the line with a newline
    if not line.endswith("\n"):
        line += "\n"

    for attempt in range(1, max_retries + 1):
        sftp = None
        try:
            sftp = ssh.open_sftp()

            # ---- 1. Prüfen / Anlegen der Datei  ----
            try:
                sftp.stat(file_path)
            except FileNotFoundError:  # file does not exist
                log_fn(f"[SFTP] Datei {file_path} nicht vorhanden – erstelle sie.")
                with sftp.file(file_path, "w", -1):
                    pass  # create an empty file

            # ---- 2. Zeile anhängen  ----
            with sftp.file(file_path, "a", -1) as f:
                f.write(line.encode(encoding))
                log_fn(f"[SFTP APPEND] Zeile angehängt: {line.rstrip()}")

            # ---- 3. Verifikation (lesen & prüfen) ----
            with sftp.file(file_path, "r", -1) as f:
                content = f.read().decode(encoding)

            if line.rstrip() not in [l.strip() for l in content.splitlines()]:
                raise RuntimeError(
                    "Nach dem Append konnte die Zeile nicht im Zielfile gefunden werden."
                )

            # Alles in Ordnung – Rückgabe
            log_fn(f"[SFTP] Erfolgreich: Zeile ist nun in {file_path} enthalten.")
            return True

        except Exception as exc:
            # Falls wir hier sind, hat etwas schiefgegangen.
            log_fn(
                f"[SFTP] Versuch {attempt}/{max_retries} fehlgeschlagen: {exc!s}"
            )
            if attempt == max_retries:
                # Nach max. Versuchen wirklich abbrechen
                raise RuntimeError(
                    f"SFTP append failed after {max_retries} attempts: {exc!s}"
                ) from exc
            # Optionaler Delay zwischen den Versuchen
            time.sleep(retry_delay)

        finally:
            # SFTP-Verbindung immer sauber schließen
            if sftp is not None:
                try:
                    sftp.close()
                except Exception:
                    pass  # nichts weiter zu tun

    # Der Code sollte hier nie ankommen – aber type‑checkers brauchen es.
    return False
'''

# ----------------------------------------------------------------------
# Anpassung Config eines Containers
# ----------------------------------------------------------------------
def _apply_patches(ssh, app_def: AppDefinition, host: RemoteHost,
                   provision: ProvisionedApp, onion_addr):
    """
    Führt alle Config-Patch-Anweisungen aus, die für die App definiert sind.

    Unterstützte Aktionen:
        COMMENT: Passende Zeilen auskommentieren.
        REPLACE: Passende Zeilen ersetzen.
        DELETE:  Passende Zeilen löschen.
        ADD:     Eine neue Zeile am Dateiende anhängen und Container neu starten.

    Alle Shell-Argumente werden sicher gequotet.
    """

    def quote(value):
        return shlex.quote(str(value))

    def escape_sed_pattern(pattern):
        """
        Escaped den Sed-Delimiter '/', ohne die Regex unnötig zu verändern.
        """
        return pattern.replace("/", r"\/")

    def escape_sed_replacement(replacement):
        """
        Escaped Sonderzeichen im Sed-Replacement.

        &  = gesamter gefundener Treffer
        \\ = Escape-Zeichen
        /  = Sed-Delimiter
        """
        return (
            replacement
            .replace("\\", r"\\")
            .replace("&", r"\&")
            .replace("/", r"\/")
        )

    # 1. Patches in definierter Reihenfolge laden
    patches = list(
        app_def.config_patches.order_by("pk")
    )

    # 2. Remote-Dateipfad aufbauen
    #base_path = f"/home/{host.ssh_user}/{provision.container_name}"

    for patch in patches:

        # 3. Aktion vor Remote-Operationen validieren
        if patch.action not in (
            ConfigPatch.ACTION_COMMENT,
            ConfigPatch.ACTION_REPLACE,
            ConfigPatch.ACTION_DELETE,
            ConfigPatch.ACTION_ADD,
        ):
            raise ValueError(
                f"Unbekannte Patch-Aktion: {patch.action}"
            )

        if (
            patch.action == ConfigPatch.ACTION_ADD
            and not patch.replacement
        ):
            raise ValueError(
                f"Patch ADD benötigt ein replacement-Feld "
                f"(Patch ID {patch.id})."
            )

        # 4. Dateipfad sicher zusammensetzen
        base_path = os.path.normpath(
            f"/home/{host.ssh_user}/{provision.container_name}"
        )
        file_path = os.path.normpath(
            os.path.join(
                base_path,
                patch.target_file.lstrip("/")
            )
        )

        # Verhindern, dass der Patch das Basisverzeichnis verlässt
        if os.path.commonpath([base_path, file_path]) != base_path:
            raise ValueError(
                f"Ungültiger Patch-Dateipfad: {patch.target_file}"
            )

        quoted_path = quote(file_path)

        # 5. Warten, bis die Datei existiert
        if not _file_exists(
            ssh,
            file_path,
            timeout=60,
            interval=6.0,
        ):
            raise FileNotFoundError(
                f"Target file {file_path} not found on remote host "
                f"after waiting."
            )

        # 6. COMMENT
        if patch.action == ConfigPatch.ACTION_COMMENT:

            pattern = escape_sed_pattern(patch.pattern)

            sed_expression = f"s/{pattern}/#&/"

            cmd = (
                f"sed -E -i {quote(sed_expression)} {quoted_path}"
            )

            print(f"[PATCH COMMENT] {cmd}")

            exit_code, out, err = _run_cmd(ssh, cmd)

            if exit_code:
                raise RuntimeError(
                    f"Patch comment failed: {err}"
                )

        # 7. REPLACE
        elif patch.action == ConfigPatch.ACTION_REPLACE:

            pattern = escape_sed_pattern(patch.pattern)

            replacement = patch.replacement or ""

            replacement_escaped = escape_sed_replacement(
                replacement
            )

            sed_expression = (
                f"s/{pattern}/{replacement_escaped}/"
            )

            cmd = (
                f"sed -E -i {quote(sed_expression)} {quoted_path}"
            )

            print(f"[PATCH REPLACE] {cmd}")

            exit_code, out, err = _run_cmd(ssh, cmd)

            if exit_code:
                raise RuntimeError(
                    f"Patch replace failed: {err}"
                )

        # 8. DELETE
        elif patch.action == ConfigPatch.ACTION_DELETE:

            pattern = escape_sed_pattern(patch.pattern)

            sed_expression = f"/{pattern}/d"

            cmd = (
                f"sed -E -i {quote(sed_expression)} {quoted_path}"
            )

            print(f"[PATCH DELETE] {cmd}")

            exit_code, out, err = _run_cmd(ssh, cmd)

            if exit_code:
                raise RuntimeError(
                    f"Patch delete failed: {err}"
                )

        # 9. ADD
        elif patch.action == ConfigPatch.ACTION_ADD:

            replacement = patch.replacement.replace(
                "<onion_address>",
                str(onion_addr or "")
            )

            # printf '%s\n' verhindert, dass Prozentzeichen
            # im Replacement als Formatierungsanweisungen
            # interpretiert werden.
            cmd = (
                f"printf '%s\\n' {quote(replacement)} "
                f">> {quoted_path}"
            )

            print(f"[PATCH ADD] {cmd}")

            exit_code, out, err = _run_cmd(ssh, cmd)

            if exit_code:
                raise RuntimeError(
                    f"Patch add failed: {err}"
                )

            # Container nur nach erfolgreichem Anhängen neu starten
            cmd = (
                f"docker restart {quote(provision.container_name)}"
            )

            print(f"[Container neu starten:] {cmd}")

            exit_code, out, err = _run_cmd(ssh, cmd)

            if exit_code:
                raise RuntimeError(
                    f"Patch add failed: {err}"
                )



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
#  Base32 encoder for a raw 32‑byte x25519 key (obsolet)
# ----------------------------------------------------------------------
'''
def _base32_x25519_key(key_hex: str) -> str:
    """
    Convert a hex string (64 chars → 32 bytes) into the 56‑char base32
    representation that Tor expects.  Padding (`=`) is stripped.
    """
    raw = bytes.fromhex(key_hex)
    if len(raw) != 32:
        raise ValueError("Public key must be 32 bytes (64 hex chars)")
    return base64.b32encode(raw).decode("ascii").rstrip("=")
'''

# ----------------------------------------------------------------------
#  Write a client‑auth file (obsolet)
# ----------------------------------------------------------------------
'''
def _write_client_auth_file(
    ssh, hidden_dir: str, public_key_hex: str, auth_file_name: Optional[str] = None
) -> str:
    """
    Create `<HiddenServiceDir>/authorized_clients/<file>.auth`
    and return the full path that was written.

    Parameters
    ----------
    ssh: paramiko.SSHClient
        The SSH connection to the remote host.
    hidden_dir: str
        The path to the hidden‑service directory (e.g. `/home/.../.tor_hidden_myservice`).
    public_key_hex: str
        The x25519 public key in hex (64 chars).
    auth_file_name: Optional[str]
        Optional explicit file name.  If omitted a random one is chosen.
    """
    client_auth_dir = os.path.join(hidden_dir, "authorized_clients")
    _run_cmd(ssh, f"mkdir -p {client_auth_dir} && chmod 700 {client_auth_dir}")

    if not auth_file_name:
        # deterministic but random‑ish name (SHA‑256 of the key)
        auth_file_name = f"{hashlib.sha256(public_key_hex.encode()).hexdigest()}.auth"

    file_path = os.path.join(client_auth_dir, auth_file_name)
    content = f"descriptor:x25519:{_base32_x25519_key(public_key_hex)}\n"

    # Use SFTP for writing the file
    with ssh.open_sftp() as sftp:
        with sftp.open(file_path, "w") as fp:
            fp.write(content)

    return file_path
'''

def _gen_x25519_keypair() -> tuple[bytes, bytes]:
    """
    Liefert (private_key_bytes, public_key_bytes) im Raw‑Format
    (jeweils 32 Byte).
    """
    priv_key = x25519.X25519PrivateKey.generate()
    priv_bytes = priv_key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption()
    )
    pub_bytes = priv_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw
    )
    return priv_bytes, pub_bytes

def _b32_encode(data: bytes) -> str:
    """Base32‑Kodierung ohne Padding‑‘=’‑Zeichen."""
    return base64.b32encode(data).decode("ascii").rstrip("=")

def _write_authorized_client_file(ssh, hidden_dir: str, pub_b32: str):
    """
    Erstellt <HiddenServiceDir>/authorized_clients/<random>.auth
    mit dem Inhalt:
        descriptor:x25519:<public‑key‑in‑base32>
    """
    auth_dir = f"{hidden_dir}/authorized_clients"
    _run_cmd(ssh, f"mkdir -p {auth_dir} && chmod 700 {auth_dir}")
    auth_path = f"{auth_dir}/client.auth"          # Dateiname ist beliebig
    content = f"descriptor:x25519:{pub_b32}\n"
    with ssh.open_sftp().open(auth_path, "w") as f:
        f.write(content)
    return auth_path


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


@shared_task(bind=True, name="paas.deploy_app_task")
def deploy_app_task(self, provision_id: int, selected_image: str, env_vars=None, tor_auth_type=None, tor_pub_key=None, **kwargs):
    """Deploy einer App als Docker‑Container + Tor‑Hidden‑Service."""
    logger.info("Starte deploy_app_task")
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
                free_port_web, free_port_api, free_port_api_2 = _reserve_ports(ssh)
                print(f"Web‑Port:  {free_port_web}")
                print(f"API‑Port:  {free_port_api}")
                print(f"API‑Port 2:  {free_port_api_2}")
            except RuntimeError:
                raise

            # Container‑Name setzen, falls noch nicht vorhanden
            if not provision.container_name:
                provision.container_name = f"{provision.user.username}-{app_def.name}-{int(time.time())}"
                provision.save(update_fields=["container_name"])

            # Tor‑Hidden‑Service
            tor_data_dir = f"/home/{host.ssh_user}/{provision.container_name}/"
            hidden_dir = f"{tor_data_dir}.tor_hidden_{provision.container_name}"

            onion_addr = ""

            if not app_def.no_hidden_service:

                _run_cmd(ssh, f"mkdir -p {hidden_dir} && chmod 700 {hidden_dir}")

                DEFAULT_SOCKS_PORT = 9050
                socks_port = DEFAULT_SOCKS_PORT if not _is_port_in_use(ssh, DEFAULT_SOCKS_PORT) else _get_free_port(ssh)

                # Client‑Auth – Schlüssel erzeugen ------------------
                if tor_auth_type == "cert":
                    # Wir besitzen bereits den öffentlichen Schlüssel: tor_pub_key
                    if not tor_pub_key:
                        raise RuntimeError("Public key fehlt, obwohl cert‑Auth angefordert")

                    # authorized_clients‑Datei anlegen
                    _write_authorized_client_file(ssh, hidden_dir, tor_pub_key)

                    # **kein** privater Key in ProvisionedApp speichern

                torrc_path = f"{tor_data_dir}.torrc_{provision.container_name}"
                torrc_content = _build_torrc(app_def, socks_port, hidden_dir, free_port_web, free_port_api, free_port_api_2, tor_auth_type=tor_auth_type,)
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
                    raise RuntimeError("Keine Onion-Adresse gefunden")

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
            if app_def.use_hostpid_namespace:
                cmd_parts.append(f"--pid host")
            if app_def.app_port_intern_web != 1:
                cmd_parts.append(f"-p {free_port_web}:{app_def.app_port_intern_web}")
            if app_def.app_port_intern_api != 1:
                cmd_parts.append(f"-p {free_port_api}:{app_def.app_port_intern_api}")
            if app_def.app_port_intern_api_2 != 1:
                cmd_parts.append(f"-p {free_port_api_2}:{app_def.app_port_intern_api_2}")
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

            # cmd_parts.append(app_def.docker_image)
            cmd_parts.append(selected_image)

            cmd = app_def.command.strip()
            if cmd:
                cmd_parts.append(cmd)

            docker_cmd = " ".join(cmd_parts)
            print(f"[deploy_app_task] Docker-Cmd: {docker_cmd}")  # Debug‑Ausgabe

            exit_code, _, err = _run_cmd(ssh, docker_cmd)
            if exit_code:
                raise RuntimeError(f"Docker-Run fehlgeschlagen: {err}")

            # Container‑ID abfragen
            ps_cmd = f"docker ps -q -f name={provision.container_name}"
            _, container_id, _ = _run_cmd(ssh, ps_cmd)
            if not container_id:
                raise RuntimeError("Kein Container-ID zurückgegeben")

            # Basis‑Daten sofort persistieren
            provision.container_id = container_id
            provision.port = free_port_web
            provision.port2 = free_port_api
            provision.port3 = free_port_api_2
            provision.status = "running"
            provision.log = f"Container {container_id} läuft auf Port {free_port_web}"
            provision.save(
                update_fields=[
                    "container_id",
                    "port",
                    "port2",
                    "port3",
                    "status",
                    "log",
                ]
            )
            provision.onion_address = onion_addr
            if onion_addr:
                provision.log += f"\nOnion-Service erstellt: http://{onion_addr}"
            else:
                provision.log += "\nKein Onion-Service eingerichtet."
            provision.save(update_fields=["onion_address", "log"])
            provision.docker_run_cmd = docker_cmd
            provision.save(update_fields=["docker_run_cmd"])
            provision.image = selected_image
            provision.save(update_fields=["image"])

            # Jetzt die Config‑Patches anwenden
            try:
                _apply_patches(ssh, app_def, host, provision, onion_addr)
            except Exception as patch_err:
                # Patch‑Fehler: Status ändern, Log erweitern, aber nicht zurücksetzen
                provision.status = "error"
                provision.log += f"\nPatch-Fehler: {patch_err}"
                provision.save(update_fields=["status", "log"])
                raise  # Celery markiert Task als fehlgeschlagen

            # *** Rückgabe‑Logik ***
            # Nach erfolgreichem Deploy können wir das Objekt zurückschicken,
            # falls wir es später in einer View brauchen.
            return provision.id

    except Exception as exc:
        if provision:
            provision.status = "error"
            provision.log = f"{provision.log or ''}\n{exc}"
            provision.save(update_fields=["status", "log"])
        logger.exception("[deploy_app_task] Fehler")
        raise  # Celery kennzeichnet Task als fehlgeschlagen


@shared_task
def delete_container_by_id(provision_id: int, *_, **__):

    try:
        provision = ProvisionedApp.objects.get(pk=provision_id)

    except ProvisionedApp.DoesNotExist:
        logger.info(
            f"[delete] ProvisionedApp {provision_id} "
            f"nicht mehr vorhanden."
        )
        return

    if provision.status in ("deleted", "deleting"):
        logger.info(f"[delete] {provision} bereits gelöscht.")
        return

    success = _cleanup_provision(provision)

    if not success:
        logger.error(
            f"[delete] Provision {provision_id} "
            f"konnte nicht vollständig bereinigt werden."
        )



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
            #with _ssh_client(host) as ssh:
            #    _, out, _ = _run_cmd(ssh, 'uptime')
            #load = _parse_loadavg(out)
            '''
            Falls `uptime` nicht verfügbar ist (z.B. auf minimalistischen Docker‑Hosts), kann man stattdessen `cat /proc/loadavg` ausführen:            
            out = _run_ssh_command(host, 'cat /proc/loadavg')
            load = float(out.split()[0])                        
            Das liefert denselben ersten Load‑Average‑Wert.
            '''
            # Methode "cat /proc/loadavg"
            with _ssh_client(host) as ssh:
                _, out, _ = _run_cmd(ssh, 'cat /proc/loadavg')
            load = float(out.split()[0])

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
    return {
        "successes": successes,
        "failures": failures,
    }



@shared_task(bind=True, max_retries=5, default_retry_delay=60)
def update_app_task(self, provision_id: int, new_image: str):
    """
    Aktualisiert einen laufenden Docker-Container mit einem neuen Image.

    - Eine SSH-Session für alle Docker-Kommandos.
    - Prüft, ob der alte Container noch existiert.
    - Stoppt und entfernt den alten Container nur, wenn er existiert.
    - Startet den neuen Container.
    - Aktualisiert anschließend das Datenbankobjekt.
    - Führt bei Fehlern einen Celery-Retry aus.
    """

    logger.info("Update einer App gestartet")

    try:
        # 1. Provision laden
        provision = ProvisionedApp.objects.select_related(
            "app", "host"
        ).get(pk=provision_id)

        host = provision.host

        logger.info(
            f"Starte App Update '{provision.container_name}' "
            f"auf {host.hostname}"
        )

        # 2. Neuen Docker-Run-Befehl erzeugen
        docker_run_cmd_new = re.sub(
            r"\S+$",
            lambda _: new_image,
            provision.docker_run_cmd,
        )

        logger.debug(f"Neuer Docker-Run-Cmd: {docker_run_cmd_new}")

        # 3. Alle Docker-Kommandos in einer SSH-Session
        with _ssh_client(host) as ssh:

            old_container_exists = False

            # -------------------------------------------------
            # Prüfen, ob der alte Container noch existiert
            # -------------------------------------------------

            if provision.container_id:

                inspect_cmd = (
                    "docker inspect --type container "
                    f"{shlex.quote(provision.container_id)}"
                )

                exit_code, stdout, stderr = _run_cmd(
                    ssh,
                    inspect_cmd,
                )

                if exit_code == 0:
                    old_container_exists = True
                elif "no such object" in stderr.lower() or "no such container" in stderr.lower():
                    stderr = stderr or ""
                    stdout = stdout or ""

                    if exit_code == 0:
                        old_container_exists = True

                    elif (
                            "no such object" in stderr.lower()
                            or "no such container" in stderr.lower()
                    ):
                        old_container_exists = False

                    else:
                        raise RuntimeError(
                            f"Docker inspect fehlgeschlagen: {stderr}"
                        )
                else:
                    raise RuntimeError(
                        f"Docker inspect fehlgeschlagen: {stderr}"
                    )

                if old_container_exists:
                    logger.info(
                        f"Alter Container {provision.container_id} "
                        "existiert noch."
                    )
                else:
                    logger.info(
                        f"Alter Container {provision.container_id} "
                        "existiert nicht mehr. Stop und Remove werden "
                        "übersprungen."
                    )

            # -------------------------------------------------
            # Alten Container stoppen und entfernen
            # -------------------------------------------------

            if old_container_exists:

                # Stop
                exit_code, out, err = _run_cmd(
                    ssh,
                    f"docker stop {shlex.quote(provision.container_id)}",
                )

                if exit_code != 0:
                    raise RuntimeError(
                        f"Docker stop fehlgeschlagen: {err}"
                    )

                logger.debug(f"Stop-Output: {out}")

                # Remove
                exit_code, out, err = _run_cmd(
                    ssh,
                    f"docker rm {shlex.quote(provision.container_id)}",
                )

                if exit_code != 0:
                    raise RuntimeError(
                        f"Docker rm fehlgeschlagen: {err}"
                    )

                logger.debug(f"Remove-Output: {out}")

            # -------------------------------------------------
            # Neuen Container starten
            # -------------------------------------------------

            exit_code, container_id_new, stderr = _run_cmd(
                ssh,
                docker_run_cmd_new,
            )

            if exit_code != 0:
                raise RuntimeError(
                    f"Docker start fehlgeschlagen: {stderr}"
                )

            container_id_new = container_id_new.strip()

            if not container_id_new:
                raise RuntimeError(
                    "Docker start lieferte keine Container-ID zurück."
                )

            logger.info(
                f"Neuer Container gestartet: {container_id_new}"
            )

        # 4. Datenbank aktualisieren
        provision.docker_run_cmd = docker_run_cmd_new
        provision.image = new_image
        provision.container_id = container_id_new

        provision.save(
            update_fields=[
                "docker_run_cmd",
                "image",
                "container_id",
            ]
        )

        logger.info(
            f"Update für Provision {provision_id} erfolgreich abgeschlossen"
        )

        return True

    except Exception as exc:
        host_name = (
            host.hostname if "host" in locals() else "<unknown>"
        )

        logger.error(
            f"Fehler beim Update von Provision {provision_id} "
            f"auf {host_name}: {exc}",
            exc_info=True,
        )

        raise self.retry(exc=exc, countdown=60)