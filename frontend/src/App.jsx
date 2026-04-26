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

  function handleSend() {
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

    // MOCK RESPONSE ONLY.
    // Later this will be replaced with fetch() to your FastAPI + Google ADK backend.
    setTimeout(() => {
      setMessages((prev) => [
        ...prev,
        {
          role: "assistant",
          content: `Explanation

The Genus run failed because the preserve command was applied before synthesis mapping was complete. The command should be moved after syn_map and before syn_opt.

Detected error codes: TUI-214 and TUI-24.

Patched Tcl Script

set_db init_lib_search_path ../lib/
set_db init_hdl_search_path ../rtl/
read_libs slow_vdd1v0_basicCells.lib

read_hdl counter.v
elaborate counter

read_sdc ../constraints/constraints_top.sdc

set_db syn_generic_effort medium
syn_generic
syn_map
set_db [get_cells -hierarchical *] .preserve true
syn_opt

report_timing > reports/report_timing.rpt
puts "--- SYNTHESIS ATTEMPTED ---"
quit

Summary of Changes

1. Moved the preserve command after syn_map.
2. Preserved unrelated Tcl commands.
3. Kept report commands unchanged.

Fix Status

Auto Fixed`,
        },
      ]);

      setCurrentStatus("Auto Fixed");
      setDetectedCodes(["TUI-214", "TUI-24"]);
    }, 600);
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
              Ask questions, attach Tcl/log/docs and debug Cadence Genus flows.
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
              placeholder="Ask the orchestrator a question or attach files and ask it to debug/explain..."
              onChange={(event) => setInput(event.target.value)}
              onKeyDown={handleKeyDown}
            />

            <button onClick={handleSend}>Send</button>
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
              <strong>Analyzer Agent</strong>
              <span>{currentStatus === "Ready" ? "Waiting" : "Completed"}</span>
            </div>

            <div>
              <strong>Diagnosis Agent</strong>
              <span>{currentStatus === "Ready" ? "Waiting" : "Completed"}</span>
            </div>

            <div>
              <strong>Fixer Agent</strong>
              <span>{currentStatus === "Ready" ? "Waiting" : "Completed"}</span>
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