from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.core.mail import send_mail
from django.db import transaction
from django.utils import timezone

from .models import Meeting, MeetingParticipant, Notification


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def send_notification_email(self, notification_id):
    notification = Notification.objects.select_related("user").filter(pk=notification_id).first()
    if not notification or not notification.user.email:
        return False
    preferences = notification.user.notification_preferences
    if preferences.get("email") is not True:
        return False
    return send_mail(
        subject=f"NexaWork: {notification.title}",
        message=f"{notification.body}\n\nSign in to NexaWork to see more.",
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[notification.user.email],
        fail_silently=False,
    )


@shared_task
def send_meeting_reminders():
    now = timezone.now()
    with transaction.atomic():
        participants = list(
            MeetingParticipant.objects.select_for_update()
            .select_related("meeting", "user")
            .filter(
                status=MeetingParticipant.Status.INVITED,
                reminder_sent_at__isnull=True,
                meeting__starts_at__gt=now,
                meeting__starts_at__lte=now + timedelta(minutes=10),
                meeting__status=Meeting.Status.SCHEDULED,
            )
        )
        notifications = []
        for participant in participants:
            participant.reminder_sent_at = now
            participant.save(update_fields=["reminder_sent_at"])
            notifications.append(
                Notification(
                    user=participant.user,
                    title=f"Meeting reminder: {participant.meeting.title}",
                    body=f"Your meeting starts at {timezone.localtime(participant.meeting.starts_at):%I:%M %p}.",
                    url=f"/meetings/{participant.meeting_id}/",
                )
            )
        created = Notification.objects.bulk_create(notifications, batch_size=500)
    for notification in created:
        send_notification_email.delay(notification.pk)
    return len(created)
