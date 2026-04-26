import { useMemo, useState } from "react";
import "./App.css";

const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL || "http://127.0.0.1:8000";

const initialMessages = [
  {
    role: "assistant",
    content:
      "Hi, I am your Agentic EDA Script Debugger. You can ask EDA questions, or attach Tcl scripts and Genus logs for debugging.",
  },
];

const statusStyle = {
  "Auto Fixed": "status-green",
  "Manual Fix Required": "status-orange",
  "Partial Fix Applied": "status-blue",
  "No Fix Needed": "status-gray",
  Ready: "status-gray",
  Running: "status-blue",
  Error: "status-orange",
};

function inferFixStatus(answer) {
  const text = (answer || "").toLowerCase();

  if (text.includes("auto fixed")) return "Auto Fixed";
  if (text.includes("manual fix required")) return "Manual Fix Required";
  if (text.includes("partial fix applied")) return "Partial Fix Applied";
  if (text.includes("no fix needed")) return "No Fix Needed";

  return "Ready";
}

function extractDetectedCodes(answer) {
  const matches = (answer || "").match(/\b[A-Z]{2,5}-\d+\b/g);
  return Array.from(new Set(matches || []));
}

function makeSessionTitle(files, message, status) {
  const names = files.map((file) => file.name).join(", ");

  if (names) {
    return names.length > 42 ? `${names.slice(0, 42)}...` : names;
  }

  const trimmed = message.trim();
  if (trimmed) {
    return trimmed.length > 42 ? `${trimmed.slice(0, 42)}...` : trimmed;
  }

  return status || "debug_session";
}

function LoginScreen({ onLogin }) {
  const [name, setName] = useState("Demo User");

  function handleSubmit(event) {
    event.preventDefault();

    const cleanName = name.trim() || "Demo User";
    onLogin(cleanName);
  }

  return (
    <div className="login-page">
      <form className="login-card" onSubmit={handleSubmit}>
        <div className="brand-icon large">E</div>

        <h1>EDA Debugger</h1>
        <p>Agentic Tcl/log debugging prototype for Cadence Genus.</p>

        <label>
          Display Name
          <input
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder="Enter your display name"
          />
        </label>

        <button type="submit">Enter Prototype</button>

        <small>
          Demo login only. Production authentication can be added later.
        </small>
      </form>
    </div>
  );
}

function App() {
  const [isLoggedIn, setIsLoggedIn] = useState(() => {
    return localStorage.getItem("eda_debugger_logged_in") === "true";
  });

  const [displayName, setDisplayName] = useState(() => {
    return localStorage.getItem("eda_debugger_display_name") || "Demo User";
  });

  const [messages, setMessages] = useState(initialMessages);
  const [input, setInput] = useState("");
  const [attachments, setAttachments] = useState([]);
  const [currentStatus, setCurrentStatus] = useState("Ready");
  const [detectedCodes, setDetectedCodes] = useState([]);
  const [showAdvancedTrace, setShowAdvancedTrace] = useState(false);
  const [historyItems, setHistoryItems] = useState([]);
  const [lastTrace, setLastTrace] = useState(null);

  const traceStatus = useMemo(() => {
    if (currentStatus === "Ready") {
      return {
        orchestrator: "Waiting",
        diagnostic: "Waiting",
        fixer: "Waiting",
        rag: "Waiting",
      };
    }

    if (currentStatus === "Running") {
      return {
        orchestrator: "Routing",
        diagnostic: "Pending",
        fixer: "Pending",
        rag: "Pending",
      };
    }

    if (currentStatus === "Error") {
      return {
        orchestrator: "Error",
        diagnostic: "Error",
        fixer: "Error",
        rag: "Check backend trace",
      };
    }

    return {
      orchestrator: "Completed",
      diagnostic: "Completed",
      fixer: "Completed",
      rag: "Used when matching error codes exist",
    };
  }, [currentStatus]);

  function handleLogin(name) {
    localStorage.setItem("eda_debugger_logged_in", "true");
    localStorage.setItem("eda_debugger_display_name", name);
    setDisplayName(name);
    setIsLoggedIn(true);
  }

  function handleLogout() {
    localStorage.removeItem("eda_debugger_logged_in");
    localStorage.removeItem("eda_debugger_display_name");
    setIsLoggedIn(false);
    setDisplayName("Demo User");
    setMessages(initialMessages);
    setInput("");
    setAttachments([]);
    setCurrentStatus("Ready");
    setDetectedCodes([]);
    setHistoryItems([]);
    setShowAdvancedTrace(false);
    setLastTrace(null);
  }

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

    const userText =
      trimmed ||
      `Please analyze the attached files: ${attachments
        .map((file) => file.name)
        .join(", ")}.`;

    const filesForRequest = [...attachments];

    setMessages((prev) => [
      ...prev,
      {
        role: "user",
        content: userText,
        files: filesForRequest.map((file) => file.name),
      },
    ]);

    setInput("");
    setCurrentStatus("Running");
    setDetectedCodes([]);

    try {
      const formData = new FormData();
      formData.append("message", userText);

      filesForRequest.forEach((file) => {
        formData.append("files", file);
      });

      const response = await fetch(`${API_BASE_URL}/chat`, {
        method: "POST",
        body: formData,
      });

      if (!response.ok) {
        throw new Error(`Backend returned ${response.status}`);
      }

      const data = await response.json();
      const answer = data.answer || "No response returned from backend.";
      const status = inferFixStatus(answer);
      const codes = extractDetectedCodes(answer);

      setMessages((prev) => [
        ...prev,
        {
          role: "assistant",
          content: answer,
        },
      ]);

      setCurrentStatus(status);
      setDetectedCodes(codes);
      setLastTrace(data.trace || null);

      setHistoryItems((prev) => [
        {
          id: Date.now(),
          title: makeSessionTitle(filesForRequest, userText, status),
          status,
          codes,
        },
        ...prev,
      ]);

      setAttachments([]);
    } catch (error) {
      const errorMessage = `Backend request failed.\n\n${error.message}`;

      setMessages((prev) => [
        ...prev,
        {
          role: "assistant",
          content: errorMessage,
        },
      ]);

      setCurrentStatus("Error");
      setLastTrace({
        backend: "failed",
        error: error.message,
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
    setLastTrace(null);
  }

  if (!isLoggedIn) {
    return <LoginScreen onLogin={handleLogin} />;
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
            <button onClick={() => setShowAdvancedTrace((value) => !value)}>
              Debug Trace
            </button>
            <button disabled>Settings</button>
          </nav>
        </div>

        <div className="sidebar-footer">
          <div className="user-card">
            <div className="avatar">
              {displayName.slice(0, 1).toUpperCase()}
            </div>
            <div>
              <p className="user-name">{displayName}</p>
              <p className="user-role">Research prototype</p>
            </div>
          </div>

          <button className="logout-btn" onClick={handleLogout}>
            Log out
          </button>
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
                {message.files && message.files.length > 0 && (
                  <div className="message-files">
                    {message.files.map((name) => (
                      <span key={name} className="file-pill">
                        {name}
                      </span>
                    ))}
                  </div>
                )}
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
              placeholder="Ask the orchestrator a question, or attach Tcl and log files for debugging..."
              onChange={(event) => setInput(event.target.value)}
              onKeyDown={handleKeyDown}
            />

            <button onClick={handleSend} disabled={currentStatus === "Running"}>
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
              <strong>Orchestrator</strong>
              <span>{traceStatus.orchestrator}</span>
            </div>

            <div>
              <strong>Diagnostic Agent</strong>
              <span>{traceStatus.diagnostic}</span>
            </div>

            <div>
              <strong>Script Fixer Agent</strong>
              <span>{traceStatus.fixer}</span>
            </div>

            <div>
              <strong>Neo4j RAG</strong>
              <span>{traceStatus.rag}</span>
            </div>

            {detectedCodes.length > 0 && (
              <div>
                <strong>Detected Codes</strong>
                <span>{detectedCodes.join(", ")}</span>
              </div>
            )}

            {lastTrace && (
              <div>
                <strong>Backend</strong>
                <span>{JSON.stringify(lastTrace)}</span>
              </div>
            )}
          </section>
        )}

        <div className="history-header small-header">
          <h3>History</h3>
          <span>{historyItems.length} runs</span>
        </div>

        <div className="history-list">
          {historyItems.length === 0 ? (
            <p className="empty-history">No debug runs yet.</p>
          ) : (
            historyItems.map((item) => (
              <button key={item.id} className="history-item">
                <strong>{item.title}</strong>
                <p>
                  {item.codes.length > 0
                    ? item.codes.join(", ")
                    : "No codes detected"}
                </p>
                <span className={`mini-status ${statusStyle[item.status]}`}>
                  {item.status}
                </span>
              </button>
            ))
          )}
        </div>
      </aside>
    </div>
  );
}

export default App;