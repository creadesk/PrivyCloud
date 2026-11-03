'''
GET ohne angemeldeten Benutzer und ohne temporäre Session-ID: Weiterleitung zum Login.
GET mit temporärer Benutzer-ID: Anzeige der 2FA-Seite.
GET mit angemeldetem Benutzer: Anzeige der 2FA-Seite.
POST mit gültigem OTP-Code: TOTP-Gerät wird bestätigt und Weiterleitung zum Dashboard.
POST mit ungültigem OTP-Code: Fehlermeldung wird angezeigt, Gerät bleibt unbestätigt.
Erfolgreiche 2FA bei temporärem Benutzer: Benutzer wird eingeloggt und temp_user_id aus der Session entfernt.
QR-Code-Datei: Wird beim Aufruf gelöscht.
'''

from unittest.mock import patch, MagicMock

from django.test import TestCase, Client
from django.contrib.auth.models import User
from django.urls import reverse
from django_otp.plugins.otp_totp.models import TOTPDevice
from authent.views import verify_2fa
import uuid




class Verify2FAViewTest(TestCase):

    def setUp(self):
        self.client = Client()
        self.url = reverse('verify_2fa')

        # Für jeden Test eine eigene IP-Adresse verwenden.
        unique_ip = uuid.uuid4().int

        self.client.defaults['REMOTE_ADDR'] = (
            f'10.{(unique_ip >> 16) % 256}.'
            f'{(unique_ip >> 8) % 256}.'
            f'{unique_ip % 256}'
        )

        self.user = User.objects.create_user(
            username='testuser',
            email='test@example.com',
            password='TestPassword123!'
        )

        self.device = TOTPDevice.objects.create(
            user=self.user,
            name='default',
            confirmed=False
        )

    def set_session(self, **kwargs):
        session = self.client.session

        for key, value in kwargs.items():
            session[key] = value

        session.save()

    @patch('authent.views.default_storage.delete')
    def test_anonymous_user_without_temp_user_id_redirects_to_login(
            self, mock_delete
    ):
        """
        Ein nicht angemeldeter Benutzer ohne temporäre Benutzer-ID
        wird zum Login weitergeleitet.
        """
        response = self.client.get(self.url)

        self.assertRedirects(response, reverse('login'))

        # Ohne img_name in der Session darf keine Datei gelöscht werden.
        mock_delete.assert_not_called()

    @patch('authent.views.default_storage.delete')
    def test_get_with_temp_user_id_displays_verify_page(self, mock_delete):
        """
        Ein Benutzer mit temporärer Session-ID erhält die 2FA-Seite.
        """
        self.set_session(temp_user_id=self.user.id)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'authent/verify_2fa.html')

        self.assertNotIn('error_message', response.context)

    @patch('authent.views.default_storage.delete')
    def test_get_with_authenticated_user_displays_verify_page(
        self, mock_delete
    ):
        """
        Ein angemeldeter Benutzer erhält die 2FA-Seite.
        """
        self.client.force_login(self.user)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'authent/verify_2fa.html')

    @patch('authent.views.default_storage.delete')
    def test_post_with_valid_otp_confirms_device(self, mock_delete):
        """
        Ein gültiger OTP-Code bestätigt das Gerät.
        """
        self.client.force_login(self.user)

        with patch.object(
                TOTPDevice,
                'verify_token',
                return_value=True
        ) as mock_verify:
            response = self.client.post(
                self.url,
                {'otp_code': '123456'}
            )

        mock_verify.assert_called_once()

        self.assertRedirects(response, reverse('dashboard'))

        self.device.refresh_from_db()

        self.assertTrue(self.device.confirmed)
        mock_verify.assert_called_once_with('123456')

    @patch('authent.views.default_storage.delete')
    def test_post_with_invalid_otp_displays_error(self, mock_delete):
        """
        Ein ungültiger OTP-Code zeigt eine Fehlermeldung an.
        """
        self.client.force_login(self.user)

        with patch.object(
                TOTPDevice,
                'verify_token',
                return_value=False
        ) as mock_verify:
            response = self.client.post(
                self.url,
                {'otp_code': '000000'}
            )

        mock_verify.assert_called_once()

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'authent/verify_2fa.html')

        self.assertContains(response, 'Ungültiger OTP-Code.')

        self.device.refresh_from_db()

        self.assertFalse(self.device.confirmed)
        mock_verify.assert_called_once_with('000000')

    @patch('authent.views.default_storage.delete')
    def test_valid_otp_logs_in_temp_user_and_removes_session_id(
        self, mock_delete
    ):
        """
        Ein Benutzer mit temporärer ID wird nach erfolgreicher
        2FA eingeloggt.

        Anschließend wird temp_user_id aus der Session entfernt.
        """
        self.set_session(temp_user_id=self.user.id)

        with patch.object(
                TOTPDevice,
                'verify_token',
                return_value=True
        ) as mock_verify:
            response = self.client.post(
                self.url,
                {'otp_code': '123456'}
            )

        mock_verify.assert_called_once()

        self.assertRedirects(response, reverse('dashboard'))

        self.assertNotIn('temp_user_id', self.client.session)

        self.assertTrue(
            User.objects.filter(
                id=self.user.id
            ).exists()
        )

        self.assertEqual(
            int(self.client.session['_auth_user_id']),
            self.user.id
        )

        self.device.refresh_from_db()
        self.assertTrue(self.device.confirmed)

    @patch('authent.views.default_storage.delete')
    def test_qr_image_is_deleted(self, mock_delete):
        """
        Die QR-Code-Datei wird beim Aufruf der View gelöscht.
        """
        self.set_session(
            temp_user_id=self.user.id,
            img_name='test_qr.png'
        )

        self.client.get(self.url)

        mock_delete.assert_called_once_with(
            'otp_qr/test_qr.png'
        )

    @patch('authent.views.default_storage.delete')
    def test_platform_name_is_in_context(self, mock_delete):
        """
        Der Plattformname wird an das Template übergeben.
        """
        self.set_session(temp_user_id=self.user.id)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertIn('PLATFORM_NAME', response.context)

    @patch('authent.views.default_storage.delete')
    def test_get_with_temp_user_id_without_totp_device_redirects_to_login(
        self, mock_delete
    ):
        """
        Ein Benutzer ohne TOTP-Gerät wird zum Login weitergeleitet.
        """
        self.device.delete()

        self.set_session(temp_user_id=self.user.id)

        response = self.client.get(self.url)

        self.assertRedirects(response, reverse('login'))

    @patch('authent.views.default_storage.delete')
    def test_get_with_invalid_temp_user_id_redirects_to_login(
        self, mock_delete
    ):
        """
        Eine ungültige temporäre Benutzer-ID führt zum Abbruch
        des Verifizierungsprozesses.
        """
        self.set_session(temp_user_id=999999)

        response = self.client.get(self.url)

        self.assertRedirects(response, reverse('login'))

    @patch('authent.views.default_storage.delete')
    def test_post_without_totp_device_redirects_to_login(
        self, mock_delete
    ):
        """
        Ein POST ohne vorhandenes TOTP-Gerät wird abgebrochen.
        """
        self.device.delete()

        self.set_session(temp_user_id=self.user.id)

        response = self.client.post(
            self.url,
            {'otp_code': '123456'}
        )

        self.assertRedirects(response, reverse('login'))

    @patch('authent.views.default_storage.delete')
    def test_post_with_invalid_temp_user_id_redirects_to_login(
        self, mock_delete
    ):
        """
        Ein POST mit ungültiger temporärer Benutzer-ID
        wird abgebrochen.
        """
        self.set_session(temp_user_id=999999)

        response = self.client.post(
            self.url,
            {'otp_code': '123456'}
        )

        self.assertRedirects(response, reverse('login'))

    @patch('authent.views.default_storage.delete')
    def test_invalid_user_does_not_attempt_otp_verification(
        self, mock_delete
    ):
        """
        Bei ungültiger Benutzer-ID darf keine OTP-Verifikation
        stattfinden.
        """
        self.set_session(temp_user_id=999999)

        with patch.object(
            TOTPDevice,
            'verify_token'
        ) as mock_verify:

            response = self.client.post(
                self.url,
                {'otp_code': '123456'}
            )

        self.assertRedirects(response, reverse('login'))
        mock_verify.assert_not_called()



    @patch('authent.views.default_storage.delete')
    def test_authenticated_user_not_found_redirects_to_login(
        self, mock_delete
    ):
        """
        Ein authentifizierter Benutzer wird bei der erneuten
        Datenbankabfrage nicht gefunden.
        """
        self.client.force_login(self.user)

        # Sicherstellen, dass kein temporärer Benutzer vorhanden ist.
        session = self.client.session
        session.pop('temp_user_id', None)
        session.save()

        with patch(
            'authent.views.User.objects.get',
            side_effect=User.DoesNotExist
        ) as mock_get:

            response = self.client.get(self.url)

        mock_get.assert_called_once_with(id=self.user.id)

        self.assertRedirects(
            response,
            reverse('login')
        )



    @patch('authent.views.default_storage.delete')
    def test_authenticated_user_not_found_redirects_to_login(
        self, mock_delete
    ):
        self.client.force_login(self.user)

        with patch(
            'authent.views.User.objects.get',
            side_effect=User.DoesNotExist
        ):
            response = self.client.get(self.url)

        self.assertRedirects(
            response,
            reverse('login')
        )

