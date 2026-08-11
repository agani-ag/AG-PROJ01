"""Web chat routes (opened in the app's WebView) — mounted at /chat/ (see main/urls.py)."""
from django.urls import path

from . import chat_views as c

urlpatterns = [
    path("", c.chat_home, name="chat_home"),
    path("open", c.chat_open, name="chat_open"),
    path("messages", c.chat_messages, name="chat_messages"),
    path("send", c.chat_send, name="chat_send"),
    path("typing", c.chat_typing, name="chat_typing"),

    # Support-agent inbox (admin_chat_mode users)
    path("inbox/", c.chat_inbox_home, name="chat_inbox_home"),
    path("inbox/list.json", c.chat_inbox_list, name="chat_inbox_list"),
    path("inbox/<int:account_id>/messages.json", c.chat_inbox_thread, name="chat_inbox_thread"),
    path("inbox/<int:account_id>/reply", c.chat_inbox_reply, name="chat_inbox_reply"),
    path("inbox/<int:account_id>/typing", c.chat_inbox_typing, name="chat_inbox_typing"),
]
