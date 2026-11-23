
"""
Zentrale SSH-Verwaltung für PrivyCloud.

Unterstützte Provider:
- ssh_config: Schlüssel aus ~/.ssh/config
- openbao: Schlüssel aus OpenBAO (direkt im Arbeitsspeicher)

Die Funktion ssh_client() ist ein Context-Manager.
"""

import io
import os
from contextlib import contextmanager

import paramiko

from paas.openbao_client import OpenBaoClient, OpenBaoError
from django.conf import settings


def ssh_config_lookup(hostname: str) -> dict:
    """
    Liest ~/.ssh/config und gibt die Optionen für hostname zurück.
    """

    cfg = paramiko.SSHConfig()
    cfg_file = os.path.expanduser("~/.ssh/config")

    if not os.path.exists(cfg_file):
        raise FileNotFoundError(f"{cfg_file} nicht gefunden")

    with open(cfg_file, encoding="utf-8") as config_file:
        cfg.parse(config_file)

    return cfg.lookup(hostname)


def _load_private_key(private_key: str, passphrase: str | None = None):
    """
    Lädt einen privaten SSH-Schlüssel direkt aus einem String.
    Es wird keine Schlüsseldatei angelegt.
    """

    key_classes = (
        paramiko.Ed25519Key,
        paramiko.ECDSAKey,
        paramiko.RSAKey,
    )

    errors = []

    for key_class in key_classes:
        try:
            return key_class.from_private_key(
                io.StringIO(private_key),
                password=passphrase,
            )
        except paramiko.SSHException as exc:
            errors.append(f"{key_class.__name__}: {exc}")

    raise OpenBaoError(
        "Der private SSH-Schlüssel konnte nicht geladen werden. "
        + " | ".join(errors)
    )


@contextmanager
def _openbao_ssh_client(host):
    """
    Baut eine SSH-Verbindung mit einem Schlüssel aus OpenBAO auf.
    """

    openbao = OpenBaoClient()
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    try:
        secret_path = host.openbao_secret_path
        secret = openbao.read_secret(secret_path)

        private_key = secret.get("private_key")
        if not private_key:
            raise OpenBaoError(
                f"Im OpenBAO-Secret '{secret_path}' fehlt private_key."
            )

        username = secret.get("username", host.ssh_user)
        port = int(secret.get("port", 22))
        passphrase = secret.get("passphrase")

        pkey = _load_private_key(private_key, passphrase)

        client.connect(
            hostname=host.hostname,
            port=port,
            username=username,
            pkey=pkey,
            timeout=10,
            allow_agent=False,
            look_for_keys=False,
        )

        yield client

    finally:
        client.close()
        openbao.close()



@contextmanager
def ssh_client(host):
    """
    Erzeugt einen SSH-Client anhand der Authentifizierungsmethode
    des RemoteHosts.
    """

    auth_method = host.ssh_auth_method

    if auth_method == "openbao":
        openbao_enabled = str(
            getattr(settings, "OPENBAO_ENABLED", "False")
        ).strip().lower() in ("1", "true", "yes", "on")

        if not openbao_enabled:
            raise OpenBaoError(
                "OpenBAO ist in den PrivyCloud-Einstellungen deaktiviert."
            )

        if not host.openbao_secret_path:
            raise OpenBaoError(
                f"Für Host '{host.hostname}' ist kein "
                "OpenBAO-Secret-Pfad hinterlegt."
            )

        with _openbao_ssh_client(host) as client:
            yield client
        return

    if auth_method != "ssh_config":
        raise ValueError(
            f"Unbekannte SSH-Authentifizierungsmethode: {auth_method}"
        )

    cfg_opts = ssh_config_lookup(host.hostname)

    username = cfg_opts.get("user", host.ssh_user)
    identity_files = cfg_opts.get("identityfile") or []
    key_file = identity_files[0] if identity_files else None

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    try:
        client.connect(
            hostname=host.hostname,
            username=username,
            key_filename=key_file,
            timeout=10,
            allow_agent=False,
            look_for_keys=False,
        )

        yield client

    finally:
        client.close()

