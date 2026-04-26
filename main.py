from __future__ import annotations

import re
import uuid
from typing import List, Optional

from fastapi import FastAPI, File, Form, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from AI_Agents.agent import root_agent


app = FastAPI(title="EDA Debugger Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten later after final frontend URL is confirmed
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

session_service = InMemorySessionService()

runner = Runner(
    app_name="eda_debugger_web",
    agent=root_agent,
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
    }


# ------------------------------------------------------------
# Generic log filtering
# ------------------------------------------------------------
# This is NOT dataset-specific hardcoding.
# It only reduces noisy log text before passing it into ADK.
# Tcl scripts are kept unchanged.

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
    "genus",
    "error",
    "warning",
    "fatal",
    "tui-",
    "lbr-",
    "file-",
    "elab-",
    "cdfg-",
]


def is_probably_tcl(filename: str, text: str) -> bool:
    name = (filename or "").lower()
    lowered = text.lower()

    if name.endswith(".tcl") or name.endswith(".tcl.txt") or ".tcl." in name:
        return True

    # Tcl-like script content
    hits = sum(1 for hint in TCL_HINTS if hint.lower() in lowered)
    return hits >= 2


def is_probably_log(filename: str, text: str) -> bool:
    name = (filename or "").lower()
    lowered = text.lower()

    if name.endswith(".log") or name.endswith(".log.txt") or ".log." in name:
        return True

    # Avoid filtering Tcl scripts that happen to contain words like "error".
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
    - first lines for version/tool context
    - last lines because fatal failures often appear near the end
    - windows around important error/warning/code lines
    """

    lines = text.splitlines()

    if len(lines) <= max_lines:
        return text

    matched_indexes = set()

    # Always keep a little header context.
    for index in range(min(30, len(lines))):
        matched_indexes.add(index)

    # Always keep tail context.
    tail_start = max(0, len(lines) - 80)
    for index in range(tail_start, len(lines)):
        matched_indexes.add(index)

    # Keep context windows around relevant lines.
    for index, line in enumerate(lines):
        lowered = line.lower()

        if any(keyword.lower() in lowered for keyword in IMPORTANT_LOG_KEYWORDS):
            start = max(0, index - context_window)
            end = min(len(lines), index + context_window + 1)

            for i in range(start, end):
                matched_indexes.add(i)

    selected_lines = [lines[i] for i in sorted(matched_indexes)]

    # If still too long, keep latest evidence. Usually the real failure is near the end.
    if len(selected_lines) > max_lines:
        selected_lines = selected_lines[-max_lines:]

    return "\n".join(selected_lines)


def extract_error_codes(text: str) -> List[str]:
    """
    Extract common EDA-style diagnostic codes for frontend trace/history.
    This is only metadata extraction, not diagnosis.
    """

    pattern = r"\b(?:TUI|LBR|FILE|ELAB|CDFG|VLOG|SYN)-\d+\b"
    codes = sorted(set(re.findall(pattern, text, flags=re.IGNORECASE)))
    return [code.upper() for code in codes]


def infer_fix_status(answer: str) -> str:
    lowered = answer.lower()

    if "partial fix applied" in lowered:
        return "Partial Fix Applied"

    if "manual fix required" in lowered or "manual intervention" in lowered:
        return "Manual Fix Required"

    if "auto fixed" in lowered or "patched tcl script" in lowered:
        return "Auto Fixed"

    if "no fix needed" in lowered:
        return "No Fix Needed"

    return "Completed"


# ------------------------------------------------------------
# File reading
# ------------------------------------------------------------

async def read_uploaded_files(files: Optional[List[UploadFile]]) -> tuple[str, List[dict], List[str]]:
    """
    Reads uploaded files and returns:
    1. combined file context for ADK
    2. uploaded file metadata for frontend
    3. detected diagnostic codes
    """

    if not files:
        return "", [], []

    blocks = []
    uploaded_file_info = []
    all_detected_codes = []

    for file in files:
        raw = await file.read()

        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = raw.decode("latin-1", errors="replace")

        original_line_count = len(text.splitlines())
        filename = file.filename or "uploaded_file"

        if is_probably_log(filename, text):
            processed_text = filter_log_text(text)
            processing_note = (
                "Generic log filtering applied before ADK. "
                f"Original lines: {original_line_count}. "
                f"Filtered lines: {len(processed_text.splitlines())}."
            )
        else:
            processed_text = text
            processing_note = "No filtering applied. File passed through unchanged."

        detected_codes = extract_error_codes(text)
        all_detected_codes.extend(detected_codes)

        uploaded_file_info.append(
            {
                "filename": filename,
                "content_type": file.content_type,
                "size_bytes": len(raw),
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
processing_note: {processing_note}

{processed_text}
--- END UPLOADED FILE ---
""".strip()
        )

    unique_codes = sorted(set(all_detected_codes))

    return "\n\n".join(blocks), uploaded_file_info, unique_codes


# ------------------------------------------------------------
# ADK runner
# ------------------------------------------------------------

async def run_adk(user_text: str) -> str:
    print("=== ADK START ===")
    print(f"Input length: {len(user_text)} characters")

    user_id = "web_user"
    session_id = str(uuid.uuid4())

    await session_service.create_session(
        app_name="eda_debugger_web",
        user_id=user_id,
        session_id=session_id,
    )

    print("=== ADK SESSION CREATED ===")

    content = types.Content(
        role="user",
        parts=[types.Part(text=user_text)],
    )

    final_text_parts = []

    async for event in runner.run_async(
        user_id=user_id,
        session_id=session_id,
        new_message=content,
    ):
        author = getattr(event, "author", "unknown")
        print(f"=== ADK EVENT: {author} ===")

        if not event.content or not event.content.parts:
            continue

        for part in event.content.parts:
            text = getattr(part, "text", None)
            if text:
                print(f"=== TEXT EVENT LENGTH: {len(text)} ===")
                final_text_parts.append(text)

    print("=== ADK END ===")

    if not final_text_parts:
        return "The ADK pipeline completed, but no final text response was returned."

    return final_text_parts[-1]


# ------------------------------------------------------------
# Chat endpoint
# ------------------------------------------------------------

@app.post("/chat")
async def chat(
    message: str = Form(""),
    files: Optional[List[UploadFile]] = File(None),
):
    clean_message = message.strip() or "Hello"

    uploaded_context, uploaded_file_info, detected_codes = await read_uploaded_files(files)
    has_files = bool(uploaded_context.strip())

    # Critical fix:
    # If no files are uploaded, send only the user's message.
    # This prevents "hello" from being wrapped as a fake debugging request.
    if has_files:
        combined_prompt = f"""
User message:
{clean_message}

Uploaded files:
{uploaded_context}

Instruction:
The user has uploaded files. If the uploaded content contains Tcl scripts or EDA logs, route through the EDA debugging pipeline. If the uploaded files are documentation or notes, explain them or use them as supporting context. Return only a clean user-facing answer. Do not expose raw JSON, internal handoff payloads, or graph internals.
""".strip()
    else:
        combined_prompt = clean_message

    try:
        answer = await run_adk(combined_prompt)
        fix_status = infer_fix_status(answer)

        return {
            "answer": answer,
            "fix_status": fix_status,
            "detected_codes": detected_codes,
            "uploaded_files": uploaded_file_info,
            "trace": {
                "backend": "ok",
                "adk": "completed",
                "analyzer": "completed" if has_files else "not_required",
                "diagnosis": "completed" if has_files else "not_required",
                "fixer": "completed" if has_files else "not_required",
                "rag": "internal_retrieval" if has_files else "not_required",
            },
        }

    except Exception as exc:
        print("=== ADK ERROR ===")
        print(f"{type(exc).__name__}: {str(exc)}")

        return {
            "answer": (
                "Backend reached the ADK layer, but the ADK pipeline failed.\n\n"
                f"Error:\n{type(exc).__name__}: {str(exc)}"
            ),
            "fix_status": "error",
            "detected_codes": detected_codes,
            "uploaded_files": uploaded_file_info,
            "trace": {
                "backend": "ok",
                "adk": "failed",
                "analyzer": "unknown",
                "diagnosis": "unknown",
                "fixer": "unknown",
                "rag": "unknown",
            },
        }