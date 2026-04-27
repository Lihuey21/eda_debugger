import { useEffect, useRef, useState } from "react";
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

const LOCAL_GREETING_REPLY =
  "Hello! Upload both a Tcl script and the matching Genus/EDA log for a full debugging run, or ask me a Cadence/EDA question.";

function isSimpleGreeting(message) {
  const normalized = String(message || "")
    .trim()
    .toLowerCase()
    .replace(/[^\w\s]/g, "")
    .replace(/\s+/g, " ");

  return [
    "hi",
    "hello",
    "hey",
    "yo",
    "sup",
    "good morning",
    "good afternoon",
    "good evening",
  ].includes(normalized);
}

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

function clearSupabaseBrowserState() {
  const shouldRemoveKey = (key) => {
    const lowerKey = key.toLowerCase();
    return (
      key.startsWith("sb-") ||
      lowerKey.includes("supabase") ||
      lowerKey.includes("auth-token")
    );
  };

  Object.keys(localStorage).forEach((key) => {
    if (shouldRemoveKey(key)) localStorage.removeItem(key);
  });

  Object.keys(sessionStorage).forEach((key) => {
    if (shouldRemoveKey(key)) sessionStorage.removeItem(key);
  });
}

function withTimeout(promise, timeoutMs = 2000) {
  let timeoutId;

  const timeoutPromise = new Promise((_, reject) => {
    timeoutId = window.setTimeout(() => {
      reject(new Error("Operation timed out"));
    }, timeoutMs);
  });

  return Promise.race([promise, timeoutPromise]).finally(() => {
    if (timeoutId) window.clearTimeout(timeoutId);
  });
}

function fireAndForget(promise, label = "background task") {
  Promise.resolve(promise).catch((error) => {
    console.warn(`${label} failed:`, error?.message || error);
  });
}

function isTemporarySessionId(sessionId) {
  return typeof sessionId === "string" && sessionId.startsWith("temp-");
}

function messageCacheKey(userId, sessionId) {
  if (!userId || !sessionId) return null;
  return `eda-debugger-messages:${userId}:${sessionId}`;
}

function loadCachedMessages(userId, sessionId) {
  const key = messageCacheKey(userId, sessionId);
  if (!key) return [];

  try {
    const raw = localStorage.getItem(key);
    if (!raw) return [];

    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];

    return parsed.filter(
      (message) =>
        message &&
        (message.role === "user" || message.role === "assistant") &&
        typeof message.content === "string"
    );
  } catch {
    return [];
  }
}

function saveCachedMessages(userId, sessionId, nextMessages) {
  const key = messageCacheKey(userId, sessionId);
  if (!key || !Array.isArray(nextMessages)) return;

  try {
    localStorage.setItem(
      key,
      JSON.stringify(
        nextMessages.map((message) => ({
          role: message.role,
          content: String(message.content || ""),
          files: Array.isArray(message.files) ? message.files : [],
        }))
      )
    );
  } catch (error) {
    console.warn("Message cache save failed:", error?.message || error);
  }
}

function migrateCachedMessages(userId, oldSessionId, newSessionId) {
  if (!userId || !oldSessionId || !newSessionId || oldSessionId === newSessionId) return;

  const oldKey = messageCacheKey(userId, oldSessionId);
  const newKey = messageCacheKey(userId, newSessionId);
  if (!oldKey || !newKey) return;

  try {
    const raw = localStorage.getItem(oldKey);
    if (!raw) return;
    localStorage.setItem(newKey, raw);
    localStorage.removeItem(oldKey);
  } catch (error) {
    console.warn("Message cache migration failed:", error?.message || error);
  }
}

function normalizeFixStatus(rawStatus, answer = "") {
  const normalized = String(rawStatus || "").trim().toLowerCase();

  if (normalized === "partial fix applied" || normalized === "partial_fix_applied") {
    return "Partial Fix Applied";
  }

  if (normalized === "manual fix required" || normalized === "manual_required") {
    return "Manual Fix Required";
  }

  if (normalized === "auto fixed" || normalized === "auto_fixed") {
    return "Auto Fixed";
  }

  if (normalized === "no fix needed" || normalized === "no_fix_needed") {
    return "No Fix Needed";
  }

  if (normalized === "cancelled" || normalized === "canceled") {
    return "Cancelled";
  }

  if (normalized === "error" || normalized === "failed") {
    return "Error";
  }

  if (normalized === "completed" || normalized === "complete") {
    return "Completed";
  }

  return inferFixStatus(answer);
}

function inferFixStatus(answer) {
  const text = String(answer || "").toLowerCase();

  if (text.includes("partial fix applied")) return "Partial Fix Applied";
  if (text.includes("manual fix required")) return "Manual Fix Required";
  if (text.includes("auto fixed")) return "Auto Fixed";
  if (text.includes("no fix needed")) return "No Fix Needed";
  if (text.includes("request cancelled")) return "Cancelled";

  return "Completed";
}

function normalizeLoadedSession(session) {
  if (!session) return session;

  if (session.last_fix_status === "Running") {
    return {
      ...session,
      last_fix_status: "Cancelled",
    };
  }

  return session;
}

function makeSessionTitle(files, message, fallback = "Debug Session") {
  const fileNames = files.map((file) => file.name).join(", ");

  if (fileNames) {
    return fileNames.length > 52 ? `${fileNames.slice(0, 52)}...` : fileNames;
  }

  const trimmed = String(message || "").trim();

  if (trimmed) {
    return trimmed.length > 52 ? `${trimmed.slice(0, 52)}...` : trimmed;
  }

  return fallback;
}

function LoginScreen({ onAuthReady }) {
  const [mode, setMode] = useState("login");
  const [displayName, setDisplayName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [authStatus, setAuthStatus] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [isResetting, setIsResetting] = useState(false);

  async function upsertProfile(currentUser, name) {
    if (!currentUser?.id) return;

    const finalName = name?.trim() || currentUser.email || "EDA User";

    const { error } = await supabase.from("profiles").upsert({
      id: currentUser.id,
      display_name: finalName,
    });

    if (error) console.warn("Profile upsert failed:", error.message);
  }

  async function handleSubmit(event) {
    event.preventDefault();

    const cleanEmail = email.trim();
    const cleanPassword = password.trim();
    const cleanName = displayName.trim() || cleanEmail || "EDA User";

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
          options: { data: { display_name: cleanName } },
        });

        if (error) throw error;

        if (data.user) await upsertProfile(data.user, cleanName);

        if (data.session?.user) {
          onAuthReady(data.session.user);
        } else {
          setAuthStatus("Account created. Check your email if confirmation is enabled.");
        }
      } else {
        const { data, error } = await supabase.auth.signInWithPassword({
          email: cleanEmail,
          password: cleanPassword,
        });

        if (error) throw error;

        if (data.user) {
          // Do not overwrite an existing saved display name during normal login.
          // Only update the profile here if the user explicitly typed a display name.
          if (displayName.trim()) {
            await upsertProfile(data.user, cleanName);
          }

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
      const { error } = await supabase.auth.resetPasswordForEmail(cleanEmail, {
        redirectTo: window.location.origin,
      });

      if (error) throw error;

      setAuthStatus("Password reset email sent. Check your inbox and follow the link.");
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
          {isSubmitting ? "Please wait..." : mode === "login" ? "Log In" : "Create Account"}
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
      </form>
    </div>
  );
}

function TextBlock({ text }) {
  return (
    <div className="text-block">
      {String(text || "")
        .split("\n")
        .map((line, index) => {
          const trimmed = line.trim();

          if (!trimmed) return <div key={index} className="line-spacer" />;

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
  const text = String(content || "");
  const parts = [];
  const regex = /```(\w+)?\n?([\s\S]*?)```/g;
  let lastIndex = 0;
  let match;

  while ((match = regex.exec(text)) !== null) {
    if (match.index > lastIndex) {
      parts.push({ type: "text", value: text.slice(lastIndex, match.index) });
    }

    parts.push({ type: "code", value: match[2] || "" });
    lastIndex = regex.lastIndex;
  }

  if (lastIndex < text.length) {
    parts.push({ type: "text", value: text.slice(lastIndex) });
  }

  if (parts.length === 0) return <TextBlock text={text} />;

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

    const cleanName = name.trim() || user.email || "EDA User";

    setIsSavingName(true);
    setStatus("");

    try {
      // Update the visible UI immediately so the Settings page never looks frozen.
      onProfileUpdated(cleanName);

      const profileWrite = supabase.from("profiles").upsert(
        {
          id: user.id,
          display_name: cleanName,
        },
        { onConflict: "id" }
      );

      const metadataWrite = supabase.auth.updateUser({
        data: { display_name: cleanName },
      });

      const [profileResult, metadataResult] = await withTimeout(
        Promise.all([profileWrite, metadataWrite]),
        5000
      );

      if (profileResult?.error) throw profileResult.error;
      if (metadataResult?.error) throw metadataResult.error;

      setStatus("Profile name updated.");
    } catch (error) {
      // Keep the local UI update, but show the real persistence problem.
      setStatus(
        `Name updated on this screen, but database save did not complete: ${
          error.message || "Unknown error"
        }`
      );
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
      const sessionResult = await withTimeout(supabase.auth.getSession(), 12000);
      const activeSession = sessionResult?.data?.session;

      if (sessionResult?.error) throw sessionResult.error;
      if (!activeSession) {
        throw new Error(
          "No active Supabase auth session found. Please log out, log in again, then change the password."
        );
      }

      const { error } = await withTimeout(
        supabase.auth.updateUser({ password: cleanPassword }),
        20000
      );
      if (error) throw error;

      setNewPassword("");
      setStatus("Password updated successfully.");
      onPasswordUpdated?.();
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

        <button type="button" className="close-settings-btn" onClick={onClose}>
          Close
        </button>
      </div>

      {recoveryMode && (
        <p className="settings-status">
          Password recovery mode is active. Enter a new password below.
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

function App() {
  const activeAbortControllerRef = useRef(null);
  const activeSessionIdRef = useRef(null);

  const [authLoading, setAuthLoading] = useState(true);
  const [user, setUser] = useState(null);
  const [displayName, setDisplayName] = useState("");

  const [sessions, setSessions] = useState([]);
  const [activeSessionId, setActiveSessionId] = useState(null);

  const [messages, setMessages] = useState(initialMessages);
  const [input, setInput] = useState("");
  const [attachments, setAttachments] = useState([]);

  const [currentStatus, setCurrentStatus] = useState("Ready");
  const [showAdvancedTrace, setShowAdvancedTrace] = useState(false);
  const [lastTrace, setLastTrace] = useState(null);
  const [runningStage, setRunningStage] = useState("idle");
  const [showSettings, setShowSettings] = useState(false);
  const [passwordRecoveryMode, setPasswordRecoveryMode] = useState(false);
  const [uiError, setUiError] = useState("");

  useEffect(() => {
    activeSessionIdRef.current = activeSessionId;
  }, [activeSessionId]);

  useEffect(() => {
    if (currentStatus !== "Running") {
      setRunningStage("idle");
      return undefined;
    }

    setRunningStage("backend");

    const agentTimer = window.setTimeout(() => {
      setRunningStage("agentic");
    }, 900);

    return () => window.clearTimeout(agentTimer);
  }, [currentStatus]);

  useEffect(() => {
    let mounted = true;
    let timedOut = false;
    let timeoutId;

    async function loadAuthSession() {
      try {
        timeoutId = window.setTimeout(() => {
          if (!mounted) return;
          timedOut = true;
          clearSupabaseBrowserState();
          setUser(null);
          setDisplayName("");
          setSessions([]);
          setActiveSessionId(null);
          activeSessionIdRef.current = null;
          setMessages(initialMessages);
          setAuthLoading(false);
        }, 8000);

        const { data, error } = await supabase.auth.getSession();
        if (!mounted || timedOut) return;

        if (error) throw error;

        const currentUser = data?.session?.user || null;
        setUser(currentUser);

        if (currentUser) {
          await loadProfile(currentUser.id, currentUser.email, currentUser);
          await loadSessions(currentUser.id);
        }
      } catch {
        if (!mounted || timedOut) return;
        clearSupabaseBrowserState();
        setUser(null);
        setDisplayName("");
        setSessions([]);
        setActiveSessionId(null);
        activeSessionIdRef.current = null;
        setMessages(initialMessages);
      } finally {
        if (timeoutId) window.clearTimeout(timeoutId);
        if (mounted && !timedOut) setAuthLoading(false);
      }
    }

    loadAuthSession();

    const { data } = supabase.auth.onAuthStateChange((event, session) => {
      const currentUser = session?.user || null;
      setUser(currentUser);

      if (event === "PASSWORD_RECOVERY") {
        setPasswordRecoveryMode(true);
        setShowSettings(true);
        setUiError("Password recovery mode active. Enter your new password in Settings.");
      }

      if (!currentUser) {
        setDisplayName("");
        setSessions([]);
        setActiveSessionId(null);
        activeSessionIdRef.current = null;
        setMessages(initialMessages);
        setAuthLoading(false);
        return;
      }

      // Important: do not await Supabase queries directly inside onAuthStateChange.
      // In development this can hold the Supabase auth storage lock and cause
      // updateUser/password changes to time out. Defer profile/history loading.
      window.setTimeout(async () => {
        try {
          await loadProfile(currentUser.id, currentUser.email, currentUser);
          await loadSessions(currentUser.id);
        } catch (error) {
          console.warn("Deferred auth-state reload failed:", error?.message || error);
        } finally {
          if (mounted) setAuthLoading(false);
        }
      }, 0);
    });

    return () => {
      mounted = false;
      if (timeoutId) window.clearTimeout(timeoutId);
      activeAbortControllerRef.current?.abort();
      activeAbortControllerRef.current = null;
      data?.subscription?.unsubscribe();
    };
  }, []);

  const activeSession = sessions.find((session) => session.id === activeSessionId);
  const statusClass = statusClassMap[currentStatus] || "status-gray";

  const traceStatus = (() => {
    const route = lastTrace?.route || lastTrace?.adk_trace?.app_name || "";

    if (currentStatus === "Ready") {
      return { backend: "waiting", agentic: "waiting" };
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
      return { backend: "error", agentic: "failed or interrupted" };
    }

    if (currentStatus === "Cancelled") {
      return { backend: "cancelled", agentic: "cancelled by user" };
    }

    return {
      backend: "ok",
      agentic: route ? `completed (${route})` : "completed",
    };
  })();

  async function loadProfile(userId, fallbackEmail, currentUser = null) {
    const { data, error } = await supabase
      .from("profiles")
      .select("display_name")
      .eq("id", userId)
      .maybeSingle();

    if (error) {
      setDisplayName(fallbackEmail || "EDA User");
      return;
    }

    const metadataName = currentUser?.user_metadata?.display_name;
    setDisplayName(data?.display_name || metadataName || fallbackEmail || "EDA User");
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

    setSessions((data || []).map(normalizeLoadedSession));
  }

  function setActiveSession(sessionId) {
    activeSessionIdRef.current = sessionId;
    setActiveSessionId(sessionId);
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
    setActiveSession(tempId);
    return tempId;
  }

  function replaceTemporarySession(tempId, realSession) {
    const normalizedSession = normalizeLoadedSession(realSession);

    setSessions((prev) => {
      const withoutTemp = prev.filter((session) => session.id !== tempId);
      return [normalizedSession, ...withoutTemp];
    });

    migrateCachedMessages(user?.id, tempId, normalizedSession.id);
    setActiveSession(normalizedSession.id);
  }

  function updateLocalSession(sessionId, updates) {
    if (!sessionId) return;

    setSessions((prev) =>
      prev.map((session) =>
        session.id === sessionId
          ? { ...session, ...updates, updated_at: new Date().toISOString() }
          : session
      )
    );
  }

  async function createSession({ title, status = "Running" }) {
    if (!user) throw new Error("User is not logged in.");

    const { data, error } = await supabase
      .from("chat_sessions")
      .insert({
        user_id: user.id,
        title,
        last_fix_status: status,
        detected_codes: [],
      })
      .select()
      .single();

    if (error) throw new Error(`Failed to create session: ${error.message}`);
    return data;
  }

  async function updateSession(sessionId, updates) {
    if (!user || !sessionId) return;

    updateLocalSession(sessionId, updates);

    if (isTemporarySessionId(sessionId)) return;

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
      const filtered = prev.filter((session) => session.id !== sessionId);
      return [normalizeLoadedSession(data), ...filtered];
    });
  }

  async function saveMessage({ sessionId, role, content, fileNames = [], fixStatus = null }) {
    if (!user || !sessionId || isTemporarySessionId(sessionId)) return;

    const { error } = await supabase.from("chat_messages").insert({
      session_id: sessionId,
      user_id: user.id,
      role,
      content,
      file_names: fileNames,
      fix_status: fixStatus,
      detected_codes: [],
    });

    if (error) console.warn("Save message failed:", error.message);
  }

  async function handleAuthReady(currentUser) {
    setUser(currentUser);
    await loadProfile(currentUser.id, currentUser.email, currentUser);
    await loadSessions(currentUser.id);
  }

  async function handleLogout() {
    activeAbortControllerRef.current?.abort();
    activeAbortControllerRef.current = null;

    clearSupabaseBrowserState();

    setUser(null);
    setDisplayName("");
    setSessions([]);
    setActiveSession(null);
    setMessages(initialMessages);
    setInput("");
    setAttachments([]);
    setCurrentStatus("Ready");
    setShowAdvancedTrace(false);
    setLastTrace(null);
    setRunningStage("idle");
    setShowSettings(false);
    setPasswordRecoveryMode(false);
    setUiError("");
    setAuthLoading(false);

    try {
      await withTimeout(supabase.auth.signOut({ scope: "local" }), 1500);
    } catch {
      // Local logout already applied. Do not block UI.
    } finally {
      clearSupabaseBrowserState();
    }
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

        if (!alreadyExists) mergedFiles.push(newFile);
      });

      return mergedFiles;
    });

    event.target.value = "";
  }

  async function loadSessionMessages(sessionId) {
    if (!user || !sessionId) return;

    activeAbortControllerRef.current?.abort();
    activeAbortControllerRef.current = null;

    setUiError("");
    setShowSettings(false);
    setPasswordRecoveryMode(false);
    setInput("");
    setAttachments([]);

    const selectedSession = normalizeLoadedSession(
      sessions.find((session) => session.id === sessionId)
    );

    setActiveSession(sessionId);
    setCurrentStatus(
      selectedSession?.last_fix_status === "Running"
        ? "Ready"
        : selectedSession?.last_fix_status || "Ready"
    );
    setLastTrace(null);
    setRunningStage("idle");

    const cachedMessages = loadCachedMessages(user.id, sessionId);

    // Show cached messages immediately if they exist, but still fetch Supabase below
    // so the clicked history item always restores the saved database version.
    setMessages(cachedMessages.length > 0 ? cachedMessages : initialMessages);

    if (selectedSession?.is_temporary || isTemporarySessionId(sessionId)) {
      return;
    }

    // The session list is already filtered by user_id.
    // Do not also filter chat_messages by user_id here, because older saved rows
    // can fail to load if message.user_id is missing/mismatched even though
    // message.session_id is correct. RLS still protects rows at the database layer.
    const { data, error } = await supabase
      .from("chat_messages")
      .select("role, content, file_names, created_at")
      .eq("session_id", sessionId)
      .order("created_at", { ascending: true });

    if (error) {
      setUiError(`Failed to load messages: ${error.message}`);
      if (cachedMessages.length === 0) setMessages(initialMessages);
      return;
    }

    const loadedMessages = (data || [])
      .filter((item) => item && (item.role === "user" || item.role === "assistant"))
      .map((item) => ({
        role: item.role,
        content: String(item.content || ""),
        files: Array.isArray(item.file_names) ? item.file_names : [],
      }));

    if (loadedMessages.length > 0) {
      setMessages(loadedMessages);
      saveCachedMessages(user.id, sessionId, loadedMessages);
      return;
    }

    if (cachedMessages.length === 0) {
      setMessages(initialMessages);
    }
  }

  async function handleSend() {
    const trimmedMessage = input.trim();
    const filesForRequest = [...attachments];

    if (currentStatus === "Running") return;
    if (!trimmedMessage && filesForRequest.length === 0) return;

    if (!user) {
      setUiError("Please log in first.");
      return;
    }

    const finalUserMessage =
      trimmedMessage ||
      `Please analyze the attached files: ${filesForRequest.map((file) => file.name).join(", ")}.`;

    // Critical demo path: greetings must never enter ADK, Supabase writes, or the backend.
    // This prevents the UI from getting stuck on "Running..." for a simple "hi".
    if (filesForRequest.length === 0 && isSimpleGreeting(finalUserMessage)) {
      const localUserMessage = { role: "user", content: finalUserMessage, files: [] };
      const localAssistantMessage = { role: "assistant", content: LOCAL_GREETING_REPLY };

      setUiError("");
      setShowSettings(false);
      setPasswordRecoveryMode(false);
      setMessages((prev) => [...prev, localUserMessage, localAssistantMessage]);
      setInput("");
      setAttachments([]);
      setCurrentStatus("Completed");
      setLastTrace({
        backend: "not_required",
        route: "frontend_local_greeting",
        adk: "not_required",
        agentic: "not_required",
      });
      setRunningStage("idle");
      return;
    }

    const fileNames = filesForRequest.map((file) => file.name);
    const sessionTitle = makeSessionTitle(filesForRequest, finalUserMessage);

    setUiError("");
    setShowSettings(false);
    setPasswordRecoveryMode(false);

    const userMessage = {
      role: "user",
      content: finalUserMessage,
      files: fileNames,
    };

    let sessionId = activeSessionIdRef.current;

    if (!sessionId) {
      sessionId = createTemporarySession(sessionTitle);
    } else {
      updateLocalSession(sessionId, {
        title: sessionTitle,
        last_fix_status: "Running",
        detected_codes: [],
      });
    }

    const messagesWithUser = [...messages, userMessage];
    setMessages(messagesWithUser);
    saveCachedMessages(user.id, sessionId, messagesWithUser);
    setInput("");
    setAttachments([]);
    setCurrentStatus("Running");
    setLastTrace(null);

    const controller = new AbortController();
    activeAbortControllerRef.current = controller;

    try {
      if (isTemporarySessionId(sessionId)) {
        try {
          const realSession = await withTimeout(
            createSession({ title: sessionTitle, status: "Running" }),
            2500
          );
          const oldSessionId = sessionId;
          replaceTemporarySession(oldSessionId, realSession);
          sessionId = realSession.id;
          saveCachedMessages(user.id, sessionId, messagesWithUser);
        } catch (error) {
          console.warn("Session creation skipped; continuing with temporary session:", error.message);
        }
      }

      fireAndForget(
        saveMessage({
          sessionId,
          role: "user",
          content: finalUserMessage,
          fileNames,
        }),
        "save user message"
      );

      updateLocalSession(sessionId, {
        title: sessionTitle,
        last_fix_status: "Running",
        detected_codes: [],
      });

      fireAndForget(
        updateSession(sessionId, {
          title: sessionTitle,
          last_fix_status: "Running",
          detected_codes: [],
        }),
        "update running session"
      );

      const formData = new FormData();
      formData.append("message", finalUserMessage);

      filesForRequest.forEach((file) => {
        formData.append("files", file);
      });

      const requestTimeoutMs = filesForRequest.length > 0 ? 300000 : 60000;
      const requestTimeoutId = window.setTimeout(() => {
        controller.abort();
      }, requestTimeoutMs);

      let response;

      try {
        response = await fetch(`${API_BASE_URL}/chat`, {
          method: "POST",
          body: formData,
          signal: controller.signal,
        });
      } finally {
        window.clearTimeout(requestTimeoutId);
      }

      if (!response.ok) throw new Error(`Backend returned HTTP ${response.status}`);

      const data = await response.json();
      const answer = data.answer || data.response || data.message || "No response returned from backend.";
      const status = normalizeFixStatus(data.fix_status, answer);

      updateLocalSession(sessionId, {
        title: sessionTitle,
        last_fix_status: status,
        detected_codes: [],
      });

      if (activeSessionIdRef.current === sessionId) {
        setMessages((prev) => {
          const updatedMessages = [...prev, { role: "assistant", content: answer }];
          saveCachedMessages(user.id, sessionId, updatedMessages);
          return updatedMessages;
        });
        setCurrentStatus(status);
        setLastTrace(data.trace || null);
      }

      fireAndForget(
        updateSession(sessionId, {
          title: sessionTitle,
          last_fix_status: status,
          detected_codes: [],
        }),
        "update completed session"
      );

      fireAndForget(
        saveMessage({
          sessionId,
          role: "assistant",
          content: answer,
          fixStatus: status,
        }),
        "save assistant message"
      );
    } catch (error) {
      const wasCancelled = error.name === "AbortError";
      const finalStatus = wasCancelled ? "Cancelled" : "Error";
      const errorMessage = wasCancelled
        ? "Request cancelled by user."
        : `Backend request failed.\n\n${error.message}`;

      updateLocalSession(sessionId, {
        last_fix_status: finalStatus,
        detected_codes: [],
      });

      if (activeSessionIdRef.current === sessionId) {
        setCurrentStatus(finalStatus);
        setLastTrace({
          backend: wasCancelled ? "cancelled" : "failed",
          error: wasCancelled ? "Request cancelled by user." : error.message,
        });
        setMessages((prev) => {
          const updatedMessages = [...prev, { role: "assistant", content: errorMessage }];
          saveCachedMessages(user.id, sessionId, updatedMessages);
          return updatedMessages;
        });
      }

      fireAndForget(
        updateSession(sessionId, {
          last_fix_status: finalStatus,
          detected_codes: [],
        }),
        "update failed session"
      );

      fireAndForget(
        saveMessage({
          sessionId,
          role: "assistant",
          content: errorMessage,
          fixStatus: finalStatus,
        }),
        "save failure message"
      );
    } finally {
      if (activeAbortControllerRef.current === controller) {
        activeAbortControllerRef.current = null;
      }
    }
  }

  function handleCancelButton() {
    const sessionId = activeSessionIdRef.current;

    activeAbortControllerRef.current?.abort();
    activeAbortControllerRef.current = null;

    setCurrentStatus("Cancelled");
    setLastTrace({ backend: "cancelled", message: "Request cancelled by user." });
    setRunningStage("idle");

    if (sessionId) {
      updateLocalSession(sessionId, {
        last_fix_status: "Cancelled",
        detected_codes: [],
      });
      fireAndForget(
        updateSession(sessionId, {
          last_fix_status: "Cancelled",
          detected_codes: [],
        }),
        "update cancelled session"
      );
    }
  }

  function handleNewChat() {
    if (activeAbortControllerRef.current && currentStatus === "Running") {
      handleCancelButton();
    }

    setActiveSession(null);
    setMessages(initialMessages);
    setInput("");
    setAttachments([]);
    setCurrentStatus("Ready");
    setShowAdvancedTrace(false);
    setLastTrace(null);
    setRunningStage("idle");
    setShowSettings(false);
    setPasswordRecoveryMode(false);
    setUiError("");
  }

  function handleOpenSettings() {
    if (activeAbortControllerRef.current && currentStatus === "Running") {
      activeAbortControllerRef.current.abort();
      activeAbortControllerRef.current = null;
      setCurrentStatus("Cancelled");
      setLastTrace({ backend: "cancelled", message: "Request cancelled because Settings was opened." });
      setRunningStage("idle");
    }

    setShowSettings(true);
    setShowAdvancedTrace(false);
    setUiError("");
  }

  async function deleteSession(sessionId) {
    if (!user || !sessionId) return;

    activeAbortControllerRef.current?.abort();
    activeAbortControllerRef.current = null;

    if (isTemporarySessionId(sessionId)) {
      const cacheKey = messageCacheKey(user.id, sessionId);
      if (cacheKey) localStorage.removeItem(cacheKey);
      setSessions((prev) => prev.filter((session) => session.id !== sessionId));
      if (activeSessionIdRef.current === sessionId) handleNewChat();
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

    const cacheKey = messageCacheKey(user.id, sessionId);
    if (cacheKey) localStorage.removeItem(cacheKey);

    setSessions((prev) => prev.filter((session) => session.id !== sessionId));

    if (activeSessionIdRef.current === sessionId) {
      handleNewChat();
    }
  }

  function handleKeyDown(event) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      handleSend();
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
            <button type="button" className="nav-active" onClick={handleNewChat}>
              New Chat
            </button>
            <button type="button" onClick={handleOpenSettings}>
              Settings
            </button>
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
                  </button>
                ))
              )}
            </div>
          </section>
        </div>

        <div className="sidebar-footer">
          <div className="user-card">
            <div className="avatar">
              {(displayName || user.email || "U").slice(0, 1).toUpperCase()}
            </div>
            <div>
              <p className="user-name">{displayName || user.email}</p>
              <p className="user-role">{user.email}</p>
            </div>
          </div>

          <button type="button" className="logout-btn" onClick={handleLogout}>
            Log out
          </button>
        </div>
      </aside>

      <main className="chat-panel">
        <header className="chat-header">
          <div>
            <h2>Agentic EDA Script Debugger</h2>
            <p>Ask questions, attach Tcl/log/docs, and debug Cadence Genus flows.</p>
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
            onPasswordUpdated={() => {
              setPasswordRecoveryMode(false);
              setUiError("Password updated successfully.");
            }}
          />
        ) : (
          <section className="messages">
            {messages.map((message, index) => (
              <div
                key={`${message.role}-${index}`}
                className={`message-row ${message.role === "user" ? "message-user" : "message-assistant"}`}
              >
                <div className="message-avatar">
                  {message.role === "user"
                    ? (displayName || user.email || "U").slice(0, 1).toUpperCase()
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
                      <span key={`${file.name}-${file.size}`} className="file-pill">
                        {file.name}
                      </span>
                    ))
                  )}
                </div>

                {attachments.length > 0 && (
                  <button type="button" className="clear-files-btn" onClick={() => setAttachments([])}>
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
                    type="button"
                    className="send-btn"
                    onClick={handleSend}
                    disabled={currentStatus === "Running"}
                  >
                    {currentStatus === "Running" ? "Running..." : "Send"}
                  </button>

                  {currentStatus === "Running" && (
                    <button type="button" className="cancel-btn" onClick={handleCancelButton}>
                      Cancel
                    </button>
                  )}
                </div>
              </div>
            </>
          )}

          <p className="disclaimer">
            EDADebugger can make mistakes. Check and clarify important informations.
          </p>
        </footer>
      </main>

      <aside className="history-panel">
        <div className="history-header">
          <h3>Current Session</h3>
          <span className={`mini-status ${statusClass}`}>{currentStatus}</span>
        </div>

        <button
          type="button"
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
          </section>
        )}
      </aside>
    </div>
  );
}

export default App;
