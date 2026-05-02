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


EDA_COMMAND_KEYWORDS = [
    "set_db",
    "read_libs",
    "read_hdl",
    "elaborate",
    "read_sdc",
    "syn_generic",
    "syn_map",
    "syn_opt",
    "report_timing",
    "report_power",
    "report_area",
    "report_qor",
    "write_hdl",
    "write_sdc",
    "write_sdf",
    "write_db",
    "write_dft_atpg",
    "write_scandef",
    "report_scan_chains",
    "check_dft_rules",
    "define_scan_chain",
    "connect_scan_chains",
    "define_shift_enable",
    "define_dft",
    "quit",
]


# ---------------------------------------------------------------------
# Neo4j
# ---------------------------------------------------------------------

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


# ---------------------------------------------------------------------
# Generic helpers
# ---------------------------------------------------------------------

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
    return (text or "").replace("\r\n", "\n").replace("\r", "\n")


def _extract_error_codes_from_text(text: str) -> List[str]:
    """Generic fallback code extractor, independent of analyzer output."""
    if not text:
        return []

    codes = re.findall(r"\b[A-Z]{2,12}-\d+\b", text)
    return _unique_preserve([code.strip() for code in codes if code.strip()])


def _command_candidates_from_text(text: str) -> List[str]:
    lowered = (text or "").lower()
    return [cmd for cmd in EDA_COMMAND_KEYWORDS if cmd in lowered]


# ---------------------------------------------------------------------
# File/text classification and extraction
# ---------------------------------------------------------------------

def _looks_like_tcl(filename: str, text: str) -> bool:
    name = (filename or "").lower()
    lowered = (text or "").lower()

    # Log-looking filenames should never be Tcl, even though logs echo Tcl commands.
    if re.search(r"(^|[._-])log\d*([._-]|$)", name):
        return False

    if "genus.log" in name or "innovus.log" in name:
        return False

    log_signals = [
        "cadence genus",
        "@genus",
        "error   :",
        "error :",
        "warning :",
        "fatal :",
        "encountered problems processing file",
        "finished executable startup",
        "checking out license",
        "#@ begin verbose source",
        "#@ end verbose source",
    ]

    if any(signal in lowered for signal in log_signals):
        return False

    if (
        name.endswith(".tcl")
        or name.endswith(".tcl.txt")
        or ".tcl." in name
        or "script" in name
    ):
        return True

    return sum(1 for hint in EDA_COMMAND_KEYWORDS if hint in lowered) >= 2


def _looks_like_log(filename: str, text: str) -> bool:
    name = (filename or "").lower()
    lowered = (text or "").lower()

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
        "#@ begin verbose source",
        "#@ end verbose source",
        "error   :",
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
        "sdc-",
        "synth-",
        "dft-",
    ]

    return sum(1 for hint in log_hints if hint in lowered) >= 2


def _extract_uploaded_file_blocks(payload: str) -> List[Dict[str, str]]:
    """
    Extract file contents from the custom wrapper used by the website/backend.

    Expected primary format:
    --- BEGIN UPLOADED FILE ---
    filename: x
    content_type: text/plain

    <content>
    --- END UPLOADED FILE ---

    Also supports a looser header variant:
    --- BEGIN UPLOADED FILE: filename ---
    <content>
    --- END UPLOADED FILE ---
    """
    if not isinstance(payload, str) or not payload.strip():
        return []

    text = _normalize_newlines(payload)
    blocks: List[Dict[str, str]] = []

    # Format A: exact backend wrapper.
    pattern_a = re.compile(
        r"---\s*BEGIN\s+UPLOADED\s+FILE\s*---\n(?P<body>.*?)\n---\s*END\s+UPLOADED\s+FILE\s*---",
        re.DOTALL | re.IGNORECASE,
    )

    for match in pattern_a.finditer(text):
        body = match.group("body").strip("\n")
        lines = body.splitlines()

        filename = "uploaded_file"
        content_type = ""
        content_start_index = 0

        for index, line in enumerate(lines):
            stripped = line.strip()

            if stripped.lower().startswith("filename:"):
                filename = stripped.split(":", 1)[1].strip()
                continue

            if stripped.lower().startswith("content_type:"):
                content_type = stripped.split(":", 1)[1].strip()
                continue

            if stripped.lower().startswith("file_kind:") or stripped.lower().startswith("processing_note:"):
                continue

            if stripped == "":
                content_start_index = index + 1
                break

        content = "\n".join(lines[content_start_index:]).strip("\n")
        blocks.append({"filename": filename, "content_type": content_type, "content": content})

    # Format B: header includes filename.
    pattern_b = re.compile(
        r"---\s*BEGIN\s+UPLOADED\s+FILE\s*:\s*(?P<filename>.*?)\s*---\n(?P<content>.*?)\n---\s*END\s+UPLOADED\s+FILE\s*---",
        re.DOTALL | re.IGNORECASE,
    )

    for match in pattern_b.finditer(text):
        filename = match.group("filename").strip() or "uploaded_file"
        content = match.group("content").strip("\n")
        blocks.append({"filename": filename, "content_type": "", "content": content})

    # De-duplicate blocks, in case both regexes caught the same text.
    unique_blocks: List[Dict[str, str]] = []
    seen = set()
    for block in blocks:
        key = (block.get("filename", ""), len(block.get("content", "")), block.get("content", "")[:80])
        if key in seen:
            continue
        seen.add(key)
        unique_blocks.append(block)

    return unique_blocks


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
    """
    Extract Tcl and log text from either:
    - backend-wrapped uploaded files; or
    - raw ADK payload/pasted text.

    Important limitation:
    If ADK Web does not pass full attachment content into the tool payload,
    this function cannot read bytes it never received. The debug prints below
    make that immediately visible.
    """
    payload = _normalize_newlines(payload or "")
    user_message = _extract_user_message(payload)

    file_blocks = _extract_uploaded_file_blocks(payload)

    print("DEBUG: Raw payload chars:", len(payload))
    print("DEBUG: BEGIN UPLOADED FILE count:", payload.upper().count("BEGIN UPLOADED FILE"))
    print("DEBUG: END UPLOADED FILE count:", payload.upper().count("END UPLOADED FILE"))
    print("DEBUG: Uploaded file blocks detected:", len(file_blocks))
    print("DEBUG: Raw payload preview:", repr(payload[:1000]))

    tcl_parts: List[str] = []
    log_parts: List[str] = []

    for i, block in enumerate(file_blocks):
        filename = block.get("filename", "")
        content = block.get("content", "")

        print(
            f"DEBUG: file_block[{i}] filename={filename!r} "
            f"chars={len(content)} "
            f"looks_log={_looks_like_log(filename, content)} "
            f"looks_tcl={_looks_like_tcl(filename, content)}"
        )

        if not content.strip():
            continue

        # Check log first because Genus logs often echo Tcl commands.
        if _looks_like_log(filename, content):
            log_parts.append(content)
        elif _looks_like_tcl(filename, content):
            tcl_parts.append(content)
        else:
            # If uncertain, classify by stronger content evidence.
            if _looks_like_log("unknown", content):
                log_parts.append(content)
            elif _looks_like_tcl("unknown", content):
                tcl_parts.append(content)

    if not file_blocks:
        # ADK Web often gives only text visible to the LLM/tool. Split what we received.
        possible_tcl, possible_log = _split_contaminated_tcl_and_log(payload, "")

        print("DEBUG: No wrapped file blocks found; using raw-payload fallback.")
        print("DEBUG: possible_tcl chars from splitter:", len(possible_tcl or ""))
        print("DEBUG: possible_log chars from splitter:", len(possible_log or ""))

        if possible_tcl and possible_log:
            tcl_parts.append(possible_tcl)
            log_parts.append(possible_log)
        elif _looks_like_log("raw_payload", payload):
            log_parts.append(payload)
        elif _looks_like_tcl("raw_payload", payload):
            tcl_parts.append(payload)

    original_tcl = "\n\n".join(tcl_parts).strip()
    original_log = "\n\n".join(log_parts).strip()

    print("DEBUG: Final original_tcl chars before split:", len(original_tcl))
    print("DEBUG: Final original_log chars before split:", len(original_log))
    print("DEBUG: original_log preview before split:", repr(original_log[:500]))

    clean_tcl, clean_log = _split_contaminated_tcl_and_log(original_tcl, original_log)

    print("DEBUG: clean_tcl chars after split:", len(clean_tcl or ""))
    print("DEBUG: clean_log chars after split:", len(clean_log or ""))
    print("DEBUG: clean_log preview after split:", repr((clean_log or "")[:500]))

    # Safety: never let the contamination splitter shrink a real log too aggressively.
    if original_log and clean_log and len(clean_log) < max(500, int(len(original_log) * 0.25)):
        print(
            "DEBUG: Splitter produced suspiciously small log. "
            f"original_log={len(original_log)}, clean_log={len(clean_log)}. Keeping original_log."
        )
        clean_log = original_log

    if original_tcl and clean_tcl and len(clean_tcl) < max(200, int(len(original_tcl) * 0.25)):
        print(
            "DEBUG: Splitter produced suspiciously small Tcl. "
            f"original_tcl={len(original_tcl)}, clean_tcl={len(clean_tcl)}. Keeping original_tcl."
        )
        clean_tcl = original_tcl

    return clean_tcl or original_tcl or "", clean_log or original_log or "", user_message


# ---------------------------------------------------------------------
# Analyzer wrappers and evidence extraction
# ---------------------------------------------------------------------

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

        blob = "\n".join(
            str(anomaly.get(key) or "")
            for key in ["code", "type", "message", "evidence"]
        )

        matches = _command_candidates_from_text(blob)
        if matches:
            return matches[0]

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
    compact.sort(key=lambda item: severity_rank.get(item.get("severity"), 4))

    return compact[:limit]


def _extract_candidate_failed_commands(
    parsed_analysis: Dict[str, Any],
    original_log: str,
    limit: int = 12,
) -> List[Dict[str, Any]]:
    """
    Evidence extractor only. It collects candidate command/error evidence.
    It does not decide root cause or fixability.
    """
    candidates: List[Dict[str, Any]] = []
    anomalies = parsed_analysis.get("anomalies", []) if isinstance(parsed_analysis, dict) else []

    if not isinstance(anomalies, list):
        anomalies = []

    for anomaly in anomalies:
        if not isinstance(anomaly, dict):
            continue

        severity = str(anomaly.get("severity") or "").upper()
        code = anomaly.get("code")
        message = anomaly.get("message")
        evidence = anomaly.get("evidence")

        blob = "\n".join(
            str(anomaly.get(key) or "")
            for key in ["code", "type", "message", "evidence"]
        )

        matched_commands = _command_candidates_from_text(blob)
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

    if original_log:
        lines = original_log.splitlines()

        for index, line in enumerate(lines):
            lower = line.lower()

            if not (
                "error" in lower
                or "fatal" in lower
                or "warning" in lower
                or re.search(r"\b[A-Z]{2,12}-\d+\b", line)
            ):
                continue

            window_start = max(0, index - 4)
            window_end = min(len(lines), index + 4)
            window = "\n".join(lines[window_start:window_end])

            matches = _command_candidates_from_text(window)
            command_guess = matches[0] if matches else "unknown"

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
            or "failed" in lower
            or "cannot" in lower
            or "invalid" in lower
            or "does not exist" in lower
            or "not found" in lower
            or "permission denied" in lower
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


def _command_positions(original_tcl: str) -> Dict[str, List[int]]:
    positions: Dict[str, List[int]] = {}
    lines = (original_tcl or "").splitlines()

    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        lowered = stripped.lower()

        for command in EDA_COMMAND_KEYWORDS:
            if re.search(rf"(^|\s){re.escape(command)}(\s|$)", lowered):
                positions.setdefault(command, []).append(index + 1)

    return positions


def _extract_tcl_features(original_tcl: str) -> Dict[str, Any]:
    text = original_tcl or ""
    lower = text.lower()
    commands_present = [command for command in EDA_COMMAND_KEYWORDS if command in lower]

    return {
        "commands_present": commands_present,
        "command_positions": _command_positions(text),
        "tcl_line_count": len(text.splitlines()),
        "tcl_char_count": len(text),
    }


def _extract_log_features(original_log: str) -> Dict[str, Any]:
    text = original_log or ""
    lower = text.lower()

    return {
        "has_error_signal": bool(re.search(r"^\s*error\s*:", lower, re.MULTILINE)),
        "has_fatal_signal": bool(re.search(r"^\s*fatal\s*:", lower, re.MULTILINE)),
        "has_warning_signal": "warning" in lower,
        "has_failed_signal": "failed" in lower,
        "has_file_access_signal": any(
            phrase in lower
            for phrase in [
                "cannot open",
                "does not exist",
                "could not be found",
                "not found",
                "no such file",
                "permission denied",
                "not readable",
            ]
        ),
        "log_line_count": len(text.splitlines()),
        "log_char_count": len(text),
    }


# ---------------------------------------------------------------------
# Generic successful-run detection
# ---------------------------------------------------------------------

def _strip_nonblocking_summary_lines(original_log: str) -> str:
    cleaned_lines = []

    for line in (original_log or "").splitlines():
        lower = line.lower()

        if re.search(r"\berror\s*=\s*0\b", lower):
            continue
        if re.search(r"\bfatal\s*=\s*0\b", lower):
            continue
        if re.search(r"\berrors?\s*:\s*0\b", lower):
            continue
        if re.search(r"\bfatals?\s*:\s*0\b", lower):
            continue

        cleaned_lines.append(line)

    return "\n".join(cleaned_lines)


def _has_blocking_error_or_fatal(original_log: str) -> bool:
    cleaned = _strip_nonblocking_summary_lines(original_log)
    lower = cleaned.lower()

    blocking_patterns = [
        r"^\s*error\s*:",
        r"^\s*fatal\s*:",
        r"\bfatal\s*:",
        r"\berror\s*:",
        r"\bencountered\s+problems\s+processing\s+file\b",
        r"\bcommand\s+failed\b",
        r"\bfailed\s+to\b",
        r"\bcannot\s+open\b",
        r"\bcould\s+not\s+be\s+found\b",
        r"\bdoes\s+not\s+exist\b",
        r"\bno\s+such\s+file\b",
        r"\bpermission\s+denied\b",
        r"\binvalid\s+command\b",
        r"\binvalid\s+value\b",
        r"\bno\s+shift\s+enable\s+signal\s+was\s+defined\b",
    ]

    return any(re.search(pattern, lower, re.MULTILINE) for pattern in blocking_patterns)


def _has_successful_completion_evidence(original_log: str) -> bool:
    lower = (original_log or "").lower()

    downstream_markers = [
        "report_timing",
        "report_power",
        "report_area",
        "report_qor",
        "write_hdl",
        "write_sdc",
        "write_sdf",
        "write_db",
        "write_dft_atpg",
        "write_scandef",
        "report_scan_chains",
    ]

    marker_count = sum(1 for marker in downstream_markers if marker in lower)

    completion_markers = [
        "end verbose source",
        "finished",
        "error=0",
        "fatal=0",
        "errors: 0",
        "fatals: 0",
    ]

    has_completion_marker = any(marker in lower for marker in completion_markers)

    return marker_count >= 3 and has_completion_marker


def _is_successful_run_with_only_nonblocking_messages(original_log: str) -> bool:
    if not original_log:
        return False

    return (
        _has_successful_completion_evidence(original_log)
        and not _has_blocking_error_or_fatal(original_log)
    )


# ---------------------------------------------------------------------
# Graph retrieval
# ---------------------------------------------------------------------

def query_graph_for_codes(codes: List[str]) -> List[Dict[str, Any]]:
    """
    Retrieve graph-grounded diagnostic guidance for known error codes.

    Important:
    This returns not only node names, but also the actual grounding text:
    - ErrorCode.notes
    - ErrorCode.meaning
    - ErrorCode.default_fixability
    - IssueType.root_cause
    - IssueType.fixability
    - FixPattern.strategy
    - FixPattern.example_good
    - FixPattern.example_bad
    - Command.description
    - DocChunk.text

    Without these fields, the downstream fixer only sees vague labels and may
    invent invalid Tcl syntax.
    """
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
        e.meaning AS error_meaning,
        e.notes AS error_notes,
        e.default_fixability AS default_fixability,
        collect(DISTINCT i.name) AS issue_types,
        collect(DISTINCT i.root_cause) AS issue_root_causes,
        collect(DISTINCT i.fixability) AS issue_fixabilities,
        collect(DISTINCT f.name) AS fix_patterns,
        collect(DISTINCT f.strategy) AS fix_strategies,
        collect(DISTINCT f.example_bad) AS example_bad,
        collect(DISTINCT f.example_good) AS example_good,
        collect(DISTINCT c.name) AS affected_commands,
        collect(DISTINCT c.description) AS command_descriptions,
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

                rows.append(
                    {
                        "code": record["code"],
                        "error_meaning": record["error_meaning"] or "",
                        "error_notes": record["error_notes"] or "",
                        "default_fixability": record["default_fixability"] or "",
                        "issue_types": [
                            item for item in (record["issue_types"] or [])
                            if item
                        ],
                        "issue_root_causes": [
                            item for item in (record["issue_root_causes"] or [])
                            if isinstance(item, str) and item.strip()
                        ],
                        "issue_fixabilities": [
                            item for item in (record["issue_fixabilities"] or [])
                            if isinstance(item, str) and item.strip()
                        ],
                        "fix_patterns": [
                            item for item in (record["fix_patterns"] or [])
                            if item
                        ],
                        "fix_strategies": [
                            item for item in (record["fix_strategies"] or [])
                            if isinstance(item, str) and item.strip()
                        ],
                        "example_bad": [
                            item for item in (record["example_bad"] or [])
                            if isinstance(item, str) and item.strip()
                        ],
                        "example_good": [
                            item for item in (record["example_good"] or [])
                            if isinstance(item, str) and item.strip()
                        ],
                        "affected_commands": [
                            item for item in (record["affected_commands"] or [])
                            if item
                        ],
                        "command_descriptions": [
                            item for item in (record["command_descriptions"] or [])
                            if isinstance(item, str) and item.strip()
                        ],
                        "documentation": docs[:3],
                    }
                )

            return rows

    except Exception as exc:
        print(f"Graph Query Error: {exc}")
        return []


def _compact_graph_context(graph_context: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Compact graph rows while preserving the exact actionable guidance needed by
    the diagnostic LLM and fixer.
    """
    compact = []

    for row in graph_context:
        if not isinstance(row, dict):
            continue

        docs = row.get("documentation", [])
        short_docs = []

        if isinstance(docs, list):
            for doc in docs[:3]:
                if isinstance(doc, str) and doc.strip():
                    short_docs.append(doc.strip()[:700])

        compact.append(
            {
                "code": row.get("code"),
                "error_meaning": row.get("error_meaning", ""),
                "error_notes": row.get("error_notes", ""),
                "default_fixability": row.get("default_fixability", ""),
                "issue_types": row.get("issue_types", []),
                "issue_root_causes": row.get("issue_root_causes", []),
                "issue_fixabilities": row.get("issue_fixabilities", []),
                "fix_patterns": row.get("fix_patterns", []),
                "fix_strategies": row.get("fix_strategies", []),
                "example_bad": row.get("example_bad", []),
                "example_good": row.get("example_good", []),
                "affected_commands": row.get("affected_commands", []),
                "command_descriptions": row.get("command_descriptions", []),
                "documentation": short_docs,
            }
        )

    return compact


def _make_retrieved_notes(graph_context: List[Dict[str, Any]]) -> str:
    """
    Build a readable RAG note string containing exact command/fix instructions.

    This is what downstream agents are most likely to follow, so include:
    - exact ErrorCode.notes
    - exact FixPattern.strategy
    - examples
    """
    notes = []

    for row in graph_context:
        if not isinstance(row, dict):
            continue

        code = row.get("code", "")
        fragments = []

        if row.get("error_meaning"):
            fragments.append(f"meaning: {row['error_meaning']}")

        if row.get("default_fixability"):
            fragments.append(f"default fixability: {row['default_fixability']}")

        if row.get("error_notes"):
            fragments.append(f"error notes: {row['error_notes']}")

        issue_types = row.get("issue_types", [])
        if issue_types:
            fragments.append(f"issue types: {', '.join(str(x) for x in issue_types[:3])}")

        issue_root_causes = row.get("issue_root_causes", [])
        if issue_root_causes:
            fragments.append(
                "issue root causes: "
                + " | ".join(str(x) for x in issue_root_causes[:3])
            )

        issue_fixabilities = row.get("issue_fixabilities", [])
        if issue_fixabilities:
            fragments.append(
                "issue fixabilities: "
                + ", ".join(str(x) for x in issue_fixabilities[:3])
            )

        fix_patterns = row.get("fix_patterns", [])
        if fix_patterns:
            fragments.append(f"fix patterns: {', '.join(str(x) for x in fix_patterns[:3])}")

        fix_strategies = row.get("fix_strategies", [])
        if fix_strategies:
            fragments.append(
                "fix strategies: "
                + " | ".join(str(x) for x in fix_strategies[:3])
            )

        example_good = row.get("example_good", [])
        if example_good:
            fragments.append(
                "example good: "
                + " | ".join(str(x) for x in example_good[:3])
            )

        example_bad = row.get("example_bad", [])
        if example_bad:
            fragments.append(
                "example bad: "
                + " | ".join(str(x) for x in example_bad[:3])
            )

        affected_commands = row.get("affected_commands", [])
        if affected_commands:
            fragments.append(
                "affected commands: "
                + ", ".join(str(x) for x in affected_commands[:5])
            )

        command_descriptions = row.get("command_descriptions", [])
        if command_descriptions:
            fragments.append(
                "command descriptions: "
                + " | ".join(str(x) for x in command_descriptions[:3])
            )

        docs = row.get("documentation", [])
        if docs:
            fragments.append(f"documentation: {str(docs[0])[:500]}")

        if fragments:
            notes.append(f"{code}: " + "\n".join(fragments))

    return "\n\n".join(notes[:5])


# ---------------------------------------------------------------------
# Output payload builders
# ---------------------------------------------------------------------

def _build_successful_run_payload(
    original_tcl: str,
    original_log: str,
    user_message: str,
    parsed_analysis: Dict[str, Any],
) -> Dict[str, Any]:
    tcl_features = _extract_tcl_features(original_tcl)
    log_features = _extract_log_features(original_log)

    retrieved_notes = (
        "Successful Genus run detected. The log reached downstream report/write "
        "commands and no blocking Error/Fatal condition was found. Warning and "
        "Info messages are observations only. Classify this case as no_fix_needed. "
        "Do not generate a patched Tcl script."
    )

    return {
        "agent_stage": "diagnostic_evidence_extracted",
        "next_agent": "error_diagnosis_agent",
        "original_tcl": original_tcl,
        "original_log_excerpt": _collect_log_excerpt(original_log, []),
        "evidence": {
            "has_issue": False,
            "summary": {
                "overall_severity": "INFO",
                "num_anomalies": 0,
                "successful_completion": True,
                "original_summary": parsed_analysis.get("summary", {})
                if isinstance(parsed_analysis, dict)
                else {},
            },
            "primary_error_codes": [],
            "failed_command": "",
            "candidate_failed_commands": [],
            "downstream_errors": [],
            "top_anomalies": [],
            "tcl_features": tcl_features,
            "log_features": log_features,
            "retrieved_notes": retrieved_notes,
            "graph_context": [],
            "user_message": user_message,
        },
    }


def extract_diagnostic_evidence(payload: str = "") -> dict:
    """
    Evidence extractor.

    Key fixes in this version:
    1. Prints raw payload/file-block diagnostics so you can see whether ADK Web
       actually passed the full uploaded log into the tool.
    2. Prevents _split_contaminated_tcl_and_log from shrinking a real log to a
       tiny fragment.
    3. Extracts error codes from analyzer output PLUS original_log PLUS raw
       payload, so DFT-116/TUI-23 can still reach Neo4j even if log extraction
       is imperfect.
    4. Keeps logic generic. No dataset-specific fix is hardcoded here.
    """
    payload = payload or ""

    original_tcl, original_log, user_message = _extract_tcl_and_log(payload)
    parsed_analysis = _build_analysis_result(original_tcl, original_log)

    if _is_successful_run_with_only_nonblocking_messages(original_log):
        result = _build_successful_run_payload(
            original_tcl=original_tcl,
            original_log=original_log,
            user_message=user_message,
            parsed_analysis=parsed_analysis,
        )

        print("\n==================================================")
        print("DEBUG: Diagnostic Evidence Extractor")
        print(f"DEBUG: Tcl chars extracted: {len(original_tcl)}")
        print(f"DEBUG: Log chars extracted: {len(original_log)}")
        print("DEBUG: Successful completed run detected.")
        print("DEBUG: Warning-code Neo4j retrieval skipped for no-fix case.")
        print("DEBUG: has_issue = False")
        print("==================================================\n")

        return result

    summary = parsed_analysis.get("summary", {}) if isinstance(parsed_analysis, dict) else {}
    anomalies = parsed_analysis.get("anomalies", []) if isinstance(parsed_analysis, dict) else []

    analysis_codes = _get_error_codes(parsed_analysis)
    fallback_code_blob = "\n".join(
        part for part in [original_log, payload]
        if isinstance(part, str) and part.strip()
    )
    fallback_codes = _extract_error_codes_from_text(fallback_code_blob)
    error_codes = _unique_preserve(analysis_codes + fallback_codes)

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
    print(f"DEBUG: Analyzer error codes: {analysis_codes}")
    print(f"DEBUG: Fallback raw/log error codes: {fallback_codes}")
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
