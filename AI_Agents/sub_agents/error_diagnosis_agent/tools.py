from __future__ import annotations

import ast
import json
import os
from typing import Any, Dict, List, Optional


# --- DOTENV LOADER ---
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    print("Warning: python-dotenv not installed. Neo4j may fail to connect.")


# --- NEO4J GRAPH DB CONNECTION ---
driver = None


def get_neo4j_driver():
    """Initializes the Neo4j driver gracefully using AuraDB credentials from .env."""
    global driver

    if driver is None:
        try:
            from neo4j import GraphDatabase

            uri = os.getenv("NEO4J_URI")
            user = os.getenv("NEO4J_USERNAME") or os.getenv("NEO4J_USER")
            password = os.getenv("NEO4J_PASSWORD")

            if not uri or not user or not password:
                print("Warning: Neo4j env vars missing. Check NEO4J_URI, NEO4J_USERNAME/NEO4J_USER, NEO4J_PASSWORD.")
                return None

            driver = GraphDatabase.driver(uri, auth=(user, password))
            driver.verify_connectivity()
            print("Neo4j connectivity verified.")

        except Exception as e:
            print(f"Warning: Neo4j not connected. {e}")
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


def _extract_outer_payload(payload: Any) -> Dict[str, Any]:
    payload = _safe_json_loads(payload)

    if not isinstance(payload, dict):
        return {}

    if any(k in payload for k in ["original_tcl", "original_log", "analysis_result", "diagnosis_result", "agent_stage"]):
        return payload

    for key in ["result", "payload", "analysis_payload", "diagnosis_payload", "data"]:
        if key in payload:
            inner = _safe_json_loads(payload[key])
            if isinstance(inner, dict):
                merged = dict(inner)
                for keep in ["original_tcl", "original_log", "analysis_result", "diagnosis_result", "agent_stage"]:
                    if keep in payload and keep not in merged:
                        merged[keep] = payload[keep]
                return merged

    return payload


def _extract_inner_analysis(payload: Any) -> Dict[str, Any]:
    payload = _extract_outer_payload(payload)

    if "analysis_result" in payload:
        inner = payload["analysis_result"]
        if isinstance(inner, dict):
            return inner
        if isinstance(inner, str):
            parsed = _safe_json_loads(inner)
            return parsed if isinstance(parsed, dict) else {}
        return {}

    for wrapper_key in [
        "analyze_tcl_script_json_response",
        "analyze_eda_log_json_response",
        "analyze_session_json_response",
    ]:
        if wrapper_key in payload:
            wrapper = payload.get(wrapper_key, {})
            if isinstance(wrapper, dict):
                inner = wrapper.get("result", "{}")
                parsed = _safe_json_loads(inner)
                return parsed if isinstance(parsed, dict) else {}

    return payload if isinstance(payload, dict) else {}


def _recover_original_from_analysis(parsed_analysis: Dict[str, Any], key: str) -> Optional[str]:
    if not isinstance(parsed_analysis, dict):
        return None

    raw_inputs = parsed_analysis.get("raw_inputs", {})
    if isinstance(raw_inputs, str):
        raw_inputs = _safe_json_loads(raw_inputs)

    if isinstance(raw_inputs, dict):
        value = raw_inputs.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    value = parsed_analysis.get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()

    return None


def _unique_preserve(seq: List[Any]) -> List[Any]:
    seen = set()
    out = []
    for item in seq:
        if item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


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

    return _unique_preserve([c for c in codes + anomaly_codes if isinstance(c, str) and c.strip()])


def query_graph_for_codes(codes: List[str]) -> List[Dict[str, Any]]:
    """
    Retrieves Neo4j context for all candidate error codes.
    This function does not decide the final fix. It only retrieves graph context.
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
                docs = [d for d in (record["documentation"] or []) if isinstance(d, str) and d.strip()]
                issue_types = [x for x in (record["issue_types"] or []) if x]
                fix_patterns = [x for x in (record["fix_patterns"] or []) if x]
                affected_commands = [x for x in (record["affected_commands"] or []) if x]

                rows.append({
                    "code": record["code"],
                    "issue_types": issue_types,
                    "fix_patterns": fix_patterns,
                    "affected_commands": affected_commands,
                    "documentation": docs,
                })

            return rows

    except Exception as e:
        print(f"Graph Query Error: {e}")
        return []


def diagnose_analysis_handoff(analysis_payload: Any) -> dict:
    """
    Retrieves structured analyzer evidence and Neo4j RAG context.
    The LLM diagnosis agent uses this output to generate the final diagnosis.
    """
    outer = _extract_outer_payload(analysis_payload)
    parsed_analysis = _extract_inner_analysis(analysis_payload)

    summary = parsed_analysis.get("summary", {}) if isinstance(parsed_analysis, dict) else {}
    anomalies = parsed_analysis.get("anomalies", []) if isinstance(parsed_analysis, dict) else []
    runtime_context = parsed_analysis.get("runtime_context", {}) if isinstance(parsed_analysis, dict) else {}
    eda_context = parsed_analysis.get("eda_context", {}) if isinstance(parsed_analysis, dict) else {}

    error_codes = _get_error_codes(parsed_analysis)

    original_tcl = outer.get("original_tcl")
    original_log = outer.get("original_log")

    if not isinstance(original_tcl, str) or not original_tcl.strip():
        original_tcl = _recover_original_from_analysis(parsed_analysis, "original_tcl")

    if not isinstance(original_log, str) or not original_log.strip():
        original_log = _recover_original_from_analysis(parsed_analysis, "original_log")

    should_skip = (
        summary.get("overall_severity") == "INFO"
        and summary.get("num_anomalies", 0) == 0
        and not anomalies
        and not error_codes
    )

    if should_skip:
        return {
            "agent_stage": "diagnosis_context_ready",
            "next_agent": "script_fixer_agent",
            "original_tcl": original_tcl,
            "original_log": original_log,
            "analysis_result": parsed_analysis,
            "diagnosis_evidence": {
                "has_issue": False,
                "error_codes": [],
                "graph_context": [],
                "notes": "No issue detected."
            }
        }

    graph_context = query_graph_for_codes(error_codes)

    combined_docs = []
    for row in graph_context:
        for doc in row.get("documentation", []):
            if doc not in combined_docs:
                combined_docs.append(doc)

    print("\n==================================================")
    print(f"DEBUG: Candidate error codes for Neo4j RAG: {error_codes}")
    print(f"DEBUG: Retrieved graph rows: {len(graph_context)}")
    print("==================================================\n")

    return {
        "agent_stage": "diagnosis_context_ready",
        "next_agent": "script_fixer_agent",
        "original_tcl": original_tcl,
        "original_log": original_log,
        "analysis_result": parsed_analysis,
        "diagnosis_evidence": {
            "has_issue": True,
            "error_codes": error_codes,
            "summary": summary,
            "runtime_context": runtime_context,
            "eda_context": eda_context,
            "anomalies": anomalies,
            "graph_context": graph_context,
            "retrieved_notes": " | ".join(combined_docs) if combined_docs else "",
            "instruction": "Use analyzer evidence plus graph_context to infer root cause, downstream errors, fixability, and recommended fix pattern."
        }
    }