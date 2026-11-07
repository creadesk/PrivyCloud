import pytest
from playwright.sync_api import Page, expect
import re
from urllib.parse import urlparse, parse_qs
import pyotp

# --------------------------------------------------
# Test-Konstanten
# --------------------------------------------------

LOGIN_URL = "/user-login/"
TEST_PASSWORD = "SuperSecure123!"

CAPTCHA_VALUE = "PASSED"

LOGIN_TITLE = "PrivyCloud - Captcha"
DASHBOARD_TITLE = "PrivyCloud - Apps"

BTN_LOGIN = "Anmelden"
BTN_CAPTCHA_SUBMIT = "Senden"
LNK_REGISTER = "Registrieren"
BTN_REGISTER = "Registrieren"
LNK_LOGIN = "Anmelden"





# --------------------------------------------------
# Wiederverwendbare Hilfsfunktionen
# --------------------------------------------------

def open_login_page(live_server, page: Page):
    """Öffnet die Login-Seite."""
    page.goto(f"{live_server.url}{LOGIN_URL}")


def solve_captcha(page: Page):
    """Löst das Captcha im Django-Test-Modus."""
    page.get_by_label("Captcha:").fill(CAPTCHA_VALUE)
    page.get_by_role("button", name=BTN_CAPTCHA_SUBMIT).click()


# --------------------------------------------------
# E2E-Tests: Captcha
# --------------------------------------------------

@pytest.mark.e2e
@pytest.mark.django_db(transaction=True)
def test_captcha_shown_when_not_solved(live_server, page: Page):
    """
    Captcha ist nicht gelöst:
    Die Captcha-Seite wird angezeigt, Login-Felder fehlen.
    """
    open_login_page(live_server, page)

    expect(page.get_by_text(LOGIN_TITLE, exact=False)).to_be_visible()

    expect(page.get_by_label("Benutzername:")).not_to_be_visible()
    expect(page.get_by_label("Passwort:")).not_to_be_visible()


@pytest.mark.e2e
@pytest.mark.django_db(transaction=True)
def test_register_form_shown_when_register_clicked(live_server, page: Page):

    """
        Captcha wird erfolgreich gelöst:
        Das Login-Formular wird angezeigt.
        """
    open_login_page(live_server, page)
    solve_captcha(page)

    expect(page.get_by_label("Benutzername:")).to_be_visible()
    expect(page.get_by_label("Passwort:")).to_be_visible()
    expect(page.get_by_role("button", name=BTN_LOGIN)).to_be_visible()

    # Link "Registrieren" klicken
    page.get_by_role("link", name=LNK_REGISTER).click()
    expect(page.get_by_label("Benutzername:")).to_be_visible()
    expect(page.get_by_label("Passwort:")).to_be_visible()
    expect(page.get_by_label("Passwort (Wiederholung):")).to_be_visible()
    expect(page.get_by_role("button", name=BTN_REGISTER)).to_be_visible()

    # Link "Anmelden" klicken
    page.get_by_role("link", name=LNK_LOGIN).click()
    expect(page.get_by_label("Benutzername:")).to_be_visible()
    expect(page.get_by_label("Passwort:")).to_be_visible()
    expect(page.get_by_role("button", name=BTN_LOGIN)).to_be_visible()

    # Link "Registrieren" klicken
    page.get_by_role("link", name=LNK_REGISTER).click()
    expect(page.get_by_label("Benutzername:")).to_be_visible()
    expect(page.get_by_label("Passwort:")).to_be_visible()
    expect(page.get_by_label("Passwort (Wiederholung):")).to_be_visible()
    expect(page.get_by_role("button", name=BTN_REGISTER)).to_be_visible()

    # --------------------------------------------------
    # 1. Benutzername zu kurz
    # --------------------------------------------------
    page.locator("#username").fill("abc")
    page.locator("#password").fill("SecureTestPassword123!")
    page.locator("#password_repeat").fill("SecureTestPassword123!")
    page.get_by_role("button", name="Registrieren").click()
    expect(page.get_by_text("Der Benutzername muss mindestens 8 Zeichen lang sein.")).to_be_visible()

    # --------------------------------------------------
    # 2. Passwort zu kurz
    # --------------------------------------------------
    page.locator("#username").fill("testuser123")
    page.locator("#password").fill("Short123!")
    page.locator("#password_repeat").fill("Short123!")
    page.get_by_role("button", name="Registrieren").click()
    expect(page.get_by_text("Das Passwort muss mindestens 15 Zeichen lang sein.")).to_be_visible()

    # --------------------------------------------------
    # 3. Kein Großbuchstabe
    # --------------------------------------------------
    page.locator("#username").fill("testuser123")
    page.locator("#password").fill("securetestpassword123")
    page.locator("#password_repeat").fill("securetestpassword123")
    page.get_by_role("button", name="Registrieren").click()
    expect(page.get_by_text("Das Passwort muss mindestens einen Großbuchstaben enthalten.")).to_be_visible()

    # --------------------------------------------------
    # 4. Kein Kleinbuchstabe
    # --------------------------------------------------
    page.locator("#username").fill("testuser123")
    page.locator("#password").fill("SECURETESTPASSWORD123")
    page.locator("#password_repeat").fill("SECURETESTPASSWORD123")
    page.get_by_role("button", name="Registrieren").click()
    expect(page.get_by_text("Das Passwort muss mindestens einen Kleinbuchstaben enthalten.")).to_be_visible()

    # --------------------------------------------------
    # 5. Keine Zahl
    # --------------------------------------------------
    page.locator("#username").fill("testuser123")
    page.locator("#password").fill("SecureTestPasswordABC")
    page.locator("#password_repeat").fill("SecureTestPasswordABC")
    page.get_by_role("button", name="Registrieren").click()
    expect(page.get_by_text("Das Passwort muss mindestens eine Zahl enthalten.")).to_be_visible()

    # --------------------------------------------------
    # 6. Unterschiedliche Passwörter
    # --------------------------------------------------
    page.locator("#username").fill("testuser123")
    page.locator("#password").fill("SecureTestPassword123!")
    page.locator("#password_repeat").fill("DifferentPassword123!")
    page.get_by_role("button", name="Registrieren").click()
    expect(page.get_by_text("Die Passwörter stimmen nicht überein.")).to_be_visible()

    # --------------------------------------------------
    # 7. Benutzername im Passwort enthalten
    # --------------------------------------------------
    page.locator("#username").fill("testuser123")
    page.locator("#password").fill("testuser123Secure123!")
    page.locator("#password_repeat").fill("testuser123Secure123!")
    page.get_by_role("button", name="Registrieren").click()
    expect(page.get_by_text("Der Benutzername darf nicht im Passwort enthalten sein.")).to_be_visible()

    # --------------------------------------------------
    # 8. Benutzereingaben ok
    # --------------------------------------------------

    username = "playwright_test_user_001"
    password = "SecureTestPassword123!"

    page.locator("#username").fill(username)
    page.locator("#password").fill(password)
    page.locator("#password_repeat").fill(password)

    page.get_by_role("button", name="Registrieren").click()

    # Überschrift prüfen
    expect(page.get_by_role(
        "heading",
        name="Zwei-Faktor-Authentifizierung (2FA) aktivieren."
    )).to_be_visible()

    # Erfolgsmeldung prüfen
    expect(page.get_by_text(
        "Registrierung erfolgreich. 2FA muss noch eingerichtet werden."
    )).to_be_visible()

    # QR-Code vorhanden und sichtbar
    qr_code = page.locator('img[alt="QR code for TOTP"]')

    expect(qr_code).to_be_visible()
    expect(qr_code).to_have_attribute(
        "src",
        re.compile(r"/media/otp_qr/otp_qr_.+\.png")
    )

    # OTP-Konfigurationslink vorhanden
    otp_link = page.locator('a[href^="otpauth://totp/"]')

    expect(otp_link).to_be_visible()

    # Weiterleitung zur 2FA-Verifizierung prüfen
    verify_link = page.get_by_role(
        "link",
        name="Weiter zur Verifizierung"
    )

    expect(verify_link).to_be_visible()
    expect(verify_link).to_have_attribute(
        "href",
        "/verify-2fa/"
    )

    # --------------------------------------------------
    # 9. Secret aus dem otpauth-Link extrahieren
    # --------------------------------------------------
    otpauth_href = otp_link.get_attribute("href")
    assert otpauth_href is not None

    parsed = urlparse(otpauth_href)
    query = parse_qs(parsed.query)
    secret = query.get("secret", [None])[0]
    assert secret is not None, "TOTP-Secret konnte nicht aus dem otpauth-Link extrahiert werden"
    print(f"Extrahiertes TOTP-Secret: {secret}")

    # --------------------------------------------------
    # 10. Weiter zur Verifizierung klicken
    # --------------------------------------------------
    verify_link.click()

    # Auf der Verify-Seite angekommen
    expect(page).to_have_url(re.compile(r".*/verify-2fa/?$"))

    # Überschrift / Inhalt der Verify-Seite prüfen (anpassen, falls dein Template anders aussieht)
    expect(page.get_by_text("Verifiziere deinen 2FA-Code", exact=False)).to_be_visible()

    # OTP-Eingabefeld vorhanden
    otp_input = page.locator("#otp_code")  # oder page.get_by_label(...) / name="otp_code"
    expect(otp_input).to_be_visible()

    # --------------------------------------------------
    # 11. Negativ-Fall: Ungültiger OTP-Code
    # --------------------------------------------------
    wrong_otp = "000000"  # garantiert falsch
    otp_input.fill(wrong_otp)
    page.get_by_role("button", name="Bestätigen").click()

    # Fehlermeldung muss erscheinen
    expect(page.get_by_text("Ungültiger OTP-Code.")).to_be_visible()

    # User darf NICHT eingeloggt sein / darf nicht auf dem Dashboard landen
    expect(page).to_have_url(re.compile(r".*/verify-2fa/?$"))
    expect(page.get_by_text(DASHBOARD_TITLE)).not_to_be_visible()

    # --------------------------------------------------
    # 12. Gültigen TOTP-Code generieren und absenden
    # --------------------------------------------------

    totp = pyotp.TOTP(secret)
    otp_code = totp.now()
    print(f"Generierter OTP-Code: {otp_code}")

    otp_input.fill(otp_code)

    # Submit-Button klicken (anpassen an deinen Button-Text / Selector)
    page.get_by_role("button", name="Bestätigen").click()

    # --------------------------------------------------
    # 13. Erfolgreiche 2FA-Verifizierung + Login prüfen
    # --------------------------------------------------
    # Nach erfolgreicher Verifizierung sollte der User eingeloggt und auf dem Dashboard landen
    expect(page.get_by_text(DASHBOARD_TITLE)).to_be_visible()
