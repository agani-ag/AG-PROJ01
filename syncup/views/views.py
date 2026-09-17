import requests
from django.contrib import messages
from django.shortcuts import render
from ..utils import send_telegram_message
from django.http.response import JsonResponse

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

def send_telegram_message_api(request):
    '''
    API endpoint to send a message to a Telegram chat.
    Expects 'chat_id' and 'message' as GET parameters.
    '''
    chat_id = request.GET.get('chat_id')
    message = request.GET.get('message')
    if not chat_id or not message:
        return JsonResponse({'error': 'chat_id and message parameters are required'}, status=400)
    try:        
        chat_id = int(chat_id)
        response = send_telegram_message(chat_id, message)
        if response.get('ok'):
            return JsonResponse({'success': 'Message sent successfully'})
        else:
            return JsonResponse({'error': 'Failed to send message', 'details': response}, status=500)
    except ValueError:
        return JsonResponse({'error': 'Invalid chat_id format'}, status=400)