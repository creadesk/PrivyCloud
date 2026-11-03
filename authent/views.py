from django.contrib.auth import logout, login, authenticate
from django.contrib.auth.models import User, AnonymousUser
from django.shortcuts import render, redirect
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
from django.db import transaction


BANNED_PREFIXES  = ('delete_me_',)
BANNED_EXACTS    = {'delete_me'}
def username_is_banned(username: str) -> bool:
    """Gibt True zurück, wenn der Username nicht erlaubt ist."""
    # Case‑insensitiv – egal, ob 'Delete_Me' oder 'DELETE_ME'
    uname = username.lower()

    # Exakte Übereinstimmung
    if uname in BANNED_EXACTS:
        return True

    # Prefix‑Check (z. B. delete_me_xxx)
    for pref in BANNED_PREFIXES:
        if uname.startswith(pref):
            return True

    return False


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
    # Image Name des QR Codes aus der Session abrufen und Bild-Datei löschen
    img_name = request.session.get('img_name')

    if img_name:
        file_path = f'otp_qr/{img_name}'
        default_storage.delete(file_path)

    # Temporäre Benutzer-ID aus der Session abrufen
    temp_user_id = request.session.get('temp_user_id')

    # Benutzer ermitteln
    user = None

    if temp_user_id:
        try:
            user = User.objects.get(id=temp_user_id)
        except User.DoesNotExist:
            return redirect('login')

    elif not isinstance(request.user, AnonymousUser):
        try:
            user = User.objects.get(id=request.user.id)
        except User.DoesNotExist:
            return redirect('login')

    else:
        return redirect('login')

    # TOTP-Gerät ermitteln
    device = user.totpdevice_set.first()

    # Ohne TOTP-Gerät kann der Verifizierungsprozess nicht fortgesetzt werden
    if device is None:
        return redirect('login')

    if request.method == 'POST':
        otp_code = request.POST.get('otp_code')

        if device.verify_token(otp_code):
            # OTP korrekt: 2FA erfolgreich bestätigt
            device.confirmed = True
            device.save()

            # Temporären Benutzer vollständig einloggen
            if temp_user_id:
                login(request, user)
                del request.session['temp_user_id']

            return redirect('dashboard')

        else:
            # Fehlerhafte OTP-Eingabe
            error_message = "Ungültiger OTP-Code."

            return render(request, 'authent/verify_2fa.html', {
                "error_message": error_message,
                "PLATFORM_NAME": PLATFORM_NAME,
            })

    return render(request, 'authent/verify_2fa.html', {
        "PLATFORM_NAME": PLATFORM_NAME,
    })



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
        username = request.POST.get("username", "")
        password = request.POST.get("password", "")
        password_repeat = request.POST.get("password_repeat", "")
        error_message = None

        if username_is_banned(username):
            error_message = (
                "Der Benutzername ist nicht erlaubt. Bitte wähle einen anderen Namen."
            )

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
        if username.casefold() in password.casefold():
            error_message = (
                "Der Benutzername darf nicht im Passwort enthalten sein."
            )

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


        # Benutzer und zugehörige Daten atomar erstellen
        saved_file_path = None

        try:
            with transaction.atomic():

                # Benutzer erstellen
                user = User(username=username)
                user.set_password(password)
                user.save()

                # Automatischer Deployment-Limit-Eintrag
                UserDeploymentLimit.objects.create(user=user)

                # TOTP-Gerät erstellen
                device = TOTPDevice.objects.create(user=user)

                # QR-Code generieren
                img = qrcode.make(device.config_url)
                img_io = BytesIO()
                img.save(img_io)
                img_io.seek(0)

                otp_config_url = device.config_url

                # QR-Code-Datei speichern
                rand_str = generateRandomString(20)
                img_name = f'otp_qr_{rand_str}.png'

                file_path = f'otp_qr/{img_name}'
                img_file = ContentFile(img_io.read())

                saved_file_path = default_storage.save(
                    file_path,
                    img_file
                )

                qr_code_url = default_storage.url(saved_file_path)

            # Ab hier wurde die Datenbanktransaktion erfolgreich
            # abgeschlossen.

            request.session['user_saved_in_this_session'] = 'YES'
            request.session['temp_user_id'] = user.id
            request.session['img_name'] = img_name

            success_message = (
                "Registrierung erfolgreich. 2FA muss noch eingerichtet werden."
            )

            return render(request, 'authent/enable_2fa.html', {
                'qr_code_url': qr_code_url,
                'success_message': success_message,
                'otp_config_url': otp_config_url,
                'PLATFORM_NAME': PLATFORM_NAME,
            })

        except Exception as e:

            # Datenbankänderungen wurden durch atomic()
            # bereits zurückgerollt.

            # Falls die QR-Code-Datei bereits geschrieben wurde,
            # muss sie separat entfernt werden.
            if saved_file_path:
                try:
                    default_storage.delete(saved_file_path)
                except Exception:
                    pass

            error_message = (
                f"Fehler beim Erstellen des Benutzers: {e}"
            )

            return render(request, 'authent/register.html', {
                'error_message': error_message,
                'PLATFORM_NAME': PLATFORM_NAME,
            })


    return render(request, 'authent/register.html', {
        'PLATFORM_NAME': PLATFORM_NAME,
    })  # Formularseite anzeigen



