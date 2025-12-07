from django.contrib.auth import logout, login, authenticate
from django.contrib.auth.models import User, AnonymousUser
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django_otp.plugins.otp_totp.models import TOTPDevice
import qrcode
from io import BytesIO
from django.core.files.storage import default_storage
from django.core.files.base import ContentFile
import random
import string
from authent.forms import CaptchaForm
from core.settings import PLATFORM_NAME, IP_RATELIMIT_PER_MINUTE
from django_smart_ratelimit import rate_limit

from paas.models import UserDeploymentLimit


def generateRandomString(length=20):
  """
  Generiert eine zufällige Zeichenkette mit Buchstaben (Groß- und Kleinbuchstaben) und Zahlen.

  Args:
    length: Die Länge der zu generierenden Zeichenkette.  Standardmäßig 20.

  Returns:
    Eine zufällige Zeichenkette.
  """
  signs = string.ascii_letters + string.digits
  randomString = ''.join(random.choice(signs) for _ in range(length))
  return randomString

@rate_limit(key='ip', rate=f'{IP_RATELIMIT_PER_MINUTE}/m', block=True)
def verify_2fa(request):
    # Image Name des QR Code aus der Session abrufen und Bild-Datei löschen
    img_name = request.session.get('img_name')
    file_path = f'otp_qr/{img_name}'
    path = default_storage.delete(file_path)

    # Stelle sicher, dass der Benutzer ID aus der Sitzung abgerufen wird
    user = None
    temp_user_id = request.session.get('temp_user_id')

    if not temp_user_id and isinstance(request.user, AnonymousUser):
        return redirect('login')  # Falls keine temporäre Benutzer-ID vorhanden ist und der Benutzer auch noch nicht eingeloggt ist, zurück zum Login

    if temp_user_id:
        user = User.objects.get(id=temp_user_id)  # Hole den temporären nicht eingeloggten Benutzer

    if not isinstance(request.user, AnonymousUser):
        user = User.objects.get(id=request.user.id)  # Hole den eingeloggten Benutzer

    device = user.totpdevice_set.first()  # Nimm das erste TOTP-Gerät des Benutzers

    if request.method == 'POST':
        otp_code = request.POST.get('otp_code')

        if device.verify_token(otp_code):  # Überprüfe den OTP-Code
            # OTP korrekt, 2FA erfolgreich bestätigt
            device.confirmed = True
            device.save()

            # Benutzer jetzt vollständig einloggen
            if temp_user_id:
                login(request, user)
                del request.session['temp_user_id']  # Lösche temporäre Benutzer-ID aus der Sitzung
            return redirect('dashboard')  # Weiterleitung zur Startseite oder Dashboard

        else:
            # Fehlerhafte OTP-Eingabe
            error_message = "Ungültiger OTP-Code."
            return render(request, 'authent/verify_2fa.html',{
                "error_message": error_message,
                "PLATFORM_NAME": PLATFORM_NAME,
            })  # Seite zur Eingabe des OTP
    return render(request, 'authent/verify_2fa.html',{
        "PLATFORM_NAME": PLATFORM_NAME,
    })  # Seite zur Eingabe des OTP


@rate_limit(key='ip', rate=f'{IP_RATELIMIT_PER_MINUTE}/m', block=True)
def login_view(request):

    error_message = None
    captcha_solved = None
    login_attempts = 0

    if 'login_attempts' in request.session:
        login_attempts = request.session['login_attempts']

    captcha_form = CaptchaForm(request.POST)
    if captcha_form.is_valid():
        request.session['captcha_solved'] = 'YES'
        request.session['login_attempts'] = 0  # to prevent login guessing more better, comment this line out
        return redirect('login')

    if 'captcha_solved' in request.session:
        captcha_solved = request.session['captcha_solved']

    if login_attempts > 4: # these two lines should prevent login guessing very well
        captcha_solved = None

    if captcha_solved != "YES":
        captcha_form = CaptchaForm()
        return render(request, 'authent/captcha.html', {
            'captcha_form': captcha_form,
            'PLATFORM_NAME': PLATFORM_NAME,
        })


    if request.method == "POST":
        username = request.POST["username"]
        password = request.POST["password"]
        user = authenticate(request,username=username,password=password)

        if user is not None:
            # Wenn der Benutzer 2FA aktiviert hat, leite zur OTP-Verifizierung weiter
            if user.totpdevice_set.exists() and user.totpdevice_set.first().confirmed:
                # Temporäre Sitzung ohne vollständiges Login, nur für 2FA
                request.session['temp_user_id'] = user.id
                return redirect('verify_2fa')  # Weiter zur OTP-Verifizierung

            # Wenn der Benutzer 2FA nicht aktiviert hat, logge ihn vollständig ein
            login(request, user)
            return redirect('dashboard')  # Weiterleitung zur Startseite oder Dashboard
        else:
            login_attempts = login_attempts+1
            request.session['login_attempts'] = login_attempts
            error_message="Benutzername oder Passwort nicht korrekt!"
    return render(request, 'authent/login.html',{
        'error_message': error_message,
        "PLATFORM_NAME": PLATFORM_NAME,
    })



def logout_view(request):
    logout(request)
    return redirect('login')


@rate_limit(key='ip', rate=f'{IP_RATELIMIT_PER_MINUTE}/m', block=True)
def register_view(request):

    captcha_solved = None
    user_saved_in_this_session = None

    if 'user_saved_in_this_session' in request.session:
        user_saved_in_this_session = request.session['user_saved_in_this_session']

    captcha_form = CaptchaForm(request.POST)
    if captcha_form.is_valid():
        request.session['captcha_solved'] = 'YES'
        request.session['user_saved_in_this_session'] = 'NO' # to prevent registration spam more better, comment this line out
        return redirect('register')

    if 'captcha_solved' in request.session:
        captcha_solved = request.session['captcha_solved']

    if user_saved_in_this_session == 'YES': # these two lines should prevent registration spam very well
        captcha_solved = None

    if captcha_solved != "YES":
        captcha_form = CaptchaForm()
        return render(request, 'authent/captcha.html', {
            'captcha_form': captcha_form,
            'PLATFORM_NAME': PLATFORM_NAME,
        })

    if request.user.is_authenticated:
        return redirect('dashboard')  # Weiterleitung zur Hauptseite, wenn bereits ein Benutzer angemeldet ist

    if request.method == 'POST':
        username = request.POST.get("username")  # Verwende .get() für sicheres Abrufen
        password = request.POST.get("password")
        password_repeat = request.POST.get("password_repeat")
        error_message = None

        if len(username) < 8:
            error_message = "Der Benutzername muss mindestens 8 Zeichen lang sein."

        # Passwortüberprüfung (Länge und Komplexität)
        if len(password) < 15:
            error_message = "Das Passwort muss mindestens 15 Zeichen lang sein."
        elif not any(c.isalnum() for c in password):
            error_message = "Das Passwort muss mindestens einen Groß- und Kleinbuchstaben sowie eine Zahl enthalten."
        elif not any(c.isupper() for c in password):
            error_message = "Das Passwort muss mindestens einen Großbuchstaben enthalten."
        elif not any(c.islower() for c in password):
            error_message = "Das Passwort muss mindestens einen Kleinbuchstaben enthalten."
        elif not any(c.isdigit() for c in password):
            error_message = "Das Passwort muss mindestens eine Zahl enthalten."
        elif password != password_repeat:
            error_message = "Die Passwörter stimmen nicht überein."
        if username in password:
            error_message = "Der Benutzername darf nicht im Passwort enthalten sein."

        if error_message:
            return render(request, 'authent/register.html', {
                'error_message': error_message,
                'PLATFORM_NAME': PLATFORM_NAME,
            })

        # Benutzername überprüfen, ob er bereits existiert
        if User.objects.filter(username=username).exists():
            error_message = "Dieser Benutzername ist bereits vergeben."
            return render(request, 'authent/register.html', {
                'error_message': error_message,
                'PLATFORM_NAME': PLATFORM_NAME,
            })

        # Benutzer erstellen
        try:
            user = User(username=username)
            user.set_password(password)  # Wichtig: Passwort verschlüsseln
            user.save()

            # -> automatischer Limit‑Eintrag
            UserDeploymentLimit.objects.create(user=user)

            request.session['user_saved_in_this_session'] = 'YES'

            # Optional: Benutzer einloggen
            #login(request, user)

            # Ohne Login, User-ID wird temporär vorgehalten
            request.session['temp_user_id'] = user.id

            # Erstelle ein neues TOTP-Gerät für den Benutzer
            device = TOTPDevice.objects.create(user=user)

            # QR-Code aus dem URL für das TOTP-Gerät generieren
            img = qrcode.make(device.config_url)  # QR-Code für den geheimen Schlüssel erstellen
            img_io = BytesIO()
            img.save(img_io)
            img_io.seek(0)
            otp_config_url = device.config_url

            # Speichern des QR-Codes im richtigen Verzeichnis
            #img_name = f'otp_qr_{user.username}.png'
            rand_str = generateRandomString(20)
            img_name = f'otp_qr_{rand_str}.png'
            request.session['img_name'] = img_name

            # Speichern im MEDIA_ROOT-Verzeichnis (sicherer Ort für benutzerspezifische Dateien)
            file_path = f'otp_qr/{img_name}'  # Verzeichnis "otp_qr" unter MEDIA_ROOT
            img_file = ContentFile(img_io.read())
            path = default_storage.save(file_path, img_file)

            # Erfolgsmeldung und Weiterleitung
            success_message = "Registrierung erfolgreich. 2FA muss noch eingerichtet werden."
            # Den Dateipfad zur Anzeige des QR-Codes zurückgeben
            return render(request, 'authent/enable_2fa.html', {
                'qr_code_url': default_storage.url(path),
                'success_message': success_message,
                'otp_config_url': otp_config_url,
                'PLATFORM_NAME': PLATFORM_NAME,
            })
        except Exception as e:
            error_message = f"Fehler beim Erstellen des Benutzers: {e}"
            return render(request, 'authent/register.html', {
                'error_message': error_message,
                'PLATFORM_NAME': PLATFORM_NAME,
            })

    return render(request, 'authent/register.html', {
        'PLATFORM_NAME': PLATFORM_NAME,
    })  # Formularseite anzeigen



