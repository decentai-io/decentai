"""A manifest and a catalog checked against the standard's schema, and the cross-references a schema can't say
(documentation/agents.md §5, §10)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

KINDS = ("secrets", "data", "files")


class Standard:
    _schema = json.loads((Path(__file__).parent / "standard.schema.json").read_text(encoding="utf-8"))
    _validators: dict[str, Draft202012Validator] = {}

    @classmethod
    def _errors(cls, kind: str, document: Any) -> list[str]:
        if kind not in cls._validators:
            cls._validators[kind] = Draft202012Validator({**cls._schema, "$ref": f"#/$defs/{kind}"})
        return [f"{'.'.join(map(str, error.absolute_path)) or kind}: {error.message}"[:300]
                for error in sorted(cls._validators[kind].iter_errors(document), key=lambda e: list(e.absolute_path))][:20]

    @classmethod
    def catalog_errors(cls, catalog: Any) -> list[str]:
        return cls._errors("catalog", catalog)

    @classmethod
    def manifest_errors(cls, manifest: Any) -> list[str]:
        """The schema's errors, then the cross-references: unique ids, what `calls` names, where scopes come from,
        which secret field a host is read from, and inputs and outputs that are JSON Schemas."""
        errors = cls._errors("manifest", manifest)
        if errors:
            return errors
        resources = {r["id"]: (kind, r) for kind in KINDS for r in (manifest.get("resources") or {}).get(kind) or []}
        if len(resources) != sum(len((manifest.get("resources") or {}).get(kind) or []) for kind in KINDS):
            errors.append("resources: every resource's id is its own, across the three kinds.")
        functions = manifest["functions"]
        if len({f["id"] for f in functions}) != len(functions):
            errors.append("functions: every function's id is its own.")
        scopes = ((manifest.get("authorization") or {}).get("scopes") or {})
        for function in functions:
            where = f"functions.{function['id']}"
            for part in ("inputs", "outputs"):
                try:
                    Draft202012Validator.check_schema(function.get(part) or {})
                except SchemaError as error:
                    errors.append(f"{where}.{part}: not a JSON Schema: {error.message[:200]}")
            for call in function.get("calls") or []:
                if call.startswith("record.") and call.split(":", 1)[1] not in resources:
                    errors.append(f"{where}.calls: {call} names no declared resource.")
            properties = (function.get("inputs") or {}).get("properties") or {}
            for scope, binding in (function.get("authorization") or {}).items():
                if scope not in scopes or binding["from_input"] not in properties:
                    errors.append(f"{where}.authorization.{scope}: a declared scope, from one of its inputs.")
        for kind, resource in resources.values():
            for field in resource.get("fields") or []:
                if field["type"] == "form" and kind != "secrets":
                    errors.append(f"resources.{resource['id']}.{field['name']}: a form field is a secret's.")
                if field["type"] in ("secret", "form", "object") and field.get("storage") == "keys":
                    errors.append(f"resources.{resource['id']}.{field['name']}: kept as values, never as keys.")
        for host in (manifest.get("network") or {}).get("hosts") or []:
            if isinstance(host, dict):
                name, _, field = host["from_secret"].partition(".")
                fields = {f["name"]: f for f in (resources.get(name, ("", {}))[1].get("fields") or [])}
                if resources.get(name, ("",))[0] != "secrets" or fields.get(field, {}).get("storage") != "keys":
                    errors.append(f"network.hosts: {host['from_secret']} is no secret's keys field.")
        return errors[:20]

    @staticmethod
    def resource(manifest: dict[str, Any], resource_id: str) -> tuple[str, dict[str, Any]] | None:
        """(kind, resource) for a resource the manifest declares."""
        for kind in KINDS:
            for resource in (manifest.get("resources") or {}).get(kind) or []:
                if resource["id"] == resource_id:
                    return kind, resource
        return None

    @staticmethod
    def function(manifest: dict[str, Any], function_id: str) -> dict[str, Any] | None:
        return next((f for f in manifest.get("functions") or [] if f["id"] == function_id), None)

    @staticmethod
    def release(entry: dict[str, Any]) -> str:
        """What a version is: its manifest, its programs, its samples and its base, as one digest."""
        content = {"manifest": entry["manifest"], "base": entry.get("base", ""),
                   "programs": {machine: f["digest"] for machine, f in entry["programs"].items()},
                   "samples": (entry.get("samples") or {}).get("digest", "")}
        return "sha256:" + hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()
