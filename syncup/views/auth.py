"""Sign-in for AG-PROJ01.

AG-PROJ01 is admin-only: its one account is the admin, a superuser created by
`manage.py createadminuser`. There are three ways in — password, the 5-character PIN, or a
one-time magic link — and each refuses anyone who isn't an active superuser. The admin manages
all three from Settings → Profile.
"""
import json
import os
from datetime import datetime
from functools import wraps

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login, logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods, require_POST

from ..forms import AdminProfileForm, AuthForm
from ..models import AdminLoginLink, AdminPin

# Sign-ins that don't go through authenticate() (PIN, magic link) have to name the backend.
MODEL_BACKEND = 'django.contrib.auth.backends.ModelBackend'
LINK_TTL_MINUTES = int(AdminLoginLink.TTL.total_seconds() // 60)


def superuser_required(view):
    """Signed-in superusers only. Anyone else is signed out and sent to the login page."""
    @wraps(view)
    @login_required
    def wrapped(request, *args, **kwargs):
        if not request.user.is_superuser:
            logout(request)
            return redirect('login')
        return view(request, *args, **kwargs)
    return wrapped


def _after_login(request):
    """Where to go after signing in: the ?next= page if it's on this site, else home."""
    nxt = request.POST.get('next') or request.GET.get('next')
    if nxt and url_has_allowed_host_and_scheme(nxt, allowed_hosts={request.get_host()},
                                               require_https=request.is_secure()):
        return redirect(nxt)
    return redirect('home')


# =============== Password login ===============
@never_cache
def login_view(request):
    if request.user.is_authenticated:
        return redirect('home')
    auth_form = AuthForm(request, data=request.POST or None)
    if request.method == 'POST':
        if auth_form.is_valid():
            login(request, auth_form.get_user())
            return _after_login(request)
        messages.error(request, ' '.join(auth_form.non_field_errors())
                       or 'Please enter a correct username and password.')
    return render(request, 'auth/login.html', {'auth_form': auth_form})


@login_required
def logout_view(request):
    logout(request)
    return redirect('login')


# =============== PIN login ===============
@require_POST
def passkey_auth(request):
    """PIN sign-in, called by the login page's "Forgot Password?" dialog, which expects
    {"message": ...} on success and {"error": ...} otherwise."""
    try:
        data = json.loads(request.body or b'{}')
    except (ValueError, TypeError):
        return JsonResponse({'error': 'Invalid JSON payload'}, status=400)
    pin = str((data.get('passkey') if isinstance(data, dict) else '') or '').strip().upper()
    if not AdminPin.PATTERN.fullmatch(pin):
        return JsonResponse({'error': 'Enter your 5-character PIN.'}, status=400)
    # PINs are stored hashed, so there's nothing to look up — check each admin's PIN in turn.
    for admin_pin in AdminPin.objects.select_related('user').filter(
            user__is_active=True, user__is_superuser=True):
        if admin_pin.check_pin(pin):
            login(request, admin_pin.user, backend=MODEL_BACKEND)
            return JsonResponse({'message': 'Passkey authentication successful'})
    return JsonResponse({'error': 'Invalid PIN.'}, status=401)


# =============== Magic link ===============
@never_cache
@require_http_methods(['GET', 'POST'])
def magic_link_login(request, token):
    """One-time sign-in link from Settings → Profile.

    GET only shows a page that auto-submits a POST; the POST redeems the token. Link-preview bots
    (WhatsApp, Slack, mail scanners) fetch with GET and don't run JavaScript, so they can't use
    the link up before the admin opens it.
    """
    if request.method == 'POST':
        user = AdminLoginLink.redeem(token)
        if user is None:
            return render(request, 'auth/magic_link.html', {'invalid': True}, status=410)
        login(request, user, backend=MODEL_BACKEND)
        return redirect('home')
    invalid = not AdminLoginLink.is_valid(token)
    return render(request, 'auth/magic_link.html', {'invalid': invalid}, status=410 if invalid else 200)


# =============== Settings → Profile ===============
@never_cache
@superuser_required
@require_http_methods(['GET', 'POST'])
def admin_profile(request):
    """One form for the admin's name, new password and new PIN (leave either blank to keep it),
    plus the one-time magic sign-in link."""
    user = request.user
    action = request.POST.get('action') if request.method == 'POST' else None
    form = AdminProfileForm(user, request.POST if action == 'profile' else None,
                            initial={'name': user.first_name or user.username})
    magic_link = None

    if action == 'profile' and form.is_valid():
        new_password = form.cleaned_data['new_password']
        new_pin = form.cleaned_data['new_pin']
        user.first_name = form.cleaned_data['name']
        if new_password:
            user.set_password(new_password)
        user.save(update_fields=['first_name', 'password'] if new_password else ['first_name'])
        if new_password:
            update_session_auth_hash(request, user)            # stay signed in on this device
            AdminLoginLink.objects.filter(user=user).delete()  # an unused link dies with the old password
        if new_pin:
            admin_pin = AdminPin.objects.filter(user=user).first() or AdminPin(user=user)
            admin_pin.set_pin(new_pin)
            admin_pin.save()
        updated = [label for label, value in (('password', new_password), ('PIN', new_pin)) if value]
        messages.success(request, 'Profile saved.' + (' New %s set.' % ' and '.join(updated) if updated else ''))
        return redirect('admin_profile')
    if action == 'link':
        token = AdminLoginLink.issue(user)
        # Rendered rather than redirected: the raw token exists only in this one response.
        magic_link = request.build_absolute_uri(reverse('magic_link_login', args=[token]))

    return render(request, 'auth/profile.html', {
        'form': form,
        'has_pin': AdminPin.objects.filter(user=user).exists(),
        'magic_link': magic_link,
        'link_ttl_minutes': LINK_TTL_MINUTES,
    })


# =============== Settings → SQLite Backup ===============
@superuser_required
def download_sqlite(request):
    sqlite_path = os.path.join(settings.BASE_DIR, 'ag-proj01.sqlite3')
    if os.path.exists(sqlite_path):
        with open(sqlite_path, 'rb') as f:
            response = HttpResponse(f.read(), content_type='application/octet-stream')
            filename = datetime.now().strftime("syncup_%Y%m%d_%H%M%S.sqlite3")
            response['Content-Disposition'] = f'attachment; filename="{filename}"'
            return response
    else:
        return HttpResponse("Database file not found", status=404)
