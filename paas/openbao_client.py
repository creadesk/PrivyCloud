
"""
OpenBAO-Client für PrivyCloud.

- AppRole-Authentifizierung
- TLS-Prüfung über die eigene PKI
- SecretID über systemd LoadCredential
- KV-v2-Lesezugriff ausschließlich unter privycloud/ssh/
- Token-Erneuerung durch erneute AppRole-Anmeldung
"""

import os
import time
from pathlib import Path
from urllib.parse import quote

import requests


class OpenBaoError(Exception):
    """Fehler bei der OpenBAO-Kommunikation."""


class OpenBaoClient:

    def __init__(self):
        self.url = os.getenv("OPENBAO_URL", "").rstrip("/")
        self.role_id = os.getenv("OPENBAO_ROLE_ID")
        self.ca_cert = os.getenv("OPENBAO_CA_CERT")
        self.kv_mount = os.getenv("OPENBAO_KV_MOUNT", "secret").strip("/")
        self.kv_version = os.getenv("OPENBAO_KV_VERSION", "2")

        if not self.url:
            raise OpenBaoError("OPENBAO_URL ist nicht konfiguriert.")

        if not self.role_id:
            raise OpenBaoError("OPENBAO_ROLE_ID ist nicht konfiguriert.")

        if not self.ca_cert or not Path(self.ca_cert).is_file():
            raise OpenBaoError("OPENBAO_CA_CERT fehlt oder ist nicht lesbar.")

        if self.kv_version != "2":
            raise OpenBaoError("Aktuell wird ausschließlich KV-Version 2 unterstützt.")

        credentials_directory = os.getenv("CREDENTIALS_DIRECTORY")

        if credentials_directory:
            # systemd: bereitgestelltes Credential bevorzugen
            self.secret_id_file = (
                    Path(credentials_directory) / "openbao_secret_id"
            )
        else:
            # Manueller Start, z. B. über runserver_plus
            self.secret_id_file = Path(
                "/etc/privycloud/openbao_secret_id"
            )

        if not self.secret_id_file.is_file():
            raise OpenBaoError(
                "Die OpenBAO SecretID-Datei wurde nicht gefunden."
            )

        if not os.access(self.secret_id_file, os.R_OK):
            raise OpenBaoError(
                "Keine Leseberechtigung für die OpenBAO SecretID-Datei."
            )

        self.session = requests.Session()
        self.session.verify = self.ca_cert

        self.token = None
        self.token_expires_at = 0

    def login(self):
        """Authentifiziert PrivyCloud über AppRole."""

        secret_id = self.secret_id_file.read_text(
            encoding="utf-8"
        ).strip()

        if not secret_id:
            raise OpenBaoError("Das SecretID-Credential ist leer.")

        try:
            response = self.session.post(
                f"{self.url}/v1/auth/approle/login",
                json={
                    "role_id": self.role_id,
                    "secret_id": secret_id,
                },
                timeout=10,
            )
            response.raise_for_status()

        except requests.RequestException as exc:
            raise OpenBaoError(
                f"AppRole-Anmeldung bei OpenBAO fehlgeschlagen: {exc}"
            ) from exc

        try:
            auth_data = response.json().get("auth", {})
            token = auth_data.get("client_token")
            lease_duration = int(auth_data.get("lease_duration", 900))

        except (ValueError, AttributeError, TypeError) as exc:
            raise OpenBaoError(
                "Ungültige Antwort von OpenBAO bei der Anmeldung."
            ) from exc

        if not token:
            raise OpenBaoError("OpenBAO hat keinen gültigen Token zurückgegeben.")

        self.token = token
        self.session.headers.update({"X-Vault-Token": self.token})

        # 60 Sekunden Sicherheitspuffer vor dem Ablauf.
        self.token_expires_at = time.monotonic() + max(
            0, lease_duration - 60
        )

        return True

    def _ensure_token(self):
        """Meldet sich bei Bedarf erneut an."""

        if not self.token or time.monotonic() >= self.token_expires_at:
            self.login()

    def read_secret(self, secret_path):
        """
        Liest ein Secret aus KV-v2.

        Erlaubt sind ausschließlich Pfade unter:
        privycloud/ssh/
        """

        secret_path = secret_path.strip("/")

        if not secret_path.startswith("privycloud/ssh/"):
            raise OpenBaoError(
                "Der Secret-Pfad muss unter privycloud/ssh/ liegen."
            )

        if any(part in ("", ".", "..") for part in secret_path.split("/")):
            raise OpenBaoError("Ungültiger Secret-Pfad.")

        self._ensure_token()

        encoded_path = quote(secret_path, safe="/")

        api_url = (
            f"{self.url}/v1/{self.kv_mount}/data/{encoded_path}"
        )

        try:
            response = self.session.get(
                api_url,
                timeout=10,
            )
            response.raise_for_status()

        except requests.RequestException as exc:
            status = (
                exc.response.status_code
                if exc.response is not None
                else "unbekannt"
            )
            raise OpenBaoError(
                f"Lesen des OpenBAO-Secrets fehlgeschlagen (HTTP {status})."
            ) from exc

        try:
            return response.json()["data"]["data"]

        except (ValueError, KeyError, TypeError) as exc:
            raise OpenBaoError(
                "Unerwartetes KV-v2-Antwortformat von OpenBAO."
            ) from exc

    def close(self):
        """Schließt die HTTP-Session und entfernt den Token aus den Headern."""

        self.session.headers.pop("X-Vault-Token", None)
        self.token = None
        self.token_expires_at = 0
        self.session.close()
