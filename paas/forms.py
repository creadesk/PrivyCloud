from django import forms
from django.utils.translation import gettext_lazy as _
from .models import AppDefinition, RemoteHost
from .fields import DurationField

# Hilfsklasse für die Optionen
DURATION_CHOICES = [
    ('no_limit', _('Ohne Limit')),
    ('1h', _('1 h')), ('2h', _('2 h')), ('3h', _('3 h')),
    # … bis 24 h …
    ('1d', _('1 Tag')), ('2d', _('2 Tage')), ('3d', _('3 Tage')),
    ('1w', _('1 Woche')), ('2w', _('2 Wochen')),
    ('1m', _('1 Monat')), ('3m', _('3 Monate')),
]

class DeployForm(forms.Form):
  app = forms.ModelChoiceField(
      queryset=AppDefinition.objects.all(),
      widget=forms.Select(attrs={'class': 'form-control'})
  )
  '''
  > **Hinweis**  
  > Wir benutzen `DurationField` statt eines einfachen `ChoiceField`.  
  > Durch die eigene Validierung können wir die Zeichenkette (z.B. `2w`) in ein `datetime.timedelta` umwandeln – das ist für die Logik später nützlich.
  '''
  duration = DurationField(
      choices=DURATION_CHOICES,
      initial='1h',
      widget=forms.Select(attrs={'class': 'form-control'}),
      label=_('Laufzeit'),
  )

class DeployFormAdmin(DeployForm):
    target_host = forms.ModelChoiceField(
        queryset=RemoteHost.objects.all(),
        widget=forms.Select(attrs={'class': 'form-control'}),
    )
