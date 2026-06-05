from __future__ import annotations

import base64
import json
import re
import uuid

import requests
from django.conf import settings
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from .models import BlockingReason, ModerationCard, OutgoingModerationEvent


CODE_RE = re.compile(r"^[A-Z_]+$")


def parse_bool(value: str | None) -> bool | None:
    if value is None:
        return None
    if value.lower() in {"true", "1", "yes"}:
        return True
    if value.lower() in {"false", "0", "no"}:
        return False
    return None


def error(status: int, code: str, message: str) -> JsonResponse:
    return JsonResponse({"code": code, "message": message}, status=status)


def reason_payload(reason: BlockingReason) -> dict:
    return {
        "id": str(reason.id),
        "code": reason.code,
        "title": reason.title,
        "description": reason.description or None,
        "hard_block": reason.hard_block,
        "is_active": reason.is_active,
    }


def request_json(request: HttpRequest) -> dict:
    if not request.body:
        return {}
    return json.loads(request.body.decode("utf-8"))


def parse_uuid(value: str | None) -> uuid.UUID | None:
    if not value:
        return None
    try:
        return uuid.UUID(value)
    except ValueError:
        return None


def decode_jwt_sub(token: str) -> uuid.UUID | None:
    parts = token.split(".")
    if len(parts) < 2:
        return None
    padded = parts[1] + "=" * (-len(parts[1]) % 4)
    try:
        claims = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
    except (ValueError, json.JSONDecodeError):
        return None
    return parse_uuid(str(claims.get("sub", "")))


def current_moderator_id(request: HttpRequest) -> uuid.UUID | None:
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        moderator_id = decode_jwt_sub(auth.removeprefix("Bearer ").strip())
        if moderator_id:
            return moderator_id
    return parse_uuid(request.headers.get("X-Moderator-Id"))


def has_skus(snapshot: dict) -> bool:
    skus = snapshot.get("skus")
    if isinstance(skus, list):
        return len(skus) > 0
    product = snapshot.get("product")
    if isinstance(product, dict) and isinstance(product.get("skus"), list):
        return len(product["skus"]) > 0
    return False


def status_for_protocol(card: ModerationCard) -> str:
    if card.status == ModerationCard.Status.MODERATED:
        return "APPROVED"
    return card.status


def ticket_payload(card: ModerationCard) -> dict:
    zero_uuid = "00000000-0000-0000-0000-000000000000"
    return {
        "id": str(card.id),
        "product_id": str(card.product_id),
        "seller_id": str(card.seller_id) if card.seller_id else zero_uuid,
        "category_id": str(card.category_id) if card.category_id else None,
        "kind": card.kind,
        "status": status_for_protocol(card),
        "queue_priority": card.queue_priority,
        "assigned_moderator_id": str(card.assigned_moderator_id) if card.assigned_moderator_id else None,
        "claimed_at": card.claimed_at.isoformat() if card.claimed_at else None,
        "claim_expires_at": card.claim_expires_at.isoformat() if card.claim_expires_at else None,
        "decision_at": card.decision_at.isoformat() if card.decision_at else None,
        "created_at": card.created_at.isoformat(),
        "updated_at": card.updated_at.isoformat() if card.updated_at else None,
    }


def product_decision_payload(card: ModerationCard) -> dict:
    return {"product_id": str(card.product_id), "status": card.status}


def find_card(*, ticket_id=None, product_id=None) -> ModerationCard | None:
    queryset = ModerationCard.objects.all()
    try:
        if ticket_id is not None:
            return queryset.get(id=ticket_id)
        return queryset.get(product_id=product_id)
    except ModerationCard.DoesNotExist:
        return None


def send_moderation_event(card: ModerationCard, payload: dict, event_type: str) -> OutgoingModerationEvent:
    event = OutgoingModerationEvent.objects.create(card=card, event_type=event_type, payload=payload)
    if not settings.B2B_BASE_URL:
        return event

    url = f"{settings.B2B_BASE_URL.rstrip('/')}/api/v1/events/moderation"
    response = requests.post(
        url,
        json={**payload, "idempotency_key": str(event.idempotency_key)},
        headers={"X-Service-Key": settings.B2B_SERVICE_KEY},
        timeout=settings.B2B_TIMEOUT_SECONDS,
    )
    if response.status_code >= 400:
        raise requests.HTTPError(f"B2B moderation event failed: {response.status_code}", response=response)
    event.delivered = True
    event.delivered_at = timezone.now()
    event.save(update_fields=["delivered", "delivered_at"])
    return event


def approve_card(request: HttpRequest, *, ticket_id=None, product_id=None, protocol_response: bool) -> JsonResponse:
    moderator_id = current_moderator_id(request)
    if moderator_id is None:
        return error(401, "UNAUTHORIZED", "Moderator identity is required")

    card = find_card(ticket_id=ticket_id, product_id=product_id)
    if card is None:
        return error(404, "NOT_FOUND", "Product not found in moderation queue")
    if card.status == ModerationCard.Status.HARD_BLOCKED:
        return error(409, "HARD_BLOCKED", "Product is permanently blocked")
    if card.status != ModerationCard.Status.IN_REVIEW:
        return error(409, "INVALID_STATUS", "Product is not in review status")
    if card.assigned_moderator_id != moderator_id:
        return error(403, "NOT_ASSIGNED", "This moderation card is not assigned to you")
    if not has_skus(card.json_after or {}):
        return error(409, "NO_SKUS", "Product has no SKUs, cannot approve")

    try:
        payload = request_json(request)
    except json.JSONDecodeError:
        return error(400, "INVALID_JSON", "Request body must be valid JSON")

    old_status = card.status
    old_decision_at = card.decision_at
    old_decision_comment = card.decision_comment
    old_blocking_reason = card.blocking_reason
    old_field_reports = card.field_reports
    card.status = ModerationCard.Status.MODERATED
    card.decision_at = timezone.now()
    card.decision_comment = str(payload.get("moderator_comment") or payload.get("comment") or "").strip()
    card.blocking_reason = None
    card.field_reports = []
    card.save(
        update_fields=[
            "status",
            "decision_at",
            "decision_comment",
            "blocking_reason",
            "field_reports",
            "updated_at",
        ]
    )

    event_payload = {"product_id": str(card.product_id), "status": "MODERATED"}
    try:
        send_moderation_event(card, event_payload, "MODERATED")
    except requests.RequestException:
        card.status = old_status
        card.decision_at = old_decision_at
        card.decision_comment = old_decision_comment
        card.blocking_reason = old_blocking_reason
        card.field_reports = old_field_reports
        card.save(
            update_fields=[
                "status",
                "decision_at",
                "decision_comment",
                "blocking_reason",
                "field_reports",
                "updated_at",
            ]
        )
        return error(500, "B2B_EVENT_FAILED", "Could not deliver moderation decision to B2B")

    return JsonResponse(ticket_payload(card) if protocol_response else product_decision_payload(card))


@csrf_exempt
@require_http_methods(["GET", "POST"])
def blocking_reasons(request: HttpRequest) -> JsonResponse:
    if request.method == "GET":
        hard_block = parse_bool(request.GET.get("hard_block"))
        is_active = parse_bool(request.GET.get("is_active"))
        if is_active is None:
            is_active = True

        queryset = BlockingReason.objects.all()
        if hard_block is not None:
            queryset = queryset.filter(hard_block=hard_block)
        queryset = queryset.filter(is_active=is_active)
        return JsonResponse([reason_payload(reason) for reason in queryset], safe=False)

    try:
        payload = request_json(request)
    except json.JSONDecodeError:
        return error(400, "INVALID_JSON", "Request body must be valid JSON")

    code = str(payload.get("code", "")).strip().upper()
    title = str(payload.get("title", "")).strip()
    hard_block = payload.get("hard_block")
    if not CODE_RE.fullmatch(code) or not title or not isinstance(hard_block, bool):
        return error(400, "INVALID_REQUEST", "code, title and hard_block are required")
    if BlockingReason.objects.filter(code=code).exists():
        return error(409, "DUPLICATE_REASON", "Blocking reason code already exists")

    reason = BlockingReason.objects.create(
        code=code,
        title=title,
        description=str(payload.get("description", "")).strip(),
        hard_block=hard_block,
    )
    return JsonResponse(reason_payload(reason), status=201)


@csrf_exempt
@require_http_methods(["PATCH", "DELETE"])
def blocking_reason_detail(request: HttpRequest, reason_id) -> JsonResponse | HttpResponse:
    try:
        reason = BlockingReason.objects.get(id=reason_id)
    except BlockingReason.DoesNotExist:
        return error(404, "NOT_FOUND", "Blocking reason not found")

    if request.method == "DELETE":
        reason.delete()
        return HttpResponse(status=204)

    try:
        payload = request_json(request)
    except json.JSONDecodeError:
        return error(400, "INVALID_JSON", "Request body must be valid JSON")

    if "title" in payload:
        title = str(payload["title"]).strip()
        if not title:
            return error(400, "INVALID_REQUEST", "title must not be empty")
        reason.title = title
    if "description" in payload:
        reason.description = str(payload["description"]).strip()
    if "is_active" in payload:
        if not isinstance(payload["is_active"], bool):
            return error(400, "INVALID_REQUEST", "is_active must be boolean")
        reason.is_active = payload["is_active"]
    reason.save()
    return JsonResponse(reason_payload(reason))


@csrf_exempt
@require_http_methods(["POST"])
def approve_ticket(request: HttpRequest, ticket_id) -> JsonResponse:
    return approve_card(request, ticket_id=ticket_id, protocol_response=True)


@csrf_exempt
@require_http_methods(["POST"])
def approve_product(request: HttpRequest, product_id) -> JsonResponse:
    return approve_card(request, product_id=product_id, protocol_response=False)
