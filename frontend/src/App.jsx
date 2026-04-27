import { useEffect, useMemo, useRef, useState } from "react";
import "./App.css";
import { supabase } from "./supabaseClient";

const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL || "http://127.0.0.1:8000";

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
  Cancelled: "status-gray",
  "Auto Fixed": "status-green",
  "Manual Fix Required": "status-orange",
  "Partial Fix Applied": "status-blue",
  "No Fix Needed": "status-gray",
};

function inferFixStatus(answer) {
  const text = (answer || "").toLowerCase();

  if (text.includes("partial fix applied")) return "Partial Fix Applied";
  if (text.includes("manual fix required")) return "Manual Fix Required";
  if (text.includes("auto fixed")) return "Auto Fixed";
  if (text.includes("no fix needed")) return "No Fix Needed";
  if (text.includes("request cancelled")) return "Cancelled";

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

function isTemporarySessionId(sessionId) {
  return typeof sessionId === "string" && sessionId.startsWith("temp-");
}

function LoginScreen({ onAuthReady }) {
  const [mode, setMode] = useState("login");
  const [displayName, setDisplayName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");

  const [authStatus, setAuthStatus] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [isResetting, setIsResetting] = useState(false);

  async function upsertProfile(user, name) {
    if (!user?.id) return;

    const finalName = name?.trim() || user.email || "EDA User";

    const { error } = await supabase.from("profiles").upsert({
      id: user.id,
      display_name: finalName,
    });

    if (error) {
      console.warn("Profile upsert failed:", error.message);
    }
  }

  async function handleSubmit(event) {
    event.preventDefault();

    const cleanEmail = email.trim();
    const cleanPassword = password.trim();
    const cleanName = displayName.trim() || "";

    if (!cleanEmail || !cleanPassword) {
      setAuthStatus("Please enter both email and password.");
      return;
    }

    setIsSubmitting(true);
    setAuthStatus("");

    try {
      if (mode === "signup") {
        const { data, error } = await supabase.auth.signUp({
          email: cleanEmail,
          password: cleanPassword,
          options: {
            data: {
              display_name: cleanName,
            },
          },
        });

        if (error) throw error;

        if (data.user) {
          await upsertProfile(data.user, cleanName);
        }

        if (data.session?.user) {
          onAuthReady(data.session.user);
        } else {
          setAuthStatus(
            "Account created. Check your email if confirmation is enabled."
          );
        }
      } else {
        const { data, error } = await supabase.auth.signInWithPassword({
          email: cleanEmail,
          password: cleanPassword,
        });

        if (error) throw error;

        if (data.user) {
          await upsertProfile(data.user, cleanName);
          onAuthReady(data.user);
        }
      }
    } catch (error) {
      setAuthStatus(error.message || "Authentication failed.");
    } finally {
      setIsSubmitting(false);
    }
  }

  async function handleForgotPassword() {
    const cleanEmail = email.trim();

    if (!cleanEmail) {
      setAuthStatus("Enter your email first, then click Forgot password.");
      return;
    }

    setIsResetting(true);
    setAuthStatus("");

    try {
      const redirectTo = window.location.origin;

      const { error } = await supabase.auth.resetPasswordForEmail(cleanEmail, {
        redirectTo,
      });

      if (error) throw error;

      setAuthStatus(
        "Password reset email sent. Check your inbox and follow the link."
      );
    } catch (error) {
      setAuthStatus(error.message || "Password reset failed.");
    } finally {
      setIsResetting(false);
    }
  }

  return (
    <div className="login-page">
      <form className="login-card" onSubmit={handleSubmit}>
        <div className="brand-icon large">E</div>

        <div>
          <h1>EDA Debugger</h1>
          <p>Agentic Tcl/log debugging prototype for Cadence Genus.</p>
        </div>

        <div className="auth-tabs">
          <button
            type="button"
            className={mode === "login" ? "auth-tab-active" : ""}
            onClick={() => setMode("login")}
          >
            Log In
          </button>

          <button
            type="button"
            className={mode === "signup" ? "auth-tab-active" : ""}
            onClick={() => setMode("signup")}
          >
            Sign Up
          </button>
        </div>

        <label>
          Display Name
          <input
            value={displayName}
            onChange={(event) => setDisplayName(event.target.value)}
            placeholder="Enter display name"
          />
        </label>

        <label>
          Email
          <input
            type="email"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            placeholder="you@example.com"
          />
        </label>

        <label>
          Password
          <input
            type="password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            placeholder="Enter password"
          />
        </label>

        <button type="submit" disabled={isSubmitting}>
          {isSubmitting
            ? "Please wait..."
            : mode === "login"
            ? "Log In"
            : "Create Account"}
        </button>

        {mode === "login" && (
          <button
            type="button"
            className="text-link-button"
            onClick={handleForgotPassword}
            disabled={isResetting}
          >
            {isResetting ? "Sending reset email..." : "Forgot password?"}
          </button>
        )}

        {authStatus && <p className="auth-status">{authStatus}</p>}

        <small>
          Supabase Auth is used for prototype login and database-backed chat
          history.
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

function SettingsPanel({
  user,
  displayName,
  recoveryMode,
  onClose,
  onProfileUpdated,
  onPasswordUpdated,
}) {
  const [name, setName] = useState(displayName || "");
  const [newPassword, setNewPassword] = useState("");
  const [status, setStatus] = useState("");
  const [isSavingName, setIsSavingName] = useState(false);
  const [isChangingPassword, setIsChangingPassword] = useState(false);

  async function handleUpdateName(event) {
    event.preventDefault();

    if (!user?.id) {
      setStatus("No logged-in user found.");
      return;
    }

    const cleanName = name.trim() || "EDA User";

    setIsSavingName(true);
    setStatus("");

    try {
      const { error } = await supabase.from("profiles").upsert({
        id: user.id,
        display_name: cleanName,
      });

      if (error) throw error;

      onProfileUpdated(cleanName);
      setStatus("Profile name updated.");
    } catch (error) {
      setStatus(error.message || "Failed to update profile.");
    } finally {
      setIsSavingName(false);
    }
  }

  async function handleChangePassword(event) {
    event.preventDefault();

    const cleanPassword = newPassword.trim();

    if (!cleanPassword) {
      setStatus("Enter a new password first.");
      return;
    }

    if (cleanPassword.length < 6) {
      setStatus("Password should be at least 6 characters.");
      return;
    }

    setIsChangingPassword(true);
    setStatus("");

    try {
      const { error } = await supabase.auth.updateUser({
        password: cleanPassword,
      });

      if (error) throw error;

      setNewPassword("");
      setStatus("Password updated successfully.");

      if (onPasswordUpdated) {
        onPasswordUpdated();
      }
    } catch (error) {
      setStatus(error.message || "Failed to update password.");
    } finally {
      setIsChangingPassword(false);
    }
  }

  return (
    <div className="settings-card">
      <div className="settings-header">
        <div>
          <h3>Settings</h3>
          <p>Manage profile and password for this prototype account.</p>
        </div>

        <button className="close-settings-btn" onClick={onClose}>
          Close
        </button>
      </div>

      {recoveryMode && (
        <p className="settings-status">
          Password recovery mode is active. Enter a new password below to finish
          resetting your account password.
        </p>
      )}

      <form className="settings-section" onSubmit={handleUpdateName}>
        <label>
          Display Name
          <input
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder="Display name"
          />
        </label>

        <button type="submit" disabled={isSavingName}>
          {isSavingName ? "Saving..." : "Update Name"}
        </button>
      </form>

      <form className="settings-section" onSubmit={handleChangePassword}>
        <label>
          New Password
          <input
            type="password"
            value={newPassword}
            onChange={(event) => setNewPassword(event.target.value)}
            placeholder="At least 6 characters"
          />
        </label>

        <button type="submit" disabled={isChangingPassword}>
          {isChangingPassword ? "Updating..." : "Change Password"}
        </button>
      </form>

      {status && <p className="settings-status">{status}</p>}
    </div>
  );
}

function App() {
  const activeAbortControllerRef = useRef(null);

  const [authLoading, setAuthLoading] = useState(true);
  const [user, setUser] = useState(null);
  const [displayName, setDisplayName] = useState("");

  const [sessions, setSessions] = useState([]);
  const [activeSessionId, setActiveSessionId] = useState(null);

  const [messages, setMessages] = useState(initialMessages);
  const [input, setInput] = useState("");
  const [attachments, setAttachments] = useState([]);

  const [currentStatus, setCurrentStatus] = useState("Ready");
  const [detectedCodes, setDetectedCodes] = useState([]);
  const [showAdvancedTrace, setShowAdvancedTrace] = useState(false);
  const [lastTrace, setLastTrace] = useState(null);
  const [runningStage, setRunningStage] = useState("idle");

  const [showSettings, setShowSettings] = useState(false);
  const [passwordRecoveryMode, setPasswordRecoveryMode] = useState(false);
  const [uiError, setUiError] = useState("");

  useEffect(() => {
    let mounted = true;
    let timeoutId;

    async function loadAuthSession() {
      try {
        timeoutId = window.setTimeout(() => {
          if (!mounted) return;

          console.warn("Supabase auth check timed out. Continuing without blocking UI.");
          setAuthLoading(false);
        }, 8000);

        const { data, error } = await supabase.auth.getSession();

        if (!mounted) return;

        if (error) {
          console.warn("Auth session load failed:", error.message);
          setUser(null);
          setUiError(`Auth session load failed: ${error.message}`);
          return;
        }

        const currentUser = data?.session?.user || null;
        setUser(currentUser);

        if (currentUser) {
          await loadProfile(currentUser.id, currentUser.email);
          await loadSessions(currentUser.id);
        }
      } catch (error) {
        if (!mounted) return;

        console.error("Unexpected auth loading error:", error);
        setUser(null);
        setUiError(
          `Unexpected auth loading error: ${error?.message || String(error)}`
        );
      } finally {
        if (timeoutId) {
          window.clearTimeout(timeoutId);
        }

        if (mounted) {
          setAuthLoading(false);
        }
      }
    }

    loadAuthSession();

    const { data } = supabase.auth.onAuthStateChange(async (_event, session) => {
      try {
        const currentUser = session?.user || null;

        setUser(currentUser);

        if (_event === "PASSWORD_RECOVERY") {
          setPasswordRecoveryMode(true);
          setShowSettings(true);
          setShowAdvancedTrace(false);
          setUiError(
            "Password recovery mode active. Enter your new password in Settings."
          );
        }

        if (currentUser) {
          await loadProfile(currentUser.id, currentUser.email);
          await loadSessions(currentUser.id);
        } else {
          setDisplayName("Demo User");
          setSessions([]);
          setActiveSessionId(null);
          setMessages(initialMessages);
        }
      } catch (error) {
        console.error("Auth state change error:", error);
        setUiError(`Auth state change error: ${error?.message || String(error)}`);
      } finally {
        setAuthLoading(false);
      }
    });

    return () => {
      mounted = false;

      if (timeoutId) {
        window.clearTimeout(timeoutId);
      }

      if (activeAbortControllerRef.current) {
        activeAbortControllerRef.current.abort();
        activeAbortControllerRef.current = null;
      }

      data?.subscription?.unsubscribe();
    };
  }, []);

  useEffect(() => {
    if (currentStatus !== "Running") {
      setRunningStage("idle");
      return undefined;
    }

    setRunningStage("backend");

    const agentTimer = setTimeout(() => {
      setRunningStage("agentic");
    }, 900);

    return () => {
      clearTimeout(agentTimer);
    };
  }, [currentStatus]);

  const activeSession = sessions.find((item) => item.id === activeSessionId);
  const statusClass = statusClassMap[currentStatus] || "status-gray";

  const traceStatus = useMemo(() => {
    const route = lastTrace?.route || lastTrace?.adk_trace?.app_name || "";

    if (currentStatus === "Ready") {
      return {
        backend: "waiting",
        agentic: "waiting",
      };
    }

    if (currentStatus === "Running") {
      return {
        backend: runningStage === "backend" ? "request active" : "ok",
        agentic:
          runningStage === "agentic"
            ? "running EDA debugging pipeline"
            : "pending",
      };
    }

    if (currentStatus === "Error") {
      return {
        backend: "error",
        agentic: "failed or interrupted",
      };
    }

    if (currentStatus === "Cancelled") {
      return {
        backend: "cancelled",
        agentic: "cancelled by user",
      };
    }

    return {
      backend: "ok",
      agentic: route ? `completed (${route})` : "completed",
    };
  }, [currentStatus, runningStage, lastTrace]);

  async function loadProfile(userId, fallbackEmail) {
    const { data, error } = await supabase
      .from("profiles")
      .select("display_name")
      .eq("id", userId)
      .maybeSingle();

    if (error) {
      console.warn("Load profile failed:", error.message);
      setDisplayName(fallbackEmail || "EDA User");
      return;
    }

    setDisplayName(data?.display_name || fallbackEmail || "EDA User");
  }

  async function loadSessions(userId) {
    const { data, error } = await supabase
      .from("chat_sessions")
      .select("*")
      .eq("user_id", userId)
      .order("updated_at", { ascending: false });

    if (error) {
      setUiError(`Failed to load sessions: ${error.message}`);
      return;
    }

    setSessions(data || []);
  }

  function createTemporarySession(title) {
    const tempId = `temp-${Date.now()}`;

    const tempSession = {
      id: tempId,
      user_id: user?.id || null,
      title,
      last_fix_status: "Running",
      detected_codes: [],
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
      is_temporary: true,
    };

    setSessions((prev) => [tempSession, ...prev]);
    setActiveSessionId(tempId);

    return tempId;
  }

  function replaceTemporarySession(tempId, realSession) {
    setSessions((prev) => {
      const withoutTemp = prev.filter((item) => item.id !== tempId);
      return [realSession, ...withoutTemp];
    });

    setActiveSessionId(realSession.id);
  }

  function updateLocalSession(sessionId, updates) {
    if (!sessionId) return;

    setSessions((prev) =>
      prev.map((session) =>
        session.id === sessionId
          ? {
              ...session,
              ...updates,
              updated_at: new Date().toISOString(),
            }
          : session
      )
    );
  }

  function cancelActiveRequest({ addMessage = false } = {}) {
    if (activeAbortControllerRef.current) {
      activeAbortControllerRef.current.abort();
      activeAbortControllerRef.current = null;
    }

    setCurrentStatus("Cancelled");
    setRunningStage("idle");
    setLastTrace({
      backend: "cancelled",
      message: "Request cancelled by user.",
    });

    if (activeSessionId) {
      updateLocalSession(activeSessionId, {
        last_fix_status: "Cancelled",
        detected_codes: [],
      });
    }

    if (addMessage) {
      setMessages((prev) => [
        ...prev,
        {
          role: "assistant",
          content: "Request cancelled by user.",
        },
      ]);
    }
  }

  async function loadSessionMessages(sessionId) {
    if (!user || !sessionId) return;

    if (activeAbortControllerRef.current) {
      cancelActiveRequest({ addMessage: false });
    }

    setUiError("");
    setShowSettings(false);
    setPasswordRecoveryMode(false);
    setShowAdvancedTrace(false);

    const selectedSession = sessions.find((item) => item.id === sessionId);

    if (selectedSession?.is_temporary || isTemporarySessionId(sessionId)) {
      setActiveSessionId(sessionId);
      setCurrentStatus(selectedSession?.last_fix_status || "Running");
      setDetectedCodes(selectedSession?.detected_codes || []);
      setLastTrace(null);
      setRunningStage("idle");
      return;
    }

    const { data, error } = await supabase
      .from("chat_messages")
      .select("*")
      .eq("session_id", sessionId)
      .eq("user_id", user.id)
      .order("created_at", { ascending: true });

    if (error) {
      setUiError(`Failed to load messages: ${error.message}`);
      return;
    }

    const loadedMessages = (data || []).map((item) => ({
      role: item.role,
      content: item.content,
      files: item.file_names || [],
    }));

    setActiveSessionId(sessionId);
    setMessages(loadedMessages.length > 0 ? loadedMessages : initialMessages);

    if (selectedSession?.last_fix_status === "Running") {
      setCurrentStatus("Cancelled");
      updateLocalSession(sessionId, {
        last_fix_status: "Cancelled",
      });
    } else if (selectedSession?.last_fix_status) {
      setCurrentStatus(selectedSession.last_fix_status);
    } else {
      setCurrentStatus("Ready");
    }

    setDetectedCodes(selectedSession?.detected_codes || []);
    setLastTrace(null);
    setRunningStage("idle");
  }

  async function createSession({
    title = "New Debug Session",
    status = null,
    codes = [],
  } = {}) {
    if (!user) {
      throw new Error("User is not logged in.");
    }

    const { data, error } = await supabase
      .from("chat_sessions")
      .insert({
        user_id: user.id,
        title,
        last_fix_status: status,
        detected_codes: codes,
      })
      .select()
      .single();

    if (error) {
      throw new Error(`Failed to create session: ${error.message}`);
    }

    return data;
  }

  async function updateSession(sessionId, updates) {
    if (!user || !sessionId) return;

    updateLocalSession(sessionId, updates);

    if (isTemporarySessionId(sessionId)) {
      return;
    }

    const { data, error } = await supabase
      .from("chat_sessions")
      .update(updates)
      .eq("id", sessionId)
      .eq("user_id", user.id)
      .select()
      .single();

    if (error) {
      console.warn("Update session failed:", error.message);
      return;
    }

    setSessions((prev) => {
      const filtered = prev.filter((item) => item.id !== sessionId);
      return [data, ...filtered];
    });
  }

  async function saveMessage({
    sessionId,
    role,
    content,
    fileNames = [],
    fixStatus = null,
    codes = [],
  }) {
    if (!user || !sessionId || isTemporarySessionId(sessionId)) return;

    const { error } = await supabase.from("chat_messages").insert({
      session_id: sessionId,
      user_id: user.id,
      role,
      content,
      file_names: fileNames,
      fix_status: fixStatus,
      detected_codes: codes,
    });

    if (error) {
      console.warn("Save message failed:", error.message);
    }
  }

  async function handleAuthReady(currentUser) {
    setUser(currentUser);
    await loadProfile(currentUser.id, currentUser.email);
    await loadSessions(currentUser.id);
  }

  async function handleLogout() {
    if (activeAbortControllerRef.current) {
      cancelActiveRequest({ addMessage: false });
    }

    await supabase.auth.signOut();

    setUser(null);
    setDisplayName("Demo User");
    setSessions([]);
    setActiveSessionId(null);
    setMessages(initialMessages);
    setInput("");
    setAttachments([]);
    setCurrentStatus("Ready");
    setDetectedCodes([]);
    setShowAdvancedTrace(false);
    setLastTrace(null);
    setRunningStage("idle");
    setShowSettings(false);
    setPasswordRecoveryMode(false);
    setUiError("");
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

    if (!user) {
      setUiError("Please log in first.");
      return;
    }

    const finalUserMessage =
      trimmedMessage ||
      `Please analyze the attached files: ${filesForRequest
        .map((file) => file.name)
        .join(", ")}.`;

    const fileNames = filesForRequest.map((file) => file.name);
    const sessionTitle = makeSessionTitle(
      filesForRequest,
      finalUserMessage,
      "Running"
    );

    setUiError("");
    setShowSettings(false);
    setPasswordRecoveryMode(false);

    const userMessage = {
      role: "user",
      content: finalUserMessage,
      files: fileNames,
    };

    setMessages((prev) => [...prev, userMessage]);
    setInput("");
    setAttachments([]);
    setCurrentStatus("Running");
    setDetectedCodes([]);
    setLastTrace(null);

    let sessionId = activeSessionId;

    if (!sessionId) {
      sessionId = createTemporarySession(sessionTitle);
    } else {
      updateLocalSession(sessionId, {
        title: sessionTitle,
        last_fix_status: "Running",
        detected_codes: [],
      });
    }

    const controller = new AbortController();
    activeAbortControllerRef.current = controller;

    try {
      if (isTemporarySessionId(sessionId)) {
        const realSession = await createSession({
          title: sessionTitle,
          status: "Running",
          codes: [],
        });

        replaceTemporarySession(sessionId, realSession);
        sessionId = realSession.id;
      }

      await saveMessage({
        sessionId,
        role: "user",
        content: finalUserMessage,
        fileNames,
      });

      await updateSession(sessionId, {
        title: sessionTitle,
        last_fix_status: "Running",
        detected_codes: [],
      });

      const formData = new FormData();
      formData.append("message", finalUserMessage);

      filesForRequest.forEach((file) => {
        formData.append("files", file);
      });

      const response = await fetch(`${API_BASE_URL}/chat`, {
        method: "POST",
        body: formData,
        signal: controller.signal,
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
      const codes = data.detected_codes?.length
        ? data.detected_codes
        : extractDetectedCodes(answer);

      const assistantMessage = {
        role: "assistant",
        content: answer,
      };

      setMessages((prev) => [...prev, assistantMessage]);
      setCurrentStatus(status);
      setDetectedCodes(codes);
      setLastTrace(data.trace || null);

      await saveMessage({
        sessionId,
        role: "assistant",
        content: answer,
        fixStatus: status,
        codes,
      });

      await updateSession(sessionId, {
        title: sessionTitle,
        last_fix_status: status,
        detected_codes: codes,
      });
    } catch (error) {
      const wasCancelled = error.name === "AbortError";

      const errorMessage = wasCancelled
        ? "Request cancelled by user."
        : `Backend request failed.\n\n${error.message}`;

      setMessages((prev) => [
        ...prev,
        {
          role: "assistant",
          content: errorMessage,
        },
      ]);

      const finalStatus = wasCancelled ? "Cancelled" : "Error";

      setCurrentStatus(finalStatus);
      setRunningStage("idle");
      setLastTrace({
        backend: wasCancelled ? "cancelled" : "failed",
        error: wasCancelled ? "Request cancelled by user." : error.message,
      });

      await saveMessage({
        sessionId,
        role: "assistant",
        content: errorMessage,
        fixStatus: finalStatus,
        codes: [],
      });

      await updateSession(sessionId, {
        last_fix_status: finalStatus,
        detected_codes: [],
      });
    } finally {
      if (activeAbortControllerRef.current === controller) {
        activeAbortControllerRef.current = null;
      }
    }
  }

  function handleCancelButton() {
    cancelActiveRequest({ addMessage: true });

    if (activeSessionId) {
      updateSession(activeSessionId, {
        last_fix_status: "Cancelled",
        detected_codes: [],
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
    if (activeAbortControllerRef.current) {
      cancelActiveRequest({ addMessage: false });

      if (activeSessionId) {
        updateSession(activeSessionId, {
          last_fix_status: "Cancelled",
          detected_codes: [],
        });
      }
    }

    setActiveSessionId(null);
    setMessages(initialMessages);
    setInput("");
    setAttachments([]);
    setCurrentStatus("Ready");
    setDetectedCodes([]);
    setShowAdvancedTrace(false);
    setLastTrace(null);
    setRunningStage("idle");
    setShowSettings(false);
    setPasswordRecoveryMode(false);
    setUiError("");
  }

  function handleOpenSettings() {
    if (activeAbortControllerRef.current) {
      cancelActiveRequest({ addMessage: false });
    }

    setShowSettings(true);
    setShowAdvancedTrace(false);
    setUiError("");
  }

  function handlePasswordUpdated() {
    setPasswordRecoveryMode(false);
    setUiError("Password updated successfully.");
  }

  async function deleteSession(sessionId) {
    if (!user || !sessionId) return;

    if (activeAbortControllerRef.current) {
      cancelActiveRequest({ addMessage: false });
    }

    if (isTemporarySessionId(sessionId)) {
      setSessions((prev) => prev.filter((item) => item.id !== sessionId));
      handleNewChat();
      return;
    }

    const { error } = await supabase
      .from("chat_sessions")
      .delete()
      .eq("id", sessionId)
      .eq("user_id", user.id);

    if (error) {
      setUiError(`Failed to delete session: ${error.message}`);
      return;
    }

    setSessions((prev) => prev.filter((item) => item.id !== sessionId));

    if (activeSessionId === sessionId) {
      handleNewChat();
    }
  }

  if (authLoading) {
    return (
      <div className="loading-page">
        <div className="agent-spinner large-spinner" />
        <p>Loading EDA Debugger...</p>
      </div>
    );
  }

  if (!user) {
    return <LoginScreen onAuthReady={handleAuthReady} />;
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

            <button onClick={handleOpenSettings}>Settings</button>
          </nav>

          <section className="sidebar-history">
            <div className="sidebar-history-header">
              <h2>History</h2>
              <span>{sessions.length}</span>
            </div>

            <div className="sidebar-history-list">
              {sessions.length === 0 ? (
                <p>No saved chats yet.</p>
              ) : (
                sessions.map((session) => (
                  <button
                    key={session.id}
                    type="button"
                    className={`sidebar-history-item ${
                      activeSessionId === session.id ? "active-session" : ""
                    }`}
                    onClick={() => loadSessionMessages(session.id)}
                  >
                    <span>{session.title}</span>
                    <small>{session.last_fix_status || "No status"}</small>
                  </button>
                ))
              )}
            </div>
          </section>
        </div>

        <div className="sidebar-footer">
          <div className="user-card">
            <div className="avatar">
              {displayName.slice(0, 1).toUpperCase()}
            </div>

            <div>
              <p className="user-name">{displayName}</p>
              <p className="user-role">{user.email}</p>
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

        {uiError && <div className="ui-error">{uiError}</div>}

        {showSettings ? (
          <SettingsPanel
            user={user}
            displayName={displayName}
            recoveryMode={passwordRecoveryMode}
            onClose={() => {
              setShowSettings(false);
              setPasswordRecoveryMode(false);
            }}
            onProfileUpdated={(newName) => setDisplayName(newName)}
            onPasswordUpdated={handlePasswordUpdated}
          />
        ) : (
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
        )}

        <footer className="composer-wrap">
          {!showSettings && (
            <>
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
                      <span
                        key={`${file.name}-${file.size}`}
                        className="file-pill"
                      >
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
                  placeholder="Ask a question, or attach Tcl and log files for debugging..."
                  onChange={(event) => setInput(event.target.value)}
                  onKeyDown={handleKeyDown}
                  disabled={currentStatus === "Running"}
                />

                <div className="composer-actions">
                  <button
                    className="send-btn"
                    onClick={handleSend}
                    disabled={currentStatus === "Running"}
                  >
                    {currentStatus === "Running" ? "Running..." : "Send"}
                  </button>

                  {currentStatus === "Running" && (
                    <button
                      className="cancel-btn"
                      type="button"
                      onClick={handleCancelButton}
                    >
                      Cancel
                    </button>
                  )}
                </div>
              </div>
            </>
          )}

          <p className="disclaimer">
            EDADebugger can make mistakes. Check and clarify important
            information.
          </p>
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
              spinning={currentStatus === "Running" && runningStage === "backend"}
            />

            <TraceStatus
              label="Agentic AI System"
              status={traceStatus.agentic}
              spinning={currentStatus === "Running" && runningStage === "agentic"}
            />

            {detectedCodes.length > 0 && (
              <div className="trace-row">
                <strong>Detected Codes</strong>
                <span>{detectedCodes.join(", ")}</span>
              </div>
            )}
          </section>
        )}

        <div className="history-header small-header">
          <h3>Run Summary</h3>
        </div>

        <div className="history-list">
          {!activeSession ? (
            <p className="empty-history">No active saved session yet.</p>
          ) : (
            <div className="history-item static-item">
              <strong>{activeSession.title || "Current session"}</strong>

              <p>
                {detectedCodes.length > 0
                  ? detectedCodes.join(", ")
                  : "No codes detected"}
              </p>

              <span className={`mini-status ${statusClass}`}>
                {currentStatus}
              </span>

              <button
                className="delete-session-btn"
                onClick={() => deleteSession(activeSession.id)}
              >
                Delete Session
              </button>
            </div>
          )}
        </div>
      </aside>
    </div>
  );
}

export default App;