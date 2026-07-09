"""Django admin for stapel-video.

Both models are ``business`` (visible, staff-manageable) by the admin-suite
default (admin-suite AS-5); neither is ops-machinery nor a secret carrier, so
both stay undecorated. ``provider_room_ref`` is an opaque provider room name,
not a credential.
"""
from django.contrib import admin

from .models import Room, RoomParticipant


@admin.register(Room)
class RoomAdmin(admin.ModelAdmin):
    list_display = ("join_code", "access_level", "admit_required", "created_by", "created_at")
    list_filter = ("access_level", "admit_required")
    search_fields = ("join_code", "scope_key", "provider_room_ref")
    raw_id_fields = ("created_by",)


@admin.register(RoomParticipant)
class RoomParticipantAdmin(admin.ModelAdmin):
    list_display = ("room", "user", "status", "role", "joined_at")
    list_filter = ("status", "role")
    raw_id_fields = ("room", "user")
