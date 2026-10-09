"""Admin → Home shortcuts: the categories and shortcuts on every app user's home page.

Categories are cards (rename, hide, delete, drag to order); shortcuts are rows inside them (on/off,
edit, delete, drag to order). "Bulk add" takes many "Category, Title, Address" lines at once.
The app reads the result from home_shortcuts.catalog. Superuser-only, like the rest of /mobile/.
"""
import json

from django.contrib import messages
from django.db import IntegrityError, transaction
from django.db.models import Count, Max, Prefetch, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from .admin_views import superuser_required
from .forms import HomeShortcutForm
from .home_shortcuts import apply_bulk, parse_bulk
from .models import HomeShortcut, ShortcutCategory


@superuser_required
def home_shortcuts(request):
    cats = (
        ShortcutCategory.objects
        .prefetch_related(Prefetch("shortcuts", queryset=HomeShortcut.objects.order_by("position", "title")))
        .annotate(total=Count("shortcuts"), live=Count("shortcuts", filter=Q(shortcuts__is_active=True)))
    )
    shown = sum(c.live for c in cats if c.is_active)
    # For the bulk-add preview ("2 new categories"); "<" escaped so a name can't end the page's script.
    names = json.dumps([c.name for c in cats]).replace("<", "\\u003c")
    return render(request, "mobileapi/home_shortcuts.html", {"categories": cats, "shown": shown, "category_names_json": names})


# --------------------------------------------------------------------------- categories
@superuser_required
@require_POST
def category_add(request):
    name = (request.POST.get("name") or "").strip()[:60]
    if not name:
        messages.error(request, "Give the category a name.")
    elif ShortcutCategory.objects.filter(name__iexact=name).exists():
        messages.error(request, f"There's already a category called “{name}”.")
    else:
        pos = (ShortcutCategory.objects.aggregate(m=Max("position"))["m"] or 0) + 1
        ShortcutCategory.objects.create(name=name, position=pos)
        messages.success(request, f"Category “{name}” added.")
    return redirect("mobile_home_shortcuts")


@superuser_required
@require_POST
def category_rename(request, category_id):
    cat = get_object_or_404(ShortcutCategory, id=category_id)
    name = (request.POST.get("name") or "").strip()[:60]
    if not name:
        messages.error(request, "Give the category a name.")
    elif ShortcutCategory.objects.filter(name__iexact=name).exclude(id=cat.id).exists():
        messages.error(request, f"There's already a category called “{name}”.")
    else:
        cat.name = name
        cat.save(update_fields=["name", "updated_at"])
        messages.success(request, "Category renamed.")
    return redirect("mobile_home_shortcuts")


@superuser_required
@require_POST
def category_toggle(request, category_id):
    cat = get_object_or_404(ShortcutCategory, id=category_id)
    cat.is_active = not cat.is_active
    cat.save(update_fields=["is_active", "updated_at"])
    messages.success(request, f"“{cat.name}” is {'shown' if cat.is_active else 'hidden'} on the home page.")
    return redirect("mobile_home_shortcuts")


@superuser_required
@require_POST
def category_delete(request, category_id):
    cat = get_object_or_404(ShortcutCategory, id=category_id)
    name = cat.name
    cat.delete()
    messages.success(request, f"Category “{name}” and its shortcuts deleted.")
    return redirect("mobile_home_shortcuts")


# --------------------------------------------------------------------------- shortcuts
@superuser_required
def shortcut_add(request):
    if not ShortcutCategory.objects.exists():
        messages.error(request, "Add a category first.")
        return redirect("mobile_home_shortcuts")
    if request.method == "POST":
        form = HomeShortcutForm(request.POST)
        if form.is_valid():
            s = form.save(commit=False)
            s.position = (s.category.shortcuts.aggregate(m=Max("position"))["m"] or 0) + 1
            try:
                s.save()
            except IntegrityError:
                messages.error(request, f"“{s.category}” already has that address.")
            else:
                messages.success(request, f"“{s.title}” added to {s.category}.")
                return redirect("mobile_home_shortcuts")
    else:
        form = HomeShortcutForm(initial={"category": request.GET.get("category")})
    return render(request, "mobileapi/home_shortcut_edit.html", {"form": form, "is_edit": False})


@superuser_required
def shortcut_edit(request, shortcut_id):
    shortcut = get_object_or_404(HomeShortcut, id=shortcut_id)
    if request.method == "POST":
        old_category = shortcut.category_id
        form = HomeShortcutForm(request.POST, instance=shortcut)
        if form.is_valid():
            s = form.save(commit=False)
            if s.category_id != old_category:  # moved: to the end of its new category
                s.position = (s.category.shortcuts.aggregate(m=Max("position"))["m"] or 0) + 1
            try:
                s.save()
            except IntegrityError:
                messages.error(request, f"“{s.category}” already has that address.")
            else:
                messages.success(request, "Shortcut saved.")
                return redirect("mobile_home_shortcuts")
    else:
        form = HomeShortcutForm(instance=shortcut)
    return render(request, "mobileapi/home_shortcut_edit.html", {"form": form, "is_edit": True, "shortcut": shortcut})


@superuser_required
@require_POST
def shortcut_toggle(request, shortcut_id):
    s = get_object_or_404(HomeShortcut, id=shortcut_id)
    s.is_active = not s.is_active
    s.save(update_fields=["is_active", "updated_at"])
    return redirect("mobile_home_shortcuts")


@superuser_required
@require_POST
def shortcut_delete(request, shortcut_id):
    s = get_object_or_404(HomeShortcut, id=shortcut_id)
    title = s.title
    s.delete()
    messages.success(request, f"“{title}” deleted.")
    return redirect("mobile_home_shortcuts")


@superuser_required
@require_POST
def reorder(request):
    """Drag-and-drop order from the page: {"kind": "categories", "ids": [...]} or
    {"kind": "shortcuts", "category": id, "ids": [...]} (a shortcut dropped into another category moves there)."""
    try:
        data = json.loads(request.body or b"{}")
        ids = [int(i) for i in data.get("ids", [])]
    except (ValueError, TypeError):
        return JsonResponse({"ok": False, "error": "Bad order"}, status=400)
    with transaction.atomic():
        if data.get("kind") == "categories":
            for pos, cid in enumerate(ids, start=1):
                ShortcutCategory.objects.filter(id=cid).update(position=pos)
        elif data.get("kind") == "shortcuts":
            cat = get_object_or_404(ShortcutCategory, id=int(data.get("category", 0)))
            taken = set(cat.shortcuts.exclude(id__in=ids).values_list("url", flat=True))
            for pos, sid in enumerate(ids, start=1):
                s = HomeShortcut.objects.filter(id=sid).first()
                if s is None:
                    continue
                if s.category_id != cat.id and s.url in taken:
                    return JsonResponse({"ok": False, "error": f"{cat.name} already has {s.url}"}, status=409)
                s.category = cat
                s.position = pos
                s.save(update_fields=["category", "position", "updated_at"])
        else:
            return JsonResponse({"ok": False, "error": "Bad order"}, status=400)
    return JsonResponse({"ok": True})


@superuser_required
@require_POST
def bulk_add(request):
    rows, skipped = parse_bulk(request.POST.get("lines", ""))
    if not rows:
        messages.error(request, "Nothing to add. Use one line per shortcut: Category, Title, Address.")
        return redirect("mobile_home_shortcuts")
    added, updated, new_categories = apply_bulk(rows)
    parts = [f"{added} shortcut{'s' if added != 1 else ''} added"]
    if updated:
        parts.append(f"{updated} renamed")
    if new_categories:
        parts.append("new categories: " + ", ".join(new_categories))
    if skipped:
        parts.append(f"{len(skipped)} line{'s' if len(skipped) != 1 else ''} skipped (no address)")
    messages.success(request, " · ".join(parts) + ".")
    return redirect("mobile_home_shortcuts")
