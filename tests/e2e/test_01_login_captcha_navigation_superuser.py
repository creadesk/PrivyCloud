
import pytest

from playwright.sync_api import Page, expect


# --------------------------------------------------
# Test-Konstanten
# --------------------------------------------------

LOGIN_URL = "/user-login/"
TEST_PASSWORD = "SuperSecure123!"

CAPTCHA_VALUE = "PASSED"

LOGIN_TITLE = "PrivyCloud - Captcha"
DASHBOARD_TITLE = "PrivyCloud - Apps"

BTN_CAPTCHA_SUBMIT = "Senden"
BTN_LOGIN = "Anmelden"
BTN_APP_DEPLOYMENT = "App Bereitstellung"
BTN_NEW_APP = "Neue App bereitstellen"
BTN_HOME = "Home"
BTN_IMAGE_MANAGEMENT = "Image Verwaltung"
BTN_DATA_CORPSES = "Datenrückstände"
BTN_DATA_EXPORT = "Datenexport"
BTN_DATA_IMPORT = "Datenimport"
BTN_ADMIN = "Admin"
LNK_GROUPS = "Groups"
LNK_USERS = "Users"
LNK_TOTPD = "TOTP devices"
LNK_APP_DEF = "App Definitions"
LNK_APP_ENV_VARIABLES = "App Environment Variables"
LNK_APP_IMAGE_TAGS = "App Image Tags"
LNK_APP_VOLUMES = "App Volumes"
LNK_DEPLOYMENT_LIMITS = "Deployment‑Limits"
LNK_PATCH_CONFIGURATIONS = "Patch Configurations"
LNK_PROVISIONED_APPS = "Provisioned Apps"
LNK_TARGET_HOSTS = "Target-Hosts"
LNK_VIEW_SITE = "View site"
BTN_LOGOUT = "Logout"






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


def login_as_user(page: Page, username: str, password: str):
    """Führt den Login mit Benutzername und Passwort durch."""
    page.get_by_label("Benutzername:").fill(username)
    page.get_by_label("Passwort:").fill(password)
    page.get_by_role("button", name=BTN_LOGIN).click()


def click_button(page: Page, name: str):
    """Klickt auf einen Button anhand seiner Beschriftung."""
    page.get_by_role("button", name=name).click()


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
def test_login_form_shown_when_captcha_solved(live_server, page: Page):
    """
    Captcha wird erfolgreich gelöst:
    Das Login-Formular wird angezeigt.
    """
    open_login_page(live_server, page)
    solve_captcha(page)

    expect(page.get_by_label("Benutzername:")).to_be_visible()
    expect(page.get_by_label("Passwort:")).to_be_visible()
    expect(page.get_by_role("button", name=BTN_LOGIN)).to_be_visible()


# --------------------------------------------------
# E2E-Tests: Superuser und Navigation
# --------------------------------------------------

@pytest.mark.e2e
@pytest.mark.django_db(transaction=True)
def test_superuser_login_and_navigation(live_server, page: Page, superuser):
    """
    Superuser kann sich einloggen und die verschiedenen
    Bereiche der Anwendung aufrufen.
    """

    # Login
    open_login_page(live_server, page)
    solve_captcha(page)

    login_as_user(
        page,
        username=superuser.username,
        password=TEST_PASSWORD,
    )

    # Dashboard
    expect(page.get_by_text(DASHBOARD_TITLE)).to_be_visible()

    # App-Bereitstellung
    click_button(page, BTN_APP_DEPLOYMENT)
    expect(page.get_by_role("button", name=BTN_NEW_APP)).to_be_visible()

    # Zurück zum Dashboard
    click_button(page, BTN_HOME)
    expect(page.get_by_role("button", name=BTN_APP_DEPLOYMENT)).to_be_visible()

    # Image-Verwaltung
    click_button(page, BTN_IMAGE_MANAGEMENT)
    expect(page.get_by_text("Docker Images – PrivyCloud")).to_be_visible()

    # Zurück zum Dashboard
    click_button(page, BTN_HOME)
    expect(page.get_by_role("button", name=BTN_APP_DEPLOYMENT)).to_be_visible()

    # Datenrückstände
    click_button(page, BTN_DATA_CORPSES)
    expect(page.get_by_text("Datenrückstände – PrivyCloud")).to_be_visible()

    # Zurück zum Dashboard
    click_button(page, BTN_HOME)
    expect(page.get_by_role("button", name=BTN_APP_DEPLOYMENT)).to_be_visible()

    # Datenexport
    click_button(page, BTN_DATA_EXPORT)
    expect(page.get_by_text("PrivyCloud - PaaS-Export-Select")).to_be_visible()

    # Zurück zum Dashboard
    click_button(page, BTN_HOME)
    expect(page.get_by_role("button", name=BTN_APP_DEPLOYMENT)).to_be_visible()

    # Datenimport
    click_button(page, BTN_DATA_IMPORT)
    expect(page.get_by_text("PrivyCloud - PaaS-Import-Select")).to_be_visible()

    # Zurück zum Dashboard
    click_button(page, BTN_HOME)
    expect(page.get_by_role("button", name=BTN_APP_DEPLOYMENT)).to_be_visible()

    # Admin Bereich aufrufen / Achtung neuer Browser Tab "admin_page" statt "page" verwenden
    with page.context.expect_page() as new_page:
        click_button(page, BTN_ADMIN)

    admin_page = new_page.value
    admin_page.wait_for_load_state()

    expect(
        admin_page.get_by_role("heading", name="Site administration")
    ).to_be_visible()

    # Link "Groups" klicken
    admin_page.get_by_role("link", name=LNK_GROUPS).click()
    expect(admin_page.get_by_text("Select group to change")).to_be_visible()

    # Link "Users" klicken
    admin_page.get_by_role("link", name=LNK_USERS).click()
    expect(admin_page.get_by_text("Select user to change")).to_be_visible()

    # Link "TOTP Devices" klicken
    admin_page.get_by_role("link", name=LNK_TOTPD).click()
    expect(admin_page.get_by_text("Select TOTP device to change")).to_be_visible()

    # Link "App Definitions" klicken
    admin_page.get_by_role("link", name=LNK_APP_DEF).click()
    expect(admin_page.get_by_text("Select App Definition to change")).to_be_visible()

    # Link "App Definitions" klicken
    admin_page.get_by_role("link", name=LNK_APP_DEF).click()
    expect(admin_page.get_by_text("Select App Definition to change")).to_be_visible()

    # Link "App Environment Variables" klicken
    admin_page.get_by_role("link", name=LNK_APP_ENV_VARIABLES).click()
    expect(admin_page.get_by_text("Select App Environment Variable to change")).to_be_visible()

    # Link "App Image Tags" klicken
    admin_page.get_by_role("link", name=LNK_APP_IMAGE_TAGS).click()
    expect(admin_page.get_by_text("Select App Image Tag to change")).to_be_visible()

    # Link "App Volumes" klicken
    admin_page.get_by_role("link", name=LNK_APP_VOLUMES).click()
    expect(admin_page.get_by_text("Select App Volume to change")).to_be_visible()

    # Link "Deployment‑Limits" klicken
    admin_page.get_by_role("link", name=LNK_DEPLOYMENT_LIMITS).click()
    expect(admin_page.get_by_text("Select Deployment‑Limit to change")).to_be_visible()

    # Link "Patch Configurations" klicken
    admin_page.get_by_role("link", name=LNK_PATCH_CONFIGURATIONS).click()
    expect(admin_page.get_by_text("Select Patch Configuration to change")).to_be_visible()

    # Link "Provisioned Apps" klicken
    admin_page.get_by_role("link", name=LNK_PATCH_CONFIGURATIONS).click()
    expect(admin_page.get_by_text("Select Patch Configuration to change")).to_be_visible()

    # Link "Provisioned Apps" klicken
    admin_page.get_by_role("link", name=LNK_PROVISIONED_APPS).click()
    expect(admin_page.get_by_text("Select Provisioned App to change")).to_be_visible()

    # Link "Target-Hosts" klicken
    admin_page.get_by_role("link", name=LNK_TARGET_HOSTS).click()
    expect(admin_page.get_by_text("Select Target-Host to change")).to_be_visible()

    # Link "View site" klicken
    admin_page.get_by_role("link", name=LNK_VIEW_SITE).click()
    expect(admin_page.get_by_role("button", name=BTN_APP_DEPLOYMENT)).to_be_visible()

    # Button "Logout" klicken
    admin_page.get_by_role("button", name=BTN_LOGOUT).click()
    expect(admin_page.get_by_text(LOGIN_TITLE, exact=False)).to_be_visible() # Captcha wird angezeigt
    expect(admin_page.get_by_label("Benutzername:")).not_to_be_visible() # Login Seite wird NICHT angezeigt
    expect(admin_page.get_by_label("Passwort:")).not_to_be_visible()






