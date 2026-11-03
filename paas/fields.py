import re
from datetime import timedelta
from django import forms
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

DURATION_RE = re.compile(r'^(?P<value>\d+)(?P<unit>[hdwm])$')

def parse_duration(value):
    """Konvertiert '1h', '2d', … in ein datetime.timedelta (oder None)."""

    if value == 'no_limit':
        return None

    if not isinstance(value, str):
        raise ValidationError(
            _('Ungültige Dauerangabe: %(value)s'),
            params={'value': value},
        )

    match = DURATION_RE.match(value)
    if not match:
        raise ValidationError(
            _('Ungültige Dauerangabe: %(value)s'),
            params={'value': value},
        )

    val = int(match.group('value'))
    unit = match.group('unit')
    if unit == 'h':
        return timedelta(hours=val)
    if unit == 'd':
        return timedelta(days=val)
    if unit == 'w':
        return timedelta(weeks=val)
    if unit == 'm':
        return timedelta(days=30 * val)

    raise ValidationError(
        _('Unbekannte Einheit: %(unit)s'),
        params={'unit': unit},
    )

class DurationField(forms.ChoiceField):
    """
    Ein ChoiceField, dessen Werte Strings wie '1h', '2d', … sind.
    In `clean()` wird der String anschließend in ein datetime.timedelta umgewandelt.
    """

    # Der Widget‑Typ bleibt unverändert
    widget = forms.Select

    def to_python(self, value):
        """
        Für die Choice-Validierung behalten wir den ursprünglichen String.
        """
        if value in (None, ''):
            if self.required:
                raise ValidationError(_('Bitte wählen Sie eine Dauer aus.'))
            return None

        return value

    def clean(self, value):
        """
        `super().clean` führt die Standard‑Choice‑Validierung aus.
        Danach wandeln wir den String in ein timedelta um.
        """
        # Standard‑Choice‑Validierung (inkl. `required`, `widget`, …)
        value = super().clean(value)   # value ist jetzt ein String

        if value in (None, ''):
            return None

        # Umwandlung in timedelta
        return parse_duration(value)
