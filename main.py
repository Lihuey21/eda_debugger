from fastapi import FastAPI, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from typing import List, Optional

app = FastAPI(title="EDA Debugger Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten later after frontend URL is final
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def health_check():
    return {
        "status": "ok",
        "service": "EDA Debugger Backend",
        "message": "FastAPI backend is running.",
    }


@app.post("/chat")
async def chat(
    message: str = Form(""),
    files: Optional[List[UploadFile]] = File(None),
):
    uploaded_files = []

    if files:
        for file in files:
            content = await file.read()
            uploaded_files.append(
                {
                    "filename": file.filename,
                    "size_bytes": len(content),
                    "content_type": file.content_type,
                }
            )

    return {
        "answer": (
            "Backend received your request successfully. "
            "Google ADK connection will be added next."
        ),
        "fix_status": "backend_test",
        "detected_codes": [],
        "uploaded_files": uploaded_files,
        "trace": {
            "backend": "ok",
            "adk": "not_connected_yet",
        },
    }