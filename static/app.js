const inputText = document.querySelector("#inputText");
const analyzeBtn = document.querySelector("#analyzeBtn");
const demoBtn = document.querySelector("#demoBtn");
const candidateList = document.querySelector("#candidateList");
const reminderList = document.querySelector("#reminderList");
const refreshBtn = document.querySelector("#refreshBtn");
const normalized = document.querySelector("#normalized");
const extractorBadge = document.querySelector("#extractorBadge");
const fileInput = document.querySelector("#fileInput");
const ollamaToggle = document.querySelector("#ollamaToggle");
const template = document.querySelector("#candidateTemplate");

const demoMessages = [
  "cll me at 9 pm",
  "Submit NLP project presentation by Friday 5 pm and call mentor tomorrow morning",
  "fees last date is 20 Oct, pay before end of day",
  "kal 8 baje gym chalna and remind me to upload assignment tonight",
  "Meeting with project team tomorrow at 6:30 pm"
];

function formatDate(value) {
  if (!value) return "No deadline detected";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("en-IN", {
    dateStyle: "medium",
    timeStyle: "short"
  }).format(date);
}

function setEmpty(message) {
  candidateList.className = "empty";
  candidateList.textContent = message;
}

async function analyze() {
  const text = inputText.value.trim();
  if (!text) {
    setEmpty("Paste a message first.");
    return;
  }
  analyzeBtn.disabled = true;
  analyzeBtn.textContent = "Analyzing";
  setEmpty("Running NLP extraction...");
  try {
    const response = await fetch("/api/analyze", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        text,
        use_ollama: ollamaToggle.checked,
        now: new Date().toISOString()
      })
    });
    const data = await response.json();
    extractorBadge.textContent = data.extractor || "rules";
    normalized.classList.remove("hidden");
    normalized.textContent = `Normalized text: ${data.normalized_text}`;
    renderCandidates(data.candidates || []);
    if (data.ollama_error) {
      normalized.textContent += " | Ollama fallback used.";
    }
  } catch (error) {
    setEmpty(`Analysis failed: ${error.message}`);
  } finally {
    analyzeBtn.disabled = false;
    analyzeBtn.textContent = "Analyze";
  }
}

function renderCandidates(items) {
  if (!items.length) {
    setEmpty("No actionable reminder was detected. Try a message with an action and time.");
    return;
  }
  candidateList.className = "";
  candidateList.innerHTML = "";
  for (const item of items) {
    const node = template.content.firstElementChild.cloneNode(true);
    node.querySelector(".type").textContent = `${item.reminder_type} • ${Math.round(item.confidence * 100)}% confidence`;
    node.querySelector("h3").textContent = item.title;
    node.querySelector(".meta").textContent = `${formatDate(item.due_at)} • ${item.priority} priority • ${item.extractor}`;
    node.querySelector(".source").textContent = `"${item.source_text}"`;
    node.querySelector(".confirm").addEventListener("click", () => confirmCandidate(item, node));
    node.querySelector(".reject").addEventListener("click", () => node.remove());
    candidateList.appendChild(node);
  }
}

async function confirmCandidate(item, node) {
  const response = await fetch("/api/reminders", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(item)
  });
  if (!response.ok) {
    alert("Could not save reminder.");
    return;
  }
  node.remove();
  await loadReminders();
}

async function loadReminders() {
  const response = await fetch("/api/reminders");
  const reminders = await response.json();
  reminderList.innerHTML = "";
  if (!reminders.length) {
    reminderList.innerHTML = '<div class="empty">No confirmed reminders yet.</div>';
    return;
  }
  for (const reminder of reminders) {
    const card = document.createElement("article");
    card.className = "reminder";
    card.innerHTML = `
      <div>
        <span class="priority ${reminder.priority}">${reminder.priority}</span>
        <h3>${escapeHtml(reminder.title)}</h3>
        <p class="meta">${formatDate(reminder.due_at)} • ${reminder.reminder_type}</p>
        <p class="source">"${escapeHtml(reminder.source_text)}"</p>
      </div>
      <button class="delete" type="button">Delete</button>
    `;
    card.querySelector(".delete").addEventListener("click", async () => {
      await fetch(`/api/reminders/${reminder.id}`, { method: "DELETE" });
      await loadReminders();
    });
    reminderList.appendChild(card);
  }
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

demoBtn.addEventListener("click", () => {
  inputText.value = demoMessages[Math.floor(Math.random() * demoMessages.length)];
  inputText.focus();
});

analyzeBtn.addEventListener("click", analyze);
refreshBtn.addEventListener("click", loadReminders);
fileInput.addEventListener("change", async (event) => {
  const file = event.target.files?.[0];
  if (!file) return;
  inputText.value = await file.text();
});

loadReminders();
