from django import forms
from .models import AppDefinition, RemoteHost


class DeployForm(forms.Form):
  app = forms.ModelChoiceField(
      queryset=AppDefinition.objects.all(),
      widget=forms.Select(attrs={'class': 'form-control'})
  )
  duration = forms.ChoiceField(
      choices=[(str(i), f"{i}h") for i in range(1, 25)],
      initial='1',
      widget=forms.Select(attrs={'class': 'form-control'})
  )

class DeployFormAdmin(forms.Form):
  app = forms.ModelChoiceField(
      queryset=AppDefinition.objects.all(),
      widget=forms.Select(attrs={'class': 'form-control'})
  )
  duration = forms.ChoiceField(
      choices=[(str(i), f"{i}h") for i in range(1, 25)],
      initial='1',
      widget=forms.Select(attrs={'class': 'form-control'})
  )
  target_host = forms.ModelChoiceField(
      queryset=RemoteHost.objects.all(),
      widget=forms.Select(attrs={'class': 'form-control'})
  )
