from captcha.fields import CaptchaField
from django import forms

class CaptchaForm(forms.Form):
    captcha = CaptchaField()
    class Meta:
        fields = "captcha"
    pass