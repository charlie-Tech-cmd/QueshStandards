from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.db import transaction
from django.utils import timezone

from .models import Notification


class InAppNotificationService:
    def create(self, user, title, body, url=""):
        notification = Notification.objects.create(
            user=user, title=title[:180], body=body[:500], url=url[:300]
        )
        transaction.on_commit(lambda: self.publish(notification))
        return notification

    def create_many(self, users, title, body, url=""):
        notification_rows = (
            Notification(user=user, title=title[:180], body=body[:500], url=url[:300])
            for user in users
        )
        created = Notification.objects.bulk_create(notification_rows, batch_size=500)
        for notification in created:
            transaction.on_commit(lambda item=notification: self.publish(item))
        return created

    @staticmethod
    def publish(notification):
        layer = get_channel_layer()
        if layer is None:
            return
        async_to_sync(layer.group_send)(
            f"nexawork.notifications.{notification.user_id}",
            {
                "type": "notification.created",
                "notification": {
                    "id": notification.pk,
                    "title": notification.title,
                    "body": notification.body,
                    "url": notification.url,
                    "created_at": timezone.localtime(notification.created_at).strftime("%H:%M"),
                },
            },
        )

    @transaction.atomic
    def deliver_announcement(self, announcement, recipients):
        return self.create_many(
            recipients,
            f"Announcement: {announcement.title}",
            announcement.body,
            "/announcements/",
        )


notification_service = InAppNotificationService()
