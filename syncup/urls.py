from django.urls import path
from .views import auth, views, media

urlpatterns = [
    # Home URL
    path('', views.home, name='home'),

    # Auth URLs — AG-PROJ01 is admin-only: password, PIN, or a one-time magic link
    path('login', auth.login_view, name='login'),
    path('login/link/<str:token>', auth.magic_link_login, name='magic_link_login'),
    path('logout', auth.logout_view, name='logout'),
    path('api/passkey-auth', auth.passkey_auth, name='passkey_auth'),

    # Settings
    path('settings/profile', auth.admin_profile, name='admin_profile'),
    path('download/sqlite', auth.download_sqlite, name='download_sqlite'),

    # API URLs
    path('api/telegram/send', views.send_telegram_message_api, name='send_telegram_message_api'),

    # Services (Media menu) — Click Send / Cloudinary / Video Player, superuser-only
    path('services/clicksend', media.clicksend, name='clicksend'),
    path('services/video-player', media.video_player, name='video_player'),
    path('services/cloudinary', media.cloudinary, name='cloudinary'),
    path('services/cloud-sign', media.cloud_sign, name='cloud_sign'),
]
