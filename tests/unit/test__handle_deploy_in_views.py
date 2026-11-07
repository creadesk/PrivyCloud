'''
Berechtigung: Super-User-only-App.
Ungültiges Docker-Image.
Host nur für Super-User.
Environment-Variablen: korrekte Übernahme.
Nicht-editierbare Environment-Variablen werden ergänzt.
Editierbare Variablen überschreiben keine falschen Standardwerte.
Fehlende erforderliche Environment-Variablen.
Undefinierte Environment-Variablen.
Überschrittene Benutzerlimits.
Erfolgreicher Deployment-Aufruf als Super-User.
Erfolgreicher Deployment-Aufruf als normaler Benutzer.
Laufzeit ohne Limit.
Laufzeit mit Ablaufdatum.
Tor-Zertifikatsauthentisierung.
'''


from datetime import timedelta
from unittest.mock import patch, MagicMock

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.test import TestCase, RequestFactory
from django.utils import timezone

from paas.models import (
    AppDefinition,
    AppImageTag,
    AppEnvVarPerApp,
    RemoteHost,
    ProvisionedApp,
)

from paas.forms import DeployForm, DeployFormAdmin
from paas.views import _handle_deploy, _validate_env_vars


User = get_user_model()


class HandleDeployTests(TestCase):

    def setUp(self):
        self.factory = RequestFactory()

        # --------------------------------------------------
        # Benutzer
        # --------------------------------------------------
        self.user = User.objects.create_user(
            username='normal_user',
            password='test123'
        )

        self.superuser = User.objects.create_superuser(
            username='admin',
            password='test123',
            email='admin@example.com'
        )

        # --------------------------------------------------
        # App
        # --------------------------------------------------
        self.app = AppDefinition.objects.create(
            name='test-app',
            display_name='Test App',
            docker_image='example/test-app',
            default_duration=1,
            super_user_only=False,
            tor_auth_type='none',
        )

        self.tag = AppImageTag.objects.create(
            app_definition=self.app,
            tag='1.0'
        )

        self.valid_image = 'example/test-app:1.0'

        # --------------------------------------------------
        # Hosts
        # --------------------------------------------------
        self.host = RemoteHost.objects.create(
            hostname='host1.example.com',
            ip_address='192.168.1.10',
            current_load=1.0,
            nur_superuser=False,
        )

        self.admin_host = RemoteHost.objects.create(
            hostname='admin-host.example.com',
            ip_address='192.168.1.20',
            current_load=1.0,
            nur_superuser=True,
        )

        # --------------------------------------------------
        # Environment-Variablen
        # --------------------------------------------------
        self.env_required = AppEnvVarPerApp.objects.create(
            app=self.app,
            key='API_KEY',
            value='default-api-key',
            optional=False,
            editable=True,
        )

        self.env_optional = AppEnvVarPerApp.objects.create(
            app=self.app,
            key='OPTIONAL_VAR',
            value='default-value',
            optional=True,
            editable=True,
        )

        self.env_readonly = AppEnvVarPerApp.objects.create(
            app=self.app,
            key='INTERNAL_VAR',
            value='internal-value',
            optional=True,
            editable=False,
        )

        # --------------------------------------------------
        # Mock für Deploy-Limits
        # --------------------------------------------------
        self.limits_patch = patch(
            'paas.views._check_user_limits',
            return_value=True
        )
        self.mock_check_limits = self.limits_patch.start()
        self.addCleanup(self.limits_patch.stop)

        # Mock für Deployment-Task
        self.deploy_patch = patch(
            'paas.views.deploy_app_task'
        )
        self.mock_deploy_task = self.deploy_patch.start()
        self.addCleanup(self.deploy_patch.stop)

    # ======================================================
    # Hilfsfunktionen
    # ======================================================

    def make_request(self, user, extra_post=None):

        post_data = {
            'image_choice': self.valid_image,
            'env_API_KEY': 'my-secret-key',
        }

        if extra_post:
            post_data.update(extra_post)

        request = self.factory.post('/deploy/', post_data)
        request.user = user

        return request

    def make_form(self, user, duration=None, target_host=None):

        if duration is None:
            duration = timedelta(hours=1)

        cleaned_data = {
            'app': self.app,
            'duration': duration,
        }

        if target_host is not None:
            cleaned_data['target_host'] = target_host

        # Ein einfacher Form-Mock vermeidet, dass die Tests
        # von der konkreten Formularvalidierung abhängen.
        form = MagicMock()
        form.cleaned_data = cleaned_data

        return form

    # ======================================================
    # 1. Berechtigungen
    # ======================================================

    def test_superuser_only_app_denies_normal_user(self):

        self.app.super_user_only = True
        self.app.save()

        request = self.make_request(self.user)
        form = self.make_form(self.user)

        with self.assertRaises(PermissionDenied):
            _handle_deploy(request, form)

        self.assertEqual(
            ProvisionedApp.objects.count(),
            0
        )

        self.mock_deploy_task.assert_not_called()

    def test_superuser_can_deploy_superuser_only_app(self):

        self.app.super_user_only = True
        self.app.save()

        request = self.make_request(self.superuser)
        form = self.make_form(
            self.superuser,
            target_host=self.host
        )

        response = self.execute_successful_deploy(
            request,
            form
        )

        self.assertEqual(response.status_code, 200)

        self.assertEqual(
            ProvisionedApp.objects.count(),
            1
        )

    # ======================================================
    # 2. Docker-Image-Validierung
    # ======================================================

    @patch('paas.views.render_deploy')
    def test_invalid_docker_image_returns_error(self, mock_render):

        mock_render.return_value = MagicMock(status_code=200)

        request = self.make_request(
            self.user,
            {'image_choice': 'evil/image:latest'}
        )

        form = self.make_form(self.user)

        response = _handle_deploy(request, form)

        mock_render.assert_called_once()

        self.assertIn(
            'nicht gültig',
            mock_render.call_args.kwargs['error']
        )

        self.assertEqual(
            ProvisionedApp.objects.count(),
            0
        )

        self.mock_deploy_task.assert_not_called()

    # ======================================================
    # 3. Host-Berechtigungen
    # ======================================================

    @patch('paas.views.render_deploy')
    def test_normal_user_cannot_use_admin_host(self, mock_render):

        mock_render.return_value = MagicMock(status_code=200)

        request = self.make_request(self.user)

        form = self.make_form(
            self.user,
            target_host=self.admin_host
        )

        response = _handle_deploy(request, form)

        mock_render.assert_called_once()

        self.assertIn(
            'ausschließlich für Super-Users',
            mock_render.call_args.kwargs['error']
        )

        self.assertEqual(
            ProvisionedApp.objects.count(),
            0
        )

        self.mock_deploy_task.assert_not_called()

    def test_superuser_can_use_admin_host(self):

        request = self.make_request(self.superuser)

        form = self.make_form(
            self.superuser,
            target_host=self.admin_host
        )

        response = self.execute_successful_deploy(
            request,
            form
        )

        provision = ProvisionedApp.objects.get()

        self.assertEqual(provision.host, self.admin_host)

    # ======================================================
    # 4. Environment-Variablen
    # ======================================================

    def test_editable_environment_variable_is_used(self):

        request = self.make_request(self.user)

        form = self.make_form(self.user)

        response = self.execute_successful_deploy(
            request,
            form
        )

        call_args = self.mock_deploy_task.call_args

        env_vars = call_args.args[2]

        self.assertEqual(
            env_vars['API_KEY'],
            'my-secret-key'
        )

    def test_non_editable_environment_variable_is_added(self):

        request = self.make_request(self.user)

        form = self.make_form(self.user)

        response = self.execute_successful_deploy(
            request,
            form
        )

        env_vars = self.mock_deploy_task.call_args.args[2]

        self.assertEqual(
            env_vars['INTERNAL_VAR'],
            'internal-value'
        )

    def test_editable_variable_overrides_database_default(self):

        request = self.make_request(
            self.user,
            {
                'env_API_KEY': 'user-provided-key'
            }
        )

        form = self.make_form(self.user)

        self.execute_successful_deploy(
            request,
            form
        )

        env_vars = self.mock_deploy_task.call_args.args[2]

        self.assertEqual(
            env_vars['API_KEY'],
            'user-provided-key'
        )

    @patch('paas.views.render_deploy')
    def test_missing_required_environment_variable(
        self,
        mock_render
    ):

        mock_render.return_value = MagicMock(status_code=200)

        request = self.factory.post(
            '/deploy/',
            {
                'image_choice': self.valid_image,
            }
        )
        request.user = self.user

        form = self.make_form(self.user)

        _handle_deploy(request, form)

        mock_render.assert_called_once()

        self.assertIn(
            'API_KEY',
            mock_render.call_args.kwargs['error']
        )

        self.assertEqual(
            ProvisionedApp.objects.count(),
            0
        )

    @patch('paas.views.render_deploy')
    def test_empty_required_environment_variable(
        self,
        mock_render
    ):

        mock_render.return_value = MagicMock(status_code=200)

        request = self.make_request(
            self.user,
            {
                'env_API_KEY': '   '
            }
        )

        form = self.make_form(self.user)

        _handle_deploy(request, form)

        mock_render.assert_called_once()

        self.assertIn(
            'leer',
            mock_render.call_args.kwargs['error']
        )

        self.assertEqual(
            ProvisionedApp.objects.count(),
            0
        )

    @patch('paas.views.render_deploy')
    def test_undefined_environment_variable(
        self,
        mock_render
    ):

        mock_render.return_value = MagicMock(status_code=200)

        request = self.make_request(
            self.user,
            {
                'env_UNKNOWN': 'something'
            }
        )

        form = self.make_form(self.user)

        _handle_deploy(request, form)

        mock_render.assert_called_once()

        self.assertIn(
            'UNKNOWN',
            mock_render.call_args.kwargs['error']
        )

        self.assertEqual(
            ProvisionedApp.objects.count(),
            0
        )

    # ======================================================
    # 5. Benutzerlimits
    # ======================================================

    @patch('paas.views.render_deploy')
    def test_user_limits_exceeded_prevents_deployment(
        self,
        mock_render
    ):

        mock_render.return_value = MagicMock(status_code=200)

        self.mock_check_limits.return_value = False

        request = self.make_request(self.user)

        form = self.make_form(self.user)

        _handle_deploy(request, form)

        mock_render.assert_called_once()

        self.assertIn(
            'L i m i t s',
            mock_render.call_args.kwargs['error']
        )

        self.assertEqual(
            ProvisionedApp.objects.count(),
            0
        )

        self.mock_deploy_task.assert_not_called()

    # ======================================================
    # 6. Erfolgreiches Deployment
    # ======================================================

    def execute_successful_deploy(self, request, form):

        # Die Provision wird angelegt und anschließend erneut
        # aus der DB geladen. Der Task wird vollständig gemockt.
        return _handle_deploy(request, form)

    def test_successful_deployment_creates_provision(self):

        request = self.make_request(self.superuser)

        form = self.make_form(
            self.superuser,
            target_host=self.host
        )

        response = self.execute_successful_deploy(
            request,
            form
        )

        self.assertEqual(response.status_code, 200)

        provision = ProvisionedApp.objects.get()

        self.assertEqual(provision.user, self.superuser)
        self.assertEqual(provision.app, self.app)
        self.assertEqual(provision.host, self.host)
        self.assertEqual(provision.status, 'pending')

        self.mock_deploy_task.assert_called_once()

        args = self.mock_deploy_task.call_args

        self.assertEqual(args.args[0], provision.id)
        self.assertEqual(args.args[1], self.valid_image)

    def test_deployment_uses_least_load_strategy_for_normal_user(self):

        with patch(
            'paas.views.LeastLoadStrategy.select_target',
            return_value=self.host
        ) as mock_strategy:

            request = self.make_request(self.user)

            form = self.make_form(self.user)

            self.execute_successful_deploy(
                request,
                form
            )

            mock_strategy.assert_called_once()

            provision = ProvisionedApp.objects.get()

            self.assertEqual(
                provision.host,
                self.host
            )

    # ======================================================
    # 7. Laufzeit / Ablaufdatum
    # ======================================================

    def test_deployment_with_duration_sets_expiration(self):

        duration = timedelta(hours=2)

        request = self.make_request(self.superuser)

        form = self.make_form(
            self.superuser,
            duration=duration,
            target_host=self.host
        )

        before = timezone.now()

        self.execute_successful_deploy(
            request,
            form
        )

        after = timezone.now()

        provision = ProvisionedApp.objects.get()

        self.assertIsNotNone(provision.expires_at)

        self.assertGreaterEqual(
            provision.expires_at,
            before + duration
        )

        self.assertLessEqual(
            provision.expires_at,
            after + duration
        )

    def test_deployment_without_duration_has_no_expiration(self):

        request = self.make_request(self.superuser)

        form = self.make_form(
            self.superuser,
            duration=None,
            target_host=self.host
        )

        # make_form behandelt None standardmäßig als 1 Stunde.
        # Deshalb setzen wir den Wert explizit zurück.
        form.cleaned_data['duration'] = None

        self.execute_successful_deploy(
            request,
            form
        )

        provision = ProvisionedApp.objects.get()

        self.assertIsNone(provision.expires_at)

    # ======================================================
    # 8. Tor-Zertifikatsauthentisierung
    # ======================================================

    @patch('paas.views.render')
    @patch('paas.views._gen_x25519_keypair')
    @patch('paas.views._b32_encode')
    def test_tor_certificate_authentication(
            self,
            mock_encode,
            mock_keypair,
            mock_render
    ):

        self.app.tor_auth_type = 'cert'
        self.app.save()

        mock_keypair.return_value = (
            b'private-key',
            b'public-key'
        )

        mock_encode.side_effect = [
            'PRIVATE_BASE32',
            'PUBLIC_BASE32'
        ]

        # Render-Mock liefert eine normale HTTP-Response.
        from django.http import HttpResponse
        mock_render.return_value = HttpResponse('OK')

        request = self.make_request(self.superuser)

        form = self.make_form(
            self.superuser,
            target_host=self.host
        )

        response = self.execute_successful_deploy(
            request,
            form
        )

        self.assertEqual(response.status_code, 200)

        # Öffentlichen Schlüssel an Deploy-Task übergeben
        kwargs = self.mock_deploy_task.call_args.kwargs

        self.assertEqual(
            kwargs['tor_auth_type'],
            'cert'
        )

        self.assertEqual(
            kwargs['tor_pub_key'],
            'PUBLIC_BASE32'
        )

        # Privaten Schlüssel im Template-Kontext prüfen
        render_context = mock_render.call_args.args[2]

        self.assertEqual(
            render_context['tor_private_key'],
            'PRIVATE_BASE32'
        )

        # Privater Schlüssel darf nicht an den Deploy-Task gehen
        self.assertNotIn(
            'PRIVATE_BASE32',
            str(self.mock_deploy_task.call_args)
        )

class ValidateEnvVarsTests(TestCase):

    def setUp(self):

        self.app = AppDefinition.objects.create(
            name='env-test-app',
            display_name='Environment Test App',
            docker_image='example/env-test-app',
        )

        # Erforderliche Variable
        AppEnvVarPerApp.objects.create(
            app=self.app,
            key='API_KEY',
            value='default-api-key',
            optional=False,
            editable=True,
        )

        # Zweite erforderliche Variable
        AppEnvVarPerApp.objects.create(
            app=self.app,
            key='DATABASE_URL',
            value='sqlite:///db.sqlite3',
            optional=False,
            editable=True,
        )

        # Optionale Variable
        AppEnvVarPerApp.objects.create(
            app=self.app,
            key='DEBUG',
            value='false',
            optional=True,
            editable=True,
        )

        # Nicht-editierbare Variable
        AppEnvVarPerApp.objects.create(
            app=self.app,
            key='INTERNAL_SECRET',
            value='secret',
            optional=True,
            editable=False,
        )

    # ======================================================
    # 1. Alle erforderlichen Variablen vorhanden
    # ======================================================

    def test_all_required_variables_present(self):

        env_vars = {
            'API_KEY': 'my-api-key',
            'DATABASE_URL': 'postgres://localhost/db',
        }

        errors = _validate_env_vars(self.app, env_vars)

        self.assertEqual(errors, [])

    # ======================================================
    # 2. Optionale Variablen dürfen fehlen
    # ======================================================

    def test_optional_variables_may_be_missing(self):

        env_vars = {
            'API_KEY': 'my-api-key',
            'DATABASE_URL': 'postgres://localhost/db',
        }

        errors = _validate_env_vars(self.app, env_vars)

        self.assertEqual(errors, [])

    # ======================================================
    # 3. Fehlende erforderliche Variable
    # ======================================================

    def test_missing_required_variable(self):

        env_vars = {
            'API_KEY': 'my-api-key',
        }

        errors = _validate_env_vars(self.app, env_vars)

        self.assertEqual(
            errors,
            ["Erforderliche Umgebungsvariable 'DATABASE_URL' fehlt."]
        )

    # ======================================================
    # 4. Alle erforderlichen Variablen fehlen
    # ======================================================

    def test_all_required_variables_missing(self):

        env_vars = {}

        errors = _validate_env_vars(self.app, env_vars)

        self.assertCountEqual(
            errors,
            [
                "Erforderliche Umgebungsvariable 'API_KEY' fehlt.",
                "Erforderliche Umgebungsvariable 'DATABASE_URL' fehlt.",
            ]
        )

    # ======================================================
    # 5. Leere erforderliche Variable
    # ======================================================

    def test_empty_required_variable(self):

        env_vars = {
            'API_KEY': '',
            'DATABASE_URL': 'postgres://localhost/db',
        }

        errors = _validate_env_vars(self.app, env_vars)

        self.assertEqual(
            errors,
            ["Erforderliche Umgebungsvariable 'API_KEY' ist leer."]
        )

    # ======================================================
    # 6. Variable mit ausschließlich Leerzeichen
    # ======================================================

    def test_required_variable_with_whitespace_is_empty(self):

        env_vars = {
            'API_KEY': '   ',
            'DATABASE_URL': 'postgres://localhost/db',
        }

        errors = _validate_env_vars(self.app, env_vars)

        self.assertEqual(
            errors,
            ["Erforderliche Umgebungsvariable 'API_KEY' ist leer."]
        )

    # ======================================================
    # 7. Optionale Variable darf leer sein
    # ======================================================

    def test_optional_variable_may_be_empty(self):

        env_vars = {
            'API_KEY': 'my-api-key',
            'DATABASE_URL': 'postgres://localhost/db',
            'DEBUG': '',
        }

        errors = _validate_env_vars(self.app, env_vars)

        self.assertEqual(errors, [])

    # ======================================================
    # 8. Nicht definierte Variable
    # ======================================================

    def test_undefined_variable(self):

        env_vars = {
            'API_KEY': 'my-api-key',
            'DATABASE_URL': 'postgres://localhost/db',
            'UNKNOWN_VAR': 'something',
        }

        errors = _validate_env_vars(self.app, env_vars)

        self.assertEqual(
            errors,
            ["Undefinierte Umgebungsvariable 'UNKNOWN_VAR'."]
        )

    # ======================================================
    # 9. Mehrere nicht definierte Variablen
    # ======================================================

    def test_multiple_undefined_variables(self):

        env_vars = {
            'API_KEY': 'my-api-key',
            'DATABASE_URL': 'postgres://localhost/db',
            'UNKNOWN_1': 'value1',
            'UNKNOWN_2': 'value2',
        }

        errors = _validate_env_vars(self.app, env_vars)

        self.assertCountEqual(
            errors,
            [
                "Undefinierte Umgebungsvariable 'UNKNOWN_1'.",
                "Undefinierte Umgebungsvariable 'UNKNOWN_2'.",
            ]
        )

    # ======================================================
    # 10. Mehrere unterschiedliche Fehler gleichzeitig
    # ======================================================

    def test_multiple_validation_errors(self):

        env_vars = {
            'API_KEY': '',
            'UNKNOWN_VAR': 'something',
        }

        errors = _validate_env_vars(self.app, env_vars)

        self.assertCountEqual(
            errors,
            [
                "Undefinierte Umgebungsvariable 'UNKNOWN_VAR'.",
                "Erforderliche Umgebungsvariable 'DATABASE_URL' fehlt.",
                "Erforderliche Umgebungsvariable 'API_KEY' ist leer.",
            ]
        )

    # ======================================================
    # 11. Nicht-editierbare Variable ist trotzdem definiert
    # ======================================================

    def test_non_editable_variable_is_valid(self):

        env_vars = {
            'API_KEY': 'my-api-key',
            'DATABASE_URL': 'postgres://localhost/db',
            'INTERNAL_SECRET': 'secret',
        }

        errors = _validate_env_vars(self.app, env_vars)

        self.assertEqual(errors, [])

    # ======================================================
    # 12. Leerer Variablenwert mit optional=True
    # ======================================================

    def test_optional_variable_with_whitespace_is_valid(self):

        env_vars = {
            'API_KEY': 'my-api-key',
            'DATABASE_URL': 'postgres://localhost/db',
            'DEBUG': '   ',
        }

        errors = _validate_env_vars(self.app, env_vars)

        self.assertEqual(errors, [])

    # ======================================================
    # 13. Unbekannte App ohne Env-Definitionen
    # ======================================================

    def test_app_without_environment_definitions(self):

        empty_app = AppDefinition.objects.create(
            name='empty-app',
            display_name='Empty App',
            docker_image='example/empty-app',
        )

        errors = _validate_env_vars(empty_app, {})

        self.assertEqual(errors, [])

    # ======================================================
    # 14. Unbekannte Variable bei App ohne Definitionen
    # ======================================================

    def test_undefined_variable_for_app_without_definitions(self):

        empty_app = AppDefinition.objects.create(
            name='empty-app-2',
            display_name='Empty App 2',
            docker_image='example/empty-app',
        )

        errors = _validate_env_vars(
            empty_app,
            {'UNKNOWN': 'value'}
        )

        self.assertEqual(
            errors,
            ["Undefinierte Umgebungsvariable 'UNKNOWN'."]
        )
