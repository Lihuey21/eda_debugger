from __future__ import annotations

import ast
import json
from typing import Any, Dict, Optional


def _maybe_parse_string(text: Any) -> Any:
    if not isinstance(text, str):
        return text

    s = text.strip()
    if not s:
        return text

    try:
        return json.loads(s)
    except Exception:
        pass

    try:
        return ast.literal_eval(s)
    except Exception:
        pass

    start = s.find("{")
    end = s.rfind("}")

    if start != -1 and end != -1 and end > start:
        candidate = s[start:end + 1]

        try:
            return json.loads(candidate)
        except Exception:
            pass

        try:
            return ast.literal_eval(candidate)
        except Exception:
            pass

    return text


def _safe_json_loads(value: Any) -> Any:
    return _maybe_parse_string(value)


def _payload_score(obj: Any) -> int:
    if not isinstance(obj, dict):
        return -1

    score = 0

    if "diagnosis_result" in obj:
        score += 100
    if "diagnosis_evidence" in obj:
        score += 90
    if "analysis_result" in obj:
        score += 40
    if "original_tcl" in obj:
        score += 20
    if "original_log" in obj:
        score += 10
    if "agent_stage" in obj:
        score += 5

    return score


def _deep_find_payload(
    obj: Any,
    depth: int = 0,
    max_depth: int = 12,
) -> Optional[Dict[str, Any]]:
    if depth > max_depth:
        return None

    obj = _maybe_parse_string(obj)

    best: Optional[Dict[str, Any]] = None
    best_score = -1

    def consider(candidate: Any) -> None:
        nonlocal best, best_score

        candidate = _maybe_parse_string(candidate)

        if isinstance(candidate, dict):
            score = _payload_score(candidate)

            if score > best_score:
                best = candidate
                best_score = score

    consider(obj)

    if isinstance(obj, dict):
        for key in [
            "result",
            "payload",
            "diagnosis_payload",
            "analysis_payload",
            "data",
            "content",
            "text",
        ]:
            if key in obj:
                found = _deep_find_payload(obj[key], depth + 1, max_depth)

                if found:
                    merged = dict(found)

                    for keep in [
                        "original_tcl",
                        "original_log",
                        "analysis_result",
                        "diagnosis_result",
                        "diagnosis_evidence",
                        "agent_stage",
                    ]:
                        if keep in obj and keep not in merged:
                            merged[keep] = obj[keep]

                    consider(merged)

        for value in obj.values():
            found = _deep_find_payload(value, depth + 1, max_depth)

            if found:
                merged = dict(found)

                for keep in [
                    "original_tcl",
                    "original_log",
                    "analysis_result",
                    "diagnosis_result",
                    "diagnosis_evidence",
                    "agent_stage",
                ]:
                    if keep in obj and keep not in merged:
                        merged[keep] = obj[keep]

                consider(merged)

    elif isinstance(obj, (list, tuple)):
        for item in obj:
            found = _deep_find_payload(item, depth + 1, max_depth)

            if found:
                consider(found)

    return best


def _extract_outer_payload(payload: Any) -> Dict[str, Any]:
    found = _deep_find_payload(payload)
    return found if isinstance(found, dict) else {}


def _clean_tcl_code(code: str) -> str:
    cleaned = code.strip()

    if cleaned.startswith("```tcl"):
        cleaned = cleaned.replace("```tcl", "", 1).strip()
    elif cleaned.startswith("```"):
        cleaned = cleaned.replace("```", "", 1).strip()

    if cleaned.endswith("```"):
        cleaned = cleaned[:-3].strip()

    return cleaned


def attempt_tcl_patch(
    payload: str = "",
    patched_tcl: str = "",
    manual_response: str = "",
    fix_status: str = "",
    explanation: str = "",
) -> str:
    """
    Packaging tool for the LLM-generated fix.

    This tool does NOT contain hardcoded repair logic.

    It only packages:
    - the LLM-generated patched Tcl, or
    - the LLM-generated manual guidance.

    RAG/Neo4j context is used upstream by the diagnosis and fixer LLM,
    but raw graph notes are not exposed in the final user-facing JSON.

    IMPORTANT:
    payload is typed as str, not Any, because ADK crashes when a tool
    parameter is annotated as Any with a default value.
    """
    outer = _extract_outer_payload(payload)

    original_tcl = outer.get("original_tcl", "") if isinstance(outer, dict) else ""

    diagnosis_result = _safe_json_loads(outer.get("diagnosis_result", {})) if isinstance(outer, dict) else {}
    diagnosis_evidence = _safe_json_loads(outer.get("diagnosis_evidence", {})) if isinstance(outer, dict) else {}

    issue_type = None
    recommended_fix_pattern = None

    if isinstance(diagnosis_result, dict):
        issue_type = diagnosis_result.get("issue_type")
        recommended_fix_pattern = diagnosis_result.get("recommended_fix_pattern")

    if not issue_type and isinstance(diagnosis_evidence, dict):
        graph_context = diagnosis_evidence.get("graph_context", [])

        if isinstance(graph_context, list) and graph_context:
            first = graph_context[0]

            if isinstance(first, dict):
                issue_types = first.get("issue_types", [])
                fix_patterns = first.get("fix_patterns", [])

                if issue_types:
                    issue_type = issue_types[0]

                if fix_patterns:
                    recommended_fix_pattern = fix_patterns[0]

    if patched_tcl and patched_tcl.strip():
        clean_code = _clean_tcl_code(patched_tcl)

        result = {
            "fix_status": fix_status or "auto_fixed",
            "issue_type": issue_type or "unknown_issue",
            "patched_tcl": clean_code,
            "applied_fix_pattern": recommended_fix_pattern or "llm_rag_generated_fix",
            "changes_applied": [
                "The Tcl script was generated by the LLM using the diagnosis payload and retrieved context."
            ],
            "explanation": (
                explanation
                or "The script-level fix was generated from the diagnosis context and the original Tcl/log evidence."
            ),
        }

        return json.dumps(result, indent=2)

    if manual_response and manual_response.strip():
        result = {
            "fix_status": fix_status or "manual_fix_required",
            "issue_type": issue_type or "unknown_issue",
            "patched_tcl": None,
            "applied_fix_pattern": recommended_fix_pattern,
            "changes_applied": [],
            "explanation": manual_response.strip(),
        }

        return json.dumps(result, indent=2)

    if isinstance(diagnosis_result, dict) and diagnosis_result.get("issue_type") == "clean_no_issue":
        result = {
            "fix_status": "no_fix_needed",
            "issue_type": "clean_no_issue",
            "patched_tcl": original_tcl,
            "applied_fix_pattern": None,
            "changes_applied": [],
            "explanation": "No issue was detected, so no Tcl patch was needed.",
        }

        return json.dumps(result, indent=2)

    result = {
        "fix_status": fix_status or "manual_fix_required",
        "issue_type": issue_type or "unknown_issue",
        "patched_tcl": None,
        "applied_fix_pattern": recommended_fix_pattern,
        "changes_applied": [],
        "explanation": (
            explanation
            or "The issue was diagnosed, but no safe patched Tcl was generated. Manual review is required."
        ),
    }

    return json.dumps(result, indent=2)