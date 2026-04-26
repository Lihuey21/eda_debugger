from __future__ import annotations

import os
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


@app.get("/")
def health_check():
    return {
        "status": "ok",
        "service": "EDA Debugger Backend",
        "message": "FastAPI backend is running.",
    }


async def read_uploaded_files(files: Optional[List[UploadFile]]) -> str:
    if not files:
        return ""

    blocks = []

    for file in files:
        raw = await file.read()

        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = raw.decode("latin-1", errors="replace")

        blocks.append(
            f"""
--- BEGIN UPLOADED FILE ---
filename: {file.filename}
content_type: {file.content_type}

{text}
--- END UPLOADED FILE ---
""".strip()
        )

    return "\n\n".join(blocks)


async def run_adk(user_text: str) -> str:
    user_id = "web_user"
    session_id = str(uuid.uuid4())

    await session_service.create_session(
        app_name="eda_debugger_web",
        user_id=user_id,
        session_id=session_id,
    )

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
        if not event.content or not event.content.parts:
            continue

        for part in event.content.parts:
            if getattr(part, "text", None):
                final_text_parts.append(part.text)

    if not final_text_parts:
        return "The ADK pipeline completed, but no final text response was returned."

    return final_text_parts[-1]


@app.post("/chat")
async def chat(
    message: str = Form(""),
    files: Optional[List[UploadFile]] = File(None),
):
    uploaded_context = await read_uploaded_files(files)

    combined_prompt = f"""
User message:
{message}

Uploaded files:
{uploaded_context}

Instruction:
Respond as the deployed Agentic EDA Script Debugger. If Tcl/log content is present, route through the debugging pipeline. If this is a general EDA question, answer through the orchestrator.
""".strip()

    try:
        answer = await run_adk(combined_prompt)

        return {
            "answer": answer,
            "fix_status": "completed",
            "detected_codes": [],
            "uploaded_files": [
                {
                    "filename": file.filename,
                    "content_type": file.content_type,
                }
                for file in files
            ]
            if files
            else [],
            "trace": {
                "backend": "ok",
                "adk": "completed",
            },
        }

    except Exception as exc:
        return {
            "answer": (
                "Backend reached the ADK layer, but the ADK pipeline failed.\n\n"
                f"Error:\n{type(exc).__name__}: {str(exc)}"
            ),
            "fix_status": "error",
            "detected_codes": [],
            "uploaded_files": [],
            "trace": {
                "backend": "ok",
                "adk": "failed",
            },
        }