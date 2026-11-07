'''
Superuser: Darf die View aufrufen.
Nicht angemeldeter Benutzer: Wird zum Login weitergeleitet.
Normaler angemeldeter Benutzer: Erhält HTTP 302 (Weiterleitung wegen fehlender Berechtigung).
Bekannte Verzeichnisse: Verzeichnisse mit einem passenden ProvisionedApp-Eintrag werden nicht angezeigt.
Unbekannte Verzeichnisse: Werden als Corpses angezeigt.
Host mit SSH-User root: Es wird /root als Home-Verzeichnis verwendet.
Host mit anderem SSH-User: Es wird /home/<ssh_user> verwendet.
Mehrere Hosts: Die Ergebnisse werden korrekt den jeweiligen Hosts zugeordnet.
Host ohne Corpses: Wird nicht in corpses_by_host aufgenommen.
SSH-Fehler: Ein fehlerhafter Host verhindert nicht die Verarbeitung der übrigen Hosts.
GET und POST: Beide HTTP-Methoden werden akzeptiert.
Andere HTTP-Methoden: Werden mit HTTP 405 abgewiesen.
_list_remote_dirs():
    - Versteckte Verzeichnisse (.ssh, .config) werden ignoriert.
    - Leere Zeilen werden ignoriert.
    - Normale Verzeichnisse bleiben erhalten.
    - Die Reihenfolge bleibt erhalten.
'''
from contextlib import contextmanager
from unittest.mock import patch, MagicMock

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from paas.views import _list_remote_dirs

from paas.models import (
    RemoteHost,
    AppDefinition,
    ProvisionedApp,
)


User = get_user_model()


class DataCorpsesViewTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.superuser = User.objects.create_superuser(
            username="admin",
            email="admin@example.com",
            password="testpassword",
        )

        cls.normal_user = User.objects.create_user(
            username="user",
            email="user@example.com",
            password="testpassword",
        )

        cls.host_root = RemoteHost.objects.create(
            hostname="root-host.example.com",
            ip_address="192.0.2.10",
            ssh_user="root",
        )

        cls.host_deploy = RemoteHost.objects.create(
            hostname="deploy-host.example.com",
            ip_address="192.0.2.11",
            ssh_user="deploy",
        )

        cls.app_definition = AppDefinition.objects.create(
            name="test-app",
            display_name="Test App",
            docker_image="example/test-app",
        )

    def setUp(self):
        self.url = reverse("paas_data_corpses")

    def mock_ssh(self, dirs_by_host):
        """
        Mockt SSH-Verbindungen und Verzeichnisauflistung.

        dirs_by_host:
            {
                hostname: [directory1, directory2, ...]
            }
        """

        @contextmanager
        def fake_ssh_client(host):
            yield MagicMock()

        def fake_list_remote_dirs(ssh, home_dir):
            # Zuordnung über den aufrufenden Host erfolgt
            # separat über die Reihenfolge der Aufrufe.
            return dirs_by_host.get(
                home_dir,
                [],
            )

        return (
            patch(
                "paas.views._ssh_client",
                side_effect=lambda host: fake_ssh_client(host),
            ),
            patch(
                "paas.views._list_remote_dirs",
                side_effect=fake_list_remote_dirs,
            ),
        )

    def test_anonymous_user_is_redirected_to_login(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 302)
        self.assertIn(
            reverse("login"),
            response.url,
        )

    def test_normal_user_is_not_allowed(self):
        self.client.force_login(self.normal_user)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 302)

    @patch("paas.views._list_remote_dirs")
    @patch("paas.views._ssh_client")
    def test_superuser_can_access_view(
        self,
        mock_ssh_client,
        mock_list_dirs,
    ):
        self.client.force_login(self.superuser)

        mock_list_dirs.return_value = []

        @contextmanager
        def fake_ssh(host):
            yield MagicMock()

        mock_ssh_client.side_effect = fake_ssh

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(
            response,
            "paas/data_corpses.html",
        )

    @patch("paas.views._list_remote_dirs")
    @patch("paas.views._ssh_client")
    def test_unknown_directories_are_detected(
        self,
        mock_ssh_client,
        mock_list_dirs,
    ):
        self.client.force_login(self.superuser)

        # Für beide Hosts dieselben Verzeichnisse zurückgeben.
        mock_list_dirs.return_value = [
            "unknown-app",
            "another-corpse",
        ]

        @contextmanager
        def fake_ssh(host):
            yield MagicMock()

        mock_ssh_client.side_effect = fake_ssh

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)

        corpses = response.context["corpses_by_host"]

        self.assertIn(self.host_root, corpses)
        self.assertIn(self.host_deploy, corpses)

        self.assertEqual(
            corpses[self.host_root],
            ["unknown-app", "another-corpse"],
        )

        self.assertEqual(
            corpses[self.host_deploy],
            ["unknown-app", "another-corpse"],
        )

    @patch("paas.views._list_remote_dirs")
    @patch("paas.views._ssh_client")
    def test_known_directories_are_filtered_out(
        self,
        mock_ssh_client,
        mock_list_dirs,
    ):
        self.client.force_login(self.superuser)

        ProvisionedApp.objects.create(
            user=self.superuser,
            app=self.app_definition,
            host=self.host_root,
            container_name="known-app",
        )

        mock_list_dirs.side_effect = [
            ["known-app", "unknown-app"],
            ["another-unknown"],
        ]

        @contextmanager
        def fake_ssh(host):
            yield MagicMock()

        mock_ssh_client.side_effect = fake_ssh

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)

        corpses = response.context["corpses_by_host"]

        self.assertEqual(
            corpses[self.host_root],
            ["unknown-app"],
        )

        self.assertEqual(
            corpses[self.host_deploy],
            ["another-unknown"],
        )

    @patch("paas.views._list_remote_dirs")
    @patch("paas.views._ssh_client")
    def test_host_without_corpses_is_not_in_result(
        self,
        mock_ssh_client,
        mock_list_dirs,
    ):
        self.client.force_login(self.superuser)

        # Beide Hosts haben keine Verzeichnisse.
        mock_list_dirs.return_value = []

        @contextmanager
        def fake_ssh(host):
            yield MagicMock()

        mock_ssh_client.side_effect = fake_ssh

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)

        corpses = response.context["corpses_by_host"]

        self.assertEqual(corpses, {})
        self.assertNotIn(self.host_root, corpses)
        self.assertNotIn(self.host_deploy, corpses)

    @patch("paas.views._list_remote_dirs")
    @patch("paas.views._ssh_client")
    def test_root_user_uses_root_home_directory(
        self,
        mock_ssh_client,
        mock_list_dirs,
    ):
        self.client.force_login(self.superuser)

        mock_list_dirs.return_value = []

        @contextmanager
        def fake_ssh(host):
            yield MagicMock()

        mock_ssh_client.side_effect = fake_ssh

        self.client.get(self.url)

        home_dirs = [
            call.args[1]
            for call in mock_list_dirs.call_args_list
        ]

        self.assertIn("/root", home_dirs)
        self.assertIn("/home/deploy", home_dirs)

    @patch("paas.views._list_remote_dirs")
    @patch("paas.views._ssh_client")
    def test_ssh_error_on_one_host_does_not_break_view(
        self,
        mock_ssh_client,
        mock_list_dirs,
    ):
        self.client.force_login(self.superuser)

        @contextmanager
        def fake_ssh(host):
            if host == self.host_root:
                raise Exception("SSH connection failed")

            yield MagicMock()

        mock_ssh_client.side_effect = fake_ssh

        mock_list_dirs.return_value = ["unknown-app"]

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)

        corpses = response.context["corpses_by_host"]

        self.assertNotIn(self.host_root, corpses)

        self.assertIn(self.host_deploy, corpses)

        self.assertEqual(
            corpses[self.host_deploy],
            ["unknown-app"],
        )

    @patch("paas.views._list_remote_dirs")
    @patch("paas.views._ssh_client")
    def test_post_request_is_allowed(
        self,
        mock_ssh_client,
        mock_list_dirs,
    ):
        self.client.force_login(self.superuser)

        mock_list_dirs.return_value = []

        @contextmanager
        def fake_ssh(host):
            yield MagicMock()

        mock_ssh_client.side_effect = fake_ssh

        response = self.client.post(self.url)

        self.assertEqual(response.status_code, 200)

    def test_put_request_is_not_allowed(self):
        self.client.force_login(self.superuser)

        response = self.client.put(self.url)

        self.assertEqual(response.status_code, 405)

    def test_delete_request_is_not_allowed(self):
        self.client.force_login(self.superuser)

        response = self.client.delete(self.url)

        self.assertEqual(response.status_code, 405)







class ListRemoteDirsTests(TestCase):

    @patch("paas.views._run_cmd")
    def test_hidden_and_empty_directories_are_filtered(self, mock_run_cmd):
        mock_run_cmd.return_value = (
            0,
            "app1\n.ssh\n\napp2\n.config\n.\n.. \n",
            "",
        )

        ssh = MagicMock()

        result = _list_remote_dirs(ssh, "/home/deploy")

        self.assertEqual(
            result,
            ["app1", "app2"],
        )

    @patch("paas.views._run_cmd")
    def test_command_error_returns_empty_list(self, mock_run_cmd):
        mock_run_cmd.return_value = (
            1,
            "",
            "Permission denied",
        )

        result = _list_remote_dirs(MagicMock(), "/home/deploy")

        self.assertEqual(result, [])
