"""A deliberately small JSON-Schema subset validator (no dependency). Model output is never trusted."""
from __future__ import annotations

import re
from typing import Any, Dict, List, Sequence

_TYPES = {
    "string": lambda v: isinstance(v, str),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "array": lambda v: isinstance(v, list),
    "object": lambda v: isinstance(v, dict),
}


def validate(schema: Dict[str, Any], value: Any, path: str = "arguments") -> List[str]:
    t = schema.get("type")
    if t is not None and not _TYPES[t](value):
        return [f"{path}: expected {t}, got {type(value).__name__}"]
    errors: List[str] = []
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: must be one of {schema['enum']}")
    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            errors.append(f"{path}: shorter than {schema['minLength']} characters")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            errors.append(f"{path}: longer than {schema['maxLength']} characters")
        if "pattern" in schema and not re.search(schema["pattern"], value):
            errors.append(f"{path}: does not match the required pattern")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{path}: must be >= {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{path}: must be <= {schema['maximum']}")
    if isinstance(value, list):
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            errors.append(f"{path}: more than {schema['maxItems']} items")
        if "items" in schema:
            for idx, item in enumerate(value):
                errors += validate(schema["items"], item, f"{path}[{idx}]")
    if isinstance(value, dict):
        props = schema.get("properties", {})
        for req in schema.get("required", []):
            if req not in value:
                errors.append(f"{path}.{req}: required")
        if schema.get("additionalProperties") is False:
            for key in value:
                if key not in props:
                    errors.append(f"{path}.{key}: unknown field")
        for key, sub in props.items():
            if key in value:
                errors += validate(sub, value[key], f"{path}.{key}")
    return errors


def apply_defaults(schema: Dict[str, Any], value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    out = dict(value)
    for key, sub in schema.get("properties", {}).items():
        if key not in out and "default" in sub:
            out[key] = sub["default"]
    return out


# --- builders -----------------------------------------------------------------------------------
def obj(properties: Dict[str, Any] = None, required: Sequence[str] = ()) -> Dict[str, Any]:
    return {"type": "object", "properties": properties or {}, "required": list(required), "additionalProperties": False}


def S(description: str, **kw: Any) -> Dict[str, Any]:
    return {"type": "string", "description": description, **kw}


def I(description: str, **kw: Any) -> Dict[str, Any]:
    return {"type": "integer", "description": description, **kw}


def N(description: str, **kw: Any) -> Dict[str, Any]:
    return {"type": "number", "description": description, **kw}


def B(description: str, **kw: Any) -> Dict[str, Any]:
    return {"type": "boolean", "description": description, **kw}
