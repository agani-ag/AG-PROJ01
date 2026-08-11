"""
URL configuration for main project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static

from mobileapi import views as mobile_views

urlpatterns = [
    path('admin/', admin.site.urls),
    path('app/v1/', include('mobileapi.urls')),          # mobile JSON API
    path('mobile/', include('mobileapi.admin_urls')),    # mobile HTML admin (superuser)
    path('chat/', include('mobileapi.chat_urls')),       # user↔admin web chat (opened in the app WebView)
    # Public privacy policy page — use this URL in Play Console → App content.
    path('privacy/', mobile_views.privacy_policy, name='privacy_policy'),
    path('', include('syncup.urls')),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
