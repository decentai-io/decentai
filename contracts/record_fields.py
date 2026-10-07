"""What is written into a record, held to the fields declared for it
(docs/agents/manifest.md, "Fields").

A resource declares its fields: a name, a type, where each is kept
(``keys`` in plain sight, ``values`` encrypted), whether it is
required, and for a ``select`` its options. A write is a map of field
name to value, and this is what checks one against the other and
splits it into the two halves the data layer keeps:

    keys, values = RecordFields.split(fields_declared, written)
    keys, values = RecordFields.split(fields_declared, written, partial=True)

It raises ``ValueError`` with a sentence for whoever wrote: a field
that was not declared, a value that is not its field's type, a
``select`` outside its options, a required field left out. ``partial``
is an update: only what is written is checked, and nothing is missed.

Shared by the two places a record is written from, so that the rule
is one rule: the backend, for what a person writes on a page and for
a credential, and the runtime, for what an agent's function writes.

Nothing given, by an agent, is nothing kept: ``None`` is how code says
a field has no value, where a form leaves a box empty. It is kept as
nothing and is not a value of the wrong type — and it does not satisfy
``required``, on a create or on an update. A person's write is not given this: a form that sends
nothing where text belongs is a form to fix.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Tuple


class RecordFields:
    """One resource's declared fields, and what a write must be."""

    @staticmethod
    def declared(resource: Dict[str, Any]) -> List[Dict[str, Any]]:
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
    def split(cls, declared: Iterable[Dict[str, Any]], fields: Any,
              partial: bool = False, nothing_is_nothing: bool = False,
              ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """Check ``fields`` against what was declared and route them
        into (keys, values). ``nothing_is_nothing`` is an agent's
        write: ``None`` is kept as no value and not checked for
        type."""
        if fields is None:
            fields = {}
        if not isinstance(fields, dict):
            raise ValueError("Fields must be an object of name/value pairs.")

        spec = {field["name"]: field for field in declared or []}

        unknown = [name for name in fields if name not in spec]
        if unknown:
            raise ValueError(
                f"Unknown fields: {', '.join(sorted(map(str, unknown)))}.")

        keys: Dict[str, Any] = {}
        values: Dict[str, Any] = {}
        for name, value in fields.items():
            if value is None and nothing_is_nothing:
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
    def provided(field: Dict[str, Any], value: Any) -> bool:
        if value is None:
            return False
        if field["type"] in ("string", "secret", "select") and str(value).strip() == "":
            return False
        return True

    @staticmethod
    def cleaned(field: Dict[str, Any], value: Any) -> Any:
        name, field_type = field["label"], field["type"]

        if field_type in ("string", "secret"):
            if not isinstance(value, str):
                raise ValueError(f"{name} must be text.")
            return value.strip() if field_type == "string" else value
        if field_type == "number":
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{name} must be a number.")
            return value
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
