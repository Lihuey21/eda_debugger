from __future__ import annotations

import ast
import json
import os
import re
from typing import Any, Dict, List, Tuple


try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    print("Warning: python-dotenv not installed. Neo4j may fail to connect.")


from ..script_analyzer_agent.tools import (
    analyze_tcl_script,
    analyze_eda_log,
    analyze_session,
    _split_contaminated_tcl_and_log,
)


driver = None


def get_neo4j_driver():
    global driver

    if driver is None:
        try:
            from neo4j import GraphDatabase

            uri = os.getenv("NEO4J_URI")
            user = os.getenv("NEO4J_USERNAME") or os.getenv("NEO4J_USER")
            password = os.getenv("NEO4J_PASSWORD")

            if not uri or not user or not password:
                print(
                    "Warning: Neo4j env vars missing. Check "
                    "NEO4J_URI, NEO4J_USERNAME/NEO4J_USER, NEO4J_PASSWORD."
                )
                return None

            driver = GraphDatabase.driver(uri, auth=(user, password))
            driver.verify_connectivity()
            print("Neo4j connectivity verified.")

        except Exception as exc:
            print(f"Warning: Neo4j not connected. {exc}")
            driver = None
            return None

    return driver


def _safe_json_loads(value: Any) -> Any:
    if not isinstance(value, str):
        return value

    text = value.strip()
    if not text:
        return value

    try:
        return json.loads(text)
    except Exception:
        pass

    try:
        parsed = ast.literal_eval(text)
        if isinstance(parsed, (dict, list, tuple, str, int, float, bool, type(None))):
            return parsed
    except Exception:
        pass

    return value


def _unique_preserve(seq: List[Any]) -> List[Any]:
    seen = set()
    out = []

    for item in seq:
        key = repr(item)
        if key in seen:
            continue
        seen.add(key)
        out.append(item)

    return out


def _normalize_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _looks_like_tcl(filename: str, text: str) -> bool:
    name = (filename or "").lower()
    lowered = text.lower()

    # Filename priority: explicit log-like names should not be treated as Tcl
    # even if the log echoes Tcl commands.
    if re.search(r"(^|[._-])log\d*([._-]|$)", name):
        return False

    if "genus.log" in name or "innovus.log" in name:
        return False

    if (
        name.endswith(".tcl")
        or name.endswith(".tcl.txt")
        or ".tcl." in name
        or "script" in name
    ):
        return True

    # Content heuristic for raw pasted Tcl.
    # Require command-style Tcl content and avoid logs with Genus banners/errors.
    log_signals = [
        "cadence genus",
        "@genus",
        "error :",
        "warning :",
        "encountered problems processing file",
        "finished executable startup",
        "checking out license",
    ]

    if any(signal in lowered for signal in log_signals):
        return False

    hints = [
        "set_db",
        "read_libs",
        "read_hdl",
        "elaborate",
        "read_sdc",
        "syn_generic",
        "syn_map",
        "syn_opt",
        "report_timing",
        "write_hdl",
        "quit",
    ]

    return sum(1 for hint in hints if hint in lowered) >= 2


def _looks_like_log(filename: str, text: str) -> bool:
    name = (filename or "").lower()
    lowered = text.lower()

    # Filename priority: catch names like:
    # genus.log
    # genus.log.txt
    # genus.log10.txt
    # genus_log10.txt
    # innovus.log26.txt
    if (
        name.endswith(".log")
        or name.endswith(".log.txt")
        or ".log." in name
        or re.search(r"(^|[._-])log\d*([._-]|$)", name)
        or "genus.log" in name
        or "innovus.log" in name
    ):
        return True

    log_hints = [
        "cadence genus",
        "@genus",
        "error :",
        "warning :",
        "fatal",
        "encountered problems processing file",
        "finished executable startup",
        "checking out license",
        "file-",
        "lbr-",
        "tui-",
        "elab-",
        "cdfg-",
    ]

    return sum(1 for hint in log_hints if hint in lowered) >= 2


def _extract_uploaded_file_blocks(payload: str) -> List[Dict[str, str]]:
    if not isinstance(payload, str) or not payload.strip():
        return []

    text = _normalize_newlines(payload)

    pattern = re.compile(
        r"--- BEGIN UPLOADED FILE ---\n(?P<body>.*?)\n--- END UPLOADED FILE ---",
        re.DOTALL,
    )

    blocks = []

    for match in pattern.finditer(text):
        body = match.group("body").strip()
        lines = body.splitlines()

        filename = "uploaded_file"
        content_type = ""
        content_start_index = 0

        for index, line in enumerate(lines):
            stripped = line.strip()

            if stripped.startswith("filename:"):
                filename = stripped.split(":", 1)[1].strip()
                continue

            if stripped.startswith("content_type:"):
                content_type = stripped.split(":", 1)[1].strip()
                continue

            if stripped.startswith("processing_note:"):
                continue

            if stripped == "":
                content_start_index = index + 1
                break

        content = "\n".join(lines[content_start_index:]).strip()

        blocks.append(
            {
                "filename": filename,
                "content_type": content_type,
                "content": content,
            }
        )

    return blocks


def _extract_text_after_marker(payload: str, marker: str) -> str:
    if marker not in payload:
        return ""

    after = payload.split(marker, 1)[1]

    for next_marker in ["\n\nUploaded files:", "\n\nInstruction:"]:
        if next_marker in after:
            after = after.split(next_marker, 1)[0]

    return after.strip()


def _extract_user_message(payload: str) -> str:
    if not isinstance(payload, str):
        return ""

    if "User message:" in payload:
        return _extract_text_after_marker(payload, "User message:")

    return payload.strip()


def _extract_tcl_and_log(payload: str) -> Tuple[str, str, str]:
    payload = _normalize_newlines(payload or "")
    user_message = _extract_user_message(payload)

    file_blocks = _extract_uploaded_file_blocks(payload)

    tcl_parts: List[str] = []
    log_parts: List[str] = []

    for block in file_blocks:
        filename = block.get("filename", "")
        content = block.get("content", "")

        if not content.strip():
            continue

        # Important:
        # Check log first because Genus logs often echo Tcl commands.
        # If we check Tcl first, genus.log10.txt can be mistaken as Tcl.
        if _looks_like_log(filename, content):
            log_parts.append(content)
        elif _looks_like_tcl(filename, content):
            tcl_parts.append(content)

    if not file_blocks:
        # For raw pasted text, try splitting contaminated Tcl/log first.
        possible_tcl, possible_log = _split_contaminated_tcl_and_log(payload, "")

        if possible_tcl and possible_log:
            tcl_parts.append(possible_tcl)
            log_parts.append(possible_log)
        elif _looks_like_log("raw_payload", payload):
            log_parts.append(payload)
        elif _looks_like_tcl("raw_payload", payload):
            tcl_parts.append(payload)

    original_tcl = "\n\n".join(tcl_parts).strip()
    original_log = "\n\n".join(log_parts).strip()

    clean_tcl, clean_log = _split_contaminated_tcl_and_log(original_tcl, original_log)

    return clean_tcl or original_tcl or "", clean_log or original_log or "", user_message


def _build_analysis_result(original_tcl: str, original_log: str) -> Dict[str, Any]:
    if original_tcl and original_log:
        return analyze_session(original_tcl, original_log)

    if original_tcl:
        return analyze_tcl_script(original_tcl)

    if original_log:
        return analyze_eda_log(original_log)

    return {
        "schema_version": 2,
        "input_kind": "none",
        "summary": {
            "overall_severity": "INFO",
            "num_anomalies": 0,
        },
        "eda_context": {},
        "runtime_context": {},
        "anomalies": [],
        "handoff": {
            "next_agent": None,
            "notes": "No Tcl or log content provided.",
        },
    }


def _get_error_codes(parsed_analysis: Dict[str, Any]) -> List[str]:
    runtime_context = parsed_analysis.get("runtime_context", {}) if isinstance(parsed_analysis, dict) else {}
    codes = runtime_context.get("error_codes", []) if isinstance(runtime_context, dict) else []

    if not isinstance(codes, list):
        codes = []

    anomaly_codes = []

    for anomaly in parsed_analysis.get("anomalies", []) if isinstance(parsed_analysis, dict) else []:
        if isinstance(anomaly, dict):
            code = anomaly.get("code")
            if isinstance(code, str) and code.strip():
                anomaly_codes.append(code.strip())

    return _unique_preserve(
        [code for code in codes + anomaly_codes if isinstance(code, str) and code.strip()]
    )


def _get_failed_command(parsed_analysis: Dict[str, Any]) -> str:
    runtime_context = parsed_analysis.get("runtime_context", {}) if isinstance(parsed_analysis, dict) else {}

    if isinstance(runtime_context, dict):
        cmd = runtime_context.get("first_suspected_failed_command")
        if isinstance(cmd, str) and cmd.strip():
            return cmd.strip()

        cmds = runtime_context.get("suspected_failed_commands", [])
        if isinstance(cmds, list) and cmds:
            first = cmds[0]
            if isinstance(first, str) and first.strip():
                return first.strip()

    anomalies = parsed_analysis.get("anomalies", []) if isinstance(parsed_analysis, dict) else []

    for anomaly in anomalies:
        if not isinstance(anomaly, dict):
            continue

        evidence = str(anomaly.get("evidence") or "").lower()
        message = str(anomaly.get("message") or "").lower()
        blob = evidence + "\n" + message

        for cmd in [
            "read_libs",
            "read_hdl",
            "read_sdc",
            "elaborate",
            "syn_generic",
            "syn_map",
            "syn_opt",
            "set_db",
        ]:
            if cmd in blob:
                return cmd

    return ""


def _top_anomalies(parsed_analysis: Dict[str, Any], limit: int = 8) -> List[Dict[str, Any]]:
    anomalies = parsed_analysis.get("anomalies", []) if isinstance(parsed_analysis, dict) else []

    compact = []

    for anomaly in anomalies:
        if not isinstance(anomaly, dict):
            continue

        compact.append(
            {
                "source": anomaly.get("source"),
                "severity": anomaly.get("severity"),
                "code": anomaly.get("code"),
                "type": anomaly.get("type"),
                "message": anomaly.get("message"),
                "evidence": anomaly.get("evidence"),
            }
        )

    severity_rank = {"FATAL": 0, "ERROR": 1, "WARNING": 2, "INFO": 3, None: 4}

    compact.sort(
        key=lambda item: severity_rank.get(item.get("severity"), 4)
    )

    return compact[:limit]

def _extract_candidate_failed_commands(
    parsed_analysis: Dict[str, Any],
    original_log: str,
    limit: int = 10,
) -> List[Dict[str, Any]]:
    """
    Evidence extractor only.

    This does not decide the root cause.
    It collects possible command/error candidates so the Diagnostic LLM can decide
    which one is primary and which ones are secondary.
    """
    candidates: List[Dict[str, Any]] = []

    anomalies = parsed_analysis.get("anomalies", []) if isinstance(parsed_analysis, dict) else []
    if not isinstance(anomalies, list):
        anomalies = []

    command_keywords = [
        "read_libs",
        "read_hdl",
        "read_sdc",
        "elaborate",
        "syn_generic",
        "syn_map",
        "syn_opt",
        "set_db",
    ]

    for anomaly in anomalies:
        if not isinstance(anomaly, dict):
            continue

        severity = str(anomaly.get("severity") or "").upper()
        code = anomaly.get("code")
        message = anomaly.get("message")
        evidence = anomaly.get("evidence")

        blob = (
            str(code or "") + "\n" +
            str(anomaly.get("type") or "") + "\n" +
            str(message or "") + "\n" +
            str(evidence or "")
        ).lower()

        matched_commands = []

        for command in command_keywords:
            if command in blob:
                matched_commands.append(command)

        # Add command-looking preserve evidence without deciding it is root cause.
        if "preserve" in blob or ".preserve" in blob:
            matched_commands.append("set_db [get_cells -hierarchical *] .preserve true")

        matched_commands = _unique_preserve(matched_commands)

        if not matched_commands:
            matched_commands = ["unknown"]

        for command in matched_commands:
            candidates.append(
                {
                    "candidate_command": command,
                    "severity": severity,
                    "code": code,
                    "message": message,
                    "evidence": evidence,
                    "source": anomaly.get("source"),
                    "type": anomaly.get("type"),
                }
            )

    # Add a light scan of log lines for command context.
    # This is extraction only, not diagnosis.
    if original_log:
        lines = original_log.splitlines()

        for index, line in enumerate(lines):
            lower = line.lower()

            if not (
                "error" in lower
                or "fatal" in lower
                or "warning" in lower
                or "tui-" in lower
                or "lbr-" in lower
                or "file-" in lower
                or "elab-" in lower
            ):
                continue

            window_start = max(0, index - 4)
            window_end = min(len(lines), index + 4)
            window = "\n".join(lines[window_start:window_end])

            command_guess = "unknown"
            window_lower = window.lower()

            for command in command_keywords:
                if command in window_lower:
                    command_guess = command
                    break

            if "preserve" in window_lower or ".preserve" in window_lower:
                command_guess = "set_db [get_cells -hierarchical *] .preserve true"

            candidates.append(
                {
                    "candidate_command": command_guess,
                    "severity": "LOG_CONTEXT",
                    "code": None,
                    "message": line.strip(),
                    "evidence": window.strip(),
                    "source": "log_window",
                    "type": "nearby_log_context",
                }
            )

    # De-duplicate while preserving order.
    unique_candidates = []
    seen = set()

    for item in candidates:
        key = (
            item.get("candidate_command"),
            item.get("severity"),
            item.get("code"),
            item.get("message"),
            item.get("evidence"),
        )

        if key in seen:
            continue

        seen.add(key)
        unique_candidates.append(item)

    return unique_candidates[:limit]


def _collect_log_excerpt(original_log: str, error_codes: List[str], max_chars: int = 3500) -> str:
    if not original_log:
        return ""

    lines = original_log.splitlines()
    selected: List[str] = []

    code_lowers = [code.lower() for code in error_codes if isinstance(code, str)]

    for index, line in enumerate(lines):
        lower = line.lower()

        is_relevant = (
            "error" in lower
            or "fatal" in lower
            or "warning" in lower
            or "cannot open" in lower
            or "could not be found" in lower
            or "does not exist" in lower
            or any(code in lower for code in code_lowers)
        )

        if not is_relevant:
            continue

        start = max(0, index - 1)
        end = min(len(lines), index + 3)

        for item in lines[start:end]:
            if item not in selected:
                selected.append(item)

    excerpt = "\n".join(selected).strip()

    if not excerpt:
        excerpt = original_log[-max_chars:]

    if len(excerpt) > max_chars:
        excerpt = excerpt[:max_chars] + "\n...[truncated]"

    return excerpt


def _extract_tcl_features(original_tcl: str) -> Dict[str, Any]:
    text = original_tcl or ""
    lower = text.lower()

    commands = []

    for command in [
        "set_db",
        "read_libs",
        "read_hdl",
        "elaborate",
        "read_sdc",
        "syn_generic",
        "syn_map",
        "syn_opt",
        "write_hdl",
        "report_timing",
    ]:
        if command in lower:
            commands.append(command)

    return {
        "commands_present": commands,
        "has_read_libs": "read_libs" in lower,
        "has_read_hdl": "read_hdl" in lower,
        "has_elaborate": "elaborate" in lower,
        "has_read_sdc": "read_sdc" in lower,
        "has_syn_generic": "syn_generic" in lower,
        "has_syn_map": "syn_map" in lower,
        "has_syn_opt": "syn_opt" in lower,
        "has_preserve_command": ".preserve" in lower or " preserve " in f" {lower} ",
        "has_map_size_ok": "map_size_ok" in lower,
        "tcl_char_count": len(text),
    }


def _extract_log_features(original_log: str) -> Dict[str, Any]:
    text = original_log or ""
    lower = text.lower()

    return {
        "has_cannot_open": "cannot open" in lower,
        "has_file_not_found": (
            "does not exist" in lower
            or "could not be found" in lower
            or "not found" in lower
            or "no such file" in lower
        ),
        "has_permission_signal": "permission denied" in lower or "readable regular file" in lower,
        "has_library_load_signal": "read_libs" in lower or "library file" in lower or "init_lib_search_path" in lower,
        "has_preserve_signal": "preserve" in lower or "partially mapped" in lower or "unmapped" in lower,
        "log_char_count": len(text),
    }


def query_graph_for_codes(codes: List[str]) -> List[Dict[str, Any]]:
    drv = get_neo4j_driver()

    if not drv or not codes:
        return []

    query = """
    UNWIND $codes AS code
    MATCH (e:ErrorCode {code: code})
    OPTIONAL MATCH (e)-[:INDICATES]->(i:IssueType)
    OPTIONAL MATCH (i)-[:SUGGESTS_FIX]->(f:FixPattern)
    OPTIONAL MATCH (i)-[:AFFECTS]->(c:Command)
    OPTIONAL MATCH (e)-[]-(dc:DocChunk)
    RETURN
        code AS code,
        collect(DISTINCT i.name) AS issue_types,
        collect(DISTINCT f.name) AS fix_patterns,
        collect(DISTINCT c.name) AS affected_commands,
        collect(DISTINCT dc.text) AS documentation
    """

    try:
        from neo4j import READ_ACCESS

        with drv.session(default_access_mode=READ_ACCESS) as session:
            result = session.run(query, codes=codes)
            rows = []

            for record in result:
                docs = [
                    doc for doc in (record["documentation"] or [])
                    if isinstance(doc, str) and doc.strip()
                ]

                issue_types = [item for item in (record["issue_types"] or []) if item]
                fix_patterns = [item for item in (record["fix_patterns"] or []) if item]
                affected_commands = [item for item in (record["affected_commands"] or []) if item]

                rows.append(
                    {
                        "code": record["code"],
                        "issue_types": issue_types,
                        "fix_patterns": fix_patterns,
                        "affected_commands": affected_commands,
                        "documentation": docs[:2],
                    }
                )

            return rows

    except Exception as exc:
        print(f"Graph Query Error: {exc}")
        return []


def _compact_graph_context(graph_context: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    compact = []

    for row in graph_context:
        if not isinstance(row, dict):
            continue

        docs = row.get("documentation", [])
        short_docs = []

        if isinstance(docs, list):
            for doc in docs[:2]:
                if isinstance(doc, str) and doc.strip():
                    short_docs.append(doc.strip()[:500])

        compact.append(
            {
                "code": row.get("code"),
                "issue_types": row.get("issue_types", []),
                "fix_patterns": row.get("fix_patterns", []),
                "affected_commands": row.get("affected_commands", []),
                "documentation": short_docs,
            }
        )

    return compact


def _make_retrieved_notes(graph_context: List[Dict[str, Any]]) -> str:
    notes = []

    for row in graph_context:
        if not isinstance(row, dict):
            continue

        code = row.get("code")
        issue_types = row.get("issue_types", [])
        fix_patterns = row.get("fix_patterns", [])
        docs = row.get("documentation", [])

        fragments = []

        if issue_types:
            fragments.append(f"issue types: {', '.join(str(x) for x in issue_types[:3])}")

        if fix_patterns:
            fragments.append(f"fix patterns: {', '.join(str(x) for x in fix_patterns[:3])}")

        if docs:
            fragments.append(f"documentation: {str(docs[0])[:350]}")

        if fragments:
            notes.append(f"{code}: " + " | ".join(fragments))

    return "\n".join(notes[:5])


def extract_diagnostic_evidence(payload: str = "") -> dict:
    """
    Evidence extractor only.

    This function intentionally does NOT decide final fixability.
    The Diagnostic Agent LLM reads this evidence and decides:
    manual_required / auto_fixable / partial_fixable / no_fix_needed.
    """
    payload = payload or ""

    original_tcl, original_log, user_message = _extract_tcl_and_log(payload)

    parsed_analysis = _build_analysis_result(original_tcl, original_log)

    summary = parsed_analysis.get("summary", {}) if isinstance(parsed_analysis, dict) else {}
    anomalies = parsed_analysis.get("anomalies", []) if isinstance(parsed_analysis, dict) else []

    error_codes = _get_error_codes(parsed_analysis)
    failed_command = _get_failed_command(parsed_analysis)
    top_anomalies = _top_anomalies(parsed_analysis, limit=8)
    candidate_failed_commands = _extract_candidate_failed_commands(
        parsed_analysis=parsed_analysis,
        original_log=original_log,
        limit=12,
    )

    graph_context_raw = query_graph_for_codes(error_codes)
    graph_context = _compact_graph_context(graph_context_raw)
    retrieved_notes = _make_retrieved_notes(graph_context)

    should_skip = (
        summary.get("overall_severity") == "INFO"
        and summary.get("num_anomalies", 0) == 0
        and not anomalies
        and not error_codes
    )

    tcl_features = _extract_tcl_features(original_tcl)
    log_features = _extract_log_features(original_log)
    relevant_log_excerpt = _collect_log_excerpt(original_log, error_codes)

    downstream_errors = []

    for anomaly in top_anomalies:
        code = anomaly.get("code")
        msg = anomaly.get("message")

        if code and msg:
            downstream_errors.append(f"{code}: {msg}")
        elif msg:
            downstream_errors.append(str(msg))

    downstream_errors = downstream_errors[:5]

    print("\n==================================================")
    print("DEBUG: Diagnostic Evidence Extractor")
    print(f"DEBUG: Tcl chars extracted: {len(original_tcl)}")
    print(f"DEBUG: Log chars extracted: {len(original_log)}")
    print(f"DEBUG: Candidate error codes for Neo4j RAG: {error_codes}")
    print(f"DEBUG: Failed command candidate: {failed_command}")
    print(f"DEBUG: Retrieved graph rows: {len(graph_context)}")
    print(f"DEBUG: Candidate failed commands extracted: {len(candidate_failed_commands)}")
    print("DEBUG: Tool did not decide fixability; LLM must decide.")
    print("==================================================\n")

    return {
        "agent_stage": "diagnostic_evidence_extracted",
        "next_agent": "error_diagnosis_agent",
        "original_tcl": original_tcl,
        "original_log_excerpt": relevant_log_excerpt,
        "evidence": {
            "has_issue": not should_skip,
            "summary": summary,
            "primary_error_codes": error_codes,
            "failed_command": failed_command,
            "candidate_failed_commands": candidate_failed_commands,
            "downstream_errors": downstream_errors,
            "top_anomalies": top_anomalies,
            "tcl_features": tcl_features,
            "log_features": log_features,
            "retrieved_notes": retrieved_notes,
            "graph_context": graph_context,
            "user_message": user_message,
        },
    }