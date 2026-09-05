"""Serializers for the call API (dataclass-DTO backed).

Every view exposes the request/response serializer seams the rest of this
module does (``SerializerSeamMixin``); these are the defaults.
"""
from stapel_core.django.api.serializers import StapelDataclassSerializer

from .dto import (
    ActiveCallResponse,
    CallCreateRequest,
    CallResponse,
    CallSessionRequest,
    CallTokenResponse,
    MediaTokenResponse,
)


class CallResponseSerializer(StapelDataclassSerializer):
    class Meta:
        dataclass = CallResponse


class CallTokenResponseSerializer(StapelDataclassSerializer):
    class Meta:
        dataclass = CallTokenResponse


class ActiveCallResponseSerializer(StapelDataclassSerializer):
    class Meta:
        dataclass = ActiveCallResponse


class MediaTokenResponseSerializer(StapelDataclassSerializer):
    class Meta:
        dataclass = MediaTokenResponse


class CallCreateRequestSerializer(StapelDataclassSerializer):
    class Meta:
        dataclass = CallCreateRequest


class CallSessionRequestSerializer(StapelDataclassSerializer):
    class Meta:
        dataclass = CallSessionRequest


__all__ = [
    "ActiveCallResponseSerializer",
    "CallCreateRequestSerializer",
    "CallResponseSerializer",
    "CallSessionRequestSerializer",
    "CallTokenResponseSerializer",
    "MediaTokenResponseSerializer",
]
