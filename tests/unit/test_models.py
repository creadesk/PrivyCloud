from unittest.mock import Mock, patch, mock_open

from django.contrib.auth.models import User
from django.test import TestCase
from django.core.exceptions import ValidationError

from paas.models import (
    AppDefinition,
    AppImageTag,
    RemoteHost,
    _ssh_config_lookup,
    _ssh_client,
)
from datetime import timedelta
from unittest.mock import Mock, patch
import docker

from django.utils import timezone
from paas.models import ProvisionedApp


# =========================================================
# Gemeinsame Testdaten
# =========================================================

class BaseModelTestCase(TestCase):

    def setUp(self):
        self.app = AppDefinition.objects.create(
            name="test-app",
            display_name="Test App",
            docker_image="example/test-app",
        )

        self.host = RemoteHost.objects.create(
            hostname="server.example.com",
            ip_address="192.168.1.10",
            ssh_user="root",
        )


# =========================================================
# AppDefinition
# =========================================================

class AppDefinitionTests(BaseModelTestCase):

    def test_str(self):
        self.assertEqual(str(self.app), "Test App")

    def test_full_docker_image_without_tag(self):
        self.assertEqual(
            self.app.full_docker_image(),
            "example/test-app",
        )

    def test_full_docker_image_with_tag(self):
        tag = AppImageTag.objects.create(
            app_definition=self.app,
            tag="1.2.3",
        )

        self.assertEqual(
            self.app.full_docker_image(tag),
            "example/test-app:1.2.3",
        )

    def test_full_docker_image_uses_first_tag(self):
        AppImageTag.objects.create(
            app_definition=self.app,
            tag="2.0",
        )
        AppImageTag.objects.create(
            app_definition=self.app,
            tag="1.0",
        )

        self.assertEqual(
            self.app.full_docker_image(),
            "example/test-app:1.0",
        )

    def test_full_docker_image_without_any_tags(self):
        self.assertIsNone(self.app.image_tags.first())

        self.assertEqual(
            self.app.full_docker_image(),
            "example/test-app",
        )

    def test_app_definition_ordering(self):
        AppDefinition.objects.create(
            name="z-app",
            display_name="Zulu",
            docker_image="example/zulu",
        )

        AppDefinition.objects.create(
            name="a-app",
            display_name="Alpha",
            docker_image="example/alpha",
        )

        names = list(
            AppDefinition.objects.values_list(
                "display_name",
                flat=True,
            )
        )

        self.assertEqual(
            names,
            ["Alpha", "Test App", "Zulu"],
        )


# =========================================================
# AppImageTag
# =========================================================

class AppImageTagTests(BaseModelTestCase):

    def test_str(self):
        tag = AppImageTag.objects.create(
            app_definition=self.app,
            tag="latest",
        )

        self.assertEqual(
            str(tag),
            "Test App – latest",
        )

    def test_tags_are_ordered(self):
        AppImageTag.objects.create(
            app_definition=self.app,
            tag="3.0",
        )

        AppImageTag.objects.create(
            app_definition=self.app,
            tag="1.0",
        )

        AppImageTag.objects.create(
            app_definition=self.app,
            tag="2.0",
        )

        tags = list(
            self.app.image_tags.values_list(
                "tag",
                flat=True,
            )
        )

        self.assertEqual(
            tags,
            ["1.0", "2.0", "3.0"],
        )

    def test_duplicate_tag_is_not_allowed(self):
        AppImageTag.objects.create(
            app_definition=self.app,
            tag="1.0",
        )

        from django.db import IntegrityError, transaction

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                AppImageTag.objects.create(
                    app_definition=self.app,
                    tag="1.0",
                )


# =========================================================
# RemoteHost
# =========================================================

class RemoteHostTests(BaseModelTestCase):

    def test_str(self):
        self.assertEqual(
            str(self.host),
            "server.example.com",
        )

    def test_default_values(self):
        self.assertEqual(self.host.ssh_user, "root")
        self.assertEqual(self.host.current_load, 0.0)
        self.assertFalse(self.host.nur_superuser)

    def test_current_load_valid(self):
        self.host.current_load = 5.5
        self.host.full_clean()

    def test_current_load_minimum(self):
        self.host.current_load = -1

        with self.assertRaises(ValidationError):
            self.host.full_clean()

    def test_current_load_maximum(self):
        self.host.current_load = 11

        with self.assertRaises(ValidationError):
            self.host.full_clean()

    def test_current_load_boundary_values(self):
        for value in (0.0, 10.0):
            with self.subTest(value=value):
                self.host.current_load = value
                self.host.full_clean()


# =========================================================
# RemoteHost.docker_images()
# =========================================================

class RemoteHostDockerImagesTests(BaseModelTestCase):

    @patch("paas.models._ssh_client")
    def test_docker_images_success(self, mock_ssh_context):

        ssh = Mock()

        mock_ssh_context.return_value.__enter__ = Mock(
            return_value=ssh
        )

        mock_ssh_context.return_value.__exit__ = Mock(
            return_value=False
        )

        ssh.exec_command.side_effect = [
            (
                None,
                Mock(read=Mock(
                    return_value=b"nginx:latest\n"
                )),
                None,
            ),
            (
                None,
                Mock(read=Mock(
                    return_value=(
                        b"nginx latest abc123 100MB\n"
                        b"redis 7.0 def456 50MB\n"
                    )
                )),
                None,
            ),
        ]

        result = self.host.docker_images()

        self.assertEqual(len(result), 2)

        self.assertEqual(
            result[0],
            {
                "repository": "nginx",
                "tag": "latest",
                "image_id": "abc123",
                "size": "100MB",
                "used": True,
            },
        )

        self.assertEqual(
            result[1],
            {
                "repository": "redis",
                "tag": "7.0",
                "image_id": "def456",
                "size": "50MB",
                "used": False,
            },
        )

        self.assertEqual(
            ssh.exec_command.call_count,
            2,
        )

    @patch("paas.models._ssh_client")
    def test_docker_images_empty_result(self, mock_ssh_context):

        ssh = Mock()

        mock_ssh_context.return_value.__enter__ = Mock(
            return_value=ssh
        )

        mock_ssh_context.return_value.__exit__ = Mock(
            return_value=False
        )

        ssh.exec_command.side_effect = [
            (
                None,
                Mock(read=Mock(return_value=b"")),
                None,
            ),
            (
                None,
                Mock(read=Mock(return_value=b"")),
                None,
            ),
        ]

        self.assertEqual(
            self.host.docker_images(),
            [],
        )

    @patch("paas.models._ssh_client")
    def test_docker_images_ssh_error(self, mock_ssh_context):

        mock_ssh_context.side_effect = Exception(
            "SSH connection failed"
        )

        self.assertEqual(
            self.host.docker_images(),
            [],
        )

    @patch("paas.models._ssh_client")
    def test_docker_images_invalid_output(self, mock_ssh_context):

        ssh = Mock()

        mock_ssh_context.return_value.__enter__ = Mock(
            return_value=ssh
        )

        mock_ssh_context.return_value.__exit__ = Mock(
            return_value=False
        )

        ssh.exec_command.side_effect = [
            (
                None,
                Mock(read=Mock(return_value=b"")),
                None,
            ),
            (
                None,
                Mock(read=Mock(return_value=b"invalid")),
                None,
            ),
        ]

        self.assertEqual(
            self.host.docker_images(),
            [],
        )


# =========================================================
# SSH-Konfiguration
# =========================================================

class SSHConfigLookupTests(TestCase):

    @patch(
        "paas.models.os.path.exists",
        return_value=False,
    )
    @patch(
        "paas.models.os.path.expanduser",
        return_value="/home/test/.ssh/config",
    )
    def test_config_file_not_found(
        self,
        mock_expanduser,
        mock_exists,
    ):

        with self.assertRaises(FileNotFoundError):
            _ssh_config_lookup("server.example.com")

    @patch("paas.models.paramiko.SSHConfig")
    @patch(
        "builtins.open",
        new_callable=mock_open,
        read_data="Host test",
    )
    @patch(
        "paas.models.os.path.exists",
        return_value=True,
    )
    @patch(
        "paas.models.os.path.expanduser",
        return_value="/tmp/config",
    )
    def test_config_lookup_success(
        self,
        mock_expanduser,
        mock_exists,
        mock_file,
        mock_config,
    ):

        config = mock_config.return_value

        config.lookup.return_value = {
            "user": "deploy",
            "identityfile": ["/tmp/id_rsa"],
        }

        result = _ssh_config_lookup(
            "server.example.com"
        )

        self.assertEqual(
            result["user"],
            "deploy",
        )

        self.assertEqual(
            result["identityfile"],
            ["/tmp/id_rsa"],
        )

        config.parse.assert_called_once()
        config.lookup.assert_called_once_with(
            "server.example.com"
        )


# =========================================================
# SSH-Client Context-Manager
# =========================================================

class SSHClientTests(TestCase):

    @patch("paas.models._ssh_config_lookup")
    @patch("paas.models.paramiko.SSHClient")
    def test_connect_success(
        self,
        mock_ssh_client,
        mock_lookup,
    ):

        mock_lookup.return_value = {
            "user": "deploy",
            "identityfile": ["/tmp/id_rsa"],
        }

        client = mock_ssh_client.return_value

        host = Mock()
        host.hostname = "server.example.com"
        host.ssh_user = "root"

        with _ssh_client(host) as ssh:
            self.assertIs(ssh, client)

        client.connect.assert_called_once_with(
            hostname="server.example.com",
            username="deploy",
            key_filename="/tmp/id_rsa",
            timeout=10,
            allow_agent=False,
            look_for_keys=False,
        )

        client.close.assert_called_once()

    @patch("paas.models._ssh_config_lookup")
    @patch("paas.models.paramiko.SSHClient")
    def test_database_user_fallback(
        self,
        mock_ssh_client,
        mock_lookup,
    ):

        mock_lookup.return_value = {}

        host = Mock()
        host.hostname = "server.example.com"
        host.ssh_user = "root"

        with _ssh_client(host):
            pass

        kwargs = (
            mock_ssh_client.return_value
            .connect.call_args.kwargs
        )

        self.assertEqual(
            kwargs["username"],
            "root",
        )

        self.assertIsNone(
            kwargs["key_filename"],
        )

    @patch("paas.models._ssh_config_lookup")
    @patch("paas.models.paramiko.SSHClient")
    def test_client_closes_after_connection_error(
        self,
        mock_ssh_client,
        mock_lookup,
    ):

        mock_lookup.return_value = {}

        client = mock_ssh_client.return_value

        client.connect.side_effect = Exception(
            "Connection failed"
        )

        host = Mock()
        host.hostname = "server.example.com"
        host.ssh_user = "root"

        with self.assertRaises(Exception):
            with _ssh_client(host):
                pass

        client.close.assert_called_once()


class ProvisionedAppBaseTestCase(TestCase):

    def setUp(self):
        self.user = User.objects.create_user(
            username="testuser",
            password="testpassword",
        )

        self.app = AppDefinition.objects.create(
            name="test-app",
            display_name="Test App",
            docker_image="example/test-app",
        )

        self.host = RemoteHost.objects.create(
            hostname="server.example.com",
            ip_address="192.168.1.10",
            ssh_user="root",
        )

        self.provisioned = ProvisionedApp.objects.create(
            user=self.user,
            app=self.app,
            host=self.host,
            container_id="container123",
            container_name="test-container",
            status="pending",
            log="",
        )

class ProvisionedAppBasicTests(ProvisionedAppBaseTestCase):

    def test_str(self):
        self.assertEqual(
            str(self.provisioned),
            "testuser – Test App on server.example.com",
        )

    def test_is_active_without_expiration(self):
        self.provisioned.status = "active"
        self.provisioned.expires_at = None
        self.assertTrue(self.provisioned.is_active())

    def test_is_active_with_future_expiration(self):
        self.provisioned.status = "active"
        self.provisioned.expires_at = (
            timezone.now() + timedelta(hours=1)
        )
        self.assertTrue(self.provisioned.is_active())

    def test_expired(self):
        self.provisioned.status = "active"
        self.provisioned.expires_at = (
            timezone.now() - timedelta(hours=1)
        )
        self.assertFalse(self.provisioned.is_active())

    def test_other_status(self):
        self.provisioned.status = "stopped"
        self.assertFalse(self.provisioned.is_active())


class DockerClientTests(ProvisionedAppBaseTestCase):

    @patch("paas.models.docker.DockerClient")
    def test_success(self, mock_docker):
        result = self.provisioned._docker_client()

        self.assertIs(result, mock_docker.return_value)

        mock_docker.assert_called_once_with(
            base_url="ssh://root@server.example.com",
            use_ssh_client=True,
        )

    @patch("paas.models.docker.DockerClient")
    def test_connection_error(self, mock_docker):
        mock_docker.side_effect = Exception("Docker unavailable")

        self.assertIsNone(self.provisioned._docker_client())

    @patch("paas.models.docker.DockerClient")
    def test_without_host(self, mock_docker):
        provisioned = ProvisionedApp(
            user=self.user,
            app=self.app,
        )

        provisioned.host_id = None

        result = provisioned._docker_client()

        self.assertIsNone(result)
        mock_docker.assert_not_called()

class RefreshStatusTests(ProvisionedAppBaseTestCase):

    def create_container(self, mock_client, status):
        client = mock_client.return_value
        container = Mock()
        container.status = status
        client.containers.get.return_value = container
        return client

    @patch.object(ProvisionedApp, "_docker_client")
    def test_no_container_id(self, mock_client):
        self.provisioned.container_id = None

        self.provisioned.refresh_status()

        self.assertEqual(self.provisioned.status, "pending")
        mock_client.assert_not_called()

    @patch.object(ProvisionedApp, "_docker_client")
    def test_client_unavailable(self, mock_client):
        mock_client.return_value = None

        self.provisioned.refresh_status()

        self.assertEqual(self.provisioned.status, "pending")

    @patch.object(ProvisionedApp, "_docker_client")
    def test_running(self, mock_client):
        client = self.create_container(mock_client, "running")

        self.provisioned.refresh_status()

        self.assertEqual(self.provisioned.status, "running")
        client.close.assert_called_once()

    @patch.object(ProvisionedApp, "_docker_client")
    def test_exited(self, mock_client):
        client = self.create_container(mock_client, "exited")

        self.provisioned.refresh_status()

        self.assertEqual(self.provisioned.status, "stopped")
        client.close.assert_called_once()

    @patch.object(ProvisionedApp, "_docker_client")
    def test_dead(self, mock_client):
        self.create_container(mock_client, "dead")

        self.provisioned.refresh_status()

        self.assertEqual(self.provisioned.status, "stopped")

    @patch.object(ProvisionedApp, "_docker_client")
    def test_created(self, mock_client):
        self.create_container(mock_client, "created")

        self.provisioned.refresh_status()

        self.assertEqual(self.provisioned.status, "stopped")

    @patch.object(ProvisionedApp, "_docker_client")
    def test_unknown_status(self, mock_client):
        self.create_container(mock_client, "restarting")

        self.provisioned.refresh_status()

        self.assertEqual(self.provisioned.status, "restarting")

    @patch.object(ProvisionedApp, "_docker_client")
    def test_not_found(self, mock_client):
        client = mock_client.return_value
        client.containers.get.side_effect = docker.errors.NotFound(
            "Missing"
        )

        self.provisioned.refresh_status()

        self.assertEqual(self.provisioned.status, "deleted")
        client.close.assert_called_once()

    @patch.object(ProvisionedApp, "_docker_client")
    def test_unexpected_error(self, mock_client):
        client = mock_client.return_value
        client.containers.get.side_effect = Exception("Error")

        self.provisioned.refresh_status()

        self.assertEqual(self.provisioned.status, "error")
        client.close.assert_called_once()

class StartContainerTests(ProvisionedAppBaseTestCase):

    def create_container(self, mock_client, status="exited"):
        client = mock_client.return_value
        container = Mock()
        container.status = status
        client.containers.get.return_value = container
        return client, container

    def test_no_container_id(self):
        self.provisioned.container_id = None

        result = self.provisioned.start_container()

        self.assertFalse(result)
        self.assertEqual(self.provisioned.status, "error")

    @patch.object(ProvisionedApp, "_docker_client", return_value=None)
    def test_no_docker_connection(self, mock_client):
        result = self.provisioned.start_container()

        self.assertFalse(result)
        self.assertEqual(self.provisioned.status, "error")

    @patch.object(ProvisionedApp, "_docker_client")
    def test_already_running(self, mock_client):
        client, container = self.create_container(
            mock_client, "running"
        )

        result = self.provisioned.start_container()

        self.assertTrue(result)
        self.assertEqual(self.provisioned.status, "running")
        container.start.assert_not_called()
        client.close.assert_called_once()

    @patch.object(ProvisionedApp, "_docker_client")
    def test_start_stopped_container(self, mock_client):
        client, container = self.create_container(
            mock_client, "exited"
        )

        result = self.provisioned.start_container()

        self.assertTrue(result)
        self.assertEqual(self.provisioned.status, "running")
        container.start.assert_called_once()
        self.assertIn("gestartet.", self.provisioned.log)
        client.close.assert_called_once()

    @patch.object(ProvisionedApp, "_docker_client")
    def test_not_found(self, mock_client):
        client = mock_client.return_value
        client.containers.get.side_effect = docker.errors.NotFound(
            "Missing"
        )

        result = self.provisioned.start_container()

        self.assertFalse(result)
        self.assertEqual(self.provisioned.status, "deleted")
        client.close.assert_called_once()

    @patch.object(ProvisionedApp, "_docker_client")
    def test_api_error(self, mock_client):
        client = mock_client.return_value
        client.containers.get.side_effect = docker.errors.APIError(
            "API failed"
        )

        result = self.provisioned.start_container()

        self.assertFalse(result)
        self.assertEqual(self.provisioned.status, "error")
        self.assertIn("API error:", self.provisioned.log)
        client.close.assert_called_once()

    @patch.object(ProvisionedApp, "_docker_client")
    def test_unexpected_error(self, mock_client):
        client = mock_client.return_value
        client.containers.get.side_effect = Exception("Unexpected")

        result = self.provisioned.start_container()

        self.assertFalse(result)
        self.assertEqual(self.provisioned.status, "error")
        self.assertIn("Unexpected error:", self.provisioned.log)
        client.close.assert_called_once()

    @patch.object(ProvisionedApp, "_docker_client")
    def test_start_command_error(self, mock_client):
        client, container = self.create_container(mock_client)
        container.start.side_effect = Exception("Start failed")

        result = self.provisioned.start_container()

        self.assertFalse(result)
        self.assertEqual(self.provisioned.status, "error")
        self.assertIn("Start failed", self.provisioned.log)
        client.close.assert_called_once()

class StopContainerTests(ProvisionedAppBaseTestCase):

    def create_container(self, mock_client, status="running"):
        client = mock_client.return_value
        container = Mock()
        container.status = status
        client.containers.get.return_value = container
        return client, container

    def test_no_container_id(self):
        self.provisioned.container_id = None

        result = self.provisioned.stop_container()

        self.assertFalse(result)
        self.assertEqual(self.provisioned.status, "stopped")

    @patch.object(ProvisionedApp, "_docker_client", return_value=None)
    def test_no_docker_connection(self, mock_client):
        result = self.provisioned.stop_container()

        self.assertFalse(result)
        self.assertEqual(self.provisioned.status, "error")

    @patch.object(ProvisionedApp, "_docker_client")
    def test_stop_running_container(self, mock_client):
        client, container = self.create_container(mock_client)

        result = self.provisioned.stop_container(timeout=15)

        self.assertTrue(result)
        container.stop.assert_called_once_with(timeout=15)
        self.assertEqual(self.provisioned.status, "stopped")
        self.assertIn("gestoppt.", self.provisioned.log)
        client.close.assert_called_once()

    @patch.object(ProvisionedApp, "_docker_client")
    def test_kill_when_stop_fails(self, mock_client):
        client, container = self.create_container(mock_client)
        container.stop.side_effect = Exception("Stop failed")

        result = self.provisioned.stop_container()

        self.assertTrue(result)
        container.kill.assert_called_once()
        self.assertEqual(self.provisioned.status, "stopped")
        client.close.assert_called_once()

    @patch.object(ProvisionedApp, "_docker_client")
    def test_exited_container_not_stopped_again(self, mock_client):
        client, container = self.create_container(
            mock_client, "exited"
        )

        result = self.provisioned.stop_container()

        self.assertTrue(result)
        container.stop.assert_not_called()
        self.assertEqual(self.provisioned.status, "stopped")
        client.close.assert_called_once()

    @patch.object(ProvisionedApp, "_docker_client")
    def test_not_found(self, mock_client):
        client = mock_client.return_value
        client.containers.get.side_effect = docker.errors.NotFound(
            "Missing"
        )

        result = self.provisioned.stop_container()

        self.assertFalse(result)
        self.assertEqual(self.provisioned.status, "deleted")
        client.close.assert_called_once()

    @patch.object(ProvisionedApp, "_docker_client")
    def test_api_error(self, mock_client):
        client = mock_client.return_value
        client.containers.get.side_effect = docker.errors.APIError(
            "API failed"
        )

        result = self.provisioned.stop_container()

        self.assertFalse(result)
        self.assertEqual(self.provisioned.status, "error")
        self.assertIn("API error:", self.provisioned.log)
        client.close.assert_called_once()

    @patch.object(ProvisionedApp, "_docker_client")
    def test_unexpected_error(self, mock_client):
        client = mock_client.return_value
        client.containers.get.side_effect = Exception("Unexpected")

        result = self.provisioned.stop_container()

        self.assertFalse(result)
        self.assertEqual(self.provisioned.status, "error")
        self.assertIn("Unexpected error:", self.provisioned.log)
        client.close.assert_called_once()