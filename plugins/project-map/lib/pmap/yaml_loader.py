"""Zero-dependency YAML loader and PMF v0 JSON Schema validator."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from pmap.models import DesignMap


def _strip_yaml_comment(line: str) -> str:
    """Remove inline YAML comments (# ...) outside of single or double quotes."""
    in_single = False
    in_double = False
    for idx, ch in enumerate(line):
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif ch == "#" and not in_single and not in_double:
            if idx == 0 or line[idx - 1].isspace():
                return line[:idx].rstrip()
    return line.rstrip()


def _parse_scalar(val: str) -> Any:
    """Parse a YAML scalar value (string, number, boolean, null, or inline list/dict)."""
    val = val.strip()
    if not val:
        return ""
    if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
        return val[1:-1]
    if val in ("true", "True"):
        return True
    if val in ("false", "False"):
        return False
    if val in ("null", "Null", "~"):
        return None
    if val.startswith("[") and val.endswith("]"):
        inner = val[1:-1].strip()
        if not inner:
            return []
        return [_parse_scalar(part) for part in inner.split(",")]
    if re.fullmatch(r"-?\d+", val):
        return int(val)
    if re.fullmatch(r"-?\d+\.\d+", val):
        return float(val)
    return val


def parse_simple_yaml(text: str) -> dict[str, Any]:
    """Parse a clean YAML subset (mappings, nested mappings, lists of mappings/scalars) using only Python stdlib."""
    stripped = text.strip()
    if stripped.startswith("{"):
        return json.loads(stripped)

    raw_lines: list[tuple[int, str]] = []
    for raw in text.splitlines():
        cleaned = _strip_yaml_comment(raw)
        if not cleaned.strip():
            continue
        indent = len(cleaned) - len(cleaned.lstrip(" "))
        raw_lines.append((indent, cleaned.strip()))

    def parse_block(index: int, min_indent: int) -> tuple[Any, int]:
        if index >= len(raw_lines):
            return {}, index

        first_indent, first_content = raw_lines[index]
        if first_indent < min_indent:
            return {}, index

        is_list = first_content.startswith("- ") or first_content == "-"
        if is_list:
            items: list[Any] = []
            while index < len(raw_lines):
                indent, content = raw_lines[index]
                if indent < first_indent:
                    break
                if indent != first_indent or not (content.startswith("- ") or content == "-"):
                    break
                after_dash = content[1:].strip()
                index += 1
                if not after_dash:
                    sub_val, index = parse_block(index, first_indent + 1)
                    items.append(sub_val)
                elif ":" in after_dash and not (after_dash.startswith('"') or after_dash.startswith("'")):
                    # List item is a dict starting on the same line as '-'
                    k, _, v_str = after_dash.partition(":")
                    item_dict: dict[str, Any] = {}
                    v_str = v_str.strip()
                    if v_str:
                        item_dict[k.strip()] = _parse_scalar(v_str)
                    else:
                        sub_val, index = parse_block(index, first_indent + 1)
                        item_dict[k.strip()] = sub_val

                    # Consume subsequent keys belonging to this list item's mapping
                    while index < len(raw_lines):
                        sub_indent, sub_content = raw_lines[index]
                        if sub_indent <= first_indent:
                            break
                        if sub_content.startswith("- ") or sub_content == "-":
                            break
                        sk, _, sv_str = sub_content.partition(":")
                        sk = sk.strip()
                        sv_str = sv_str.strip()
                        index += 1
                        if sv_str:
                            item_dict[sk] = _parse_scalar(sv_str)
                        else:
                            nested_val, index = parse_block(index, sub_indent + 1)
                            item_dict[sk] = nested_val
                    items.append(item_dict)
                else:
                    items.append(_parse_scalar(after_dash))
            return items, index

        mapping: dict[str, Any] = {}
        while index < len(raw_lines):
            indent, content = raw_lines[index]
            if indent < first_indent:
                break
            if content.startswith("- ") or content == "-":
                break
            key, sep, val_str = content.partition(":")
            if not sep:
                index += 1
                continue
            key = key.strip()
            val_str = val_str.strip()
            index += 1
            if val_str:
                mapping[key] = _parse_scalar(val_str)
            else:
                nested_val, index = parse_block(index, indent + 1)
                mapping[key] = nested_val
        return mapping, index

    result, _ = parse_block(0, 0)
    if not isinstance(result, dict):
        raise ValueError("Top-level YAML document must be a mapping")
    return result


def load_design_map(path: str | Path) -> DesignMap:
    """Load a DesignMap from a YAML or JSON file."""
    text = Path(path).read_text(encoding="utf-8")
    data = parse_simple_yaml(text)
    errors = validate_pmf_document(data)
    if errors:
        raise ValueError(f"Invalid design map in {path}: {'; '.join(errors)}")
    return DesignMap.from_dict(data)


def validate_pmf_document(doc: dict[str, Any], schema_path: str | Path | None = None) -> list[str]:
    """Validate a PMF v0 dictionary against the PMF v0 specification rules (and schema file if provided).

    Returns a list of human-readable validation error strings (empty list if valid).
    """
    errors: list[str] = []
    if not isinstance(doc, dict):
        return ["Root document must be a JSON object"]

    if schema_path is not None:
        # Full JSON Schema validation when the optional `jsonschema` package is installed;
        # the structural checks below mirror the schema and always run.
        schema_obj = json.loads(Path(schema_path).read_text(encoding="utf-8"))
        try:
            import jsonschema  # type: ignore[import-not-found]
        except ImportError:
            jsonschema = None
        if jsonschema is not None:
            validator_cls = jsonschema.validators.validator_for(schema_obj)
            for err in validator_cls(schema_obj).iter_errors(doc):
                where = "/".join(str(p) for p in err.absolute_path)
                errors.append(f"schema: {err.message} (at /{where})")

    pmf_version = doc.get("pmf_version")
    if not isinstance(pmf_version, str) or not re.fullmatch(r"0\.\d+\.\d+", pmf_version):
        errors.append(f"Invalid or missing 'pmf_version': {pmf_version!r}")

    kind = doc.get("kind")
    if kind not in ("design", "actual", "combined"):
        errors.append(f"Invalid 'kind': {kind!r} (must be 'design', 'actual', or 'combined')")
        return errors

    if kind in ("design", "combined"):
        project = doc.get("project")
        if not isinstance(project, dict) or not isinstance(project.get("name"), str) or not project["name"].strip():
            errors.append("'project.name' is required for design maps")

        subsystems = doc.get("subsystems")
        if not isinstance(subsystems, list):
            errors.append("'subsystems' must be a list")
        else:
            seen_ids: set[str] = set()
            for idx, sub in enumerate(subsystems):
                if not isinstance(sub, dict):
                    errors.append(f"subsystems[{idx}] must be an object")
                    continue
                sid = sub.get("id")
                if not isinstance(sid, str) or not re.fullmatch(r"[a-zA-Z0-9_-]+", sid):
                    errors.append(f"subsystems[{idx}].id is invalid: {sid!r}")
                elif sid in seen_ids:
                    errors.append(f"Duplicate subsystem id: {sid!r}")
                else:
                    seen_ids.add(sid)
                paths = sub.get("paths")
                if not isinstance(paths, list) or not paths or not all(isinstance(p, str) and p for p in paths):
                    errors.append(f"subsystems[{idx}].paths must be a non-empty list of glob strings")

        allowed = doc.get("allowed_dependencies")
        if not isinstance(allowed, list):
            errors.append("'allowed_dependencies' must be a list")
        else:
            for idx, dep in enumerate(allowed):
                if not isinstance(dep, dict):
                    errors.append(f"allowed_dependencies[{idx}] must be an object")
                    continue
                if not isinstance(dep.get("from"), str) or not dep.get("from"):
                    errors.append(f"allowed_dependencies[{idx}].from must be a non-empty string")
                if not isinstance(dep.get("to"), str) or not dep.get("to"):
                    errors.append(f"allowed_dependencies[{idx}].to must be a non-empty string")

    if kind in ("actual", "combined"):
        gen_at = doc.get("generated_at")
        if not isinstance(gen_at, str) or not gen_at:
            errors.append("'generated_at' is required for actual maps")

        scanner = doc.get("scanner")
        if not isinstance(scanner, dict) or not scanner.get("name") or not scanner.get("version"):
            errors.append("'scanner.name' and 'scanner.version' are required for actual maps")

        roots = doc.get("import_roots", ["."])
        if not isinstance(roots, list) or not all(isinstance(r, str) and r for r in roots):
            errors.append("'import_roots' must be a list of non-empty strings")

        scan_errors = doc.get("errors", [])
        if not isinstance(scan_errors, list):
            errors.append("'errors' must be a list")
        else:
            for idx, err in enumerate(scan_errors):
                if not isinstance(err, dict) or not isinstance(err.get("path"), str) or not err.get("path"):
                    errors.append(f"errors[{idx}].path must be a non-empty string")
                elif err.get("kind") not in ("syntax_error", "decode_error", "duplicate_module"):
                    errors.append(f"errors[{idx}].kind invalid: {err.get('kind')!r}")

        nodes = doc.get("nodes")
        if not isinstance(nodes, list):
            errors.append("'nodes' must be a list")
        else:
            for idx, node in enumerate(nodes):
                if not isinstance(node, dict):
                    errors.append(f"nodes[{idx}] must be an object")
                    continue
                for req_field in ("id", "kind", "name", "qualified_name"):
                    if not isinstance(node.get(req_field), str) or not node.get(req_field):
                        errors.append(f"nodes[{idx}].{req_field} must be a non-empty string")
                if node.get("kind") not in ("module", "class", "function", "method"):
                    errors.append(f"nodes[{idx}].kind invalid: {node.get('kind')!r}")
                ext = node.get("external")
                if not isinstance(ext, bool):
                    errors.append(f"nodes[{idx}].external must be a boolean")
                loc = node.get("location")
                if ext is False:
                    if not isinstance(loc, dict):
                        errors.append(f"nodes[{idx}].location is required for internal nodes")
                    else:
                        if not isinstance(loc.get("path"), str) or not loc.get("path"):
                            errors.append(f"nodes[{idx}].location.path must be a non-empty string")
                        if not isinstance(loc.get("line_start"), int) or loc.get("line_start", 0) < 1:
                            errors.append(f"nodes[{idx}].location.line_start must be >= 1")
                        if not isinstance(loc.get("line_end"), int) or loc.get("line_end", 0) < 1:
                            errors.append(f"nodes[{idx}].location.line_end must be >= 1")
                        fhash = loc.get("file_hash")
                        if not isinstance(fhash, str) or not re.fullmatch(r"sha256:[a-f0-9]{64}", fhash):
                            errors.append(f"nodes[{idx}].location.file_hash invalid: {fhash!r}")

        edges = doc.get("edges")
        if not isinstance(edges, list):
            errors.append("'edges' must be a list")
        else:
            for idx, edge in enumerate(edges):
                if not isinstance(edge, dict):
                    errors.append(f"edges[{idx}] must be an object")
                    continue
                for req_field in ("from", "to", "kind", "confidence", "source"):
                    if not isinstance(edge.get(req_field), str) or not edge.get(req_field):
                        errors.append(f"edges[{idx}].{req_field} must be a non-empty string")
                if edge.get("kind") not in ("contains", "imports", "calls", "inherits"):
                    errors.append(f"edges[{idx}].kind invalid: {edge.get('kind')!r}")
                if edge.get("confidence") not in ("resolved", "inferred", "unresolved"):
                    errors.append(f"edges[{idx}].confidence invalid: {edge.get('confidence')!r}")
                cands = edge.get("candidates")
                if cands is not None and (
                    not isinstance(cands, list) or not cands or not all(isinstance(c, str) and c for c in cands)
                ):
                    errors.append(f"edges[{idx}].candidates must be a non-empty list of node ids")
                if "type_only" in edge and not isinstance(edge["type_only"], bool):
                    errors.append(f"edges[{idx}].type_only must be a boolean")
                if edge.get("confidence") == "resolved" and str(edge.get("to", "")).startswith(
                    ("unresolved:", "ambiguous:")
                ):
                    errors.append(f"edges[{idx}] is resolved but points to a placeholder target {edge.get('to')!r}")

    return errors
