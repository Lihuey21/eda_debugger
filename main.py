from __future__ import annotations

import re
import time
import uuid
from typing import List, Optional, Tuple

from fastapi import FastAPI, File, Form, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from AI_Agents.agent import root_agent as orchestrator_agent
from AI_Agents.sub_agents.eda_debug_pipeline.agent import root_agent as pipeline_agent


app = FastAPI(title="EDA Debugger Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

session_service = InMemorySessionService()

orchestrator_runner = Runner(
    app_name="eda_debugger_orchestrator",
    agent=orchestrator_agent,
    session_service=session_service,
)

pipeline_runner = Runner(
    app_name="eda_debugger_pipeline",
    agent=pipeline_agent,
    session_service=session_service,
)


# ------------------------------------------------------------
# Health check
# ------------------------------------------------------------

@app.get("/")
def health_check():
    return {
        "status": "ok",
        "service": "EDA Debugger Backend",
        "message": "FastAPI backend is running.",
        "routing": {
            "simple_greeting": "local_fast_response",
            "general_chat": "orchestrator_guarded_no_pipeline",
            "tcl_plus_log_upload": "pipeline_direct",
        },
    }


# ------------------------------------------------------------
# File type / code detection helpers
# ------------------------------------------------------------

IMPORTANT_LOG_KEYWORDS = [
    "error",
    "warning",
    "fatal",
    "exception",
    "failed",
    "cannot",
    "can't",
    "invalid",
    "not found",
    "does not exist",
    "no such file",
    "permission",
    "denied",
    "TUI-",
    "LBR-",
    "FILE-",
    "ELAB-",
    "CDFG-",
    "VLOG-",
    "SYN-",
    "read_libs",
    "read_hdl",
    "elaborate",
    "read_sdc",
    "syn_generic",
    "syn_map",
    "syn_opt",
    "set_db",
]

TCL_HINTS = [
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

LOG_HINTS = [
    "cadence genus",
    "@genus",
    "genus",
    "error",
    "warning",
    "fatal",
    "tui-",
    "lbr-",
    "file-",
    "elab-",
    "cdfg-",
    "encountered problems processing file",
    "finished executable startup",
    "checking out license",
]


def is_log_filename(filename: str) -> bool:
    name = (filename or "").lower()

    return (
        name.endswith(".log")
        or name.endswith(".log.txt")
        or ".log." in name
        or "genus.log" in name
        or "innovus.log" in name
        or bool(re.search(r"(^|[._-])log\d*([._-]|$)", name))
    )


def is_tcl_filename(filename: str) -> bool:
    name = (filename or "").lower()

    return (
        name.endswith(".tcl")
        or name.endswith(".tcl.txt")
        or ".tcl." in name
        or "script" in name
    )


def is_probably_tcl(filename: str, text: str) -> bool:
    name = (filename or "").lower()
    lowered = (text or "").lower()

    if is_log_filename(name):
        return False

    if is_tcl_filename(name):
        return True

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

    hits = sum(1 for hint in TCL_HINTS if hint.lower() in lowered)
    return hits >= 2


def is_probably_log(filename: str, text: str) -> bool:
    name = (filename or "").lower()
    lowered = (text or "").lower()

    if is_log_filename(name):
        return True

    if is_probably_tcl(filename, text):
        return False

    hits = sum(1 for hint in LOG_HINTS if hint in lowered)
    return hits >= 2


def filter_log_text(
    text: str,
    context_window: int = 6,
    max_lines: int = 260,
) -> str:
    """
    Keep relevant log evidence only:
    - header context for tool/version info
    - tail context for final failures
    - windows around error/warning/code lines

    Tcl scripts are never filtered.
    """

    lines = text.splitlines()

    if len(lines) <= max_lines:
        return text

    matched_indexes = set()

    for index in range(min(30, len(lines))):
        matched_indexes.add(index)

    tail_start = max(0, len(lines) - 80)

    for index in range(tail_start, len(lines)):
        matched_indexes.add(index)

    for index, line in enumerate(lines):
        lowered = line.lower()

        if any(keyword.lower() in lowered for keyword in IMPORTANT_LOG_KEYWORDS):
            start = max(0, index - context_window)
            end = min(len(lines), index + context_window + 1)

            for i in range(start, end):
                matched_indexes.add(i)

    selected_lines = [lines[i] for i in sorted(matched_indexes)]

    if len(selected_lines) > max_lines:
        selected_lines = selected_lines[-max_lines:]

    return "\n".join(selected_lines)


def extract_error_codes(text: str) -> List[str]:
    pattern = r"\b(?:TUI|LBR|FILE|ELAB|CDFG|VLOG|SYN)-\d+\b"
    codes = sorted(set(re.findall(pattern, text or "", flags=re.IGNORECASE)))
    return [code.upper() for code in codes]


def infer_fix_status(answer: str) -> str:
    lowered = (answer or "").lower()

    if "partial fix applied" in lowered:
        return "Partial Fix Applied"

    if "manual fix required" in lowered or "manual intervention" in lowered:
        return "Manual Fix Required"

    if "auto fixed" in lowered or "patched tcl script" in lowered:
        return "Auto Fixed"

    if "no fix needed" in lowered:
        return "No Fix Needed"

    return "Completed"


def is_simple_greeting(message: str) -> bool:
    normalized = re.sub(r"[^\w\s]", "", (message or "").strip().lower())
    normalized = re.sub(r"\s+", " ", normalized)

    return normalized in {
        "hi",
        "hello",
        "hey",
        "yo",
        "sup",
        "good morning",
        "good afternoon",
        "good evening",
    }


def build_guarded_orchestrator_prompt(clean_message: str) -> str:
    """
    Prevent no-file conceptual questions from being misrouted into the
    Tcl/log debugging pipeline. The orchestrator is still used, but it is
    explicitly forced into manager / Q&A mode.
    """

    return f"""
NO_UPLOADED_FILES = true
REQUEST_TYPE = general_or_theoretical_question

User message:
{clean_message}

Instruction:
The user did not upload any Tcl script, Genus log, Innovus log, or EDA file.

You are in manager / explainable-QA mode.

Hard routing rule:
- Do NOT transfer to eda_debug_pipeline.
- Do NOT call the diagnosis agent.
- Do NOT call the fixer agent.
- Do NOT produce "Fix Status", "No Fix Needed", "Auto Fixed", "Manual Fix Required", or "Partial Fix Applied".
- Do NOT pretend that a Tcl script or log was provided.

Answer directly as a Senior Mentor.

Allowed:
- Explain Cadence Genus concepts.
- Explain Tcl scripting concepts.
- Explain synthesis-flow concepts such as read_libs, read_hdl, elaborate, read_sdc, syn_generic, syn_map, and syn_opt.
- Answer general EDA debugging questions.

Only use the full EDA debugging pipeline when actual uploaded Tcl/log evidence is present.
""".strip()


# ------------------------------------------------------------
# Uploaded file reading
# ------------------------------------------------------------

async def read_uploaded_files(
    files: Optional[List[UploadFile]],
) -> Tuple[str, List[dict], List[str], bool, bool]:
    """
    Returns:
    1. combined file context for ADK
    2. uploaded file metadata for frontend
    3. detected diagnostic codes
    4. has_tcl
    5. has_log
    """

    if not files:
        return "", [], [], False, False

    blocks: List[str] = []
    uploaded_file_info: List[dict] = []
    all_detected_codes: List[str] = []

    has_tcl = False
    has_log = False

    for file in files:
        raw = await file.read()

        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = raw.decode("latin-1", errors="replace")

        filename = file.filename or "uploaded_file"
        original_line_count = len(text.splitlines())

        file_is_log = is_probably_log(filename, text)
        file_is_tcl = is_probably_tcl(filename, text)

        if file_is_log:
            has_log = True
            processed_text = filter_log_text(text)
            processing_note = (
                "Generic log filtering applied before ADK. "
                f"Original lines: {original_line_count}. "
                f"Filtered lines: {len(processed_text.splitlines())}."
            )
            file_kind = "log"
        elif file_is_tcl:
            has_tcl = True
            processed_text = text
            processing_note = "Tcl script passed through unchanged."
            file_kind = "tcl"
        else:
            processed_text = text
            processing_note = "No filtering applied. File passed through unchanged."
            file_kind = "unknown"

        detected_codes = extract_error_codes(text)
        all_detected_codes.extend(detected_codes)

        uploaded_file_info.append(
            {
                "filename": filename,
                "content_type": file.content_type,
                "size_bytes": len(raw),
                "file_kind": file_kind,
                "original_line_count": original_line_count,
                "processed_line_count": len(processed_text.splitlines()),
                "detected_codes": detected_codes,
            }
        )

        blocks.append(
            f"""
--- BEGIN UPLOADED FILE ---
filename: {filename}
content_type: {file.content_type}
file_kind: {file_kind}
processing_note: {processing_note}

{processed_text}
--- END UPLOADED FILE ---
""".strip()
        )

    unique_codes = sorted(set(all_detected_codes))

    return "\n\n".join(blocks), uploaded_file_info, unique_codes, has_tcl, has_log


# ------------------------------------------------------------
# ADK runner
# ------------------------------------------------------------

async def run_adk(
    runner: Runner,
    app_name: str,
    user_text: str,
    preferred_final_authors: Optional[List[str]] = None,
) -> tuple[str, dict]:
    """
    Run one isolated ADK session and return a user-facing answer.

    This function contains no issue-specific EDA fix rules. For multi-agent runs,
    it only prefers the final text emitted by the expected final-response agent,
    if that author exists. Otherwise, it falls back to the last text event.
    """
    started = time.perf_counter()

    print("=== ADK START ===")
    print(f"App name: {app_name}")
    print(f"Input length: {len(user_text)} characters")

    user_id = "web_user"
    session_id = str(uuid.uuid4())

    await session_service.create_session(
        app_name=app_name,
        user_id=user_id,
        session_id=session_id,
    )

    print("=== ADK SESSION CREATED ===")

    content = types.Content(
        role="user",
        parts=[types.Part(text=user_text)],
    )

    text_events: List[dict] = []
    event_authors: List[str] = []

    async for event in runner.run_async(
        user_id=user_id,
        session_id=session_id,
        new_message=content,
    ):
        author = getattr(event, "author", "unknown")
        author_text = str(author)
        event_authors.append(author_text)
        print(f"=== ADK EVENT: {author_text} ===")

        if not event.content or not event.content.parts:
            continue

        for part in event.content.parts:
            text = getattr(part, "text", None)

            if text:
                print(f"=== TEXT EVENT LENGTH: {len(text)} ===")
                text_events.append(
                    {
                        "author": author_text,
                        "text": text,
                    }
                )

    elapsed = round(time.perf_counter() - started, 3)

    print("=== ADK END ===")
    print(f"ADK elapsed seconds: {elapsed}")

    if not text_events:
        answer = "The ADK pipeline completed, but no final text response was returned."
        selected_answer_source = "none"
    else:
        preferred_final_authors = preferred_final_authors or []
        selected_event = None

        for preferred_author in preferred_final_authors:
            for item in reversed(text_events):
                if item["author"] == preferred_author:
                    selected_event = item
                    break
            if selected_event:
                break

        if selected_event is None:
            selected_event = text_events[-1]

        answer = selected_event["text"]
        selected_answer_source = selected_event["author"]

    trace = {
        "app_name": app_name,
        "session_id": session_id,
        "elapsed_seconds": elapsed,
        "event_authors": event_authors,
        "text_event_count": len(text_events),
        "selected_answer_source": selected_answer_source,
        "preferred_final_authors": preferred_final_authors or [],
    }

    return answer, trace

# ------------------------------------------------------------
# Chat endpoint
# ------------------------------------------------------------

@app.post("/chat")
async def chat(
    message: str = Form(""),
    files: Optional[List[UploadFile]] = File(None),
):
    request_started = time.perf_counter()

    clean_message = message.strip() or "Hello"

    (
        uploaded_context,
        uploaded_file_info,
        detected_codes,
        has_tcl,
        has_log,
    ) = await read_uploaded_files(files)

    has_files = bool(uploaded_context.strip())
    should_run_pipeline_direct = has_tcl and has_log

    # --------------------------------------------------------
    # Fast local route for tiny greetings.
    # This prevents greetings from entering ADK/pipeline and
    # keeps the UI responsive during demos.
    # --------------------------------------------------------
    if not has_files and is_simple_greeting(clean_message):
        total_elapsed = round(time.perf_counter() - request_started, 3)

        return {
            "answer": (
                "Hello! Upload both a Tcl script and the corresponding Genus/EDA log "
                "for a full debugging run, or ask me a Cadence/EDA question."
            ),
            "fix_status": "Completed",
            "detected_codes": [],
            "uploaded_files": [],
            "trace": {
                "backend": "ok",
                "route": "local_fast_greeting",
                "has_tcl": False,
                "has_log": False,
                "total_elapsed_seconds": total_elapsed,
                "adk": "not_required",
                "orchestrator": "not_required",
                "diagnostic": "not_required",
                "fixer": "not_required",
                "rag": "not_required",
            },
        }

    if should_run_pipeline_direct:
        selected_runner = pipeline_runner
        selected_app_name = "eda_debugger_pipeline"
        route = "pipeline_direct"

        combined_prompt = f"""
User message:
{clean_message}

Uploaded files:
{uploaded_context}

Instruction:
This request contains both a Tcl script and an EDA/Genus log. Run the EDA debugging pipeline directly. Diagnose the root cause and produce the final user-facing result through the fixer agent. Do not ask for confirmation. Do not expose raw JSON or internal payloads.
""".strip()

    else:
        selected_runner = orchestrator_runner
        selected_app_name = "eda_debugger_orchestrator"
        route = "orchestrator"

        if has_files:
            combined_prompt = f"""
User message:
{clean_message}

Uploaded files:
{uploaded_context}

Instruction:
The user uploaded files, but the backend did not detect a complete Tcl+log debugging pair. Use the orchestrator. If this is one file only, explain or review it directly. If a full Tcl+log pair is needed, ask the user to upload both files.

Do not produce "Fix Status", "No Fix Needed", "Auto Fixed", or "Manual Fix Required" unless an actual debugging/fix task is being performed.
""".strip()
        else:
            combined_prompt = build_guarded_orchestrator_prompt(clean_message)

    print("=== BACKEND ROUTING ===")
    print(f"Route: {route}")
    print(f"has_files: {has_files}")
    print(f"has_tcl: {has_tcl}")
    print(f"has_log: {has_log}")
    print(f"uploaded files: {[item['filename'] for item in uploaded_file_info]}")

    try:
        preferred_final_authors = ["script_fixer_agent"] if should_run_pipeline_direct else []

        answer, adk_trace = await run_adk(
            runner=selected_runner,
            app_name=selected_app_name,
            user_text=combined_prompt,
            preferred_final_authors=preferred_final_authors,
        )

        fix_status = infer_fix_status(answer)
        total_elapsed = round(time.perf_counter() - request_started, 3)

        return {
            "answer": answer,
            "fix_status": fix_status,
            "detected_codes": detected_codes,
            "uploaded_files": uploaded_file_info,
            "trace": {
                "backend": "ok",
                "route": route,
                "has_tcl": has_tcl,
                "has_log": has_log,
                "total_elapsed_seconds": total_elapsed,
                "adk": "completed",
                "adk_trace": adk_trace,
                "orchestrator": "bypassed" if should_run_pipeline_direct else "used",
                "diagnostic": "completed" if should_run_pipeline_direct else "via_orchestrator_or_not_required",
                "fixer": "completed" if should_run_pipeline_direct else "via_orchestrator_or_not_required",
                "rag": "internal_retrieval_when_matching_codes_exist" if has_files else "not_required",
            },
        }

    except Exception as exc:
        total_elapsed = round(time.perf_counter() - request_started, 3)

        print("=== ADK ERROR ===")
        print(f"{type(exc).__name__}: {str(exc)}")

        return {
            "answer": (
                "Backend reached the ADK layer, but the ADK run failed.\n\n"
                f"Route: {route}\n\n"
                f"Error:\n{type(exc).__name__}: {str(exc)}"
            ),
            "fix_status": "Error",
            "detected_codes": detected_codes,
            "uploaded_files": uploaded_file_info,
            "trace": {
                "backend": "ok",
                "route": route,
                "has_tcl": has_tcl,
                "has_log": has_log,
                "total_elapsed_seconds": total_elapsed,
                "adk": "failed",
                "orchestrator": "bypassed" if should_run_pipeline_direct else "used",
                "diagnostic": "unknown",
                "fixer": "unknown",
                "rag": "unknown",
            },
        }
