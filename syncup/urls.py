from django.urls import path
from .views import auth, views, media, live

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

    # Live Broadcast (Mode 3 — WebRTC). MUST precede the generic live/<slug> routes
    # below, or "broadcast" would be captured as a channel slug.
    path('live/broadcast', live.broadcast_viewer, name='broadcast_viewer'),
    path('live/broadcast/state', live.broadcast_state_api, name='broadcast_state'),
    path('live/broadcast/signal', live.broadcast_signal_api, name='broadcast_signal'),
    path('live/broadcast/poll', live.broadcast_poll_api, name='broadcast_poll'),
    path('live/broadcast/live', live.broadcast_live_api, name='broadcast_live'),
    path('live/admin/broadcast', live.broadcast_admin, name='broadcast_admin'),

    # Live Channels (Radio / TV) — public viewer + superuser admin
    path('live/<slug:slug>', live.live_viewer, name='live_viewer'),
    path('live/<slug:slug>/state.json', live.live_state, name='live_state'),
    path('live/admin/<slug:slug>', live.live_admin, name='live_admin'),
    path('live/admin/<slug:slug>/track/add', live.live_track_add, name='live_track_add'),
    path('live/admin/track/<int:track_id>/edit', live.live_track_edit, name='live_track_edit'),
    path('live/admin/track/<int:track_id>/delete', live.live_track_delete, name='live_track_delete'),
    path('live/admin/<slug:slug>/reorder', live.live_track_reorder, name='live_track_reorder'),
    path('live/admin/<slug:slug>/control', live.live_control, name='live_control'),
]
