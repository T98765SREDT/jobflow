"""Input validation for JobFlow's JSON API."""

from __future__ import annotations

import json
import re
import unicodedata
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable
from urllib.parse import parse_qsl, urlencode, urlparse, urlsplit, urlunsplit


STATUSES = ("Wishlist", "Applied", "Interview", "Offer", "Rejected")
STAGES = ("Wishlist", "Ready", "Applied", "Interview", "Offer", "Closed")
OUTCOMES = ("Rejected", "Withdrawn", "No response", "Expired", "Offer declined", "Accepted")
LEGACY_STATUS_TO_STAGE = {
    "Wishlist": "Wishlist",
    "Applied": "Applied",
    "Interview": "Interview",
    "Offer": "Offer",
    "Rejected": "Closed",
}
STAGE_TO_LEGACY_STATUS = {
    "Wishlist": "Wishlist",
    "Ready": "Applied",
    "Applied": "Applied",
    "Interview": "Interview",
    "Offer": "Offer",
    "Closed": "Rejected",
}
WORK_MODES = ("Remote", "Hybrid", "On-site")
CURRENCIES = ("USD", "EUR", "JPY", "GBP", "CNY")
SALARY_PERIODS = ("Hourly", "Monthly", "Annual")
EVENT_TYPES = ("applied", "status_changed", "interview", "follow_up", "note", "offer", "rejection", "custom")
EVENT_ORIGINS = ("system", "user", "import", "legacy")
TASK_KINDS = ("follow_up", "preparation", "interview", "decision", "custom")
REQUIREMENT_CATEGORIES = (
    "skill", "experience", "language", "location", "work_authorization", "compensation", "other",
)
REQUIREMENT_ASSESSMENTS = ("met", "partial", "gap", "unknown")
ARTIFACT_KINDS = ("job_description", "resume", "cover_letter", "portfolio", "assessment", "other")

# These parameters are commonly added by analytics systems and do not identify
# a distinct job posting. Identity-bearing query parameters are intentionally
# preserved during canonicalization.
TRACKING_QUERY_PARAMETERS = frozenset({
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "gclid", "dclid", "fbclid", "msclkid", "mc_cid", "mc_eid", "_hsenc", "_hsmi",
})

ALLOWED_FIELDS = {
    "company",
    "role",
    "location",
    "work_mode",
    "status",
    "stage",
    "outcome",
    "version",
    "closed_at",
    "waiting_until",
    "source",
    "url",
    "salary_min",
    "salary_max",
    "salary_period",
    "currency",
    "applied_date",
    "next_action_date",
    "notes",
}


class ValidationError(ValueError):
    """Raised when an API payload cannot be accepted."""

    def __init__(self, errors: dict[str, str]):
        super().__init__("Invalid application data")
        self.errors = errors


def canonical_url(value: Any) -> str:
    """Return a stable URL identity while retaining meaningful query values."""
    text = _text(value)
    if not text:
        return ""
    try:
        parsed = urlsplit(text)
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
            return ""
        hostname = parsed.hostname.casefold()
        try:
            port = parsed.port
        except ValueError:
            return ""
        default_port = (parsed.scheme.lower() == "http" and port == 80) or (parsed.scheme.lower() == "https" and port == 443)
        # Credentials are never part of job identity and must not be echoed
        # into fingerprints or preview responses.
        netloc = hostname if not port or default_port else f"{hostname}:{port}"
        path = re.sub(r"/+", "/", parsed.path or "/")
        if path != "/":
            path = path.rstrip("/")
        query_items = [
            (key, item)
            for key, item in parse_qsl(parsed.query, keep_blank_values=True)
            if key.casefold() not in TRACKING_QUERY_PARAMETERS
        ]
        query_items.sort()
        query = urlencode(query_items, doseq=True)
        return urlunsplit((parsed.scheme.lower(), netloc, path, query, ""))
    except (TypeError, ValueError):
        return ""


def normalize_identity_text(value: Any) -> str:
    """Normalize human identity fields without deleting meaningful Unicode."""
    return " ".join(unicodedata.normalize("NFKC", _text(value)).casefold().split())


def application_fingerprint(record: dict[str, Any]) -> str:
    """Build the documented primary URL or fallback identity fingerprint."""
    url = canonical_url(record.get("url"))
    if url:
        return f"url:{url}"
    return "details:" + "\x1f".join(
        normalize_identity_text(record.get(field)) for field in ("company", "role", "location")
    )


def duplicate_reason(incoming: dict[str, Any], existing: dict[str, Any]) -> str | None:
    """Return why two records share an application identity, if they do."""
    incoming_url = canonical_url(incoming.get("url"))
    existing_url = canonical_url(existing.get("url"))
    if incoming_url and existing_url and incoming_url == existing_url:
        return "canonical_url"
    if not incoming_url and not existing_url and application_fingerprint(incoming) == application_fingerprint(existing):
        return "company_role_location"
    return None


def find_duplicate_matches(incoming: Iterable[dict[str, Any]], existing: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return deterministic duplicate matches for preview and import decisions."""
    matches: list[dict[str, Any]] = []
    existing_records = list(existing)
    for incoming_index, record in enumerate(incoming):
        for existing_record in existing_records:
            reason = duplicate_reason(record, existing_record)
            if reason:
                matches.append({
                    "incoming_index": incoming_index,
                    "existing_application_id": existing_record.get("id"),
                    "reason": reason,
                    "fingerprint": application_fingerprint(record),
                })
                break
    return matches


def validate_transition(payload: Any) -> dict[str, Any]:
    """Validate a lifecycle transition request.

    ``request_id`` and ``expected_version`` are optional for backwards
    compatibility with direct callers, but clients should send both when a
    write may be retried.
    """
    if not isinstance(payload, dict):
        raise ValidationError({"body": "Expected a JSON object."})
    allowed = {"to_stage", "outcome", "occurred_at", "expected_version", "request_id"}
    errors: dict[str, str] = {}
    unknown = set(payload) - allowed
    if unknown:
        errors["body"] = f"Unknown fields: {', '.join(sorted(unknown))}."

    to_stage = _text(payload.get("to_stage"))
    if to_stage not in STAGES:
        errors["to_stage"] = f"Choose one of: {', '.join(STAGES)}."

    outcome = _text(payload.get("outcome")) or None
    if outcome is not None and outcome not in OUTCOMES:
        errors["outcome"] = f"Choose one of: {', '.join(OUTCOMES)}."
    if to_stage == "Closed" and outcome is None:
        errors["outcome"] = "Closing an application requires an outcome."
    if to_stage != "Closed" and outcome is not None:
        errors["outcome"] = "Only Closed applications can have an outcome."

    occurred_at = payload.get("occurred_at")
    if occurred_at in (None, ""):
        occurred_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    else:
        occurred_at = _validate_datetime(occurred_at, "occurred_at", errors)

    expected_version = payload.get("expected_version")
    if expected_version is not None:
        if isinstance(expected_version, bool) or not isinstance(expected_version, int) or expected_version < 1:
            errors["expected_version"] = "Expected version must be a positive integer."

    request_id = payload.get("request_id")
    if request_id is not None:
        request_id = _text(request_id)
        if not request_id:
            errors["request_id"] = "Request ID cannot be empty."
        elif len(request_id) > 128:
            errors["request_id"] = "Request ID must be 128 characters or fewer."

    if errors:
        raise ValidationError(errors)
    return {
        "to_stage": to_stage,
        "outcome": outcome,
        "occurred_at": occurred_at,
        "expected_version": expected_version,
        "request_id": request_id,
    }


def validate_task(payload: Any, *, partial: bool = False) -> dict[str, Any]:
    """Validate a task payload shared by the task API and browser demo."""
    if not isinstance(payload, dict):
        raise ValidationError({"body": "Expected a JSON object."})
    allowed = {"kind", "title", "due_date", "completed_at", "version", "expected_version"}
    errors: dict[str, str] = {}
    unknown = set(payload) - allowed
    if unknown:
        errors["body"] = f"Unknown fields: {', '.join(sorted(unknown))}."
    cleaned: dict[str, Any] = {}
    for field in ("kind", "title"):
        if not partial or field in payload:
            value = _text(payload.get(field))
            if field == "kind" and value not in TASK_KINDS:
                errors[field] = f"Choose one of: {', '.join(TASK_KINDS)}."
            if field == "title" and not value:
                errors[field] = "This field is required."
            if field == "title" and len(value) > 200:
                errors[field] = "Must be 200 characters or fewer."
            cleaned[field] = value
    for field in ("due_date",):
        if field in payload:
            cleaned[field] = _validate_date(payload[field], field, errors)
        elif not partial:
            errors[field] = "This field is required."
    if "completed_at" in payload:
        cleaned["completed_at"] = _validate_datetime(payload["completed_at"], "completed_at", errors)
    elif not partial:
        cleaned["completed_at"] = None
    for field in ("version", "expected_version"):
        if field in payload:
            value = payload[field]
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                errors[field] = "Version must be a positive integer."
            else:
                cleaned[field] = value
    if not partial:
        cleaned.setdefault("version", 1)
    if errors:
        raise ValidationError(errors)
    return cleaned


def validate_as_of(value: Any) -> str:
    """Validate the optional Today API date."""
    text = _text(value) or date.today().isoformat()
    errors: dict[str, str] = {}
    _validate_date(text, "as_of", errors)
    if errors:
        raise ValidationError(errors)
    return text


def validate_requirement(payload: Any, *, partial: bool = False) -> dict[str, Any]:
    """Validate one visible job requirement and its supporting evidence."""
    if not isinstance(payload, dict):
        raise ValidationError({"body": "Expected a JSON object."})
    allowed = {"criterion", "category", "assessment", "evidence", "weight", "position"}
    errors: dict[str, str] = {}
    unknown = set(payload) - allowed
    if unknown:
        errors["body"] = f"Unknown fields: {', '.join(sorted(unknown))}."
    cleaned: dict[str, Any] = {}
    if not partial or "criterion" in payload:
        criterion = _text(payload.get("criterion"))
        if not criterion:
            errors["criterion"] = "This field is required."
        elif len(criterion) > 240:
            errors["criterion"] = "Must be 240 characters or fewer."
        cleaned["criterion"] = criterion
    if not partial or "category" in payload:
        category = _text(payload.get("category"))
        if category not in REQUIREMENT_CATEGORIES:
            errors["category"] = f"Choose one of: {', '.join(REQUIREMENT_CATEGORIES)}."
        cleaned["category"] = category
    if not partial or "assessment" in payload:
        assessment = _text(payload.get("assessment")) or "unknown"
        if assessment not in REQUIREMENT_ASSESSMENTS:
            errors["assessment"] = f"Choose one of: {', '.join(REQUIREMENT_ASSESSMENTS)}."
        cleaned["assessment"] = assessment
    if "evidence" in payload or not partial:
        evidence = _text(payload.get("evidence"))
        if len(evidence) > 2000:
            errors["evidence"] = "Must be 2000 characters or fewer."
        cleaned["evidence"] = evidence
    if "weight" in payload or not partial:
        weight = payload.get("weight", 1)
        if isinstance(weight, bool) or not isinstance(weight, int) or weight < 1 or weight > 5:
            errors["weight"] = "Weight must be an integer from 1 to 5."
        else:
            cleaned["weight"] = weight
    if "position" in payload or not partial:
        position = payload.get("position", 0)
        if isinstance(position, bool) or not isinstance(position, int) or position < 0:
            errors["position"] = "Position must be a non-negative integer."
        else:
            cleaned["position"] = position
    if errors:
        raise ValidationError(errors)
    return cleaned


def validate_artifact(payload: Any, *, partial: bool = False) -> dict[str, Any]:
    """Validate metadata for one linked application material."""
    if not isinstance(payload, dict):
        raise ValidationError({"body": "Expected a JSON object."})
    allowed = {"kind", "label", "uri", "version_label", "notes"}
    errors: dict[str, str] = {}
    unknown = set(payload) - allowed
    if unknown:
        errors["body"] = f"Unknown fields: {', '.join(sorted(unknown))}."
    cleaned: dict[str, Any] = {}
    if not partial or "kind" in payload:
        kind = _text(payload.get("kind"))
        if kind not in ARTIFACT_KINDS:
            errors["kind"] = f"Choose one of: {', '.join(ARTIFACT_KINDS)}."
        cleaned["kind"] = kind
    if not partial or "label" in payload:
        label = _text(payload.get("label"))
        if not label:
            errors["label"] = "This field is required."
        elif len(label) > 160:
            errors["label"] = "Must be 160 characters or fewer."
        cleaned["label"] = label
    if "uri" in payload or not partial:
        uri = _text(payload.get("uri"))
        if len(uri) > 500:
            errors["uri"] = "Must be 500 characters or fewer."
        elif uri:
            parsed = urlparse(uri)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                errors["uri"] = "Enter a complete http:// or https:// URL."
        cleaned["uri"] = uri
    if "version_label" in payload or not partial:
        version_label = _text(payload.get("version_label"))
        if len(version_label) > 80:
            errors["version_label"] = "Must be 80 characters or fewer."
        cleaned["version_label"] = version_label
    if "notes" in payload or not partial:
        notes = _text(payload.get("notes"))
        if len(notes) > 2000:
            errors["notes"] = "Must be 2000 characters or fewer."
        cleaned["notes"] = notes
    if errors:
        raise ValidationError(errors)
    return cleaned


def validate_submission(payload: Any) -> dict[str, Any]:
    """Validate a request to create an immutable submission snapshot."""
    if not isinstance(payload, dict):
        raise ValidationError({"body": "Expected a JSON object."})
    allowed = {"artifact_ids", "notes", "submitted_at"}
    errors: dict[str, str] = {}
    unknown = set(payload) - allowed
    if unknown:
        errors["body"] = f"Unknown fields: {', '.join(sorted(unknown))}."
    artifact_ids = payload.get("artifact_ids")
    if not isinstance(artifact_ids, list) or not artifact_ids:
        errors["artifact_ids"] = "Choose at least one material."
        cleaned_ids: list[int] = []
    elif len(artifact_ids) > 100:
        errors["artifact_ids"] = "A submission can contain at most 100 materials."
        cleaned_ids = []
    else:
        cleaned_ids = []
        for index, artifact_id in enumerate(artifact_ids):
            if isinstance(artifact_id, bool) or not isinstance(artifact_id, int) or artifact_id < 1:
                errors[f"artifact_ids.{index}"] = "Material IDs must be positive integers."
            elif artifact_id in cleaned_ids:
                errors["artifact_ids"] = "A material can only be selected once."
            else:
                cleaned_ids.append(artifact_id)
    notes = _text(payload.get("notes"))
    if len(notes) > 2000:
        errors["notes"] = "Must be 2000 characters or fewer."
    submitted_at = payload.get("submitted_at")
    if submitted_at in (None, ""):
        submitted_at = None
    else:
        submitted_at = _validate_datetime(submitted_at, "submitted_at", errors)
    if errors:
        raise ValidationError(errors)
    return {"artifact_ids": cleaned_ids, "notes": notes, "submitted_at": submitted_at}


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _validate_date(value: Any, field: str, errors: dict[str, str]) -> str | None:
    text = _text(value)
    if not text:
        return None
    try:
        date.fromisoformat(text)
    except ValueError:
        errors[field] = "Use ISO format YYYY-MM-DD."
    return text


def _validate_datetime(value: Any, field: str, errors: dict[str, str]) -> str | None:
    text = _text(value)
    if not text:
        return None
    try:
        datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        errors[field] = "Use an ISO date-time value."
    return text


def _validate_salary(value: Any, field: str, errors: dict[str, str]) -> float | int | None:
    if value in (None, ""):
        return None
    try:
        amount = Decimal(str(value))
        if not amount.is_finite() or amount < 0 or amount.as_tuple().exponent < -2:
            raise ValueError
        return int(amount) if amount == amount.to_integral_value() else float(amount)
    except (InvalidOperation, TypeError, ValueError):
        errors[field] = "Enter a non-negative amount with no more than two decimal places."
        return None


def validate_application(payload: Any, *, partial: bool = False) -> dict[str, Any]:
    """Validate and normalize a create or update payload."""
    if not isinstance(payload, dict):
        raise ValidationError({"body": "Expected a JSON object."})

    errors: dict[str, str] = {}
    unknown = set(payload) - ALLOWED_FIELDS
    if unknown:
        errors["body"] = f"Unknown fields: {', '.join(sorted(unknown))}."

    cleaned: dict[str, Any] = {}
    required = ("company", "role", "work_mode")
    for field in required:
        if not partial or field in payload:
            value = _text(payload.get(field))
            if not value:
                errors[field] = "This field is required."
            cleaned[field] = value

    for field, maximum in (
        ("company", 120),
        ("role", 160),
        ("location", 120),
        ("source", 80),
        ("url", 500),
        ("notes", 4000),
    ):
        if field in payload:
            value = _text(payload[field])
            if len(value) > maximum:
                errors[field] = f"Must be {maximum} characters or fewer."
            cleaned[field] = value

    if not partial:
        for field in ("location", "source", "url", "notes"):
            cleaned.setdefault(field, "")
        for field in ("salary_min", "salary_max", "applied_date", "next_action_date", "waiting_until"):
            cleaned.setdefault(field, None)

    has_status = "status" in payload
    has_stage = "stage" in payload
    lifecycle_supplied = has_status or has_stage or "outcome" in payload or "closed_at" in payload
    if not partial and not has_status and not has_stage:
        errors["status"] = "This field is required."

    raw_status = _text(payload.get("status")) if has_status else ""
    raw_stage = _text(payload.get("stage")) if has_stage else ""
    status_valid = raw_status in STATUSES
    stage_valid = raw_stage in STAGES
    if has_status and not status_valid:
        errors["status"] = f"Choose one of: {', '.join(STATUSES)}."
    if has_stage and not stage_valid:
        errors["stage"] = f"Choose one of: {', '.join(STAGES)}."
    if status_valid and stage_valid and LEGACY_STATUS_TO_STAGE[raw_status] != raw_stage:
        errors["stage"] = "Stage and status must describe the same lifecycle stage."
    if stage_valid:
        cleaned["stage"] = raw_stage
        cleaned["status"] = STAGE_TO_LEGACY_STATUS[raw_stage]
    elif status_valid:
        cleaned["stage"] = LEGACY_STATUS_TO_STAGE[raw_status]
        cleaned["status"] = raw_status
    elif has_stage or has_status or not partial:
        cleaned["stage"] = raw_stage
        cleaned["status"] = raw_status

    if "outcome" in payload:
        outcome = _text(payload["outcome"]) or None
        if outcome is not None and outcome not in OUTCOMES:
            errors["outcome"] = f"Choose one of: {', '.join(OUTCOMES)}."
        cleaned["outcome"] = outcome
    elif not partial:
        cleaned["outcome"] = None

    if "version" in payload:
        version = payload["version"]
        if isinstance(version, bool) or not isinstance(version, int) or version < 1:
            errors["version"] = "Version must be a positive integer."
        else:
            cleaned["version"] = version
    elif not partial:
        cleaned["version"] = 1

    if "closed_at" in payload:
        cleaned["closed_at"] = _validate_datetime(payload["closed_at"], "closed_at", errors)
    elif not partial:
        cleaned["closed_at"] = None

    # A legacy status=Rejected request remains valid during the compatibility
    # window. It is represented canonically as Closed/Rejected with a timestamp.
    if cleaned.get("stage") == "Closed" and not partial and raw_status == "Rejected" and "outcome" not in payload:
        cleaned["outcome"] = "Rejected"
    if cleaned.get("stage") == "Closed":
        if not cleaned.get("outcome") and not partial:
            errors["outcome"] = "Closed applications require an outcome."
        if not cleaned.get("closed_at"):
            if not partial and raw_status == "Rejected" and "stage" not in payload and "closed_at" not in payload:
                cleaned["closed_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            elif not partial:
                errors["closed_at"] = "Closed applications require a closed-at timestamp."
    else:
        if cleaned.get("outcome") and (not partial or lifecycle_supplied):
            errors["outcome"] = "Only Closed applications can have an outcome."
        if cleaned.get("closed_at") and (not partial or lifecycle_supplied):
            errors["closed_at"] = "Only Closed applications can have a closed-at timestamp."
        if not partial or lifecycle_supplied:
            cleaned["outcome"] = None
            cleaned["closed_at"] = None
    if "work_mode" in cleaned and cleaned["work_mode"] not in WORK_MODES:
        errors["work_mode"] = f"Choose one of: {', '.join(WORK_MODES)}."

    if "salary_period" in payload:
        salary_period = _text(payload["salary_period"]) or "Annual"
        if salary_period not in SALARY_PERIODS:
            errors["salary_period"] = f"Choose one of: {', '.join(SALARY_PERIODS)}."
        cleaned["salary_period"] = salary_period
    elif not partial:
        cleaned["salary_period"] = "Annual"

    if "currency" in payload:
        currency = _text(payload["currency"]).upper() or "USD"
        if currency not in CURRENCIES:
            errors["currency"] = f"Choose one of: {', '.join(CURRENCIES)}."
        cleaned["currency"] = currency
    elif not partial:
        cleaned["currency"] = "USD"

    for field in ("salary_min", "salary_max"):
        if field in payload:
            cleaned[field] = _validate_salary(payload[field], field, errors)

    for field in ("applied_date", "next_action_date", "waiting_until"):
        if field in payload:
            cleaned[field] = _validate_date(payload[field], field, errors)

    if "url" in cleaned and cleaned["url"]:
        parsed = urlparse(cleaned["url"])
        hostname = parsed.hostname or ""
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or not hostname
            or any(character.isspace() for character in cleaned["url"])
        ):
            errors["url"] = "Enter a complete http:// or https:// URL."

    minimum = cleaned.get("salary_min")
    maximum = cleaned.get("salary_max")
    if minimum is not None and maximum is not None and maximum < minimum:
        errors["salary_max"] = "Maximum salary cannot be below minimum salary."

    applied_date = cleaned.get("applied_date")
    next_action_date = cleaned.get("next_action_date")
    if applied_date and next_action_date and next_action_date < applied_date:
        errors["next_action_date"] = "Next action cannot be before the applied date."

    if errors:
        raise ValidationError(errors)
    return cleaned


def validate_event(payload: Any, *, partial: bool = False) -> dict[str, Any]:
    """Validate one application timeline event."""

    if not isinstance(payload, dict):
        raise ValidationError({"body": "Expected a JSON object."})
    allowed = {
        "event_type", "title", "details", "occurred_at", "from_stage", "to_stage",
        "origin", "payload_json", "request_id",
    }
    errors: dict[str, str] = {}
    unknown = set(payload) - allowed
    if unknown:
        errors["body"] = f"Unknown fields: {', '.join(sorted(unknown))}."

    event_type = _text(payload.get("event_type"))
    title = _text(payload.get("title"))
    details = _text(payload.get("details"))
    occurred_at = _text(payload.get("occurred_at"))
    if not partial or "event_type" in payload:
        if event_type not in EVENT_TYPES:
            errors["event_type"] = f"Choose one of: {', '.join(EVENT_TYPES)}."
    if not partial or "title" in payload:
        if not title:
            errors["title"] = "This field is required."
        elif len(title) > 160:
            errors["title"] = "Must be 160 characters or fewer."
    if "details" in payload and len(details) > 4_000:
        errors["details"] = "Must be 4000 characters or fewer."
    if occurred_at:
        try:
            datetime.fromisoformat(occurred_at.replace("Z", "+00:00"))
        except ValueError:
            errors["occurred_at"] = "Use an ISO date-time value."
    from_stage = _text(payload.get("from_stage")) or None
    to_stage = _text(payload.get("to_stage")) or None
    if from_stage is not None and from_stage not in STAGES:
        errors["from_stage"] = f"Choose one of: {', '.join(STAGES)}."
    if to_stage is not None and to_stage not in STAGES:
        errors["to_stage"] = f"Choose one of: {', '.join(STAGES)}."
    origin = _text(payload.get("origin")) or "user"
    if origin not in EVENT_ORIGINS:
        errors["origin"] = f"Choose one of: {', '.join(EVENT_ORIGINS)}."
    request_id = payload.get("request_id")
    if request_id is not None:
        request_id = _text(request_id)
        if not request_id:
            errors["request_id"] = "Request ID cannot be empty."
        elif len(request_id) > 128:
            errors["request_id"] = "Request ID must be 128 characters or fewer."
    payload_json = payload.get("payload_json", "{}")
    if isinstance(payload_json, dict):
        payload_json = payload_json
    elif isinstance(payload_json, str):
        try:
            parsed = json.loads(payload_json)
            if not isinstance(parsed, dict):
                raise ValueError
            payload_json = parsed
        except (TypeError, ValueError, json.JSONDecodeError):
            errors["payload_json"] = "Payload must be a JSON object."
            payload_json = {}
    else:
        errors["payload_json"] = "Payload must be a JSON object."
        payload_json = {}
    if errors:
        raise ValidationError(errors)
    return {
        "event_type": event_type,
        "title": title,
        "details": details,
        "occurred_at": occurred_at or None,
        "from_stage": from_stage,
        "to_stage": to_stage,
        "origin": origin,
        "payload_json": payload_json,
        "request_id": request_id,
    }
