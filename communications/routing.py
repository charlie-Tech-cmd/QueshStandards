from django.urls import path

from .consumers import ChannelConsumer, NotificationConsumer

websocket_urlpatterns = [
    path("ws/channels/<int:channel_id>/", ChannelConsumer.as_asgi()),
    path("ws/notifications/", NotificationConsumer.as_asgi()),
]
