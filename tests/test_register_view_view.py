'''
CAPTCHA nicht gelöst → CAPTCHA-Seite.
CAPTCHA erfolgreich gelöst → Weiterleitung zurück zu register.
Bereits angemeldeter Benutzer → Weiterleitung zu dashboard.
Ungültiger Benutzername.
Zu kurzes Passwort.
Fehlende Passwortkomplexität.
Unterschiedliche Passwörter.
Benutzername im Passwort.
Bereits existierender Benutzername.
Erfolgreiche Registrierung.
Automatischer UserDeploymentLimit-Eintrag.
Automatische Erstellung eines TOTP-Geräts.
Session-Werte nach erfolgreicher Registrierung.
Fehler beim Erstellen des Benutzers.
'''


import uuid
from unittest.mock import patch

from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth.models import User
from django_otp.plugins.otp_totp.models import TOTPDevice

from paas.models import UserDeploymentLimit


class RegisterViewTests(TestCase):

    def setUp(self):
        self.client = Client()
        self.url = reverse('register')

        # Für jeden Test eine eigene IP-Adresse verwenden,
        # damit das Rate-Limiting nicht zwischen Tests greift.
        unique_ip = uuid.uuid4().int

        self.client.defaults['REMOTE_ADDR'] = (
            f'10.{(unique_ip >> 16) % 256}.'
            f'{(unique_ip >> 8) % 256}.'
            f'{unique_ip % 256}'
        )

    def solve_captcha(self):
        """
        Löst das CAPTCHA über den vorgesehenen View-Ablauf.
        """
        response = self.client.post(self.url, {})
        self.assertEqual(response.status_code, 200)

        response = self.client.post(self.url, {
            # CaptchaForm wird für den konkreten Test
            # separat gemockt, siehe solve_captcha_mocked.
        })

        return response

    def solve_captcha_mocked(self):
        """
        Simuliert ein erfolgreich gelöstes CAPTCHA.
        """
        with patch(
            'authent.views.CaptchaForm.is_valid',
            return_value=True
        ):
            response = self.client.post(self.url)

        self.assertRedirects(response, self.url)

        # Session neu laden, damit die CAPTCHA-Freigabe
        # für die folgenden Requests erhalten bleibt.
        session = self.client.session
        session['captcha_solved'] = 'YES'
        session.save()

    def registration_data(
        self,
        username='testuser123',
        password='SecurePassword123!'
    ):
        return {
            'username': username,
            'password': password,
            'password_repeat': password,
        }

    def test_get_without_captcha_shows_captcha_page(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'authent/captcha.html')

    def test_captcha_success_redirects_to_register(self):
        with patch(
            'authent.views.CaptchaForm.is_valid',
            return_value=True
        ):
            response = self.client.post(self.url)

        self.assertRedirects(response, self.url)

        session = self.client.session
        self.assertEqual(session['captcha_solved'], 'YES')
        self.assertEqual(
            session['user_saved_in_this_session'],
            'NO'
        )

    def test_get_after_captcha_shows_registration_page(self):
        self.solve_captcha_mocked()

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'authent/register.html')

    def test_authenticated_user_is_redirected_to_dashboard(self):
        user = User.objects.create_user(
            username='loggedinuser',
            password='SecurePassword123!'
        )

        self.solve_captcha_mocked()
        self.client.force_login(user)

        response = self.client.get(self.url)

        self.assertRedirects(response, reverse('dashboard'))

    def test_username_too_short(self):
        self.solve_captcha_mocked()

        response = self.client.post(
            self.url,
            self.registration_data(username='short')
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'authent/register.html')
        self.assertContains(
            response,
            'Der Benutzername muss mindestens 8 Zeichen lang sein.'
        )

        self.assertFalse(
            User.objects.filter(username='short').exists()
        )

    def test_banned_username(self):
        self.solve_captcha_mocked()

        with patch(
            'authent.views.username_is_banned',
            return_value=True
        ):
            response = self.client.post(
                self.url,
                self.registration_data(username='banneduser')
            )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            'Der Benutzername ist nicht erlaubt.'
        )

        self.assertFalse(
            User.objects.filter(username='banneduser').exists()
        )

    def test_password_too_short(self):
        self.solve_captcha_mocked()

        response = self.client.post(
            self.url,
            {
                'username': 'validusername',
                'password': 'Short123!',
                'password_repeat': 'Short123!',
            }
        )

        self.assertContains(
            response,
            'Das Passwort muss mindestens 15 Zeichen lang sein.'
        )

        self.assertFalse(
            User.objects.filter(username='validusername').exists()
        )

    def test_password_without_uppercase(self):
        self.solve_captcha_mocked()

        password = 'securepassword123!'

        response = self.client.post(
            self.url,
            self.registration_data(
                username='validusername',
                password=password
            )
        )

        self.assertContains(
            response,
            'Das Passwort muss mindestens einen Großbuchstaben enthalten.'
        )

    def test_password_without_lowercase(self):
        self.solve_captcha_mocked()

        password = 'SECUREPASSWORD123!'

        response = self.client.post(
            self.url,
            self.registration_data(
                username='validusername',
                password=password
            )
        )

        self.assertContains(
            response,
            'Das Passwort muss mindestens einen Kleinbuchstaben enthalten.'
        )

    def test_password_without_number(self):
        self.solve_captcha_mocked()

        password = 'SecurePasswordABC!'

        response = self.client.post(
            self.url,
            self.registration_data(
                username='validusername',
                password=password
            )
        )

        self.assertContains(
            response,
            'Das Passwort muss mindestens eine Zahl enthalten.'
        )

    def test_password_without_alphanumeric_character(self):
        self.solve_captcha_mocked()

        password = '!!!!!!!!!!!!!!!'

        response = self.client.post(
            self.url,
            self.registration_data(
                username='validusername',
                password=password
            )
        )

        self.assertContains(
            response,
            'Das Passwort muss mindestens einen Groß- und Kleinbuchstaben sowie eine Zahl enthalten.'
        )

    def test_passwords_do_not_match(self):
        self.solve_captcha_mocked()

        response = self.client.post(
            self.url,
            {
                'username': 'validusername',
                'password': 'SecurePassword123!',
                'password_repeat': 'DifferentPassword123!',
            }
        )

        self.assertContains(
            response,
            'Die Passwörter stimmen nicht überein.'
        )


    def test_username_must_not_be_part_of_password(self):
        self.solve_captcha_mocked()

        username = 'validusername'

        passwords = [
            'validusername123!Secure',
            'Validusername123!Secure',
            'VALIDUSERNAME123!Secure',
        ]

        for password in passwords:
            with self.subTest(password=password):

                response = self.client.post(
                    self.url,
                    self.registration_data(
                        username=username,
                        password=password
                    )
                )

                self.assertEqual(response.status_code, 200)

                self.assertContains(
                    response,
                    'Der Benutzername darf nicht im Passwort enthalten sein.'
                )

                self.assertFalse(
                    User.objects.filter(username=username).exists()
                )


    def test_duplicate_username(self):
        User.objects.create_user(
            username='existinguser',
            password='SecurePassword123!'
        )

        self.solve_captcha_mocked()

        response = self.client.post(
            self.url,
            self.registration_data(username='existinguser')
        )

        self.assertContains(
            response,
            'Dieser Benutzername ist bereits vergeben.'
        )

        self.assertEqual(
            User.objects.filter(username='existinguser').count(),
            1
        )

    def test_successful_registration(self):
        self.solve_captcha_mocked()

        response = self.client.post(
            self.url,
            self.registration_data()
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(
            response,
            'authent/enable_2fa.html'
        )

        user = User.objects.get(username='testuser123')

        # Passwort muss gehasht gespeichert sein.
        self.assertTrue(
            user.check_password('SecurePassword123!')
        )

        # Benutzer wird nicht automatisch eingeloggt.
        self.assertFalse(
            '_auth_user_id' in self.client.session
        )

        # Deployment-Limit muss automatisch erstellt werden.
        self.assertTrue(
            UserDeploymentLimit.objects.filter(user=user).exists()
        )

        # TOTP-Gerät muss angelegt worden sein.
        self.assertTrue(
            TOTPDevice.objects.filter(user=user).exists()
        )

        # Session-Werte prüfen.
        session = self.client.session

        self.assertEqual(
            session['user_saved_in_this_session'],
            'YES'
        )

        self.assertEqual(
            session['temp_user_id'],
            user.id
        )

        self.assertTrue(
            session['img_name'].startswith('otp_qr_')
        )

        # Erfolgsmeldung und QR-Code prüfen.
        self.assertContains(
            response,
            'Registrierung erfolgreich.'
        )

        self.assertIn('qr_code_url', response.context)
        self.assertIn('otp_config_url', response.context)

    def test_registration_blocked_after_successful_registration(self):
        self.solve_captcha_mocked()

        response = self.client.post(
            self.url,
            self.registration_data()
        )

        self.assertEqual(response.status_code, 200)

        # Ein weiterer Aufruf in derselben Session darf
        # keine zweite Registrierung ermöglichen.
        response = self.client.post(
            self.url,
            self.registration_data(
                username='anotheruser123',
                password='AnotherSecurePassword123!'
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(
            response,
            'authent/captcha.html'
        )

        self.assertTrue(
            User.objects.filter(username='testuser123').exists()
        )

        self.assertFalse(
            User.objects.filter(username='anotheruser123').exists()
        )


    def test_registration_error_rolls_back_all_database_changes(self):
        self.solve_captcha_mocked()

        username = 'testuser123'

        with patch(
            'authent.views.qrcode.make',
            side_effect=Exception('Test QR generation error')
        ):
            response = self.client.post(
                self.url,
                self.registration_data(username=username)
            )

        # Fehlerseite wird angezeigt.
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(
            response,
            'authent/register.html'
        )

        self.assertContains(
            response,
            'Fehler beim Erstellen des Benutzers:'
        )

        # Benutzer muss zurückgerollt worden sein.
        self.assertFalse(
            User.objects.filter(username=username).exists()
        )

        # Deployment-Limit muss ebenfalls zurückgerollt worden sein.
        self.assertFalse(
            UserDeploymentLimit.objects.filter(
                user__username=username
            ).exists()
        )

        # Auch das TOTP-Gerät darf nicht existieren.
        self.assertFalse(
            TOTPDevice.objects.filter(
                user__username=username
            ).exists()
        )

        # Session darf keine Registrierungsdaten enthalten.
        session = self.client.session

        self.assertNotIn('temp_user_id', session)
        self.assertNotIn('img_name', session)

        self.assertNotEqual(
            session.get('user_saved_in_this_session'),
            'YES'
        )

    def test_deployment_limit_error_rolls_back_user(self):
        self.solve_captcha_mocked()

        with patch(
            'authent.views.UserDeploymentLimit.objects.create',
            side_effect=Exception('Deployment limit error')
        ):
            response = self.client.post(
                self.url,
                self.registration_data()
            )

        self.assertEqual(response.status_code, 200)

        self.assertFalse(
            User.objects.filter(username='testuser123').exists()
        )

        self.assertFalse(
            UserDeploymentLimit.objects.filter(
                user__username='testuser123'
            ).exists()
        )

        self.assertFalse(
            TOTPDevice.objects.filter(
                user__username='testuser123'
            ).exists()
        )


    def test_qr_code_storage_error_rolls_back_database_changes(self):
        self.solve_captcha_mocked()

        with patch(
            'authent.views.default_storage.save',
            side_effect=Exception('Storage error')
        ):
            response = self.client.post(
                self.url,
                self.registration_data()
            )

        self.assertEqual(response.status_code, 200)

        self.assertTemplateUsed(
            response,
            'authent/register.html'
        )

        self.assertContains(
            response,
            'Fehler beim Erstellen des Benutzers:'
        )

        self.assertFalse(
            User.objects.filter(username='testuser123').exists()
        )

        self.assertFalse(
            UserDeploymentLimit.objects.filter(
                user__username='testuser123'
            ).exists()
        )

        self.assertFalse(
            TOTPDevice.objects.filter(
                user__username='testuser123'
            ).exists()
        )


    def test_storage_url_error_deletes_saved_qr_file(self):
        self.solve_captcha_mocked()

        with patch(
            'authent.views.default_storage.save',
            return_value='otp_qr/test.png'
        ), patch(
            'authent.views.default_storage.url',
            side_effect=Exception('Storage URL error')
        ), patch(
            'authent.views.default_storage.delete',
            side_effect=Exception('Delete error')
        ) as mock_delete:

            response = self.client.post(
                self.url,
                self.registration_data()
            )

        self.assertEqual(response.status_code, 200)

        self.assertTemplateUsed(
            response,
            'authent/register.html'
        )

        self.assertContains(
            response,
            'Fehler beim Erstellen des Benutzers:'
        )

        # Datei muss zum Löschen übergeben worden sein.
        mock_delete.assert_called_once_with(
            'otp_qr/test.png'
        )

        # Auch wenn das Löschen fehlschlägt, darf die View
        # nicht mit einer weiteren Exception abbrechen.
        self.assertFalse(
            User.objects.filter(
                username='testuser123'
            ).exists()
        )

        self.assertFalse(
            UserDeploymentLimit.objects.filter(
                user__username='testuser123'
            ).exists()
        )

        self.assertFalse(
            TOTPDevice.objects.filter(
                user__username='testuser123'
            ).exists()
        )
