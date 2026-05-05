# Agentic EDA Script Debugger

## 1. Project Overview

Agentic EDA Script Debugger is a prototype AI-assisted debugging system for Cadence Genus Tcl synthesis flows. The system accepts a Tcl script and a corresponding Genus execution log, identifies the primary root cause of a failed run, retrieves relevant debugging knowledge from a Neo4j knowledge graph and returns either a patched Tcl script or a manual-fix recommendation.

The project is built as an agentic AI pipeline using Google ADK. It is intended as a Final Year Project prototype and focuses on Tcl/log debugging for Genus synthesis flows, including selected DFT-related cases.

## 2. Main Features

- Upload and analyze Genus Tcl scripts and execution logs.
- Route simple greetings and general EDA questions without unnecessarily running the full debugging pipeline.
- Detect whether uploaded files form a complete Tcl + log debugging pair.
- Preprocess long Genus logs before sending them into the agent pipeline.
- Retrieve tool-specific guidance from a Neo4j knowledge graph.
- Classify debugging cases into No Fix Needed, Manual Fix Required, Auto Fixed and Partial Fix Applied.
- Generate patched Tcl scripts for safe auto-fixable cases.
- Provide manual recommendations for external environment or file-system issues.
- Frontend chat interface with session history, settings and advanced trace panel.

## 3. System Architecture

The system contains three main layers.

### 3.1 Frontend

Location:

```text
frontend/
```

The frontend is built using React and Vite. It provides the user interface for login, password reset, chat interaction, file upload, chat history, current session trace and settings.

Important files:

```text
frontend/src/App.jsx
frontend/src/App.css
frontend/package.json
frontend/vite.config.js
```

### 3.2 Backend

Location:

```text
main.py
```

The backend is built with FastAPI. It handles file upload, Tcl/log classification, long-log filtering, routing between local fast response, orchestrator and full debugging pipeline, calling the Google ADK runners and returning the final agent response to the frontend.

### 3.3 Agent Pipeline

Location:

```text
AI_Agents/
```

Main agent components:

```text
AI_Agents/agent.py
AI_Agents/sub_agents/eda_debug_pipeline/agent.py
AI_Agents/sub_agents/script_analyzer_agent/
AI_Agents/sub_agents/error_diagnosis_agent/
AI_Agents/sub_agents/script_fixer_agent/
```

The high-level pipeline is:

```text
User Tcl + log
→ Backend file preprocessing
→ ADK pipeline
→ Script Analyzer Agent
→ Error Diagnosis Agent
→ Neo4j knowledge retrieval
→ Script Fixer Agent
→ Final diagnosis/fix response
```

## 4. Knowledge Graph

Neo4j is used as the knowledge layer for error codes, issue types, commands and fix patterns.

Example supported cases include:

- Library/path issue requiring manual verification.
- Successful run with no fix required.
- TUI-214 `.preserve true` flow-order issue.
- TUI-23 invalid enum value for synthesis effort.
- DFT-116 missing DFT shift-enable definition.

The project does not include private Neo4j credentials. The database connection must be configured using environment variables.

## 5. Test Cases Used

The prototype was evaluated using five representative Genus debugging cases:

| Test Case | Category | Expected Status | Purpose |
|---|---|---|---|
| TC1 | Successful Genus run | No Fix Needed | Confirms that the agent does not hallucinate fixes for successful runs |
| TC2 | Missing library/path issue | Manual Fix Required | Confirms that external file-system issues are not blindly auto-fixed |
| TC3 | TUI-214 preserve attribute ordering | Auto Fixed | Confirms RAG-grounded Tcl command relocation |
| TC4 | TUI-23 invalid synthesis effort enum | Auto Fixed | Confirms attribute-value correction |
| TC5 | DFT-116 missing DFT shift-enable signal | Auto Fixed | Confirms DFT-specific debugging and graph-grounded Tcl insertion |

## 6. Setup Instructions

### 6.1 Prerequisites

Install:

- Python 3.10 or later
- Node.js 18 or later
- Neo4j database
- Google ADK dependencies
- OpenAI/LiteLLM-compatible API access

### 6.2 Backend Setup

From the project root:

```bash
python -m venv .venv
```

Activate the virtual environment.

Windows PowerShell:

```bash
.venv\\Scripts\\Activate.ps1
```

Install dependencies:

```bash
pip install -r requirements.txt
```

Create a `.env` file based on `.env.example` and fill in your own keys.

Required backend environment variables:

```text
OPENAI_API_KEY=your_openai_api_key
GOOGLE_GENAI_USE_VERTEXAI=0
NEO4J_URI=your_neo4j_uri
NEO4J_USERNAME=your_neo4j_username
NEO4J_PASSWORD=your_neo4j_password
```

Run the backend locally:

```bash
uvicorn main:app --reload
```

The backend health check should be available at:

```text
http://localhost:8000/
```

### 6.3 Frontend Setup

Go to the frontend folder:

```bash
cd frontend
```

Install dependencies:

```bash
npm install
```

Create `frontend/.env` based on the frontend environment variables required by the project.

Typical frontend variables:

```text
VITE_API_BASE_URL=http://localhost:8000
VITE_SUPABASE_URL=your_supabase_project_url
VITE_SUPABASE_ANON_KEY=your_supabase_anon_key
```

Run the frontend:

```bash
npm run dev
```

The frontend should be available at the Vite local URL, usually:

```text
http://localhost:5173/
```

## 7. Running the System

1. Start the backend:

```bash
uvicorn main:app --reload
```

2. Start the frontend:

```bash
cd frontend
npm run dev
```

3. Open the frontend in the browser.
4. Login or create an account.
5. Upload one Tcl script and one matching Genus log.
6. Submit the debugging request.
7. Review the diagnosis, fix status and patched Tcl script if applicable.

## 8. Deployment Notes

For Render deployment, backend environment variables must be configured in Render:

```text
OPENAI_API_KEY
GOOGLE_GENAI_USE_VERTEXAI
NEO4J_URI
NEO4J_USERNAME
NEO4J_PASSWORD
```

Frontend deployment must set:

```text
VITE_API_BASE_URL
VITE_SUPABASE_URL
VITE_SUPABASE_ANON_KEY
```

The frontend API URL should point to the deployed backend URL.

## 9. Limitations

This project is a prototype and is not intended to replace professional EDA signoff or manual engineering review. The current system supports selected Genus Tcl/log debugging patterns and relies on the quality of the uploaded log, Tcl script and Neo4j knowledge graph entries.

Known limitations:

- Limited dataset size.
- Dependent on available Genus logs and controlled test cases.
- Neo4j knowledge graph must be populated with relevant error-code rules.
- Long raw logs require filtering or compaction.
- Some fixes remain manual because external files, libraries, paths, licenses or permissions cannot be verified by the agent.


