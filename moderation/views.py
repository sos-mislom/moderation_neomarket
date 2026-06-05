from __future__ import annotations

import json
import re

from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from .models import BlockingReason


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
