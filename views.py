"""DRF views for stapel-video.

Thin views over :mod:`services`. Scope resolution/membership goes through the
``SCOPE_PROVIDER`` seam; the video backend through the ``VIDEO_PROVIDER`` seam.
The participants listing uses stapel-core's ``AnchorPagination`` (limit/offset
is forbidden shelf-wide). The webhook ingress is unauthenticated — the provider
signs it, and ``services.handle_webhook`` verifies the signature.
"""
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import permissions, status
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView
from stapel_core.django.api.errors import StapelErrorResponse, StapelResponse
from stapel_core.django.api.pagination import AnchorPagination
from stapel_core.django.api.permissions import (
    ANONYMOUS_DENIED,
    HasWorkspaceMandateIfScoped,
)

from . import services
from .dto import (
    AdmitResponse,
    JoinResponse,
    ParticipantListResponse,
    ParticipantResponse,
    RoomResponse,
    ScopeUsageMonth,
    ScopeUsageResponse,
    ScopeUsageRow,
)
from .errors import (
    ERR_400_INVALID_ACCESS_LEVEL,
    ERR_400_INVALID_USAGE_PERIOD,
    ERR_400_INVALID_WEBHOOK,
    ERR_403_JOIN_DENIED,
    ERR_403_NOT_ROOM_HOST,
    ERR_403_NOT_ROOM_PARTICIPANT,
    ERR_404_PARTICIPANT_NOT_FOUND,
    ERR_404_ROOM_NOT_FOUND,
    ERR_404_SCOPE_NOT_FOUND,
)
from .models import AccessLevel, ParticipantRole
from .providers import VideoProviderError
from .scope import get_scope_provider
from .serializers import (
    AdmitResponseSerializer,
    JoinRequestSerializer,
    JoinResponseSerializer,
    LobbyActionRequestSerializer,
    ParticipantListResponseSerializer,
    RoomCreateRequestSerializer,
    RoomResponseSerializer,
    ScopeUsageResponseSerializer,
)


class ParticipantAnchorPagination(AnchorPagination):
    """FIFO (lobby-order) anchor pagination over ``joined_at``."""

    anchor_field = "joined_at"
    ordering = "joined_at"
    page_size = 100
    max_page_size = 1000


class UsageThrottle(ScopedRateThrottle):
    """Rate for the usage read, taken from ``STAPEL_VIDEO``.

    A library must not reach into the project's ``DEFAULT_THROTTLE_RATES`` to
    declare its own limit — that dict belongs to the host, and a module that
    writes into it silently changes rates the host set for its own endpoints.
    The scope name is this module's (``video-scope-usage``) and the number
    comes from this module's namespace. ``None`` disables it, which DRF reads
    as "no rate configured" and lets through.
    """

    def get_rate(self):
        from .conf import video_settings

        return video_settings.USAGE_THROTTLE


class SerializerSeamMixin:
    """Overridable serializer seam for every stapel-video APIView.

    Host projects can swap the request/response serializer of any view by
    subclassing and setting ``request_serializer_class`` /
    ``response_serializer_class`` — no need to rewrite the HTTP method bodies.
    """

    request_serializer_class = None
    response_serializer_class = None

    def get_request_serializer_class(self):
        return self.request_serializer_class

    def get_response_serializer_class(self):
        return self.response_serializer_class


# ── Mappers ──────────────────────────────────────────────────────────────


def _is_participant(room, user) -> bool:
    """Does this caller already belong to the room (creator or a row)?

    A join code circulates: it is an invitation to ASK, never proof of
    belonging. Anything that names other people or names the tenant is gated
    on this, not on holding the code.
    """
    if user is None or not getattr(user, "is_authenticated", False):
        return False
    if room.created_by_id == user.pk:
        return True
    return room.participants.filter(user_id=user.pk).exists()


def room_to_dto(room, *, reveal_scope: bool = True) -> RoomResponse:
    return RoomResponse(
        id=str(room.id),
        join_code=room.join_code,
        # dto.py documents scope_key as the workspace/org id. A stranger with
        # a join code may see that the room exists; which organization is
        # meeting in it is not part of that.
        scope_key=room.scope_key if reveal_scope else "",
        access_level=room.access_level,
        admit_required=room.admit_required,
        created_by_id=str(room.created_by_id),
        provider_room_ref=room.provider_room_ref,
    )


def usage_to_dto(scope_key, tz: str, buckets: list) -> ScopeUsageResponse:
    """The rollup's plain dicts as the response DTO.

    A mapper next to ``room_to_dto`` rather than a body inside the view: the
    read has two entry shapes (a range of months and a single one) that must
    answer identically, and a client that had to branch on which query
    parameter it sent would be carrying the server's plumbing.
    """
    return ScopeUsageResponse(
        scope_key=str(scope_key),
        tz=tz,
        months=[
            ScopeUsageMonth(
                month=bucket["month"],
                period_start=bucket["period_start"],
                period_end=bucket["period_end"],
                users=[ScopeUsageRow(**row) for row in bucket["users"]],
            )
            for bucket in buckets
        ],
    )


def participant_to_dto(participant) -> ParticipantResponse:
    return ParticipantResponse(
        id=str(participant.id),
        user_id=str(participant.user_id),
        status=participant.status,
        role=participant.role,
        joined_at=participant.joined_at,
    )


# ── Views ────────────────────────────────────────────────────────────────


@extend_schema(tags=["Video"])
class RoomListCreateView(SerializerSeamMixin, APIView):
    """Create a room (the creator is auto-admitted as host, with a token)."""

    permission_classes = [permissions.IsAuthenticated]
    request_serializer_class = RoomCreateRequestSerializer
    response_serializer_class = JoinResponseSerializer

    @extend_schema(
        request=RoomCreateRequestSerializer,
        responses={201: JoinResponseSerializer},
    )
    def post(self, request):  # noqa: R007
        ser = self.get_request_serializer_class()(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data
        access_level = data.access_level
        if access_level is not None and access_level not in AccessLevel.values:
            return StapelErrorResponse(400, ERR_400_INVALID_ACCESS_LEVEL)
        scope_key = get_scope_provider().resolve(request)
        room = services.create_room(
            request.user,
            scope_key=scope_key,
            access_level=access_level,
            admit_required=data.admit_required,
        )
        host = room.participants.get(role=ParticipantRole.HOST)
        token = services._mint_token(room, request.user, data.client_session_id)
        response_cls = self.get_response_serializer_class()
        return StapelResponse(
            response_cls(
                JoinResponse(
                    status="admitted",
                    room=room_to_dto(room),
                    participant=participant_to_dto(host),
                    token=token,
                )
            ),
            status=status.HTTP_201_CREATED,
        )


@extend_schema(tags=["Video"])
class RoomDetailView(SerializerSeamMixin, APIView):
    """Room info by join code."""

    permission_classes = [permissions.IsAuthenticated]
    response_serializer_class = RoomResponseSerializer

    @extend_schema(responses={200: RoomResponseSerializer})
    def get(self, request, join_code):  # noqa: R007
        room = services.get_room(join_code)
        if room is None:
            return StapelErrorResponse(404, ERR_404_ROOM_NOT_FOUND)
        response_cls = self.get_response_serializer_class()
        return StapelResponse(
            response_cls(
                room_to_dto(
                    room, reveal_scope=_is_participant(room, request.user)
                )
            )
        )


@extend_schema(tags=["Video"])
class RoomJoinView(SerializerSeamMixin, APIView):
    """Join a room by join code. Resolves the access level to admitted /
    waiting / denied."""

    permission_classes = [permissions.IsAuthenticated]
    request_serializer_class = JoinRequestSerializer
    response_serializer_class = JoinResponseSerializer

    @extend_schema(
        request=JoinRequestSerializer, responses={200: JoinResponseSerializer}
    )
    def post(self, request, join_code):  # noqa: R007
        room = services.get_room(join_code)
        if room is None:
            return StapelErrorResponse(404, ERR_404_ROOM_NOT_FOUND)
        ser = self.get_request_serializer_class()(data=request.data or {})
        ser.is_valid(raise_exception=True)
        result = services.join_room(
            request.user, room, request, ser.validated_data.client_session_id
        )
        if result["status"] == "denied":
            return StapelErrorResponse(403, ERR_403_JOIN_DENIED)
        response_cls = self.get_response_serializer_class()
        return StapelResponse(
            response_cls(
                JoinResponse(
                    status=result["status"],
                    room=room_to_dto(room),
                    participant=participant_to_dto(result["participant"]),
                    token=result.get("token"),
                )
            )
        )


@extend_schema(tags=["Video"])
class RoomParticipantsView(SerializerSeamMixin, APIView):
    """Anchor-paginated (FIFO) listing of a room's participants."""

    permission_classes = [permissions.IsAuthenticated]
    response_serializer_class = ParticipantListResponseSerializer

    @extend_schema(
        parameters=[
            OpenApiParameter("anchor", str, description="joined_at cursor (exclusive)"),
            OpenApiParameter("limit", int, description="Page size (default 100, max 1000)"),
            OpenApiParameter(
                "direction", str, description="next (default) | prev | center"
            ),
        ],
        responses={200: ParticipantListResponseSerializer},
    )
    def get(self, request, join_code):  # noqa: R007
        room = services.get_room(join_code)
        if room is None:
            return StapelErrorResponse(404, ERR_404_ROOM_NOT_FOUND)
        # Every row here is somebody's identity. Holding the join code is not
        # authority to enumerate who else is in the call.
        if not _is_participant(room, request.user):
            return StapelErrorResponse(403, ERR_403_NOT_ROOM_PARTICIPANT)
        qs = services.participants_queryset(room)
        paginator = ParticipantAnchorPagination()
        page = paginator.paginate_queryset(qs, request, view=self)
        items = [participant_to_dto(p) for p in page]
        next_anchor = None
        prev_anchor = None
        if paginator._items:
            if paginator._has_next:
                next_anchor = paginator._items[-1].joined_at.isoformat()
            if paginator._has_prev:
                prev_anchor = paginator._items[0].joined_at.isoformat()
        response_cls = self.get_response_serializer_class()
        return StapelResponse(
            response_cls(
                ParticipantListResponse(
                    items=items,
                    next_anchor=next_anchor,
                    prev_anchor=prev_anchor,
                    has_next=paginator._has_next,
                    has_prev=paginator._has_prev,
                    count=len(items),
                )
            )
        )


class _LobbyActionView(SerializerSeamMixin, APIView):
    """Shared host-only lobby guard + participant lookup."""

    permission_classes = [permissions.IsAuthenticated]
    request_serializer_class = LobbyActionRequestSerializer

    def _resolve(self, request, join_code):
        room = services.get_room(join_code)
        if room is None:
            return None, StapelErrorResponse(404, ERR_404_ROOM_NOT_FOUND)
        if room.created_by_id != request.user.id:
            return None, StapelErrorResponse(403, ERR_403_NOT_ROOM_HOST)
        ser = self.get_request_serializer_class()(data=request.data)
        ser.is_valid(raise_exception=True)
        return (room, ser.validated_data.participant_id), None


@extend_schema(tags=["Video"])
class LobbyAdmitView(_LobbyActionView):
    """Admit a waiting participant (host-only). Returns their join token."""

    response_serializer_class = AdmitResponseSerializer

    @extend_schema(
        request=LobbyActionRequestSerializer, responses={200: AdmitResponseSerializer}
    )
    def post(self, request, join_code):  # noqa: R007
        resolved, err = self._resolve(request, join_code)
        if err is not None:
            return err
        room, participant_id = resolved
        participant = services.admit_participant(room, participant_id)
        if participant is None:
            return StapelErrorResponse(404, ERR_404_PARTICIPANT_NOT_FOUND)
        response_cls = self.get_response_serializer_class()
        return StapelResponse(
            response_cls(
                AdmitResponse(
                    participant=participant_to_dto(participant),
                    token=participant.admit_token,
                )
            )
        )


@extend_schema(tags=["Video"])
class LobbyDenyView(_LobbyActionView):
    """Deny a waiting participant (host-only)."""

    response_serializer_class = LobbyActionRequestSerializer

    @extend_schema(
        request=LobbyActionRequestSerializer, responses={200: LobbyActionRequestSerializer}
    )
    def post(self, request, join_code):  # noqa: R007
        resolved, err = self._resolve(request, join_code)
        if err is not None:
            return err
        room, participant_id = resolved
        participant = services.deny_participant(room, participant_id)
        if participant is None:
            return StapelErrorResponse(404, ERR_404_PARTICIPANT_NOT_FOUND)
        return StapelResponse({"status": "denied", "participant_id": str(participant.id)})  # noqa: R006


@extend_schema(tags=["Video"])
class ScopeUsageView(SerializerSeamMixin, APIView):
    """``GET /video/api/v1/scopes/{scope_key}/usage/`` — one partition's
    per-month, per-person call time.

    The read behind a workspace-admin "who talked how much" screen, and the
    reason it is in the library rather than in one host: the missing dimension
    was a class-level gap in the meter (there was no way to ask about a
    partition at all), and a host-side join would have re-implemented the
    union arithmetic next to the table that already owns it.

    Two gates, in order — see :mod:`stapel_video.usage`:

    * ``HasWorkspaceMandateIfScoped`` (the stapel-calendar 0.5.0 gate on
      by-id reads): anonymous is refused in every deployment shape, the guest
      state is refused where it exists, and a lookup that could not be made
      is a 503 rather than a verdict about the caller.
    * ``usage.may_read_scope``: the caller must hold
      ``STAPEL_VIDEO["USAGE_MANDATE"]`` **in this scope**, resolved through
      the workspaces access registry. Holding a mandate somewhere is not
      authority over a workspace id somebody typed into a URL.

    A scope the caller may not read answers **404**, identically to a scope
    that does not exist and to one with no calls in it. 403 would confirm that
    a guessed tenant id is real.
    """

    permission_classes = [HasWorkspaceMandateIfScoped]
    #: Declared even though the gate above carries the same attribute: this
    #: view's refusal of anonymous callers is a property of the endpoint, and
    #: reading it should not require resolving which permission class is in
    #: the list this month.
    stapel_anonymous_access = ANONYMOUS_DENIED
    throttle_classes = [UsageThrottle]
    throttle_scope = "video-scope-usage"
    response_serializer_class = ScopeUsageResponseSerializer

    @extend_schema(
        parameters=[
            OpenApiParameter(
                "months",
                int,
                description=(
                    "How many calendar months back to report, newest first "
                    "(default 6, max 36). Ignored when `month` is given."
                ),
            ),
            OpenApiParameter(
                "month",
                str,
                description=(
                    "A single calendar month, YYYY-MM. Answers a one-element "
                    "`months` list, so the client renders one shape either way."
                ),
            ),
            OpenApiParameter(
                "tz",
                str,
                description=(
                    "IANA time zone the month buckets are cut in (default "
                    "UTC). Boundaries are LOCAL midnight, so a DST month is "
                    "an hour short or an hour long."
                ),
            ),
        ],
        responses={200: ScopeUsageResponseSerializer},
    )
    def get(self, request, scope_key):  # noqa: R007
        from . import presence, usage

        if not usage.may_read_scope(request, scope_key):
            return StapelErrorResponse(404, ERR_404_SCOPE_NOT_FOUND)

        tz = request.query_params.get("tz") or "UTC"
        month = (request.query_params.get("month") or "").strip()
        try:
            if month:
                start, end = presence.month_bounds(month, tz)
                buckets = [
                    {
                        "month": month,
                        "period_start": start.isoformat(),
                        "period_end": end.isoformat(),
                        "users": presence.usage_rollup(
                            scope_key=scope_key, period_start=start, period_end=end
                        ),
                    }
                ]
            else:
                buckets = presence.usage_rollup_by_month(
                    scope_key=scope_key,
                    months=_months(request.query_params.get("months")),
                    tz=tz,
                )
        except (presence.InvalidPeriod, presence.InvalidTimezone, ValueError):
            return StapelErrorResponse(400, ERR_400_INVALID_USAGE_PERIOD)

        response_cls = self.get_response_serializer_class()
        return StapelResponse(response_cls(usage_to_dto(scope_key, tz, buckets)))


def _months(raw) -> int:
    """The ``months`` query parameter, or the default.

    A missing value is the default; a malformed one is a 400 by way of the
    ``ValueError`` the caller catches. Silently defaulting on ``months=abc``
    would answer a question nobody asked and look like it worked.
    """
    from .presence import ROLLUP_DEFAULT_MONTHS

    if raw in (None, ""):
        return ROLLUP_DEFAULT_MONTHS
    value = int(raw)
    if value < 1:
        raise ValueError(f"months must be positive, got {value}")
    return value


@extend_schema(tags=["Video"])
class WebhookIngressView(APIView):
    """Provider webhook ingress (unauthenticated — the provider signs it).

    ``services.handle_webhook`` verifies the signature and, on an egress-ended
    event, emits ``video.egress_ended`` for stapel-recordings to finalize the
    upload."""

    permission_classes = [permissions.AllowAny]
    authentication_classes: list = []

    @extend_schema(request=None, responses={200: None})
    def post(self, request):  # noqa: R007
        auth_header = request.META.get("HTTP_AUTHORIZATION", "")
        try:
            parsed = services.handle_webhook(request.body, auth_header)
        except VideoProviderError:
            return StapelErrorResponse(400, ERR_400_INVALID_WEBHOOK)
        return StapelResponse({"event": parsed.get("event"), "ok": True})  # noqa: R006
