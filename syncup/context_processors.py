"""Context for the admin shell (sidebar)."""


def admin_shell(request):
    user = getattr(request, "user", None)
    if not (user and user.is_authenticated and user.is_superuser):
        return {}

    def unread_chats():
        # Imported lazily; the query only runs if the sidebar actually renders the badge.
        from mobileapi.models import AppChatMessage
        return AppChatMessage.objects.filter(sender="user", read_by_admin=False).count()

    return {"nav_unread_chats": unread_chats}
