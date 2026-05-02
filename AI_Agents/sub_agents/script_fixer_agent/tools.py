from __future__ import annotations

import ast
import json
import re
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



# ------------------------------------------------------------
# RAG constraint helpers
# ------------------------------------------------------------

def _flatten_text(value: Any, max_depth: int = 8) -> str:
    """Convert nested payload fragments into searchable constraint text."""
    pieces: list[str] = []

    def visit(obj: Any, depth: int) -> None:
        if depth > max_depth:
            return

        obj = _maybe_parse_string(obj)

        if isinstance(obj, str):
            if obj.strip():
                pieces.append(obj.strip())
            return

        if isinstance(obj, dict):
            for key, child in obj.items():
                key_text = str(key).lower()
                if key_text in {
                    "retrieved_notes",
                    "recommended_fix_strategy",
                    "fix_pattern",
                    "fix_patterns",
                    "notes",
                    "rule",
                    "rules",
                    "constraint",
                    "constraints",
                    "documentation",
                    "message",
                    "root_cause",
                }:
                    visit(child, depth + 1)
                elif isinstance(child, (dict, list, tuple)):
                    visit(child, depth + 1)
            return

        if isinstance(obj, (list, tuple, set)):
            for item in obj:
                visit(item, depth + 1)

    visit(value, 0)
    return "\n".join(dict.fromkeys(pieces))


def _extract_constraint_text(outer: Dict[str, Any]) -> str:
    evidence = _extract_diagnosis_evidence(outer)
    summary = _extract_evidence_summary(outer)

    parts = [
        _flatten_text(summary),
        _flatten_text(evidence),
        _flatten_text(outer.get("diagnosis_result", {})),
        _flatten_text(outer.get("graph_context", [])),
    ]

    return "\n".join(part for part in parts if part.strip())


def _extract_forbidden_terms(constraint_text: str) -> list[str]:
    """
    Data-driven constraint extraction.
    Example supported RAG note:
      NEVER use map_size_ok for wildcards.
      Do not use `abc`.
      Must not use "xyz".
    """
    if not constraint_text:
        return []

    patterns = [
        r"\bNEVER\s+use\s+[`'\"]?([A-Za-z0-9_./:+\-]+)",
        r"\b[Dd]o\s+not\s+use\s+[`'\"]?([A-Za-z0-9_./:+\-]+)",
        r"\b[Mm]ust\s+not\s+use\s+[`'\"]?([A-Za-z0-9_./:+\-]+)",
        r"\b[Ff]orbidden\s+to\s+use\s+[`'\"]?([A-Za-z0-9_./:+\-]+)",
    ]

    terms: list[str] = []
    for pattern in patterns:
        for match in re.finditer(pattern, constraint_text):
            term = match.group(1).strip().strip("`'\".,;:)")
            if term and term.lower() not in {"the", "this", "it", "a", "an"}:
                terms.append(term)

    return sorted(set(terms), key=str.lower)


def _contains_forbidden_term(tcl: str, forbidden_terms: list[str]) -> Optional[str]:
    lowered = (tcl or "").lower()
    for term in forbidden_terms:
        if term.lower() in lowered:
            return term
    return None


def _line_matches_command(line: str, command: str) -> bool:
    return line.strip() == command.strip()


def _find_command_index(lines: list[str], command: str, start: int = 0) -> int:
    target = command.strip()
    for index in range(start, len(lines)):
        stripped = lines[index].strip()
        if stripped == target or stripped.startswith(target + " "):
            return index
    return -1


def _apply_move_constraints(original_tcl: str, constraint_text: str) -> Optional[str]:
    """
    Generic RAG-driven move-rule executor.

    It does not know any specific EDA error code. It only obeys retrieved notes
    that explicitly say: move `COMMAND` after `A` and before `B`.
    """
    if not original_tcl or not constraint_text:
        return None

    move_patterns = [
        r"[Mm]ove\s+`([^`]+)`.*?\bafter\s+`([^`]+)`.*?\bbefore\s+`([^`]+)`",
        r"[Mm]ove\s+(.+?)\s+after\s+([^\n.]+?)\s+and\s+before\s+([^\n.]+)",
    ]

    for pattern in move_patterns:
        match = re.search(pattern, constraint_text, flags=re.DOTALL)
        if not match:
            continue

        command = match.group(1).strip().strip("`'\". ")
        after_cmd = match.group(2).strip().strip("`'\". ")
        before_cmd = match.group(3).strip().strip("`'\". ")

        if not command or not after_cmd or not before_cmd:
            continue

        lines = original_tcl.splitlines()
        remaining = [line for line in lines if not _line_matches_command(line, command)]

        after_index = _find_command_index(remaining, after_cmd)
        if after_index == -1:
            continue

        before_index = _find_command_index(remaining, before_cmd, start=after_index + 1)
        if before_index == -1:
            continue

        insert_at = before_index
        if insert_at > 0 and remaining[insert_at - 1].strip():
            remaining.insert(insert_at, "")
            insert_at += 1

        remaining.insert(insert_at, command)

        if insert_at + 1 < len(remaining) and remaining[insert_at + 1].strip():
            remaining.insert(insert_at + 1, "")

        return "\n".join(remaining).strip()

    return None


def _constraint_failure_message(forbidden_term: str, fix_status: str) -> str:
    return (
        "**Explanation**\n\n"
        "The generated Tcl patch was rejected because it violated the retrieved "
        "knowledge-graph constraints. The diagnostic evidence forbids using "
        f"`{forbidden_term}`, but the proposed patch still contained it.\n\n"
        "This is not safe to package as an automatic Tcl fix. The engineer should "
        "review the retrieved fix strategy and regenerate the patch so that it obeys "
        "all MUST/NEVER and before/after ordering constraints.\n\n"
        "**Summary of Issues**\n\n"
        f"1. Proposed patch contained forbidden term: `{forbidden_term}`.\n"
        "2. Patch did not fully obey retrieved RAG constraints.\n\n"
        "**Recommendations**\n\n"
        "1. Follow the retrieved fix strategy exactly.\n"
        "2. Do not introduce values or commands that the retrieved notes forbid.\n"
        "3. If retrieved notes specify command ordering, relocate the original command instead of replacing it.\n\n"
        "**Example Manual Fix Template**\n\n"
        "TCL TEMPLATE:\n"
        "```tcl\n"
        "# Review the retrieved fix strategy and apply only the allowed command movement/rewrite.\n"
        "# Do not use terms that retrieved_notes marks as forbidden.\n"
        "```\n\n"
        "**Fix Status**\n\n"
        f"{fix_status or 'Manual Fix Required'}"
    )


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

    constraint_text = _extract_constraint_text(outer)
    forbidden_terms = _extract_forbidden_terms(constraint_text)

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
    # Enforce retrieved RAG constraints generically.
    # ------------------------------------------------------------
    if patched_tcl_clean:
        changes = [
            "The Tcl script was generated by the Fixer Agent using the diagnostic payload."
        ]

    
        rag_moved_tcl = _apply_move_constraints(original_tcl, constraint_text)
        if rag_moved_tcl:
            patched_tcl_clean = rag_moved_tcl
            changes.append(
                "The Tcl command ordering was constrained using the retrieved knowledge-graph move rule."
            )

        forbidden = _contains_forbidden_term(patched_tcl_clean, forbidden_terms)
        if forbidden:
            result = {
                "fix_status": "manual_fix_required",
                "issue_type": issue_type,
                "patched_tcl": None,
                "applied_fix_pattern": recommended_fix_pattern,
                "changes_applied": [],
                "explanation": _constraint_failure_message(
                    forbidden_term=forbidden,
                    fix_status="Manual Fix Required",
                ),
                "fixability": fixability,
                "fixability_reason": fixability_reason,
                "constraint_violation": {
                    "forbidden_term": forbidden,
                    "forbidden_terms": forbidden_terms,
                },
            }
            return json.dumps(result, indent=2)

        result = {
            "fix_status": fix_status or (
                "partial_fix_applied" if fixability == "partial_fixable" else "auto_fixed"
            ),
            "issue_type": issue_type,
            "patched_tcl": patched_tcl_clean,
            "applied_fix_pattern": recommended_fix_pattern or "llm_generated_fix",
            "changes_applied": changes,
            "explanation": (
                explanation_text
                or _minimal_missing_content_message(fix_status or "Auto Fixed")
            ),
            "fixability": fixability,
            "fixability_reason": fixability_reason,
            "forbidden_terms_checked": forbidden_terms,
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