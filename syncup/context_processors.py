"""Context for the admin shell (sidebar)."""


def admin_shell(request):
    user = getattr(request, "user", None)
    if not (user and user.is_authenticated and user.is_superuser):
        return {}

    def chat_counts():
        # Imported lazily; the queries only run if the sidebar renders the counts. app.js keeps them live.
        from mobileapi.admin_views import chat_counts
        return chat_counts()

    return {"nav_chat_counts": chat_counts}
