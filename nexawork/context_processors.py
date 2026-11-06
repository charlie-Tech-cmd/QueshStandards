from django.utils import timezone

from communications.models import Notification
from governance.services import has_permission


def workplace_context(request):
    if not request.user.is_authenticated:
        return {}
    return {
        "unread_notifications": Notification.objects.filter(user=request.user, is_read=False).count(),
        "now": timezone.now(),
        "can_manage_workspace": request.user.can_manage_workplace
        or has_permission(request.user, "USER_VIEW"),
    }
