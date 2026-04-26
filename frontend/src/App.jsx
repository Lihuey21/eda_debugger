import { useState } from "react";
import "./App.css";

const initialMessages = [
  {
    role: "assistant",
    content:
      "Hi, I am your Agentic EDA Script Debugger. You can ask me EDA questions, or attach files such as Tcl scripts, Genus logs, constraints, reports, or documentation for debugging/explanation.",
  },
];

const historyItems = [
  {
    id: 1,
    title: "counter_preserve_issue",
    status: "Auto Fixed",
    codes: ["TUI-214", "TUI-24"],
  },
  {
    id: 2,
    title: "riscv_library_failure",
    status: "Manual Fix Required",
    codes: ["FILE-100", "LBR-68"],
  },
  {
    id: 3,
    title: "mixed_library_warning",
    status: "Partial Fix Applied",
    codes: ["LBR-9", "TUI-214"],
  },
];

const statusStyle = {
  "Auto Fixed": "status-green",
  "Manual Fix Required": "status-orange",
  "Partial Fix Applied": "status-blue",
  "No Fix Needed": "status-gray",
  backend_test: "status-blue",
  error: "status-orange",
  Ready: "status-gray",
  Running: "status-blue",
};

function App() {
  const [messages, setMessages] = useState(initialMessages);
  const [input, setInput] = useState("");
  const [attachments, setAttachments] = useState([]);
  const [currentStatus, setCurrentStatus] = useState("Ready");
  const [detectedCodes, setDetectedCodes] = useState([]);
  const [showAdvancedTrace, setShowAdvancedTrace] = useState(false);
  const [trace, setTrace] = useState({
    backend: "waiting",
    adk: "waiting",
  });

  function handleFilesSelected(event) {
    const newFiles = Array.from(event.target.files || []);

    setAttachments((prevFiles) => {
      const mergedFiles = [...prevFiles];

      newFiles.forEach((newFile) => {
        const alreadyExists = mergedFiles.some(
          (existingFile) =>
            existingFile.name === newFile.name &&
            existingFile.size === newFile.size &&
            existingFile.lastModified === newFile.lastModified
        );

        if (!alreadyExists) {
          mergedFiles.push(newFile);
        }
      });

      return mergedFiles;
    });

    event.target.value = "";
  }

  async function handleSend() {
    const trimmed = input.trim();

    if (!trimmed && attachments.length === 0) return;

    const attachmentNames =
      attachments.length > 0
        ? attachments.map((file) => file.name).join(", ")
        : "No files attached";

    const userText =
      trimmed || `Please analyze the attached files: ${attachmentNames}.`;

    setMessages((prev) => [
      ...prev,
      {
        role: "user",
        content: userText,
      },
    ]);

    setInput("");
    setCurrentStatus("Running");
    setTrace({
      backend: "sending request",
      adk: "waiting",
    });

    try {
      const apiBaseUrl = import.meta.env.VITE_API_BASE_URL;

      if (!apiBaseUrl) {
        throw new Error(
          "Missing VITE_API_BASE_URL. Add it in Render frontend environment variables."
        );
      }

      const formData = new FormData();
      formData.append("message", userText);

      attachments.forEach((file) => {
        formData.append("files", file);
      });

      const response = await fetch(`${apiBaseUrl}/chat`, {
        method: "POST",
        body: formData,
      });

      if (!response.ok) {
        throw new Error(`Backend returned HTTP ${response.status}`);
      }

      const data = await response.json();

      const answer =
        data.answer ||
        "Backend responded, but no answer field was returned.";

      setMessages((prev) => [
        ...prev,
        {
          role: "assistant",
          content: answer,
        },
      ]);

      setCurrentStatus(data.fix_status || "Completed");
      setDetectedCodes(data.detected_codes || []);
      setTrace(data.trace || { backend: "ok", adk: "unknown" });
    } catch (error) {
      console.error(error);

      setMessages((prev) => [
        ...prev,
        {
          role: "assistant",
          content: `Backend connection failed.

Reason:
${error.message}

Check:
1. Your Render backend is running.
2. VITE_API_BASE_URL is set in the frontend Render environment variables.
3. The frontend was redeployed after adding the environment variable.`,
        },
      ]);

      setCurrentStatus("error");
      setTrace({
        backend: "failed",
        adk: "not reached",
      });
    }
  }

  function handleKeyDown(event) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      handleSend();
    }
  }

  function clearAttachments() {
    setAttachments([]);
  }

  function handleNewChat() {
    setMessages(initialMessages);
    setInput("");
    setAttachments([]);
    setCurrentStatus("Ready");
    setDetectedCodes([]);
    setShowAdvancedTrace(false);
    setTrace({
      backend: "waiting",
      adk: "waiting",
    });
  }

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div>
          <div className="brand">
            <div className="brand-icon">E</div>
            <div>
              <h1>EDA Debugger</h1>
              <p>Google ADK Tcl Assistant</p>
            </div>
          </div>

          <nav className="nav">
            <button className="nav-active" onClick={handleNewChat}>
              New Chat
            </button>
            <button>Debug Sessions</button>
            <button>Cadence Links</button>
            <button>Settings</button>
          </nav>
        </div>

        <div className="sidebar-footer">
          <div className="user-card">
            <div className="avatar">L</div>
            <div>
              <p className="user-name">Lihuey21</p>
              <p className="user-role">Research prototype</p>
            </div>
          </div>

          <button className="logout-btn">Log out</button>
        </div>
      </aside>

      <main className="chat-panel">
        <header className="chat-header">
          <div>
            <h2>Agentic EDA Script Debugger</h2>
            <p>
              Ask questions, attach Tcl/log/docs, and debug Cadence Genus flows.
            </p>
          </div>

          <div className="top-actions">
            <span className="kg-pill">Neo4j RAG</span>
            <span className="kg-pill">Google ADK</span>
          </div>
        </header>

        <section className="messages">
          {messages.map((message, index) => (
            <div
              key={`${message.role}-${index}`}
              className={`message-row ${
                message.role === "user" ? "message-user" : "message-assistant"
              }`}
            >
              <div className="message-avatar">
                {message.role === "user" ? "U" : "A"}
              </div>

              <div className="message-bubble">
                <pre>{message.content}</pre>
              </div>
            </div>
          ))}
        </section>

        <footer className="composer-wrap">
          <div className="file-strip">
            <label className="attach-chip">
              <input type="file" multiple onChange={handleFilesSelected} />
              <span>Attach Files</span>
            </label>

            <div className="selected-files">
              {attachments.length === 0 ? (
                <span>No files attached</span>
              ) : (
                attachments.map((file) => (
                  <span key={`${file.name}-${file.size}`} className="file-pill">
                    {file.name}
                  </span>
                ))
              )}
            </div>

            {attachments.length > 0 && (
              <button className="clear-files-btn" onClick={clearAttachments}>
                Clear
              </button>
            )}
          </div>

          <div className="composer">
            <textarea
              value={input}
              placeholder="Ask the orchestrator a question, or attach files and ask it to debug/explain..."
              onChange={(event) => setInput(event.target.value)}
              onKeyDown={handleKeyDown}
            />

            <button onClick={handleSend}>
              {currentStatus === "Running" ? "Running..." : "Send"}
            </button>
          </div>
        </footer>
      </main>

      <aside className="history-panel">
        <div className="history-header">
          <h3>Current Session</h3>
          <span className={`mini-status ${statusStyle[currentStatus]}`}>
            {currentStatus}
          </span>
        </div>

        <button
          className="trace-panel-button"
          onClick={() => setShowAdvancedTrace((value) => !value)}
        >
          {showAdvancedTrace ? "Hide Advanced Trace" : "Show Advanced Trace"}
        </button>

        {showAdvancedTrace && (
          <section className="trace-panel">
            <div>
              <strong>Backend API</strong>
              <span>{trace.backend || "waiting"}</span>
            </div>

            <div>
              <strong>Google ADK</strong>
              <span>{trace.adk || "waiting"}</span>
            </div>

            <div>
              <strong>Analyzer Agent</strong>
              <span>{currentStatus === "Ready" ? "Waiting" : "Pending"}</span>
            </div>

            <div>
              <strong>Diagnosis Agent</strong>
              <span>{currentStatus === "Ready" ? "Waiting" : "Pending"}</span>
            </div>

            <div>
              <strong>Fixer Agent</strong>
              <span>{currentStatus === "Ready" ? "Waiting" : "Pending"}</span>
            </div>

            <div>
              <strong>Neo4j RAG</strong>
              <span>Backend retrieval only</span>
            </div>

            {detectedCodes.length > 0 && (
              <div>
                <strong>Detected Codes</strong>
                <span>{detectedCodes.join(", ")}</span>
              </div>
            )}
          </section>
        )}

        <div className="history-header small-header">
          <h3>History</h3>
          <span>{historyItems.length} runs</span>
        </div>

        <div className="history-list">
          {historyItems.map((item) => (
            <button key={item.id} className="history-item">
              <strong>{item.title}</strong>
              <p>{item.codes.join(", ")}</p>
              <span className={`mini-status ${statusStyle[item.status]}`}>
                {item.status}
              </span>
            </button>
          ))}
        </div>
      </aside>
    </div>
  );
}

export default App;