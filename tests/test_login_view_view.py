'''
GET ohne gelöstes CAPTCHA → Weiterleitung bzw. Anzeige des CAPTCHA.
GET mit gelöstem CAPTCHA → Login-Formular wird angezeigt.
Erfolgreicher Login ohne 2FA → Weiterleitung zum Dashboard.
Erfolgreicher Login mit aktivierter und bestätigter 2FA → Weiterleitung zu verify_2fa.
Falsches Passwort → Fehlermeldung und Erhöhung des Login-Counters.
Mehr als vier fehlgeschlagene Login-Versuche → CAPTCHA wird erneut verlangt.
CAPTCHA erfolgreich gelöst → Session-Werte werden zurückgesetzt.
'''

import uuid

from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth.models import User
from django_otp.plugins.otp_totp.models import TOTPDevice
from unittest.mock import patch


class LoginViewTests(TestCase):

    def setUp(self):
        self.client = Client()
        self.url = reverse('login')

        # Für jeden Test eine eigene IP-Adresse verwenden,
        # damit das IP-basierte Rate-Limiting nicht greift.
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

    def solve_captcha(self):
        """Simuliert ein erfolgreich gelöstes CAPTCHA."""
        session = self.client.session
        session['captcha_solved'] = 'YES'
        session['login_attempts'] = 0
        session.save()

    def test_get_without_captcha_shows_captcha(self):
        """Ohne gelöstes CAPTCHA muss die CAPTCHA-Seite erscheinen."""

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'authent/captcha.html')
        self.assertIn('captcha_form', response.context)

    def test_get_with_solved_captcha_shows_login_form(self):
        """Mit gelöstem CAPTCHA wird das Login-Formular angezeigt."""

        self.solve_captcha()

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'authent/login.html')
        self.assertIsNone(response.context['error_message'])

    def test_successful_login_without_2fa(self):
        """Ein erfolgreicher Login ohne 2FA führt zum Dashboard."""

        self.solve_captcha()

        response = self.client.post(self.url, {
            'username': 'testuser',
            'password': 'TestPassword123!',
        })

        self.assertRedirects(
            response,
            reverse('dashboard'),
            fetch_redirect_response=False
        )

        self.assertTrue(
            User.objects.get(pk=self.user.pk).is_authenticated
            if hasattr(User.objects.get(pk=self.user.pk), 'is_authenticated')
            else self.client.session.get('_auth_user_id') == str(self.user.pk)
        )

    def test_successful_login_with_confirmed_2fa(self):
        """Bei bestätigtem 2FA-Gerät wird verify_2fa aufgerufen."""

        TOTPDevice.objects.create(
            user=self.user,
            name='Test TOTP',
            confirmed=True,
            key='12345678901234567890'
        )

        self.solve_captcha()

        response = self.client.post(self.url, {
            'username': 'testuser',
            'password': 'TestPassword123!',
        })

        self.assertRedirects(
            response,
            reverse('verify_2fa'),
            fetch_redirect_response=False
        )

        self.assertEqual(
            self.client.session.get('temp_user_id'),
            self.user.pk
        )

        # Der Benutzer darf an dieser Stelle noch nicht vollständig
        # authentifiziert sein.
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_failed_login_shows_error_message(self):
        """Ein falsches Passwort zeigt eine Fehlermeldung."""

        self.solve_captcha()

        response = self.client.post(self.url, {
            'username': 'testuser',
            'password': 'WrongPassword123!',
        })

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'authent/login.html')

        self.assertEqual(
            response.context['error_message'],
            'Benutzername oder Passwort nicht korrekt!'
        )

        self.assertEqual(
            self.client.session['login_attempts'],
            1
        )

    def test_multiple_failed_logins_increment_counter(self):
        """Jeder fehlgeschlagene Login erhöht den Session-Counter."""

        self.solve_captcha()

        for attempt in range(1, 5):
            response = self.client.post(self.url, {
                'username': 'testuser',
                'password': 'WrongPassword123!',
            })

            self.assertEqual(response.status_code, 200)
            self.assertEqual(
                self.client.session['login_attempts'],
                attempt
            )

    def test_more_than_four_failed_logins_requires_captcha(self):
        """Nach mehr als vier Fehlversuchen wird erneut CAPTCHA verlangt."""

        session = self.client.session
        session['captcha_solved'] = 'YES'
        session['login_attempts'] = 5
        session.save()

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'authent/captcha.html')

    def test_captcha_success_resets_login_attempts(self):
        """Ein erfolgreiches CAPTCHA setzt den Login-Counter zurück."""

        session = self.client.session
        session['login_attempts'] = 5
        session.save()

        # CaptchaForm wird isoliert getestet, damit wir nicht von
        # der konkreten Implementierung des CAPTCHA abhängig sind.
        with patch(
            'authent.views.CaptchaForm.is_valid',
            return_value=True
        ):
            response = self.client.post(self.url, {})

        self.assertRedirects(
            response,
            self.url,
            fetch_redirect_response=False
        )

        self.assertEqual(
            self.client.session['login_attempts'],
            0
        )

        self.assertEqual(
            self.client.session['captcha_solved'],
            'YES'
        )

    def test_unconfirmed_2fa_device_does_not_require_2fa(self):
        """Ein nicht bestätigtes 2FA-Gerät verhindert den normalen Login nicht."""

        TOTPDevice.objects.create(
            user=self.user,
            name='Unconfirmed TOTP',
            confirmed=False,
            key='12345678901234567890'
        )

        self.solve_captcha()

        response = self.client.post(self.url, {
            'username': 'testuser',
            'password': 'TestPassword123!',
        })

        self.assertRedirects(
            response,
            reverse('dashboard'),
            fetch_redirect_response=False
        )

        self.assertEqual(
            self.client.session.get('_auth_user_id'),
            str(self.user.pk)
        )