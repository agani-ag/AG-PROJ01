import requests
from django.contrib import messages
from django.shortcuts import render
from ..utils import send_telegram_message
from django.http.response import JsonResponse

def home(request):
    return render(request, 'home.html')

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