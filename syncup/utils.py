from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from django.conf import settings
import requests
import logging
import base64
import json

# =============== Telegram Bot ===============
GROUPS = settings.TELEGRAM_GROUPS
BOT = settings.TELEGRAM_BOT_TOKEN
logger = logging.getLogger(__name__)

def send_telegram_message(chatID: int, message):
    url = f'https://api.telegram.org/bot{BOT}/sendMessage'
    payload = {
        'chat_id': GROUPS[chatID],
        'text': message,
        'parse_mode': 'MarkdownV2'
    }
    headers = {'Content-Type': 'application/json'}

    # 1. Configure a Retry Strategy
    # This will retry on 502, 503, or 504 errors specifically
    retry_strategy = Retry(
        total=3,                # Total number of retries
        backoff_factor=1,       # Wait 1s, 2s, 4s between retries
        status_forcelist=[502, 503, 504], 
        allowed_methods=["POST"] # Ensure it retries on POST requests
    )
    
    adapter = HTTPAdapter(max_retries=retry_strategy)
    
    try:
        # 2. Use a Session to apply the retry logic
        with requests.Session() as session:
            session.mount("https://", adapter)
            
            # Lowered timeout to 3.5s so retries don't take forever
            response = session.post(
                url, 
                json=payload, 
                headers=headers, 
                timeout=3.5 
            )
            
            data = response.json()

            # Fallback: if MarkdownV2 parsing fails (400), retry as plain text
            if response.status_code == 400:
                description = data.get('description', '')
                logger.warning(f"MarkdownV2 rejected (400): {description}. Retrying as plain text.")
                payload.pop('parse_mode')
                response = session.post(
                    url,
                    json=payload,
                    headers=headers,
                    timeout=3.5
                )
                data = response.json()

            response.raise_for_status()
            
            if not data.get('ok'):
                logger.warning(f"Telegram API returned error: {data.get('description')}")
                return None
                
            return data

    except requests.exceptions.RetryError:
        logger.error("Telegram Proxy failed after multiple retry attempts (503).")
    except requests.exceptions.Timeout:
        logger.error("Telegram notification timed out.")
    except requests.exceptions.ProxyError:
        logger.error("Initial Proxy connection failed (503).")
    except requests.exceptions.RequestException as e:
        logger.error(f"Telegram communication error: {e}")
    
    return None

# =============== JSON Encoding/Decoding ===============
def encode_via_json(data):
    json_str = json.dumps(data)
    json_bytes = json_str.encode("utf-8")
    encoded = base64.urlsafe_b64encode(json_bytes).decode("utf-8")
    return encoded

def decode_via_json(encoded):
    json_bytes = base64.urlsafe_b64decode(encoded)
    json_str = json_bytes.decode("utf-8")
    data = json.loads(json_str)
    return data
