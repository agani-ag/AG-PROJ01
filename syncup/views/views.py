from django.shortcuts import render

PLAY_STORE_URL = 'https://play.google.com/store/apps/details?id={}'


def home(request):
    """Public landing page for the SyncUp Android app. Everything app-specific (package name for
    the Play Store link + QR, company, support contacts) comes from App config (/mobile/config)."""
    from mobileapi.models import AppConfig
    from mobileapi.views import PRIVACY_FALLBACK_COMPANY

    cfg = AppConfig.load()
    package = (cfg.android_package_name or '').strip() or AppConfig._meta.get_field('android_package_name').default
    return render(request, 'home.html', {
        'package_name': package,
        'play_url': PLAY_STORE_URL.format(package),
        'company_name': cfg.privacy_company_name or PRIVACY_FALLBACK_COMPANY,
        'support_email': cfg.support_email or '',
        'support_phone': cfg.support_phone or '',
    })
