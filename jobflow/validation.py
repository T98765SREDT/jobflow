"""Input validation for JobFlow's JSON API."""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlparse


STATUSES = ("Wishlist", "Applied", "Interview", "Offer", "Rejected")
WORK_MODES = ("Remote", "Hybrid", "On-site")
CURRENCIES = ("USD", "EUR", "JPY", "GBP", "CNY")
SALARY_PERIODS = ("Hourly", "Monthly", "Annual")

ALLOWED_FIELDS = {
    "company",
    "role",
    "location",
    "work_mode",
    "status",
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
    required = ("company", "role", "status", "work_mode")
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
        for field in ("salary_min", "salary_max", "applied_date", "next_action_date"):
            cleaned.setdefault(field, None)

    if "status" in cleaned and cleaned["status"] not in STATUSES:
        errors["status"] = f"Choose one of: {', '.join(STATUSES)}."
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

    for field in ("applied_date", "next_action_date"):
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
