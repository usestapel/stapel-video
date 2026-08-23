"""Serializers for the stapel-video API (dataclass-DTO backed).

Every view exposes request/response serializer seams (SerializerSeamMixin);
these are the defaults.
"""
from stapel_core.django.api.serializers import StapelDataclassSerializer

from .dto import (
    AdmitResponse,
    JoinRequest,
    JoinResponse,
    LobbyActionRequest,
    ParticipantListResponse,
    ParticipantResponse,
    RoomCreateRequest,
    RoomResponse,
    ScopeUsageMonth,
    ScopeUsageResponse,
    ScopeUsageRow,
)


class RoomResponseSerializer(StapelDataclassSerializer):
    class Meta:
        dataclass = RoomResponse


class ParticipantResponseSerializer(StapelDataclassSerializer):
    class Meta:
        dataclass = ParticipantResponse


class JoinResponseSerializer(StapelDataclassSerializer):
    class Meta:
        dataclass = JoinResponse


class AdmitResponseSerializer(StapelDataclassSerializer):
    class Meta:
        dataclass = AdmitResponse


class ParticipantListResponseSerializer(StapelDataclassSerializer):
    class Meta:
        dataclass = ParticipantListResponse


class ScopeUsageRowSerializer(StapelDataclassSerializer):
    class Meta:
        dataclass = ScopeUsageRow


class ScopeUsageMonthSerializer(StapelDataclassSerializer):
    class Meta:
        dataclass = ScopeUsageMonth


class ScopeUsageResponseSerializer(StapelDataclassSerializer):
    class Meta:
        dataclass = ScopeUsageResponse


class RoomCreateRequestSerializer(StapelDataclassSerializer):
    class Meta:
        dataclass = RoomCreateRequest


class JoinRequestSerializer(StapelDataclassSerializer):
    class Meta:
        dataclass = JoinRequest


class LobbyActionRequestSerializer(StapelDataclassSerializer):
    class Meta:
        dataclass = LobbyActionRequest
