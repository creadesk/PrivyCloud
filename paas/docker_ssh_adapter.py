
"""
Docker-SSH-Adapter für PrivyCloud.

Verwendet einen bereits authentifizierten Paramiko-Client,
ohne einen SSH-Private-Key als Datei abzulegen.
"""

from docker.transport.sshconn import SSHHTTPAdapter


class PrivyCloudSSHAdapter(SSHHTTPAdapter):
    """
    Docker-SSH-Adapter mit einem vorhandenen Paramiko-Client.

    Der Adapter übernimmt die Lebensdauer des SSH-Kontexts.
    Beim Schließen des Docker-Clients wird auch der Kontext beendet.
    """

    def __init__(
        self,
        base_url,
        ssh_client,
        ssh_context=None,
        timeout=60,
    ):
        super().__init__(
            base_url,
            timeout=timeout,
            shell_out=True,
        )

        self.ssh_client = ssh_client
        self.ssh_context = ssh_context
        self.ssh_params = {}

    def get_connection(self, url, proxies=None):
        """Prüft, ob der vorhandene SSH-Transport aktiv ist."""

        transport = self.ssh_client.get_transport()

        if transport is None or not transport.is_active():
            raise RuntimeError(
                "Der Paramiko-SSH-Transport ist nicht aktiv."
            )

        return super().get_connection(url, proxies)

    def close(self):
        """Schließt Docker-Verbindungen und den SSH-Kontext."""

        ssh_client = self.ssh_client

        # Verhindert, dass die Basisklasse den Client zusätzlich schließt.
        self.ssh_client = None

        try:
            super().close()
        finally:
            if self.ssh_context is not None:
                context = self.ssh_context
                self.ssh_context = None
                context.__exit__(None, None, None)
            elif ssh_client is not None:
                ssh_client.close()
