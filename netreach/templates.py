"""Message templates with {placeholder} and {placeholder|fallback} fields."""

import re

FIELDS = ("first_name", "last_name", "full_name", "company", "position", "headline", "location")
_PLACEHOLDER_RE = re.compile(r"\{(\w+)(?:\|([^}]*))?\}")


class TemplateError(ValueError):
    pass


def check(template: str) -> None:
    unknown = sorted({m.group(1) for m in _PLACEHOLDER_RE.finditer(template)} - set(FIELDS))
    if unknown:
        raise TemplateError(
            f"Unknown placeholder(s): {', '.join(unknown)}. Available: {', '.join(FIELDS)}"
        )


def render(template: str, contact) -> str:
    """Fill a template from a contact row. Blank values use the fallback after `|`."""
    check(template)
    values = {field: (contact[field] or "").strip() for field in FIELDS if field != "full_name"}
    values["full_name"] = f"{values['first_name']} {values['last_name']}".strip()

    def substitute(match):
        return values[match.group(1)] or (match.group(2) or "")

    return _PLACEHOLDER_RE.sub(substitute, template).strip()
