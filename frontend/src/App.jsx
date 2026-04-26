import { useEffect, useMemo, useState } from "react";
import "./App.css";

const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL || "http://127.0.0.1:8000";

const STORAGE_KEYS = {
  loggedIn: "eda_debugger_logged_in",
  displayName: "eda_debugger_display_name",
  history: "eda_debugger_history",
};

const initialMessages = [
  {
    role: "assistant",
    content:
      "Hi, I am your Agentic EDA Script Debugger. You can ask me EDA questions, or attach Tcl scripts and Genus logs for debugging.",
  },
];

const statusClassMap = {
  Ready: "status-gray",
  Running: "status-blue",
  Completed: "status-green",
  Error: "status-orange",
  "Auto Fixed": "status-green",
  "Manual Fix Required": "status-orange",
  "Partial Fix Applied": "status-blue",
  "No Fix Needed": "status-gray",
};

function inferFixStatus(answer) {
  const text = (answer || "").toLowerCase();

  if (text.includes("auto fixed")) return "Auto Fixed";
  if (text.includes("manual fix required")) return "Manual Fix Required";
  if (text.includes("partial fix applied")) return "Partial Fix Applied";
  if (text.includes("no fix needed")) return "No Fix Needed";

  return "Completed";
}

function extractDetectedCodes(answer) {
  const matches = (answer || "").match(/\b[A-Z]{2,6}-\d+\b/g);
  return Array.from(new Set(matches || []));
}

function makeSessionTitle(files, message, status) {
  const fileNames = files.map((file) => file.name).join(", ");

  if (fileNames) {
    return fileNames.length > 48 ? `${fileNames.slice(0, 48)}...` : fileNames;
  }

  const trimmed = message.trim();

  if (trimmed) {
    return trimmed.length > 48 ? `${trimmed.slice(0, 48)}...` : trimmed;
  }

  return status || "debug_session";
}

function safeLoadHistory() {
  try {
    const raw = localStorage.getItem(STORAGE_KEYS.history);
    const parsed = JSON.parse(raw || "[]");
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function saveHistory(historyItems) {
  localStorage.setItem(STORAGE_KEYS.history, JSON.stringify(historyItems));
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

        <div>
          <h1>EDA Debugger</h1>
          <p>Agentic Tcl/log debugging prototype for Cadence Genus.</p>
        </div>

        <label>
          Display Name
          <input
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder="Enter display name"
          />
        </label>

        <button type="submit">Enter Prototype</button>

        <small>
          Prototype login only. Persistent production authentication can be added
          with a database service later.
        </small>
      </form>
    </div>
  );
}

function TraceStatus({ label, status, spinning = false }) {
  return (
    <div className="trace-row">
      <strong>{label}</strong>

      <span className="trace-status">
        {spinning && <span className="agent-spinner" />}
        {status}
      </span>
    </div>
  );
}

function TextBlock({ text }) {
  const lines = text.split("\n");

  return (
    <div className="text-block">
      {lines.map((line, index) => {
        const trimmed = line.trim();

        if (!trimmed) {
          return <div key={index} className="line-spacer" />;
        }

        const boldMatch = trimmed.match(/^\*\*(.+)\*\*$/);

        if (boldMatch) {
          return (
            <div key={index} className="markdown-heading">
              {boldMatch[1]}
            </div>
          );
        }

        return <div key={index}>{line}</div>;
      })}
    </div>
  );
}

function MessageContent({ content }) {
  const text = content || "";
  const parts = [];
  const regex = /```(\w+)?\n?([\s\S]*?)```/g;

  let lastIndex = 0;
  let match;

  while ((match = regex.exec(text)) !== null) {
    if (match.index > lastIndex) {
      parts.push({
        type: "text",
        value: text.slice(lastIndex, match.index),
      });
    }

    parts.push({
      type: "code",
      language: match[1] || "",
      value: match[2] || "",
    });

    lastIndex = regex.lastIndex;
  }

  if (lastIndex < text.length) {
    parts.push({
      type: "text",
      value: text.slice(lastIndex),
    });
  }

  if (parts.length === 0) {
    return <TextBlock text={text} />;
  }

  return (
    <div className="rendered-message">
      {parts.map((part, index) => {
        if (part.type === "code") {
          return (
            <pre key={index} className="code-block">
              <code>{part.value.trim()}</code>
            </pre>
          );
        }

        return <TextBlock key={index} text={part.value} />;
      })}
    </div>
  );
}

function App() {
  const [isLoggedIn, setIsLoggedIn] = useState(() => {
    return localStorage.getItem(STORAGE_KEYS.loggedIn) === "true";
  });

  const [displayName, setDisplayName] = useState(() => {
    return localStorage.getItem(STORAGE_KEYS.displayName) || "Demo User";
  });

  const [messages, setMessages] = useState(initialMessages);
  const [input, setInput] = useState("");
  const [attachments, setAttachments] = useState([]);
  const [currentStatus, setCurrentStatus] = useState("Ready");
  const [detectedCodes, setDetectedCodes] = useState([]);
  const [showAdvancedTrace, setShowAdvancedTrace] = useState(false);
  const [historyItems, setHistoryItems] = useState(() => safeLoadHistory());
  const [lastTrace, setLastTrace] = useState(null);
  const [runningStage, setRunningStage] = useState("idle");

  useEffect(() => {
    saveHistory(historyItems);
  }, [historyItems]);

  useEffect(() => {
    if (currentStatus !== "Running") {
      setRunningStage("idle");
      return undefined;
    }

    setRunningStage("orchestrator");

    const diagnosticTimer = setTimeout(() => {
      setRunningStage("diagnostic");
    }, 800);

    const fixerTimer = setTimeout(() => {
      setRunningStage("fixer");
    }, 2400);

    return () => {
      clearTimeout(diagnosticTimer);
      clearTimeout(fixerTimer);
    };
  }, [currentStatus]);

  const statusClass = statusClassMap[currentStatus] || "status-gray";

  const traceStatus = useMemo(() => {
    if (currentStatus === "Ready") {
      return {
        backend: "waiting",
        orchestrator: "waiting",
        diagnostic: "waiting",
        fixer: "waiting",
        rag: "waiting",
      };
    }

    if (currentStatus === "Running") {
      return {
        backend: "request active",
        orchestrator:
          runningStage === "orchestrator" ? "routing request" : "completed",
        diagnostic:
          runningStage === "diagnostic"
            ? "extracting evidence + diagnosing"
            : runningStage === "orchestrator"
            ? "pending"
            : "completed",
        fixer:
          runningStage === "fixer"
            ? "generating fix / guidance"
            : runningStage === "orchestrator" || runningStage === "diagnostic"
            ? "pending"
            : "completed",
        rag:
          runningStage === "diagnostic" || runningStage === "fixer"
            ? "retrieving when matching codes exist"
            : "pending",
      };
    }

    if (currentStatus === "Error") {
      return {
        backend: "error",
        orchestrator: "check backend",
        diagnostic: "check backend",
        fixer: "check backend",
        rag: "check backend",
      };
    }

    return {
      backend: "ok",
      orchestrator: "completed",
      diagnostic: "completed",
      fixer: "completed",
      rag: "retrieval used when matching codes exist",
    };
  }, [currentStatus, runningStage]);

  function handleLogin(name) {
    localStorage.setItem(STORAGE_KEYS.loggedIn, "true");
    localStorage.setItem(STORAGE_KEYS.displayName, name);
    setDisplayName(name);
    setIsLoggedIn(true);
  }

  function handleLogout() {
    localStorage.removeItem(STORAGE_KEYS.loggedIn);
    localStorage.removeItem(STORAGE_KEYS.displayName);

    setIsLoggedIn(false);
    setDisplayName("Demo User");
    setMessages(initialMessages);
    setInput("");
    setAttachments([]);
    setCurrentStatus("Ready");
    setDetectedCodes([]);
    setShowAdvancedTrace(false);
    setLastTrace(null);
    setRunningStage("idle");
  }

  function handleFilesSelected(event) {
    const newFiles = Array.from(event.target.files || []);

    setAttachments((prevFiles) => {
      const mergedFiles = [...prevFiles];

      newFiles.forEach((newFile) => {
        const alreadyExists = mergedFiles.some((existingFile) => {
          return (
            existingFile.name === newFile.name &&
            existingFile.size === newFile.size &&
            existingFile.lastModified === newFile.lastModified
          );
        });

        if (!alreadyExists) {
          mergedFiles.push(newFile);
        }
      });

      return mergedFiles;
    });

    event.target.value = "";
  }

  async function handleSend() {
    const trimmedMessage = input.trim();
    const filesForRequest = [...attachments];

    if (!trimmedMessage && filesForRequest.length === 0) {
      return;
    }

    const finalUserMessage =
      trimmedMessage ||
      `Please analyze the attached files: ${filesForRequest
        .map((file) => file.name)
        .join(", ")}.`;

    const fileNames = filesForRequest.map((file) => file.name);

    setMessages((prev) => [
      ...prev,
      {
        role: "user",
        content: finalUserMessage,
        files: fileNames,
      },
    ]);

    setInput("");
    setAttachments([]);
    setCurrentStatus("Running");
    setDetectedCodes([]);
    setLastTrace(null);

    try {
      const formData = new FormData();
      formData.append("message", finalUserMessage);

      filesForRequest.forEach((file) => {
        formData.append("files", file);
      });

      const response = await fetch(`${API_BASE_URL}/chat`, {
        method: "POST",
        body: formData,
      });

      if (!response.ok) {
        throw new Error(`Backend returned HTTP ${response.status}`);
      }

      const data = await response.json();

      const answer =
        data.answer ||
        data.response ||
        data.message ||
        "No response returned from backend.";

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
          title: makeSessionTitle(filesForRequest, finalUserMessage, status),
          status,
          codes,
          createdAt: new Date().toISOString(),
        },
        ...prev,
      ]);
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
    setRunningStage("idle");
  }

  function clearHistory() {
    setHistoryItems([]);
    localStorage.removeItem(STORAGE_KEYS.history);
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
                {message.role === "user"
                  ? displayName.slice(0, 1).toUpperCase()
                  : "A"}
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

                <MessageContent content={message.content} />
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
              disabled={currentStatus === "Running"}
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
          <span className={`mini-status ${statusClass}`}>{currentStatus}</span>
        </div>

        <button
          className="trace-panel-button"
          onClick={() => setShowAdvancedTrace((value) => !value)}
        >
          {showAdvancedTrace ? "Hide Advanced Trace" : "Show Advanced Trace"}
        </button>

        {showAdvancedTrace && (
          <section className="trace-panel">
            <TraceStatus
              label="Backend API"
              status={traceStatus.backend}
              spinning={currentStatus === "Running"}
            />

            <TraceStatus
              label="Google ADK Orchestrator"
              status={traceStatus.orchestrator}
              spinning={
                currentStatus === "Running" && runningStage === "orchestrator"
              }
            />

            <TraceStatus
              label="Diagnostic Agent"
              status={traceStatus.diagnostic}
              spinning={
                currentStatus === "Running" && runningStage === "diagnostic"
              }
            />

            <TraceStatus
              label="Script Fixer Agent"
              status={traceStatus.fixer}
              spinning={currentStatus === "Running" && runningStage === "fixer"}
            />

            <TraceStatus
              label="Neo4j RAG"
              status={traceStatus.rag}
              spinning={
                currentStatus === "Running" &&
                (runningStage === "diagnostic" || runningStage === "fixer")
              }
            />

            {detectedCodes.length > 0 && (
              <div className="trace-row">
                <strong>Detected Codes</strong>
                <span>{detectedCodes.join(", ")}</span>
              </div>
            )}

            {lastTrace && (
              <div className="trace-row">
                <strong>Backend Trace</strong>
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
                  {item.codes && item.codes.length > 0
                    ? item.codes.join(", ")
                    : "No codes detected"}
                </p>

                <span
                  className={`mini-status ${
                    statusClassMap[item.status] || "status-gray"
                  }`}
                >
                  {item.status}
                </span>
              </button>
            ))
          )}
        </div>

        {historyItems.length > 0 && (
          <button className="clear-history-btn" onClick={clearHistory}>
            Clear History
          </button>
        )}
      </aside>
    </div>
  );
}

export default App;