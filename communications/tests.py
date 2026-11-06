from datetime import timedelta
from unittest.mock import patch

from asgiref.sync import async_to_sync
from channels.db import database_sync_to_async
from channels.routing import URLRouter
from channels.testing import WebsocketCommunicator
from django.test import TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from organization.models import Organization

from .models import (
    Channel,
    ChannelMembership,
    Announcement,
    Meeting,
    MeetingEvent,
    MeetingParticipant,
    Message,
    Notification,
)
from .routing import websocket_urlpatterns
from .notifications import notification_service


class CommunicationAccessTests(TestCase):
    def setUp(self):
        self.organization = Organization.objects.create(name="Nexa Labs", slug="nexa-labs")
        self.other_organization = Organization.objects.create(name="Other Org", slug="other-org")
        self.employee = User.objects.create_user(
            employee_id="NW-1", password="SecurePassword!123", organization=self.organization
        )
        self.colleague = User.objects.create_user(
            employee_id="NW-2", password="SecurePassword!123", organization=self.organization
        )
        self.other_employee = User.objects.create_user(
            employee_id="NW-3", password="SecurePassword!123", organization=self.other_organization
        )
        self.public_channel = Channel.objects.create(
            organization=self.organization, name="announcements", kind=Channel.Kind.PUBLIC
        )
        self.private_channel = Channel.objects.create(
            organization=self.organization, name="leadership", kind=Channel.Kind.PRIVATE
        )

    def test_public_channel_messages_post_and_poll(self):
        self.client.force_login(self.employee)
        response = self.client.post(
            reverse("channel_detail", args=[self.public_channel.pk]),
            {"body": "Hello, team"},
        )
        self.assertRedirects(response, reverse("channel_detail", args=[self.public_channel.pk]))
        message = Message.objects.get(channel=self.public_channel)
        self.assertEqual(message.author, self.employee)

        response = self.client.get(
            reverse("channel_messages", args=[self.public_channel.pk]), {"after": 0}
        )
        self.assertEqual(response.json()["messages"][0]["body"], "Hello, team")

    def test_private_channel_requires_membership(self):
        self.client.force_login(self.employee)
        self.assertEqual(
            self.client.get(reverse("channel_detail", args=[self.private_channel.pk])).status_code,
            404,
        )
        ChannelMembership.objects.create(channel=self.private_channel, user=self.employee)
        self.assertEqual(
            self.client.get(reverse("channel_detail", args=[self.private_channel.pk])).status_code,
            200,
        )

    def test_user_cannot_access_another_organizations_channel(self):
        foreign_channel = Channel.objects.create(
            organization=self.other_organization, name="other", kind=Channel.Kind.PUBLIC
        )
        self.client.force_login(self.employee)
        self.assertEqual(
            self.client.get(reverse("channel_detail", args=[foreign_channel.pk])).status_code,
            404,
        )

    def test_host_can_remove_a_participant_and_rejoin_is_delayed(self):
        meeting = Meeting.objects.create(
            organization=self.organization,
            title="Planning",
            starts_at=timezone.now() + timedelta(hours=1),
            host=self.employee,
        )
        participant = MeetingParticipant.objects.create(meeting=meeting, user=self.colleague)
        self.client.force_login(self.employee)
        response = self.client.post(
            reverse("remove_meeting_participant", args=[meeting.pk, self.colleague.pk])
        )
        self.assertRedirects(response, reverse("meeting_detail", args=[meeting.pk]))
        participant.refresh_from_db()
        self.assertGreater(participant.removed_until, timezone.now())
        self.client.force_login(self.colleague)
        response = self.client.post(
            reverse("meeting_detail", args=[meeting.pk]), {"action": "join"}
        )
        self.assertRedirects(response, reverse("meeting_list"))
        participant.refresh_from_db()
        self.assertGreater(participant.removed_until, timezone.now())

    def test_meeting_detail_page_renders_for_an_employee(self):
        meeting = Meeting.objects.create(
            organization=self.organization,
            title="Planning",
            starts_at=timezone.now() + timedelta(hours=1),
            host=self.employee,
        )
        self.client.force_login(self.employee)
        response = self.client.get(reverse("meeting_detail", args=[meeting.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Join when ready")
        response = self.client.post(
            reverse("meeting_detail", args=[meeting.pk]), {"action": "join"}
        )
        self.assertRedirects(response, reverse("meeting_detail", args=[meeting.pk]))
        meeting.refresh_from_db()
        self.assertEqual(meeting.status, Meeting.Status.ACTIVE)
        self.assertTrue(meeting.participant_records.filter(user=self.employee).exists())

    def test_host_can_lock_and_unlock_meeting(self):
        meeting = Meeting.objects.create(
            organization=self.organization,
            title="Planning",
            starts_at=timezone.now() + timedelta(hours=1),
            host=self.employee,
        )
        self.client.force_login(self.employee)

        response = self.client.post(reverse("meeting_lock", args=[meeting.pk]))

        self.assertRedirects(response, reverse("meeting_detail", args=[meeting.pk]))
        meeting.refresh_from_db()
        self.assertTrue(meeting.is_locked)
        self.assertTrue(MeetingEvent.objects.filter(meeting=meeting, event_type="locked").exists())

        self.client.post(reverse("meeting_lock", args=[meeting.pk]))
        meeting.refresh_from_db()
        self.assertFalse(meeting.is_locked)

    def test_host_can_promote_a_joined_meeting_participant(self):
        meeting = Meeting.objects.create(
            organization=self.organization,
            title="Planning",
            starts_at=timezone.now() + timedelta(hours=1),
            host=self.employee,
        )
        participant = MeetingParticipant.objects.create(
            meeting=meeting,
            user=self.colleague,
            status=MeetingParticipant.Status.JOINED,
        )
        self.client.force_login(self.employee)

        response = self.client.post(
            reverse("meeting_promote", args=[meeting.pk, self.colleague.pk])
        )

        self.assertRedirects(response, reverse("meeting_detail", args=[meeting.pk]))
        participant.refresh_from_db()
        self.assertEqual(participant.role, MeetingParticipant.Role.COHOST)
        self.assertTrue(
            MeetingEvent.objects.filter(meeting=meeting, event_type="participant_role_changed").exists()
        )

    @patch("communications.views.media_provider.mute_participant", return_value=True)
    def test_host_can_mute_a_joined_participant(self, mute_participant):
        meeting = Meeting.objects.create(
            organization=self.organization,
            title="Planning",
            starts_at=timezone.now() + timedelta(hours=1),
            host=self.employee,
        )
        MeetingParticipant.objects.create(
            meeting=meeting,
            user=self.colleague,
            status=MeetingParticipant.Status.JOINED,
        )
        self.client.force_login(self.employee)

        response = self.client.post(
            reverse("meeting_mute_participant", args=[meeting.pk, self.colleague.pk]),
            {"track_sid": "TR_audio", "muted": "true"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok", "muted": True})
        mute_participant.assert_called_once()
        self.assertTrue(
            MeetingEvent.objects.filter(meeting=meeting, event_type="participant_muted").exists()
        )

    @patch("communications.views.media_provider.mute_participant", return_value=False)
    def test_mute_does_not_report_success_without_a_media_provider(self, mute_participant):
        meeting = Meeting.objects.create(
            organization=self.organization,
            title="Planning",
            starts_at=timezone.now() + timedelta(hours=1),
            host=self.employee,
        )
        MeetingParticipant.objects.create(
            meeting=meeting,
            user=self.colleague,
            status=MeetingParticipant.Status.JOINED,
        )
        self.client.force_login(self.employee)

        response = self.client.post(
            reverse("meeting_mute_participant", args=[meeting.pk, self.colleague.pk]),
            {"track_sid": "TR_audio", "muted": "true"},
        )

        self.assertEqual(response.status_code, 503)
        self.assertFalse(MeetingEvent.objects.filter(meeting=meeting, event_type="participant_muted").exists())
        mute_participant.assert_called_once()

    def test_assistant_page_accepts_a_workplace_question(self):
        self.client.force_login(self.employee)
        response = self.client.post(
            reverse("assistant_home"), {"question": "Where can I find files?"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Open Files")

    def test_employee_cannot_remove_a_meeting_participant(self):
        meeting = Meeting.objects.create(
            organization=self.organization,
            title="Planning",
            starts_at=timezone.now() + timedelta(hours=1),
            host=self.colleague,
        )
        MeetingParticipant.objects.create(meeting=meeting, user=self.other_employee)
        self.client.force_login(self.employee)
        response = self.client.post(
            reverse("remove_meeting_participant", args=[meeting.pk, self.other_employee.pk])
        )
        self.assertEqual(response.status_code, 403)

    def test_notifications_are_private_and_can_be_marked_read(self):
        notification = Notification.objects.create(user=self.employee, title="Update", body="Read this.")
        self.client.force_login(self.colleague)
        self.assertEqual(
            self.client.post(reverse("mark_notification_read", args=[notification.pk])).status_code,
            404,
        )
        self.client.force_login(self.employee)
        self.assertEqual(
            self.client.post(reverse("mark_notification_read", args=[notification.pk])).status_code,
            302,
        )
        notification.refresh_from_db()
        self.assertTrue(notification.is_read)


class ChannelWebSocketTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.organization = Organization.objects.create(name="Realtime Org", slug="realtime-org")
        self.user = User.objects.create_user(
            employee_id="RT-EMP", password="SecurePassword!123", organization=self.organization
        )
        self.other = User.objects.create_user(
            employee_id="RT-OTHER", password="SecurePassword!123", organization=self.organization
        )
        self.channel = Channel.objects.create(organization=self.organization, name="live")
        self.private_channel = Channel.objects.create(
            organization=self.organization, name="private", kind=Channel.Kind.PRIVATE
        )

    def test_authenticated_member_can_send_a_real_time_message(self):
        async_to_sync(self._send_message)()
        self.assertTrue(Message.objects.filter(channel=self.channel, author=self.user, body="Live from Django Channels").exists())

    async def _send_message(self):
        communicator = WebsocketCommunicator(
            URLRouter(websocket_urlpatterns), f"/ws/channels/{self.channel.pk}/"
        )
        communicator.scope["user"] = self.user
        connected, _ = await communicator.connect()
        self.assertTrue(connected)
        await communicator.send_json_to({"type": "message.send", "body": "Live from Django Channels"})
        event = await communicator.receive_json_from(timeout=3)
        self.assertEqual(event["type"], "message.created")
        self.assertEqual(event["body"], "Live from Django Channels")
        await communicator.disconnect()

    def test_nonmember_cannot_connect_to_private_channel_websocket(self):
        async_to_sync(self._reject_private_channel)()

    async def _reject_private_channel(self):
        communicator = WebsocketCommunicator(
            URLRouter(websocket_urlpatterns), f"/ws/channels/{self.private_channel.pk}/"
        )
        communicator.scope["user"] = self.user
        connected, close_code = await communicator.connect()
        self.assertFalse(connected)
        self.assertEqual(close_code, 4403)


class RealtimeNotificationWebSocketTests(TransactionTestCase):
    def setUp(self):
        self.organization = Organization.objects.create(name="Announcements Org", slug="announcements-org")
        self.user = User.objects.create_user(
            employee_id="ANN-EMP", password="test-password", organization=self.organization
        )
        self.channel = Channel.objects.create(organization=self.organization, name="live")
        self.announcement = Announcement.objects.create(
            organization=self.organization,
            title="Team update",
            body="The announcement is live.",
            created_by=self.user,
        )

    def test_published_announcement_is_delivered_to_signed_in_employee(self):
        async_to_sync(self._receive_announcement)()

    async def _receive_announcement(self):
        communicator = WebsocketCommunicator(
            URLRouter(websocket_urlpatterns), "/ws/notifications/"
        )
        communicator.scope["user"] = self.user
        connected, _ = await communicator.connect()
        self.assertTrue(connected)

        await database_sync_to_async(notification_service.deliver_announcement)(
            self.announcement, [self.user]
        )

        event = await communicator.receive_json_from(timeout=3)
        self.assertEqual(event["type"], "notification.created")
        self.assertEqual(event["title"], "Announcement: Team update")
        self.assertEqual(event["body"], "The announcement is live.")
        await communicator.disconnect()

    def test_cross_organization_user_cannot_connect_to_channel_websocket(self):
        other_org = Organization.objects.create(name="Foreign Org", slug="foreign-org")
        stranger = User.objects.create_user(
            employee_id="FOREIGN", password="SecurePassword!123", organization=other_org
        )
        async_to_sync(self._reject_user)(stranger)

    async def _reject_user(self, user):
        communicator = WebsocketCommunicator(
            URLRouter(websocket_urlpatterns), f"/ws/channels/{self.channel.pk}/"
        )
        communicator.scope["user"] = user
        connected, close_code = await communicator.connect()
        self.assertFalse(connected)
        self.assertEqual(close_code, 4403)
