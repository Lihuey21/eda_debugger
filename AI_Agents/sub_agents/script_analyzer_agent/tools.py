"""
tools.py

Stage 1: Script Analyzer tools
- analyze_tcl_script: static + Genus-aware Tcl analysis
- analyze_eda_log: parse EDA/tool logs with Genus-aware extraction
- analyze_session: combine Tcl + log into one structured JSON output
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

_SEVERITY_ORDER = {"INFO": 0, "WARNING": 1, "ERROR": 2, "FATAL": 3}


def _max_severity(a: str, b: str) -> str:
    return a if _SEVERITY_ORDER.get(a, 0) >= _SEVERITY_ORDER.get(b, 0) else b


def _safe_strip(s: Optional[str]) -> Optional[str]:
    if s is None:
        return None
    s2 = s.strip()
    return s2 if s2 else None


def _as_int(s: Optional[str]) -> Optional[int]:
    if not s:
        return None
    try:
        return int(s)
    except Exception:
        return None


def _normalize_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _dedupe_anomalies(anoms: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen = set()
    out = []
    for a in anoms:
        key = (
            a.get("type"),
            a.get("severity"),
            a.get("code"),
            a.get("file"),
            a.get("line"),
            a.get("message"),
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(a)
    return out


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


_LOG_START_MARKERS = [
    "Cadence Genus(TM) Synthesis Solution.",
    "#@ Processing -files option",
    "@genus 1> source ",
    "Encountered problems processing file:",
    "Checking out license: Genus_Synthesis",
    "Version:",
]


def _split_contaminated_tcl_and_log(
    tcl_text: Optional[str],
    log_text: Optional[str],
) -> Tuple[Optional[str], Optional[str]]:
    tcl = _normalize_newlines(tcl_text) if isinstance(tcl_text, str) else None
    log = _normalize_newlines(log_text) if isinstance(log_text, str) else None

    if tcl:
        split_pos = None
        for marker in _LOG_START_MARKERS:
            idx = tcl.find(marker)
            if idx != -1 and (split_pos is None or idx < split_pos):
                split_pos = idx

        if split_pos is not None:
            extracted_tcl = tcl[:split_pos].rstrip()
            extracted_log = tcl[split_pos:].lstrip()

            if extracted_tcl:
                tcl = extracted_tcl
            if extracted_log:
                if log and log.strip():
                    if extracted_log not in log:
                        log = extracted_log + "\n" + log
                else:
                    log = extracted_log

    if isinstance(tcl, str):
        tcl = tcl.strip() or None
    if isinstance(log, str):
        log = log.strip() or None

    return tcl, log


_GENUS_ERROR_CODE_RE = re.compile(r"\b([A-Z]{2,10}-\d{1,5})\b")
_FILE_LINE_RE = re.compile(r"(?P<file>[^:\s]+):(?P<line>\d+)(?::(?P<col>\d+))?")
_GENERIC_PREFIX_RE = re.compile(
    r"^\s*(?P<sev>ERROR|WARNING|INFO|FATAL)\s*:\s*(?P<msg>.*)$",
    re.IGNORECASE,
)
_VERILATOR_RE = re.compile(
    r"^\s*%(?P<severity>Warning|Error|Fatal)-(?P<code>[A-Za-z0-9_]+):\s+"
    r"(?P<file>[^:]+):(?P<line>\d+):(?P<col>\d+):\s*(?P<msg>.*)$"
)

_READ_LIBS_RE = re.compile(r"^\s*read_libs\b(.*)$")
_READ_HDL_RE = re.compile(r"^\s*read_hdl\b(.*)$")
_READ_SDC_RE = re.compile(r"^\s*read_sdc\b\s+(.+?)\s*$")
_ELAB_RE = re.compile(r"^\s*elaborate\b(?:\s+([^\s#;]+))?")
_SET_DB_LIB_RE = re.compile(r"^\s*set_db\s+init_lib_search_path\s+(.+?)\s*$")
_SET_DB_HDL_RE = re.compile(r"^\s*set_db\s+init_hdl_search_path\s+(.+?)\s*$")
_SYN_GENERIC_RE = re.compile(r"^\s*syn_generic\b")
_SYN_MAP_RE = re.compile(r"^\s*syn_map\b")
_SYN_OPT_RE = re.compile(r"^\s*syn_opt\b")


def _clean_tcl_value(raw: str) -> str:
    value = raw.strip()
    if "#" in value:
        value = value.split("#", 1)[0].rstrip()
    return value.strip()


def _split_brace_or_space_list(raw: str) -> List[str]:
    text = _clean_tcl_value(raw)
    if not text:
        return []
    if text.startswith("{") and text.endswith("}"):
        inner = text[1:-1].strip()
        return [tok for tok in re.split(r"\s+", inner) if tok]
    return [tok for tok in re.split(r"\s+", text) if tok]


def _extract_failed_command_hint(line: str) -> Optional[str]:
    lower = line.lower()
    for cmd in ["read_libs", "read_sdc", "elaborate", "read_hdl", "syn_generic", "syn_map", "syn_opt"]:
        if cmd in lower:
            return cmd
    return None


def _logical_tcl_lines(script: str) -> List[Tuple[int, str]]:
    raw_lines = _normalize_newlines(script).splitlines()
    out: List[Tuple[int, str]] = []

    buf: List[str] = []
    start_line: Optional[int] = None
    brace_depth = 0

    for idx, raw in enumerate(raw_lines, start=1):
        line = raw.rstrip("\n")
        stripped = line.strip()

        if not buf:
            start_line = idx

        if not buf and (not stripped or stripped.startswith("#") or stripped.startswith(";#")):
            out.append((idx, line))
            continue

        buf.append(line)
        brace_depth += line.count("{") - line.count("}")
        continued = stripped.endswith("\\") and not stripped.endswith("\\\\")

        if brace_depth <= 0 and not continued:
            merged = " ".join(part.strip() for part in buf if part.strip())
            out.append((start_line or idx, merged))
            buf = []
            start_line = None
            brace_depth = 0

    if buf:
        merged = " ".join(part.strip() for part in buf if part.strip())
        out.append((start_line or len(raw_lines), merged))

    return out


def analyze_tcl_script(script: str) -> Dict[str, Any]:
    script = _normalize_newlines(script)
    raw_lines = script.splitlines()
    logical_lines = _logical_tcl_lines(script)

    anomalies: List[Dict[str, Any]] = []

    brace_balance = 0
    bracket_balance = 0
    quote_open = False

    suspicious_cmds = [
        r"^\s*source\s+\S+",
        r"^\s*exec\s+.+",
    ]

    lib_search_paths: List[Dict[str, Any]] = []
    hdl_search_paths: List[Dict[str, Any]] = []
    read_libs_entries: List[Dict[str, Any]] = []
    read_hdl_entries: List[Dict[str, Any]] = []
    read_sdc_entries: List[Dict[str, Any]] = []
    elaborate_targets: List[Dict[str, Any]] = []
    flow_steps: List[Dict[str, Any]] = []
    commands_found: List[str] = []

    for i, raw in enumerate(raw_lines, start=1):
        line = raw.rstrip("\n")
        stripped = line.strip()

        if stripped.startswith("#") or stripped.startswith(";#") or not stripped:
            continue

        brace_balance += line.count("{") - line.count("}")
        bracket_balance += line.count("[") - line.count("]")

        quotes = re.findall(r'(?<!\\)"', line)
        if len(quotes) % 2 == 1:
            quote_open = not quote_open

        if stripped.endswith("\\") and not stripped.endswith("\\\\") and i == len(raw_lines):
            anomalies.append({
                "type": "tcl_continuation_at_eof",
                "severity": "WARNING",
                "code": "TCL_CONT_EOF",
                "file": None,
                "line": i,
                "col": None,
                "message": "Line ends with a continuation backslash at end-of-file; script may be truncated.",
                "evidence": line,
            })

        for pat in suspicious_cmds:
            if re.search(pat, line):
                anomalies.append({
                    "type": "tcl_suspicious_command",
                    "severity": "INFO",
                    "code": "TCL_SUSPICIOUS",
                    "file": None,
                    "line": i,
                    "col": None,
                    "message": f"Command may depend on environment/filesystem: {stripped.split()[0]}",
                    "evidence": line,
                })
                break

        if brace_balance < 0:
            anomalies.append({
                "type": "tcl_unmatched_brace",
                "severity": "ERROR",
                "code": "TCL_BRACE_NEG",
                "file": None,
                "line": i,
                "col": None,
                "message": "Encountered '}' without matching '{' earlier.",
                "evidence": line,
            })
            brace_balance = 0

        if bracket_balance < 0:
            anomalies.append({
                "type": "tcl_unmatched_bracket",
                "severity": "ERROR",
                "code": "TCL_BRACKET_NEG",
                "file": None,
                "line": i,
                "col": None,
                "message": "Encountered ']' without matching '[' earlier.",
                "evidence": line,
            })
            bracket_balance = 0

    for i, line in logical_lines:
        stripped = line.strip()

        if stripped.startswith("#") or stripped.startswith(";#") or not stripped:
            continue

        m = _SET_DB_LIB_RE.match(line)
        if m:
            value = _clean_tcl_value(m.group(1))
            lib_search_paths.append({"line": i, "value": value})
            commands_found.append("set_db init_lib_search_path")
            flow_steps.append({"line": i, "command": "set_db init_lib_search_path"})
            continue

        m = _SET_DB_HDL_RE.match(line)
        if m:
            value = _clean_tcl_value(m.group(1))
            hdl_search_paths.append({"line": i, "value": value})
            commands_found.append("set_db init_hdl_search_path")
            flow_steps.append({"line": i, "command": "set_db init_hdl_search_path"})
            continue

        m = _READ_LIBS_RE.match(line)
        if m:
            payload = m.group(1)
            libs = _split_brace_or_space_list(payload)
            read_libs_entries.append({"line": i, "libs": libs, "raw": stripped})
            commands_found.append("read_libs")
            flow_steps.append({"line": i, "command": "read_libs"})
            if not libs:
                anomalies.append({
                    "type": "eda_missing_read_libs_argument",
                    "severity": "WARNING",
                    "code": "EDA_READ_LIBS_EMPTY",
                    "file": None,
                    "line": i,
                    "col": None,
                    "message": "read_libs found but no library argument was extracted.",
                    "evidence": line,
                })
            continue

        m = _READ_HDL_RE.match(line)
        if m:
            payload = m.group(1)
            files = _split_brace_or_space_list(payload)
            read_hdl_entries.append({"line": i, "files": files, "raw": stripped})
            commands_found.append("read_hdl")
            flow_steps.append({"line": i, "command": "read_hdl"})
            if not files:
                anomalies.append({
                    "type": "eda_missing_read_hdl_argument",
                    "severity": "WARNING",
                    "code": "EDA_READ_HDL_EMPTY",
                    "file": None,
                    "line": i,
                    "col": None,
                    "message": "read_hdl found but no HDL files were extracted.",
                    "evidence": line,
                })
            continue

        m = _READ_SDC_RE.match(line)
        if m:
            path = _clean_tcl_value(m.group(1))
            read_sdc_entries.append({"line": i, "path": path, "raw": stripped})
            commands_found.append("read_sdc")
            flow_steps.append({"line": i, "command": "read_sdc"})
            continue

        m = _ELAB_RE.match(line)
        if m:
            top = _safe_strip(m.group(1))
            elaborate_targets.append({"line": i, "top": top, "raw": stripped})
            commands_found.append("elaborate")
            flow_steps.append({"line": i, "command": "elaborate"})
            continue

        if _SYN_GENERIC_RE.match(line):
            commands_found.append("syn_generic")
            flow_steps.append({"line": i, "command": "syn_generic"})
            continue

        if _SYN_MAP_RE.match(line):
            commands_found.append("syn_map")
            flow_steps.append({"line": i, "command": "syn_map"})
            continue

        if _SYN_OPT_RE.match(line):
            commands_found.append("syn_opt")
            flow_steps.append({"line": i, "command": "syn_opt"})
            continue

    if brace_balance != 0:
        anomalies.append({
            "type": "tcl_brace_imbalance",
            "severity": "ERROR",
            "code": "TCL_BRACE_IMBALANCE",
            "file": None,
            "line": None,
            "col": None,
            "message": f"Unbalanced braces detected. Net balance = {brace_balance}.",
            "evidence": None,
        })

    if bracket_balance != 0:
        anomalies.append({
            "type": "tcl_bracket_imbalance",
            "severity": "ERROR",
            "code": "TCL_BRACKET_IMBALANCE",
            "file": None,
            "line": None,
            "col": None,
            "message": f"Unbalanced brackets detected. Net balance = {bracket_balance}.",
            "evidence": None,
        })

    if quote_open:
        anomalies.append({
            "type": "tcl_quote_imbalance",
            "severity": "ERROR",
            "code": "TCL_QUOTE_IMBALANCE",
            "file": None,
            "line": None,
            "col": None,
            "message": "Unbalanced double-quotes detected (odd number of unescaped quotes).",
            "evidence": None,
        })

    found_cmds = [step["command"] for step in flow_steps]

    if "read_libs" not in found_cmds:
        anomalies.append({
            "type": "eda_missing_command",
            "severity": "WARNING",
            "code": "EDA_MISSING_READ_LIBS",
            "file": None,
            "line": None,
            "col": None,
            "message": "No read_libs command found in Tcl script.",
            "evidence": None,
        })

    if "read_hdl" not in found_cmds:
        anomalies.append({
            "type": "eda_missing_command",
            "severity": "WARNING",
            "code": "EDA_MISSING_READ_HDL",
            "file": None,
            "line": None,
            "col": None,
            "message": "No read_hdl command found in Tcl script.",
            "evidence": None,
        })

    if "elaborate" not in found_cmds:
        anomalies.append({
            "type": "eda_missing_command",
            "severity": "WARNING",
            "code": "EDA_MISSING_ELABORATE",
            "file": None,
            "line": None,
            "col": None,
            "message": "No elaborate command found in Tcl script.",
            "evidence": None,
        })

    if "read_sdc" not in found_cmds:
        anomalies.append({
            "type": "eda_missing_command",
            "severity": "WARNING",
            "code": "EDA_MISSING_READ_SDC",
            "file": None,
            "line": None,
            "col": None,
            "message": "No read_sdc command found in Tcl script.",
            "evidence": None,
        })

    command_to_first_line = {}
    for step in flow_steps:
        command_to_first_line.setdefault(step["command"], step["line"])

    if "read_hdl" in command_to_first_line and "elaborate" in command_to_first_line:
        if command_to_first_line["elaborate"] < command_to_first_line["read_hdl"]:
            anomalies.append({
                "type": "eda_flow_order_issue",
                "severity": "WARNING",
                "code": "EDA_FLOW_ORDER_ELAB_BEFORE_HDL",
                "file": None,
                "line": command_to_first_line["elaborate"],
                "col": None,
                "message": "elaborate appears before read_hdl; flow order may be invalid.",
                "evidence": None,
            })

    if "elaborate" in command_to_first_line and "read_sdc" in command_to_first_line:
        if command_to_first_line["read_sdc"] < command_to_first_line["elaborate"]:
            anomalies.append({
                "type": "eda_flow_order_issue",
                "severity": "INFO",
                "code": "EDA_FLOW_ORDER_SDC_BEFORE_ELAB",
                "file": None,
                "line": command_to_first_line["read_sdc"],
                "col": None,
                "message": "read_sdc appears before elaborate. This may be acceptable in some flows, but verify intended ordering.",
                "evidence": None,
            })

    anomalies = _dedupe_anomalies(anomalies)

    overall = "INFO"
    for a in anomalies:
        overall = _max_severity(overall, a.get("severity", "INFO"))

    eda_context = {
        "tool_guess": "genus",
        "commands_found": _unique_preserve(commands_found),
        "lib_search_paths": lib_search_paths,
        "hdl_search_paths": hdl_search_paths,
        "read_libs": read_libs_entries,
        "read_hdl": read_hdl_entries,
        "read_sdc": read_sdc_entries,
        "elaborate_targets": elaborate_targets,
        "flow_steps": flow_steps,
        "flow_step_presence": {
            "syn_generic": "syn_generic" in found_cmds,
            "syn_map": "syn_map" in found_cmds,
            "syn_opt": "syn_opt" in found_cmds,
        },
    }

    return {
        "schema_version": 2,
        "input_kind": "tcl",
        "summary": {
            "overall_severity": overall,
            "num_lines": len(raw_lines),
            "num_anomalies": len(anomalies),
        },
        "eda_context": eda_context,
        "anomalies": anomalies,
        "handoff": {
            "next_agent": "error_diagnosis_agent" if overall in ("WARNING", "ERROR", "FATAL") else None,
            "notes": "Static + EDA-aware Tcl analysis complete. Downstream diagnosis should use eda_context, anomalies, and log evidence.",
        },
    }


def analyze_eda_log(log_text: str) -> Dict[str, Any]:
    log_text = _normalize_newlines(log_text)
    lines = log_text.splitlines()
    anomalies: List[Dict[str, Any]] = []

    error_codes: List[str] = _unique_preserve(_GENUS_ERROR_CODE_RE.findall(log_text))
    suspected_failed_commands: List[str] = []
    tool_guess = "genus" if "genus" in log_text.lower() else "generic_eda"
    overall = "INFO"

    for idx, raw in enumerate(lines, start=1):
        line = raw.rstrip("\n")
        lower = line.lower()

        failed_cmd = _extract_failed_command_hint(line)
        if failed_cmd:
            suspected_failed_commands.append(failed_cmd)

        m = _VERILATOR_RE.match(line)
        if m:
            sev_raw = m.group("severity").upper()
            sev = "WARNING" if sev_raw == "WARNING" else ("ERROR" if sev_raw == "ERROR" else "FATAL")
            overall = _max_severity(overall, sev)
            anomalies.append({
                "type": "tool_message",
                "severity": sev,
                "code": m.group("code"),
                "file": _safe_strip(m.group("file")),
                "line": _as_int(m.group("line")),
                "col": _as_int(m.group("col")),
                "message": _safe_strip(m.group("msg")),
                "evidence": line,
            })
            continue

        g = _GENERIC_PREFIX_RE.match(line)
        if g:
            sev = g.group("sev").upper()
            sev = "WARNING" if sev == "WARNING" else sev
            overall = _max_severity(overall, sev)

            msg = g.group("msg").strip()
            fl = _FILE_LINE_RE.search(msg)
            code_match = _GENUS_ERROR_CODE_RE.search(msg)

            anomalies.append({
                "type": "tool_message",
                "severity": sev,
                "code": code_match.group(1) if code_match else None,
                "file": _safe_strip(fl.group("file")) if fl else None,
                "line": _as_int(fl.group("line")) if fl else None,
                "col": _as_int(fl.group("col")) if fl else None,
                "message": msg,
                "evidence": line,
            })
            continue

        if re.search(r"\b(error|fatal)\b", line, re.IGNORECASE):
            overall = _max_severity(overall, "ERROR")
            fl = _FILE_LINE_RE.search(line)
            code_match = _GENUS_ERROR_CODE_RE.search(line)
            anomalies.append({
                "type": "tool_message",
                "severity": "ERROR",
                "code": code_match.group(1) if code_match else None,
                "file": _safe_strip(fl.group("file")) if fl else None,
                "line": _as_int(fl.group("line")) if fl else None,
                "col": _as_int(fl.group("col")) if fl else None,
                "message": line.strip(),
                "evidence": line,
            })
            continue

        if re.search(r"\bwarning\b", line, re.IGNORECASE):
            overall = _max_severity(overall, "WARNING")
            fl = _FILE_LINE_RE.search(line)
            code_match = _GENUS_ERROR_CODE_RE.search(line)
            anomalies.append({
                "type": "tool_message",
                "severity": "WARNING",
                "code": code_match.group(1) if code_match else None,
                "file": _safe_strip(fl.group("file")) if fl else None,
                "line": _as_int(fl.group("line")) if fl else None,
                "col": _as_int(fl.group("col")) if fl else None,
                "message": line.strip(),
                "evidence": line,
            })
            continue

    if any(code in error_codes for code in ["FILE-100", "LBR-68", "TUI-24"]):
        overall = _max_severity(overall, "ERROR")
        anomalies.append({
            "type": "runtime_library_load_error",
            "severity": "ERROR",
            "code": next((c for c in error_codes if c in {"FILE-100", "LBR-68", "TUI-24"}), "FILE-100"),
            "file": None,
            "line": None,
            "col": None,
            "message": "Genus reported library file open / existence / attribute errors during read_libs.",
            "evidence": " ".join(error_codes),
        })

    anomalies = _dedupe_anomalies(anomalies)
    error_codes = _unique_preserve(error_codes)
    suspected_failed_commands = _unique_preserve(suspected_failed_commands)

    runtime_context = {
        "tool_guess": tool_guess,
        "error_codes": error_codes,
        "suspected_failed_commands": suspected_failed_commands,
        "first_error_code": error_codes[0] if error_codes else None,
        "first_suspected_failed_command": suspected_failed_commands[0] if suspected_failed_commands else None,
    }

    return {
        "schema_version": 2,
        "input_kind": "log",
        "summary": {
            "overall_severity": overall,
            "num_lines": len(lines),
            "num_anomalies": len(anomalies),
        },
        "runtime_context": runtime_context,
        "anomalies": anomalies,
        "handoff": {
            "next_agent": "error_diagnosis_agent" if overall in ("WARNING", "ERROR", "FATAL") else None,
            "notes": "Log parsing complete. Downstream diagnosis should map error_codes, failed_command hints, and anomalies to root cause.",
        },
    }


def analyze_session(tcl_script: str, log_text: str) -> Dict[str, Any]:
    tcl_script, log_text = _split_contaminated_tcl_and_log(tcl_script, log_text)

    tcl_res = analyze_tcl_script(tcl_script or "")
    log_res = analyze_eda_log(log_text or "")

    merged: List[Dict[str, Any]] = []

    for a in tcl_res.get("anomalies", []):
        a2 = dict(a)
        a2["source"] = "tcl"
        merged.append(a2)

    for a in log_res.get("anomalies", []):
        a2 = dict(a)
        a2["source"] = "log"
        merged.append(a2)

    merged = _dedupe_anomalies(merged)

    overall = "INFO"
    for a in merged:
        overall = _max_severity(overall, a.get("severity", "INFO"))

    return {
        "schema_version": 2,
        "input_kind": "session",
        "summary": {
            "overall_severity": overall,
            "num_anomalies": len(merged),
            "tcl_overall": tcl_res.get("summary", {}).get("overall_severity"),
            "log_overall": log_res.get("summary", {}).get("overall_severity"),
        },
        "eda_context": tcl_res.get("eda_context", {}),
        "runtime_context": log_res.get("runtime_context", {}),
        "anomalies": merged,
        "handoff": {
            "next_agent": "error_diagnosis_agent" if overall in ("WARNING", "ERROR", "FATAL") else None,
            "notes": "Merged static Tcl findings + runtime log findings for downstream diagnosis/fixing.",
        },
    }


def _next_agent_for_result(result: dict) -> str | None:
    anomalies = result.get("anomalies", [])
    runtime_context = result.get("runtime_context", {})
    error_codes = runtime_context.get("error_codes", []) if isinstance(runtime_context, dict) else []
    overall = result.get("summary", {}).get("overall_severity", "INFO")

    has_problem = bool(anomalies) or bool(error_codes) or overall in ("WARNING", "ERROR", "FATAL")
    return "error_diagnosis_agent" if has_problem else None


# --- [FIXED] THE TOKEN DIET HANDOFF FUNCTIONS ---
# These functions explicitly drop `original_log` to save token limits!

def analyze_tcl_script_handoff(script: str) -> dict:
    clean_tcl, extracted_log = _split_contaminated_tcl_and_log(script, None)
    result = analyze_tcl_script(clean_tcl or "")
    
    return {
        "agent_stage": "analysis_complete",
        "next_agent": _next_agent_for_result(result),
        "original_tcl": clean_tcl,
        "original_log": None, # Dropped to save LLM context window
        "analysis_result": result,
    }


def analyze_eda_log_handoff(log_text: str) -> dict:
    result = analyze_eda_log(log_text)
    
    return {
        "agent_stage": "analysis_complete",
        "next_agent": _next_agent_for_result(result),
        "original_tcl": None,
        "original_log": None, # Dropped to save LLM context window
        "analysis_result": result,
    }


def analyze_session_handoff(tcl_script: str, log_text: str) -> dict:
    clean_tcl, clean_log = _split_contaminated_tcl_and_log(tcl_script, log_text)
    result = analyze_session(clean_tcl or "", clean_log or "")
    
    return {
        "agent_stage": "analysis_complete",
        "next_agent": _next_agent_for_result(result),
        "original_tcl": clean_tcl, # Keep the Tcl so the Fixer can patch it
        "original_log": None,      # Dropped to save LLM context window
        "analysis_result": result,
    }