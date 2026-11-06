"""Validators for Indian identifiers and hospital branding.

GSTIN: only the regular-taxpayer form (state code + PAN + entity number + "Z" + check character),
which is what a hospital holds. TDS/TCS deductor GSTINs (TAN-based) and the 96/99 special codes
are out of scope and are refused.
"""

import re

from django.core.exceptions import ValidationError

from ihr_tenant_settings.india import GST_STATE_CODES

GSTIN_RE = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")
PIN_RE = re.compile(r"^[1-9][0-9]{5}$")
COLOUR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
_BASE36 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"

MAX_LOGO_BYTES = 256 * 1024
# The logo's type is read from its first bytes, never from a file name or a client header.
# SVG is refused: it can carry scripts.
_LOGO_SIGNATURES = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
)


def gstin_check_character(first_14):
    """The 15th GSTIN character: base-36 checksum over the first 14 (weights 1, 2, 1, 2, ...)."""
    total = 0
    for index, char in enumerate(first_14):
        product = _BASE36.index(char) * (2 if index % 2 else 1)
        total += product // 36 + product % 36
    return _BASE36[(36 - total % 36) % 36]


def validate_gstin(value):
    if not GSTIN_RE.match(value or ""):
        msg = "GSTIN must be 15 characters: state code, PAN, entity number, Z, check character."
        raise ValidationError(msg)
    if value[:2] not in GST_STATE_CODES:
        msg = f"GSTIN state code {value[:2]} is not a GST state or territory code."
        raise ValidationError(msg)
    if gstin_check_character(value[:14]) != value[14]:
        msg = "GSTIN check character does not match: please re-check the number."
        raise ValidationError(msg)


def validate_pin_code(value):
    if not PIN_RE.match(value or ""):
        msg = "PIN code must be 6 digits and cannot start with 0."
        raise ValidationError(msg)


def validate_colour(value):
    if not COLOUR_RE.match(value or ""):
        msg = "Colour must be a hex value such as #0f766e."
        raise ValidationError(msg)


def logo_content_type(data):
    """Content type of a logo from its bytes, or ValidationError if not an allowed image."""
    if len(data) > MAX_LOGO_BYTES:
        msg = f"Logo must be at most {MAX_LOGO_BYTES // 1024} KB."
        raise ValidationError(msg)
    for signature, content_type in _LOGO_SIGNATURES:
        if data.startswith(signature):
            return content_type
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    msg = "Logo must be a PNG, JPEG or WebP image (SVG is not accepted)."
    raise ValidationError(msg)
