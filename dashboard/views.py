from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from core.settings import PLATFORM_NAME, USER_RATELIMIT_PER_HOUR, STRING_TO_ADMIN_PATH
from django_smart_ratelimit import rate_limit

@login_required
@rate_limit(key='user', rate=f'{USER_RATELIMIT_PER_HOUR}/h')
def dashboard(request):
    return render(request,'dashboard/index.html', {
        "PLATFORM_NAME": PLATFORM_NAME,
        "STRING_TO_ADMIN_PATH": STRING_TO_ADMIN_PATH,
    })
