"""DRF views for stapel-video.

Thin views over :mod:`services`. Scope resolution/membership goes through the
``SCOPE_PROVIDER`` seam; the video backend through the ``VIDEO_PROVIDER`` seam.
The participants listing uses stapel-core's ``AnchorPagination`` (limit/offset
is forbidden shelf-wide). The webhook ingress is unauthenticated — the provider
signs it, and ``services.handle_webhook`` verifies the signature.
"""
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import permissions, status
from rest_framework.views import APIView
from stapel_core.django.api.errors import StapelErrorResponse, StapelResponse
from stapel_core.django.api.pagination import AnchorPagination

from . import services
from .dto import (
    AdmitResponse,
    JoinResponse,
    ParticipantListResponse,
    ParticipantResponse,
    RoomResponse,
)
from .errors import (
    ERR_400_INVALID_ACCESS_LEVEL,
    ERR_400_INVALID_WEBHOOK,
    ERR_403_JOIN_DENIED,
    ERR_403_NOT_ROOM_HOST,
    ERR_404_PARTICIPANT_NOT_FOUND,
    ERR_404_ROOM_NOT_FOUND,
)
from .models import AccessLevel, ParticipantRole
from .providers import VideoProviderError
from .scope import get_scope_provider
from .serializers import (
    AdmitResponseSerializer,
    JoinResponseSerializer,
    LobbyActionRequestSerializer,
    ParticipantListResponseSerializer,
    RoomCreateRequestSerializer,
    RoomResponseSerializer,
)


class ParticipantAnchorPagination(AnchorPagination):
    """FIFO (lobby-order) anchor pagination over ``joined_at``."""

    anchor_field = "joined_at"
    ordering = "joined_at"
    page_size = 100
    max_page_size = 1000


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


def room_to_dto(room) -> RoomResponse:
    return RoomResponse(
        id=str(room.id),
        join_code=room.join_code,
        scope_key=room.scope_key,
        access_level=room.access_level,
        admit_required=room.admit_required,
        created_by_id=str(room.created_by_id),
        provider_room_ref=room.provider_room_ref,
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
        token = services._mint_token(room, request.user)
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
        return StapelResponse(response_cls(room_to_dto(room)))


@extend_schema(tags=["Video"])
class RoomJoinView(SerializerSeamMixin, APIView):
    """Join a room by join code. Resolves the access level to admitted /
    waiting / denied."""

    permission_classes = [permissions.IsAuthenticated]
    response_serializer_class = JoinResponseSerializer

    @extend_schema(request=None, responses={200: JoinResponseSerializer})
    def post(self, request, join_code):  # noqa: R007
        room = services.get_room(join_code)
        if room is None:
            return StapelErrorResponse(404, ERR_404_ROOM_NOT_FOUND)
        result = services.join_room(request.user, room, request)
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
