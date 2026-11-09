"""A record's write held to its declared fields and split into ``keys``, kept plain, and ``values``, encrypted
(documentation/agents.md, "Fields"). A write that doesn't fit raises ``ValueError`` with a sentence for its
writer."""

from __future__ import annotations

from typing import Any, Iterable


class RecordFields:
    """One resource's declared fields, and what a write must be."""

    @staticmethod
    def declared(resource: dict[str, Any]) -> list[dict[str, Any]]:
        """A manifest's field list as ``split`` reads it: label,
        storage and required filled in where the manifest left them
        to their defaults, options kept."""
        return [
            {
                "name": str(field.get("name") or ""),
                "label": str(field.get("label") or field.get("name") or ""),
                "type": str(field.get("type") or "string"),
                "storage": str(field.get("storage") or "keys"),
                "required": bool(field.get("required")),
                "options": list(field.get("options") or []),
            }
            for field in resource.get("fields") or []
        ]

    @classmethod
    def split(cls, declared: Iterable[dict[str, Any]], fields: Any,
              partial: bool = False, from_agent: bool = False,
              ) -> tuple[dict[str, Any], dict[str, Any]]:
        """(keys, values). ``partial`` is an update: only what is written is checked. ``from_agent``: ``None`` is
        kept as no value, not checked for type, and still doesn't satisfy ``required``. A form's empty box is
        never ``None``."""
        if fields is None:
            fields = {}
        if not isinstance(fields, dict):
            raise ValueError("Fields must be an object of name/value pairs.")

        spec = {field["name"]: field for field in declared or []}

        unknown = [name for name in fields if name not in spec]
        if unknown:
            raise ValueError(
                f"Unknown fields: {', '.join(sorted(map(str, unknown)))}.")

        keys: dict[str, Any] = {}
        values: dict[str, Any] = {}
        for name, value in fields.items():
            if value is None and from_agent:
                cleaned = None
            else:
                cleaned = cls.cleaned(spec[name], value)
            (values if spec[name]["storage"] == "values" else keys)[name] = cleaned

        # An update is held to what it writes: a required field may be
        # left out of one, and may not be written empty by one.
        missing = [
            field["label"] for field in spec.values()
            if field["required"]
            and (field["name"] in fields or not partial)
            and not cls.provided(field, fields.get(field["name"]))
        ]
        if missing:
            raise ValueError(f"Required: {', '.join(missing)}.")

        return keys, values

    @staticmethod
    def provided(field: dict[str, Any], value: Any) -> bool:
        if value is None:
            return False
        if field["type"] in ("string", "secret", "select") and str(value).strip() == "":
            return False
        return True

    @staticmethod
    def cleaned(field: dict[str, Any], value: Any) -> Any:
        name, field_type = field["label"], field["type"]

        if field_type in ("string", "secret"):
            if not isinstance(value, str):
                raise ValueError(f"{name} must be text.")
            return value.strip() if field_type == "string" else value
        if field_type == "number":
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{name} must be a number.")
            return value
        # A credential definition's type, kept by the API server; a
        # manifest's field never declares it.
        if field_type == "boolean":
            if not isinstance(value, bool):
                raise ValueError(f"{name} must be true or false.")
            return value
        if field_type == "select":
            value = str(value or "").strip()
            if value not in field["options"]:
                raise ValueError(
                    f"{name} must be one of: {', '.join(field['options'])}."
                )
            return value
        # A manifest's data resources may declare structured payloads;
        # a credential's form never offers this type.
        if field_type == "object":
            if not isinstance(value, dict):
                raise ValueError(f"{name} must be an object.")
            return value
        raise ValueError(f"{name}: unsupported field type.")
