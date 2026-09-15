from django.contrib import admin
from .models import LiveChannel, LiveTrack, BroadcastState


class LiveTrackInline(admin.TabularInline):
    model = LiveTrack
    extra = 0


@admin.register(LiveChannel)
class LiveChannelAdmin(admin.ModelAdmin):
    list_display = ('name', 'slug', 'kind', 'is_live', 'version', 'anchor_time')
    inlines = [LiveTrackInline]


@admin.register(LiveTrack)
class LiveTrackAdmin(admin.ModelAdmin):
    list_display = ('channel', 'order', 'title', 'source_type', 'duration_seconds', 'is_active')
    list_filter = ('channel', 'source_type', 'is_active')
    ordering = ('channel', 'order')


@admin.register(BroadcastState)
class BroadcastStateAdmin(admin.ModelAdmin):
    list_display = ('room', 'is_live', 'updated_at')