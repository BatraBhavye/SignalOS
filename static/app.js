const inputText = document.querySelector("#inputText");
const analyzeBtn = document.querySelector("#analyzeBtn");
const candidateList = document.querySelector("#candidateList");
const reminderList = document.querySelector("#reminderList");
const normalized = document.querySelector("#normalized");
const fileInput = document.querySelector("#fileInput");
const template = document.querySelector("#candidateTemplate");
const filterButtons = document.querySelectorAll("[data-filter]");
const nextDueTitle = document.querySelector("#nextDueTitle");
const nextDueTime = document.querySelector("#nextDueTime");
const todayCount = document.querySelector("#todayCount");
const importantCount = document.querySelector("#importantCount");
const recurringCount = document.querySelector("#recurringCount");
const pendingCount = document.querySelector("#pendingCount");
const pendingBadge = document.querySelector("#pendingBadge");
const setupStatus = document.querySelector("#setupStatus");
const phoneUrl = document.querySelector("#phoneUrl");
const webhookUrl = document.querySelector("#webhookUrl");
const calendarUrl = document.querySelector("#calendarUrl");
const copyButtons = document.querySelectorAll("[data-copy-target]");

let activeFilter = "scheduled";
let remindersCache = [];
let pendingCache = [];

function formatDate(value) {
  if (!value) return "No deadline detected";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("en-IN", {
    dateStyle: "medium",
    timeStyle: "short"
  }).format(date);
}

function isToday(value) {
  if (!value) return false;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return false;
  const now = new Date();
  return date.getFullYear() === now.getFullYear() &&
    date.getMonth() === now.getMonth() &&
    date.getDate() === now.getDate();
}

function toDateTimeLocal(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  const offset = date.getTimezoneOffset();
  const local = new Date(date.getTime() - offset * 60000);
  return local.toISOString().slice(0, 16);
}

function fromDateTimeLocal(value) {
  if (!value) return null;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return null;
  return date.toISOString();
}

function recurrenceLabel(recurrence = {}) {
  if (!recurrence || recurrence.kind === "none") return "One-time";
  if (recurrence.kind === "monthly" && recurrence.day_of_month) {
    return `Monthly on day ${recurrence.day_of_month}`;
  }
  if (recurrence.kind === "weekly") return "Weekly";
  if (recurrence.kind === "daily") return "Daily";
  if (recurrence.kind === "yearly") return "Yearly";
  return recurrence.kind;
}

function setEmpty(message) {
  candidateList.className = "empty-state";
  candidateList.textContent = message;
}

function updatePendingCount() {
  const count = pendingCache.length;
  pendingCount.textContent = count;
  pendingBadge.textContent = `${count} pending`;
}

function setBusy(isBusy) {
  analyzeBtn.disabled = isBusy;
  analyzeBtn.textContent = isBusy ? "Analyzing" : "Analyze";
}

async function analyze() {
  const text = inputText.value.trim();
  if (!text) {
    setEmpty("Paste a message first, or send one from your phone shortcut.");
    return;
  }
  setBusy(true);
  setEmpty("Running extraction...");
  try {
    const response = await fetch("/api/analyze", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        text,
        now: new Date().toISOString(),
        source_type: "text"
      })
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    if (data.ollama_error) {
      normalized.classList.remove("hidden");
      normalized.textContent = "Local AI was unavailable, so SignalOS used the deterministic parser.";
    } else {
      normalized.classList.add("hidden");
      normalized.textContent = "";
    }
    renderCandidates(data.candidates || []);
  } catch (error) {
    setEmpty(`Analysis failed: ${error.message}`);
  } finally {
    setBusy(false);
  }
}

function renderCandidates(items, emptyMessage = "No actionable reminder was detected. Try a message with an action, date, time, or recurrence.") {
  if (!items.length) {
    setEmpty(emptyMessage);
    return;
  }
  candidateList.className = "candidate-stack";
  candidateList.innerHTML = "";
  for (const item of items) {
    const node = template.content.firstElementChild.cloneNode(true);
    const recurrence = item.recurrence || { kind: "none", interval: 1 };
    const sourceLabel = item.pending_id ? sourceName(item.source_type) : item.reminder_type.replace("_", " ");
    node.querySelector(".type-chip").textContent = sourceLabel;
    node.querySelector(".confidence-chip").textContent = `${Math.round((item.confidence || 0) * 100)}% confidence`;
    node.querySelector(".edit-title").value = item.title || "";
    node.querySelector(".edit-type").value = item.reminder_type || "reminder";
    node.querySelector(".edit-due").value = toDateTimeLocal(item.due_at);
    node.querySelector(".edit-priority").value = item.priority || "medium";
    node.querySelector(".edit-recurrence").value = recurrence.kind || "none";
    node.querySelector(".edit-day").value = recurrence.day_of_month || "";
    node.querySelector(".reason").textContent = item.source_reason || "SignalOS found action signals in the source text.";
    node.querySelector(".source").textContent = item.source_text ? `Source: "${item.source_text}"` : "";
    node.querySelector(".confirm").addEventListener("click", () => confirmCandidate(item, node));
    node.querySelector(".reject").addEventListener("click", async () => {
      if (item.pending_id) {
        await fetch(`/api/pending/${item.pending_id}`, { method: "DELETE" });
        pendingCache = pendingCache.filter((pending) => pending.pending_id !== item.pending_id);
        updatePendingCount();
      }
      node.remove();
      if (!candidateList.children.length) setEmpty("No pending confirmations.");
    });
    candidateList.appendChild(node);
  }
}

function readCandidateFromNode(item, node) {
  const dayValue = node.querySelector(".edit-day").value;
  const kind = node.querySelector(".edit-recurrence").value;
  return {
    title: node.querySelector(".edit-title").value.trim() || "New reminder",
    reminder_type: node.querySelector(".edit-type").value,
    due_at: fromDateTimeLocal(node.querySelector(".edit-due").value),
    priority: node.querySelector(".edit-priority").value,
    confidence: item.confidence || 0.7,
    source_text: item.source_text || inputText.value.trim(),
    source_type: item.source_type || "text",
    extractor: item.extractor || "manual",
    source_reason: item.source_reason || "",
    lead_minutes: item.lead_minutes || 0,
    recurrence: {
      kind,
      interval: 1,
      day_of_month: dayValue ? Number(dayValue) : null,
      day_of_week: item.recurrence?.day_of_week ?? null,
      month: item.recurrence?.month ?? null
    }
  };
}

async function confirmCandidate(item, node) {
  const payload = readCandidateFromNode(item, node);
  const response = await fetch("/api/reminders", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  });
  if (!response.ok) {
    alert("Could not save reminder.");
    return;
  }
  if (item.pending_id) {
    await fetch(`/api/pending/${item.pending_id}`, { method: "DELETE" });
    pendingCache = pendingCache.filter((pending) => pending.pending_id !== item.pending_id);
    updatePendingCount();
  }
  node.remove();
  if (!candidateList.children.length) setEmpty("Saved. Add another message when ready.");
  await loadReminders();
}

function groupForReminder(reminder) {
  if (reminder.status === "completed") return "completed";
  const recurrence = reminder.recurrence || {};
  if (recurrence.kind && recurrence.kind !== "none") return "recurring";
  return "scheduled";
}

function filteredReminders() {
  if (activeFilter === "all") return remindersCache;
  if (activeFilter === "recurring") {
    return remindersCache.filter((item) => item.recurrence?.kind && item.recurrence.kind !== "none");
  }
  if (activeFilter === "important") {
    return remindersCache.filter((item) => item.reminder_type === "important_date");
  }
  return remindersCache.filter((item) => item.status === activeFilter);
}

function renderOverview() {
  const scheduled = remindersCache.filter((item) => item.status === "scheduled");
  const upcoming = scheduled
    .filter((item) => item.due_at && !Number.isNaN(new Date(item.due_at).getTime()))
    .sort((a, b) => new Date(a.due_at) - new Date(b.due_at));
  const next = upcoming[0];
  nextDueTitle.textContent = next ? next.title : "No upcoming item";
  nextDueTime.textContent = next ? formatDate(next.due_at) : "Add a reminder to begin";
  todayCount.textContent = scheduled.filter((item) => isToday(item.due_at)).length;
  importantCount.textContent = remindersCache.filter((item) => item.reminder_type === "important_date").length;
  recurringCount.textContent = remindersCache.filter((item) => item.recurrence?.kind && item.recurrence.kind !== "none").length;
  updatePendingCount();
}

function renderReminders() {
  renderOverview();
  const reminders = filteredReminders();
  reminderList.innerHTML = "";
  if (!reminders.length) {
    reminderList.innerHTML = '<div class="empty-state timeline-empty">No reminders in this view.</div>';
    return;
  }
  for (const reminder of reminders) {
    const card = document.createElement("article");
    card.className = `reminder ${groupForReminder(reminder)} ${reminder.priority}`;
    card.innerHTML = `
      <div class="reminder-main">
        <div class="reminder-row">
          <span class="chip">${escapeHtml(reminder.priority)}</span>
          <span class="chip">${escapeHtml(reminder.reminder_type.replace("_", " "))}</span>
          <span class="chip">${escapeHtml(recurrenceLabel(reminder.recurrence))}</span>
        </div>
        <h3>${escapeHtml(reminder.title)}</h3>
        <p class="meta">${formatDate(reminder.due_at)} | ${escapeHtml(reminder.status)}</p>
        <p class="source">${escapeHtml(reminder.source_text || "")}</p>
      </div>
      <div class="reminder-actions">
        <button class="complete secondary" type="button">${reminder.status === "completed" ? "Reopen" : "Done"}</button>
        <button class="delete danger" type="button">Delete</button>
      </div>
    `;
    card.querySelector(".complete").addEventListener("click", async () => {
      await updateStatus(reminder.id, reminder.status === "completed" ? "scheduled" : "completed");
    });
    card.querySelector(".delete").addEventListener("click", async () => {
      await fetch(`/api/reminders/${reminder.id}`, { method: "DELETE" });
      await loadReminders();
    });
    reminderList.appendChild(card);
  }
}

async function updateStatus(id, status) {
  await fetch(`/api/reminders/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ status })
  });
  await loadReminders();
}

async function loadReminders() {
  const response = await fetch("/api/reminders");
  remindersCache = await response.json();
  renderReminders();
}

async function loadPendingCandidates() {
  const response = await fetch("/api/pending");
  if (!response.ok) return;
  pendingCache = await response.json();
  updatePendingCount();
  if (pendingCache.length) {
    renderCandidates(pendingCache);
  }
}

async function loadSetup() {
  try {
    const response = await fetch("/api/setup");
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const setup = await response.json();
    phoneUrl.textContent = setup.phone_url;
    webhookUrl.textContent = setup.webhook_url;
    calendarUrl.textContent = setup.calendar_url;
    setupStatus.textContent = "ready";
  } catch (error) {
    setupStatus.textContent = "offline";
  }
}

function sourceName(value) {
  if (value === "iphone_shortcut") return "From iPhone";
  if (value === "file") return "From file";
  return "Pending";
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

filterButtons.forEach((button) => {
  button.addEventListener("click", () => {
    activeFilter = button.dataset.filter;
    filterButtons.forEach((item) => item.classList.toggle("active", item === button));
    renderReminders();
  });
});

copyButtons.forEach((button) => {
  button.addEventListener("click", async () => {
    const target = document.querySelector(`#${button.dataset.copyTarget}`);
    const value = target?.textContent || "";
    if (!value || value === "Detecting...") return;
    try {
      await navigator.clipboard.writeText(value);
      button.textContent = "Copied";
      window.setTimeout(() => {
        button.textContent = "Copy";
      }, 1400);
    } catch (error) {
      button.textContent = "Select";
    }
  });
});

analyzeBtn.addEventListener("click", analyze);
fileInput.addEventListener("change", async (event) => {
  const file = event.target.files?.[0];
  if (!file) return;
  inputText.value = await file.text();
});

if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("/static/service-worker.js").catch(() => {});
}

loadReminders();
loadPendingCandidates();
loadSetup();
