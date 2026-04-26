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

    if "diagnosis_evidence" in obj:
        score += 100
    if "evidence_summary" in obj:
        score += 80
    if "original_tcl" in obj:
        score += 40
    if "agent_stage" in obj:
        score += 10
    if "analysis_result" in obj:
        score += 5
    if "diagnosis_result" in obj:
        score += 5
    if "original_log" in obj:
        score += 3

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
                        "original_log_excerpt",
                        "analysis_result",
                        "diagnosis_result",
                        "diagnosis_evidence",
                        "evidence_summary",
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
                    "original_log_excerpt",
                    "analysis_result",
                    "diagnosis_result",
                    "diagnosis_evidence",
                    "evidence_summary",
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


def _extract_diagnosis_evidence(outer: Dict[str, Any]) -> Dict[str, Any]:
    evidence = outer.get("diagnosis_evidence", {})
    evidence = _safe_json_loads(evidence)

    if isinstance(evidence, dict):
        return evidence

    return {}


def _extract_evidence_summary(outer: Dict[str, Any]) -> Dict[str, Any]:
    evidence = _extract_diagnosis_evidence(outer)

    summary = evidence.get("evidence_summary", {})
    summary = _safe_json_loads(summary)

    if isinstance(summary, dict):
        return summary

    direct = outer.get("evidence_summary", {})
    direct = _safe_json_loads(direct)

    if isinstance(direct, dict):
        return direct

    return {}


def _extract_fixability(outer: Dict[str, Any]) -> str:
    evidence = _extract_diagnosis_evidence(outer)
    summary = _extract_evidence_summary(outer)

    for source in [evidence, summary, outer]:
        if isinstance(source, dict):
            value = source.get("fixability")
            if isinstance(value, str) and value.strip():
                return value.strip()

    return ""


def _extract_fixability_reason(outer: Dict[str, Any]) -> str:
    evidence = _extract_diagnosis_evidence(outer)
    summary = _extract_evidence_summary(outer)

    for source in [evidence, summary, outer]:
        if isinstance(source, dict):
            value = source.get("fixability_reason")
            if isinstance(value, str) and value.strip():
                return value.strip()

            value = source.get("reason")
            if isinstance(value, str) and value.strip():
                return value.strip()

    return ""


def _get_issue_type_and_fix_pattern(outer: Dict[str, Any]) -> tuple[str, Optional[str]]:
    diagnosis_evidence = _safe_json_loads(outer.get("diagnosis_evidence", {}))
    diagnosis_result = _safe_json_loads(outer.get("diagnosis_result", {}))

    issue_type = None
    recommended_fix_pattern = None

    if isinstance(diagnosis_result, dict):
        issue_type = diagnosis_result.get("issue_type")
        recommended_fix_pattern = diagnosis_result.get("recommended_fix_pattern")

    if isinstance(diagnosis_evidence, dict):
        summary = _safe_json_loads(diagnosis_evidence.get("evidence_summary", {}))

        if isinstance(summary, dict):
            strategy = summary.get("recommended_fix_strategy")

            if isinstance(strategy, str) and strategy.strip():
                recommended_fix_pattern = strategy.strip()

        graph_context = diagnosis_evidence.get("graph_context", [])

        if isinstance(graph_context, list) and graph_context:
            first = graph_context[0]

            if isinstance(first, dict):
                issue_types = first.get("issue_types", [])
                fix_patterns = first.get("fix_patterns", [])

                if not issue_type and issue_types:
                    issue_type = issue_types[0]

                if not recommended_fix_pattern and fix_patterns:
                    recommended_fix_pattern = fix_patterns[0]

    return issue_type or "diagnosed_issue", recommended_fix_pattern


def _minimal_missing_content_message(fix_status: str) -> str:
    return (
        "**Explanation**\n\n"
        "The Fixer Agent did not provide enough user-facing explanation text to package a complete response. "
        "Please rerun the request so the agent can generate the required explanation from the diagnostic payload.\n\n"
        "**Fix Status**\n\n"
        f"{fix_status or 'Manual Fix Required'}"
    )


def attempt_tcl_patch(
    payload: str = "",
    patched_tcl: str = "",
    manual_response: str = "",
    fix_status: str = "",
    explanation: str = "",
    fixability: str = "",
    fixability_reason: str = "",
) -> str:
    """
    Packaging + safety gate only.

    The Diagnostic Agent decides fixability.
    The Fixer Agent writes the explanation/manual/auto/partial/no-fix answer.
    This tool does not generate diagnostic explanation content.
    """
    outer = _extract_outer_payload(payload)

    original_tcl = outer.get("original_tcl", "") if isinstance(outer, dict) else ""
    issue_type, recommended_fix_pattern = _get_issue_type_and_fix_pattern(outer)

    payload_fixability = _extract_fixability(outer)
    payload_fixability_reason = _extract_fixability_reason(outer)

    fixability = (fixability or "").strip() or payload_fixability
    fixability_reason = (fixability_reason or "").strip() or payload_fixability_reason

    patched_tcl_clean = _clean_tcl_code(patched_tcl) if patched_tcl and patched_tcl.strip() else ""
    manual_text = manual_response.strip() if manual_response and manual_response.strip() else ""
    explanation_text = explanation.strip() if explanation and explanation.strip() else ""

    print("\n================ FIXER TOOL DEBUG ================")
    print(f"DEBUG: fixability = {fixability}")
    print(f"DEBUG: requested fix_status = {fix_status}")
    print(f"DEBUG: patched_tcl provided = {bool(patched_tcl_clean)}")
    print(f"DEBUG: manual_response provided = {bool(manual_text)}")
    print(f"DEBUG: explanation provided = {bool(explanation_text)}")
    print("==================================================\n")

    # ------------------------------------------------------------
    # Gate 1: manual_required means no patched Tcl is allowed.
    # The tool does not generate the manual explanation.
    # The Fixer Agent must provide manual_response.
    # ------------------------------------------------------------
    if fixability == "manual_required":
        explanation_out = manual_text or explanation_text

        if not explanation_out:
            explanation_out = _minimal_missing_content_message("Manual Fix Required")

        result = {
            "fix_status": "manual_fix_required",
            "issue_type": issue_type,
            "patched_tcl": None,
            "applied_fix_pattern": recommended_fix_pattern,
            "changes_applied": [],
            "explanation": explanation_out,
            "fixability": fixability,
            "fixability_reason": fixability_reason,
        }

        return json.dumps(result, indent=2)

    # ------------------------------------------------------------
    # Gate 2: no_fix_needed should not invent a new patch.
    # ------------------------------------------------------------
    if fixability == "no_fix_needed":
        result = {
            "fix_status": "no_fix_needed",
            "issue_type": "clean_no_issue",
            "patched_tcl": original_tcl,
            "applied_fix_pattern": None,
            "changes_applied": [],
            "explanation": (
                explanation_text
                or "**Explanation**\n\nNo issue was detected, so no Tcl patch was needed.\n\n**Fix Status**\n\nNo Fix Needed"
            ),
            "fixability": fixability,
            "fixability_reason": fixability_reason,
        }

        return json.dumps(result, indent=2)

    # ------------------------------------------------------------
    # Gate 3: partial_fixable must not become auto_fixed.
    # ------------------------------------------------------------
    if fixability == "partial_fixable" and (fix_status or "").strip() == "auto_fixed":
        fix_status = "partial_fix_applied"

    # ------------------------------------------------------------
    # Gate 4: auto_fixable needs an actual patch.
    # ------------------------------------------------------------
    if fixability == "auto_fixable" and not patched_tcl_clean:
        result = {
            "fix_status": "manual_fix_required",
            "issue_type": issue_type,
            "patched_tcl": None,
            "applied_fix_pattern": recommended_fix_pattern,
            "changes_applied": [],
            "explanation": (
                explanation_text
                or manual_text
                or _minimal_missing_content_message("Manual Fix Required")
            ),
            "fixability": fixability,
            "fixability_reason": fixability_reason,
        }

        return json.dumps(result, indent=2)

    # ------------------------------------------------------------
    # Patched Tcl path for auto_fixable / partial_fixable.
    # ------------------------------------------------------------
    if patched_tcl_clean:
        result = {
            "fix_status": fix_status or (
                "partial_fix_applied" if fixability == "partial_fixable" else "auto_fixed"
            ),
            "issue_type": issue_type,
            "patched_tcl": patched_tcl_clean,
            "applied_fix_pattern": recommended_fix_pattern or "llm_generated_fix",
            "changes_applied": [
                "The Tcl script was generated by the Fixer Agent using the diagnostic payload."
            ],
            "explanation": (
                explanation_text
                or _minimal_missing_content_message(fix_status or "Auto Fixed")
            ),
            "fixability": fixability,
            "fixability_reason": fixability_reason,
        }

        return json.dumps(result, indent=2)

    # ------------------------------------------------------------
    # Manual response path.
    # ------------------------------------------------------------
    if manual_text:
        result = {
            "fix_status": fix_status or "manual_fix_required",
            "issue_type": issue_type,
            "patched_tcl": None,
            "applied_fix_pattern": recommended_fix_pattern,
            "changes_applied": [],
            "explanation": manual_text,
            "fixability": fixability,
            "fixability_reason": fixability_reason,
        }

        return json.dumps(result, indent=2)

    # ------------------------------------------------------------
    # Final packaging fallback.
    # No diagnostic explanation is generated here.
    # ------------------------------------------------------------
    result = {
        "fix_status": fix_status or "manual_fix_required",
        "issue_type": issue_type,
        "patched_tcl": None,
        "applied_fix_pattern": recommended_fix_pattern,
        "changes_applied": [],
        "explanation": explanation_text or _minimal_missing_content_message(fix_status or "Manual Fix Required"),
        "fixability": fixability,
        "fixability_reason": fixability_reason,
    }

    return json.dumps(result, indent=2)