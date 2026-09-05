"""DRF views for 1:1 calls — thin over :mod:`stapel_video.calls.services`.

**Every refusal that concerns a call somebody is not party to is a 404.** Not
a 403, and not only on the read: on accept, decline, hangup and the token
re-mint too. A call id names two people and the conversation they are having,
so a 403 would confirm that a guessed id is a real call — and confirming
existence is the whole of what there is to leak here. "No such call" and "not
your call" are one answer, given once.

The state refusals are different and stay distinct, because they are about a
call the caller genuinely holds: ``409`` for accepting something that is no
longer ringing, ``409`` for being busy. Those tell a client to re-read, which
is exactly what it should do.
"""
from drf_spectacular.utils import extend_schema
from rest_framework import permissions, status
from rest_framework.views import APIView
from stapel_core.django.api.errors import StapelErrorResponse, StapelResponse

from ..errors import (
    ERR_400_INVALID_CALLEE,
    ERR_403_CALL_NOT_ALLOWED,
    ERR_404_CALL_NOT_FOUND,
    ERR_409_CALL_BUSY,
    ERR_409_CALL_STATE,
    ERR_503_CALL_PROVIDER,
)
from ..views import SerializerSeamMixin
from . import services
from .dto import (
    ActiveCallResponse,
    CallResponse,
    CallTokenResponse,
    MediaTokenResponse,
)
from .models import CallState
from .serializers import (
    ActiveCallResponseSerializer,
    CallCreateRequestSerializer,
    CallResponseSerializer,
    CallSessionRequestSerializer,
    CallTokenResponseSerializer,
    MediaTokenResponseSerializer,
)


# ── Mapper ───────────────────────────────────────────────────────────────


def call_to_dto(call) -> CallResponse:
    return CallResponse(
        id=str(call.id),
        thread_key=call.thread_key,
        caller_id=str(call.caller_id),
        callee_id=str(call.callee_id),
        room_name=call.room_name,
        media=call.media,
        state=call.state,
        end_reason=call.end_reason,
        started_at=call.started_at.isoformat() if call.started_at else "",
        answered_at=call.answered_at.isoformat() if call.answered_at else None,
        ended_at=call.ended_at.isoformat() if call.ended_at else None,
        duration_seconds=call.duration_seconds,
        expires_at=_expires_at(call),
    )


def _expires_at(call) -> str | None:
    """When the ring runs out — only while it is actually ringing.

    Sent so the client counts down against the SERVER's deadline instead of
    starting its own 45 seconds when the frame happened to arrive. The two
    differ by the delivery latency plus any clock skew, and the visible defect
    of getting it wrong is an overlay that outlives the call it is announcing.
    """
    if call.state != CallState.RINGING or not call.started_at:
        return None
    from datetime import timedelta

    from ..conf import video_settings

    return (
        call.started_at
        + timedelta(seconds=int(video_settings.CALL_RING_TIMEOUT_SECONDS or 45))
    ).isoformat()


# ── Views ────────────────────────────────────────────────────────────────


@extend_schema(tags=["Video"])
class CallListCreateView(SerializerSeamMixin, APIView):
    """``POST /video/api/v1/calls`` — ring somebody.

    Answers the CALLER's token. The callee's is minted by ``accept`` and
    never travels on the ring frame.
    """

    permission_classes = [permissions.IsAuthenticated]
    request_serializer_class = CallCreateRequestSerializer
    response_serializer_class = CallTokenResponseSerializer

    @extend_schema(
        request=CallCreateRequestSerializer,
        responses={201: CallTokenResponseSerializer},
    )
    def post(self, request):  # noqa: R007
        ser = self.get_request_serializer_class()(data=request.data or {})
        ser.is_valid(raise_exception=True)
        data = ser.validated_data
        try:
            call, token, url = services.create_call(
                request.user,
                callee_id=data.callee_id,
                request=request,
                thread_key=data.thread_key or "",
                media=data.media or "video",
                client_session_id=data.client_session_id,
            )
        except services.InvalidCallee:
            return StapelErrorResponse(400, ERR_400_INVALID_CALLEE)
        except services.CallNotAllowed:
            return StapelErrorResponse(403, ERR_403_CALL_NOT_ALLOWED)
        except services.CallBusy:
            return StapelErrorResponse(409, ERR_409_CALL_BUSY)
        except services.CallProviderUnavailable:
            return StapelErrorResponse(503, ERR_503_CALL_PROVIDER)
        response_cls = self.get_response_serializer_class()
        return StapelResponse(
            response_cls(CallTokenResponse(call=call_to_dto(call), token=token, url=url)),
            status=status.HTTP_201_CREATED,
        )


@extend_schema(tags=["Video"])
class ActiveCallView(SerializerSeamMixin, APIView):
    """``GET /video/api/v1/calls/active`` — the caller's own live call, if any.

    The repair for every dropped frame: the socket is best-effort, so a lost
    ``call.incoming`` would be a call that never rang and a lost
    ``call.ended`` a ring that never stops. The front reads this on mount and
    on every realtime reconnect (SPEC §7.1), which is what turns "the socket
    is unreliable" from a defect into a property.
    """

    permission_classes = [permissions.IsAuthenticated]
    response_serializer_class = ActiveCallResponseSerializer

    @extend_schema(responses={200: ActiveCallResponseSerializer})
    def get(self, request):  # noqa: R007
        call = services.active_call_for(request.user)
        response_cls = self.get_response_serializer_class()
        return StapelResponse(
            response_cls(
                ActiveCallResponse(call=call_to_dto(call) if call else None)
            )
        )


class _CallActionView(SerializerSeamMixin, APIView):
    """Shared party guard: resolve the call or answer 404."""

    permission_classes = [permissions.IsAuthenticated]

    def _resolve(self, request, call_id):
        call = services.get_call(call_id, request.user)
        if call is None:
            return None, StapelErrorResponse(404, ERR_404_CALL_NOT_FOUND)
        return call, None

    def _session_id(self, request):
        ser = CallSessionRequestSerializer(data=request.data or {})
        ser.is_valid(raise_exception=True)
        return ser.validated_data.client_session_id


@extend_schema(tags=["Video"])
class CallDetailView(_CallActionView):
    """``GET /video/api/v1/calls/{id}`` — one call, for its two parties."""

    response_serializer_class = CallResponseSerializer

    @extend_schema(responses={200: CallResponseSerializer})
    def get(self, request, call_id):  # noqa: R007
        call, err = self._resolve(request, call_id)
        if err is not None:
            return err
        response_cls = self.get_response_serializer_class()
        return StapelResponse(response_cls(call_to_dto(call)))


@extend_schema(tags=["Video"])
class CallAcceptView(_CallActionView):
    """``POST /calls/{id}/accept`` — the callee picks up, and gets a token."""

    request_serializer_class = CallSessionRequestSerializer
    response_serializer_class = CallTokenResponseSerializer

    @extend_schema(
        request=CallSessionRequestSerializer,
        responses={200: CallTokenResponseSerializer},
    )
    def post(self, request, call_id):  # noqa: R007
        call, err = self._resolve(request, call_id)
        if err is not None:
            return err
        try:
            call, token, url = services.accept_call(
                call, request.user, self._session_id(request)
            )
        except services.CallNotAllowed:
            # The caller trying to accept their own call is not a party
            # problem, it is a role problem — but answering 403 would still
            # only be told to somebody who is already in the call, so the
            # distinction costs nothing and reads correctly.
            return StapelErrorResponse(403, ERR_403_CALL_NOT_ALLOWED)
        except services.CallBusy:
            return StapelErrorResponse(409, ERR_409_CALL_BUSY)
        except services.InvalidCallState:
            return StapelErrorResponse(409, ERR_409_CALL_STATE)
        response_cls = self.get_response_serializer_class()
        return StapelResponse(
            response_cls(CallTokenResponse(call=call_to_dto(call), token=token, url=url))
        )


@extend_schema(tags=["Video"])
class CallDeclineView(_CallActionView):
    """``POST /calls/{id}/decline`` — the callee says no."""

    response_serializer_class = CallResponseSerializer

    @extend_schema(request=None, responses={200: CallResponseSerializer})
    def post(self, request, call_id):  # noqa: R007
        call, err = self._resolve(request, call_id)
        if err is not None:
            return err
        try:
            call = services.decline_call(call, request.user)
        except services.CallNotAllowed:
            return StapelErrorResponse(403, ERR_403_CALL_NOT_ALLOWED)
        except services.InvalidCallState:
            return StapelErrorResponse(409, ERR_409_CALL_STATE)
        response_cls = self.get_response_serializer_class()
        return StapelResponse(response_cls(call_to_dto(call)))


@extend_schema(tags=["Video"])
class CallHangupView(_CallActionView):
    """``POST /calls/{id}/hangup`` — either party ends it."""

    response_serializer_class = CallResponseSerializer

    @extend_schema(request=None, responses={200: CallResponseSerializer})
    def post(self, request, call_id):  # noqa: R007
        call, err = self._resolve(request, call_id)
        if err is not None:
            return err
        try:
            call = services.hangup_call(call, request.user)
        except services.CallNotAllowed:
            return StapelErrorResponse(403, ERR_403_CALL_NOT_ALLOWED)
        except services.InvalidCallState:
            return StapelErrorResponse(409, ERR_409_CALL_STATE)
        response_cls = self.get_response_serializer_class()
        return StapelResponse(response_cls(call_to_dto(call)))


@extend_schema(tags=["Video"])
class CallTokenView(_CallActionView):
    """``POST /calls/{id}/token`` — a fresh credential for a call in progress.

    A media token is presented AGAIN on every full reconnect and nothing
    re-mints it automatically, so without this endpoint the token's TTL is a
    hard ceiling on coming back from a tunnel — and the failure looks like a
    network fault rather than an expiry. Live calls only: re-minting a grant
    for a call that is over would hand out a key to a room nobody is in.
    """

    request_serializer_class = CallSessionRequestSerializer
    response_serializer_class = MediaTokenResponseSerializer

    @extend_schema(
        request=CallSessionRequestSerializer,
        responses={200: MediaTokenResponseSerializer},
    )
    def post(self, request, call_id):  # noqa: R007
        call, err = self._resolve(request, call_id)
        if err is not None:
            return err
        if not call.is_live:
            return StapelErrorResponse(409, ERR_409_CALL_STATE)
        token, url = services.mint_token_for(
            call, request.user, self._session_id(request)
        )
        response_cls = self.get_response_serializer_class()
        return StapelResponse(response_cls(MediaTokenResponse(token=token, url=url)))


__all__ = [
    "ActiveCallView",
    "CallAcceptView",
    "CallDeclineView",
    "CallDetailView",
    "CallHangupView",
    "CallListCreateView",
    "CallTokenView",
    "call_to_dto",
]
