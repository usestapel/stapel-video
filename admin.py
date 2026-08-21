"""Django admin for stapel-video.

``Room`` and ``RoomParticipant`` are ``business`` (visible, staff-manageable)
by the admin-suite default (admin-suite AS-5); neither is ops-machinery nor a
secret carrier, so both stay undecorated. ``provider_room_ref`` is an opaque
provider room name, not a credential.

``ParticipantSpan`` is the exception: a meter is read-only in the admin, for
the same reason the stapel-agent prompt ledger is. Rows are written by the
webhook ingest and the sweeper and summed into somebody's invoice, and a
staffer editing one by hand is a silent restatement of a closed period.
"""
from django.contrib import admin

from .models import ParticipantSpan, Room, RoomParticipant


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


@admin.register(ParticipantSpan)
class ParticipantSpanAdmin(admin.ModelAdmin):
    list_display = (
        "room_key",
        "user_id",
        "connection_id",
        "joined_at",
        "left_at",
        "close_reason",
    )
    list_filter = ("close_reason",)
    search_fields = ("room_key", "user_id", "connection_id")
    date_hierarchy = "joined_at"
    readonly_fields = tuple(f.name for f in ParticipantSpan._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        # Retention deletes spans; a person does not. `manage.py
        # video_purge_spans` is the one path, so the window is a policy
        # instead of whatever somebody cleared out last week.
        return False
