"""
One place for phone-number handling: parse, normalize to E.164 (+<country><number>),
validate, and find accounts that already use a number.

Uses Google's libphonenumber rules (the `phonenumbers` package), so every country
is handled the same way -- India (+91), Ireland (+353), everywhere else.

Two levels of strictness:
  * normalize_phone(raw)               lenient -- for login/OTP. Accepts any number
                                       whose length is possible for its country, so a
                                       student is never locked out by number-range
                                       metadata. Behaves like the previous normalizer
                                       for numbers sent with "+".
  * normalize_phone(raw, strict=True)  for data entered by admins / profiles: the
                                       number must be a valid number for its country.

Numbers typed without a country code are read as Indian (DEFAULT_REGION), the
primary user base; numbers with "+" or "00" use their own country code.

Stored numbers are never rewritten here; lookups compare normalized forms so an
account saved as "+91 98765 43210" is still found for "+919876543210".
"""
import re

import phonenumbers
from phonenumbers import NumberParseException, PhoneNumberFormat

DEFAULT_REGION = "IN"
# E.164: at most 15 digits after "+"; the shortest real numbers have 7.
MIN_DIGITS, MAX_DIGITS = 7, 15
# Spaces, dashes, dots, brackets and slashes are formatting; anything else is not a phone number.
_ALLOWED = re.compile(r"^[0-9 \t+\-().\/]+$")  # ASCII digits only (no other scripts)


def _parse(text, region):
    try:
        return phonenumbers.parse(text, region)
    except NumberParseException:
        return None


def _e164(number):
    return phonenumbers.format_number(number, PhoneNumberFormat.E164)


def normalize_phone(raw, *, strict=False, default_region=DEFAULT_REGION):
    """
    Returns the E.164 form ("+353871234567") or "" when the input is not a usable
    phone number. Never raises.
    """
    if raw is None:
        return ""
    text = str(raw).strip()
    if not text or not _ALLOWED.match(text):
        return ""
    if text.startswith("+00"):
        text = "+" + text[3:]
    elif text.startswith("00"):
        text = "+" + text[2:]
    digits = re.sub(r"[^0-9]", "", text)
    if not (MIN_DIGITS <= len(digits) <= MAX_DIGITS + 1):  # +1: a trunk "0" may still be inside
        return ""

    if text.startswith("+"):
        candidates = [_parse(text, None)]
    else:
        # No country code: first as a number of the default country (e.g. 9876543210
        # -> +91...), then as a full international number typed without "+"
        # (e.g. 353871234567 -> +353...).
        candidates = [_parse(text, default_region), _parse("+" + digits, None)]
    candidates = [c for c in candidates if c is not None]

    for number in candidates:
        if phonenumbers.is_valid_number(number):
            return _checked(number)
    if strict:
        return ""
    # Lenient: a length-plausible number. For input without "+", prefer the
    # international reading -- what the previous normalizer did ("+" + digits).
    for number in reversed(candidates) if not text.startswith("+") else candidates:
        if phonenumbers.is_possible_number(number):
            return _checked(number)
    return ""


def _checked(number):
    e164 = _e164(number)
    return e164 if MIN_DIGITS <= len(e164) - 1 <= MAX_DIGITS else ""


def phone_region(e164):
    """ISO region code ("IN", "IE", ...) of a normalized number, or ""."""
    number = _parse(e164, None) if e164 else None
    return phonenumbers.region_code_for_number(number) if number else ""


def interakt_recipient(e164):
    """
    Recipient fields for Interakt's message API.
      * Indian numbers: {"fullPhoneNumber": "919876543210"} -- exactly what production has
        always sent (proven to work); unchanged on purpose.
      * Every other country: Interakt's documented form, {"countryCode": "+353",
        "phoneNumber": "871234567"} (national number without country code or leading 0),
        so Interakt never has to guess where the country code ends.
    """
    number = _parse(e164, None)
    if number is None or number.country_code == 91:
        return {"fullPhoneNumber": (e164 or "").lstrip("+")}
    return {
        "countryCode": f"+{number.country_code}",
        "phoneNumber": phonenumbers.national_significant_number(number),
    }


def same_phone_users(e164, queryset=None, exclude_pk=None, explicit_only=False):
    """
    Users whose stored phone_number is the same number as `e164`, whatever format it
    was stored in (spaces, dashes, missing "+"...). Read-only.

    explicit_only=True (used for logging in): only stored values that carry their own
    country code ("+..." or "00...") count -- a value like "9876543210" is ambiguous
    and is never matched to an identity.
    """
    from django.contrib.auth import get_user_model

    if not e164:
        return []
    qs = queryset if queryset is not None else get_user_model().objects.all()
    if exclude_pk is not None:
        qs = qs.exclude(pk=exclude_pk)
    tail = re.sub(r"\D", "", e164)[-7:]
    # Cheap pre-filter on the last digits, then an exact comparison of normalized forms.
    candidates = qs.filter(phone_number__isnull=False).filter(
        phone_number__regex=r"[\s\-().]*".join(tail) + r"\D*$"
    )
    matches = [u for u in candidates if normalize_phone(u.phone_number) == e164]
    if explicit_only:
        matches = [u for u in matches if (u.phone_number or "").strip().startswith(("+", "00"))]
    return matches
