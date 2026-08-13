/* ═══════════════════════════════════════════════════════
   面试模拟 Agent — 前端逻辑
   无框架原生 JS: 落地页 ⇄ 聊天页视图切换 + 侧边栏历史管理
   ═══════════════════════════════════════════════════════ */

const $ = (sel) => document.querySelector(sel);

const state = {
  currentSession: null,   // 当前会话 id
  currentMeta: null,      // 当前会话元数据
  canResume: false,
  sending: false,
};

const TYPE_LABELS = {
  technical: "🔧 技术基础",
  scenario: "🏗️ 场景设计",
  project: "📂 项目深挖",
  behavioral: "💬 行为面试",
  coding: "💻 代码实操",
};

/* ═══════════════ 视图切换 ═══════════════ */

function showLanding() {
  $("#landing").hidden = false;
  $("#chat-app").hidden = true;
  state.currentSession = null;
  state.currentMeta = null;
}

function showChat() {
  $("#landing").hidden = true;
  $("#chat-app").hidden = false;
  loadHistory();
}

/* ═══════════════ 落地页逻辑 ═══════════════ */

const dropZone = $("#drop-zone");
const fileInput = $("#file-input");
let selectedFile = null;

dropZone.addEventListener("click", () => fileInput.click());
dropZone.addEventListener("dragover", (e) => {
  e.preventDefault();
  dropZone.classList.add("dragover");
});
dropZone.addEventListener("dragleave", () => dropZone.classList.remove("dragover"));
dropZone.addEventListener("drop", (e) => {
  e.preventDefault();
  dropZone.classList.remove("dragover");
  if (e.dataTransfer.files.length) setFile(e.dataTransfer.files[0]);
});
fileInput.addEventListener("change", () => {
  if (fileInput.files.length) setFile(fileInput.files[0]);
});

function setFile(file) {
  if (!file.name.toLowerCase().endsWith(".pdf")) {
    showLandingError("仅支持 PDF 文件");
    return;
  }
  selectedFile = file;
  $("#file-name").textContent = "📎 " + file.name;
}

function showLandingError(msg) {
  const el = $("#landing-error");
  el.textContent = msg;
  el.hidden = false;
}

$("#start-btn").addEventListener("click", startInterview);

async function startInterview() {
  const text = $("#resume-text").value.trim();
  if (!text && !selectedFile) {
    showLandingError("请粘贴简历文本或上传 PDF 简历");
    return;
  }

  const btn = $("#start-btn");
  btn.disabled = true;
  btn.textContent = "解析简历中…";
  $("#landing-error").hidden = true;

  try {
    const fd = new FormData();
    if (selectedFile) fd.append("file", selectedFile);
    else fd.append("text", text);

    const resp = await fetch("/api/interviews", { method: "POST", body: fd });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.detail || "创建失败");

    openSession(data.session_id);
    showChat();
    renderMessages(data.messages);
    setMockBanner(data.mock);
  } catch (e) {
    showLandingError(e.message);
  } finally {
    btn.disabled = false;
    btn.textContent = "开始对话";
  }
}

/* ═══════════════ 侧边栏历史 ═══════════════ */

async function loadHistory() {
  try {
    const resp = await fetch("/api/interviews");
    const data = await resp.json();
    renderHistory(data.sessions);
  } catch (e) {
    console.error("加载历史失败:", e);
  }
}

function renderHistory(sessions) {
  const list = $("#history-list");
  list.innerHTML = "";

  if (!sessions.length) {
    list.innerHTML = `<div class="empty-history">还没有面试记录<br>点击上方按钮开始第一次模拟面试吧</div>`;
    return;
  }

  for (const s of sessions) {
    list.appendChild(createHistoryItem(s));
  }
}

function createHistoryItem(s) {
  const item = document.createElement("div");
  item.className = "history-item" + (s.session_id === state.currentSession ? " active" : "");
  item.dataset.id = s.session_id;

  // 置顶图标
  if (s.pinned) {
    const pin = document.createElement("span");
    pin.className = "pin-icon";
    pin.textContent = "📌";
    item.appendChild(pin);
  }

  // 标题 + 时间
  const text = document.createElement("div");
  text.className = "item-text";
  text.innerHTML = `
    <div class="item-title"></div>
    <div class="item-date">${formatDate(s.created_at)}${s.status === "in_progress" ? " · 进行中" : ""}</div>`;
  text.querySelector(".item-title").textContent = s.display_name;
  item.appendChild(text);

  // 三点按钮（hover 显示）
  const menuBtn = document.createElement("button");
  menuBtn.className = "item-menu-btn";
  menuBtn.textContent = "⋮";
  menuBtn.title = "更多操作";
  item.appendChild(menuBtn);

  // 弹出菜单
  const menu = document.createElement("div");
  menu.className = "item-menu";
  menu.hidden = true;
  menu.innerHTML = `
    <div data-act="pin">${s.pinned ? "取消置顶" : "置顶"}</div>
    <div data-act="rename">重命名</div>
    <div data-act="delete" class="danger">删除</div>`;
  item.appendChild(menu);

  // 点击条目 → 打开会话
  item.addEventListener("click", (e) => {
    if (e.target.closest(".item-menu") || e.target.closest(".item-menu-btn")) return;
    openSession(s.session_id);
  });

  // 三点按钮 → 切换菜单
  menuBtn.addEventListener("click", (e) => {
    e.stopPropagation();
    closeAllMenus();
    menu.hidden = !menu.hidden;
    menuBtn.classList.toggle("open", !menu.hidden);
  });

  // 菜单项
  menu.querySelectorAll("div").forEach((el) => {
    el.addEventListener("click", (e) => {
      e.stopPropagation();
      menu.hidden = true;
      menuBtn.classList.remove("open");
      const act = el.dataset.act;
      if (act === "pin") togglePin(s.session_id, !s.pinned);
      else if (act === "rename") startRename(item, s);
      else if (act === "delete") askDelete(s);
    });
  });

  return item;
}

function closeAllMenus() {
  document.querySelectorAll(".item-menu").forEach((m) => (m.hidden = true));
  document.querySelectorAll(".item-menu-btn").forEach((b) => b.classList.remove("open"));
}

document.addEventListener("click", closeAllMenus);

function formatDate(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  const now = new Date();
  const pad = (n) => String(n).padStart(2, "0");
  const hm = `${pad(d.getHours())}:${pad(d.getMinutes())}`;
  if (d.toDateString() === now.toDateString()) return `今天 ${hm}`;
  const yesterday = new Date(now);
  yesterday.setDate(now.getDate() - 1);
  if (d.toDateString() === yesterday.toDateString()) return `昨天 ${hm}`;
  return `${d.getMonth() + 1}/${d.getDate()} ${hm}`;
}

/* ── 置顶 / 重命名 / 删除 ── */

async function togglePin(sessionId, pinned) {
  await fetch(`/api/interviews/${sessionId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ pinned }),
  });
  loadHistory();
}

function startRename(item, s) {
  const titleEl = item.querySelector(".item-title");
  const input = document.createElement("input");
  input.value = s.custom_name || s.position;
  titleEl.textContent = "";
  titleEl.appendChild(input);
  input.focus();
  input.select();

  let done = false;
  const finish = async (save) => {
    if (done) return;
    done = true;
    if (save && input.value.trim() && input.value.trim() !== s.display_name) {
      await fetch(`/api/interviews/${s.session_id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ custom_name: input.value.trim() }),
      });
    }
    loadHistory();
  };

  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") finish(true);
    else if (e.key === "Escape") finish(false);
  });
  input.addEventListener("blur", () => finish(true));
}

function askDelete(s) {
  $("#modal-name").textContent = s.display_name;
  const overlay = $("#modal-overlay");
  overlay.hidden = false;
  $("#modal-cancel").onclick = () => (overlay.hidden = true);
  $("#modal-confirm").onclick = async () => {
    overlay.hidden = true;
    const resp = await fetch(`/api/interviews/${s.session_id}`, { method: "DELETE" });
    if (resp.ok && state.currentSession === s.session_id) {
      showLanding();
    }
    loadHistory();
  };
}

/* ═══════════════ 打开会话 ═══════════════ */

async function openSession(sessionId) {
  const resp = await fetch(`/api/interviews/${sessionId}`);
  if (!resp.ok) return;
  const data = await resp.json();

  state.currentSession = sessionId;
  state.currentMeta = data.meta;
  // 注意: 后端 JSON 字段是 snake_case（can_resume），不是 camelCase
  state.canResume = data.can_resume;

  // 更新侧边栏高亮
  document.querySelectorAll(".history-item").forEach((el) => {
    el.classList.toggle("active", el.dataset.id === sessionId);
  });

  $("#chat-title").textContent = data.meta.display_name;
  $("#chat-progress").textContent = "";
  renderMessages(data.messages);
  setMockBanner(data.mock);
  updateComposer();
}

function setMockBanner(mock) {
  $("#mock-banner").hidden = !mock;
}

/* ═══════════════ 消息渲染 ═══════════════ */

function renderMessages(messages) {
  const container = $("#messages");
  container.innerHTML = "";
  for (const m of messages) appendMessage(m);
  scrollToBottom();
}

function appendMessage(m) {
  const container = $("#messages");
  const wrapper = document.createElement("div");
  wrapper.className = "msg " + (m.role === "user" ? "user" : "assistant");

  switch (m.kind) {
    case "warmup":
      wrapper.appendChild(assistantBubble(m.content));
      break;

    case "question":
      wrapper.appendChild(questionCard(m));
      break;

    case "answer":
      wrapper.appendChild(userBubble(m.content));
      break;

    case "evaluation":
      wrapper.appendChild(evalCard(m.evaluation));
      break;

    case "follow_up":
      wrapper.appendChild(assistantBubble("🔍 " + m.content));
      break;

    case "report":
      wrapper.appendChild(reportCard(m.report));
      break;

    default:
      wrapper.appendChild(assistantBubble(m.content || ""));
  }

  container.appendChild(wrapper);
  scrollToBottom();
}

function userBubble(text) {
  const b = document.createElement("div");
  b.className = "bubble";
  b.textContent = text;
  return b;
}

function assistantBubble(text) {
  const b = document.createElement("div");
  b.className = "bubble";
  b.textContent = text;
  return b;
}

function questionCard(m) {
  const q = m.question || {};
  const card = document.createElement("div");
  card.className = "question-card";
  const meta = document.createElement("div");
  meta.className = "q-meta";
  meta.innerHTML = `
    <span class="q-type">${TYPE_LABELS[q.type] || "📝 题目"}</span>
    <span class="q-category">${q.category || ""}</span>
    <span class="q-difficulty">${"★".repeat(q.difficulty || 0)}${"☆".repeat(Math.max(0, 5 - (q.difficulty || 0)))}</span>`;
  const body = document.createElement("div");
  body.textContent = m.content || q.question || "";
  card.appendChild(meta);
  card.appendChild(body);
  return card;
}

function evalCard(ev) {
  const card = document.createElement("div");
  card.className = "eval-card";
  card.innerHTML = `
    <div class="eval-score-row">
      <span class="eval-score">${ev.total_score}</span>
      <span class="eval-score-max">/ 10</span>
      <span class="eval-level">${ev.level}</span>
    </div>
    <div class="eval-dims">
      ${dimBar("正确性", ev.correctness)}
      ${dimBar("深度", ev.depth)}
      ${dimBar("结构", ev.structure)}
      ${dimBar("相关性", ev.relevance)}
    </div>
    ${ev.overall_comment ? `<div class="eval-comment">💬 ${ev.overall_comment}</div>` : ""}
    <div class="eval-tags">
      ${(ev.strengths || []).map((s) => `<span class="eval-tag">👍 ${s}</span>`).join("")}
      ${(ev.weaknesses || []).map((w) => `<span class="eval-tag weak">⚠️ ${w}</span>`).join("")}
    </div>`;
  return card;
}

function dimBar(name, score) {
  return `
    <div class="eval-dim">
      <div class="dim-name">${name} ${score}/10</div>
      <div class="dim-bar"><div class="dim-fill" style="width:${score * 10}%"></div></div>
    </div>`;
}

function reportCard(r) {
  const card = document.createElement("div");
  card.className = "report-card";
  card.innerHTML = `
    <div class="report-header">
      <div class="report-score">${r.overall_score}<span style="font-size:18px;color:#9aa3b2"> / 10</span></div>
      <div class="report-level">${r.overall_level}</div>
      <span class="report-verdict">${r.verdict || ""}</span>
      ${r.verdict_reason ? `<div style="margin-top:8px;font-size:12.5px;color:#6b7280">${r.verdict_reason}</div>` : ""}
    </div>
    <div class="eval-dims">
      ${dimBar("正确性", r.avg_correctness)}
      ${dimBar("深度", r.avg_depth)}
      ${dimBar("结构", r.avg_structure)}
      ${dimBar("相关性", r.avg_relevance)}
    </div>
    ${(r.main_strengths || []).length ? `
      <div class="report-section">
        <h4>👍 主要优势</h4>
        <ul>${r.main_strengths.map((s) => `<li>${s}</li>`).join("")}</ul>
      </div>` : ""}
    ${(r.main_weaknesses || []).length ? `
      <div class="report-section">
        <h4>⚠️ 待提升</h4>
        <ul>${r.main_weaknesses.map((w) => `<li>${w}</li>`).join("")}</ul>
      </div>` : ""}
    ${r.improvement_advice ? `
      <div class="report-section">
        <h4>💡 改进建议</h4>
        <div class="report-advice">${r.improvement_advice}</div>
      </div>` : ""}`;
  return card;
}

function scrollToBottom() {
  const container = $("#messages");
  container.scrollTop = container.scrollHeight;
}

/* ═══════════════ 输入区 ═══════════════ */

const answerInput = $("#answer-input");

answerInput.addEventListener("input", () => {
  answerInput.style.height = "auto";
  answerInput.style.height = Math.min(answerInput.scrollHeight, 140) + "px";
});

answerInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    sendAnswer();
  }
});

$("#send-btn").addEventListener("click", sendAnswer);
$("#skip-btn").addEventListener("click", skipQuestion);

function updateComposer() {
  // 仅进行中的会话可输入；已结束或未选中 → 禁用
  const disabled = !state.currentSession || !state.canResume;
  answerInput.disabled = disabled;
  $("#skip-btn").hidden = disabled;
  $("#send-btn").disabled = disabled;
  $("#composer-hint").textContent = disabled
    ? "本场面试已结束，可点击左侧「开始新面试」再来一场"
    : "";
  if (!disabled) answerInput.focus();
}

async function sendAnswer() {
  const text = answerInput.value.trim();
  if (!text || state.sending || !state.currentSession) return;

  state.sending = true;
  answerInput.value = "";
  answerInput.style.height = "auto";
  appendMessage({ role: "user", kind: "answer", content: text });

  const loading = addLoadingIndicator();
  $("#send-btn").disabled = true;

  try {
    const resp = await fetch(`/api/interviews/${state.currentSession}/answer`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ answer: text }),
    });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.detail || "提交失败");

    removeLoadingIndicator(loading);

    if (data.evaluation) {
      appendMessage({ role: "assistant", kind: "evaluation", evaluation: data.evaluation });
    }
    if (data.report) {
      appendMessage({ role: "assistant", kind: "report", report: data.report });
      state.canResume = false;
      $("#chat-progress").textContent = "面试结束";
      updateComposer();
      loadHistory();
    } else if (data.phase === "follow_up") {
      // 追问: question 字段是同一道题，消息体是追问内容
      // 服务端已把追问记录进 messages，这里从返回的 question 判断
      // 简化: 重新拉取会话消息保证一致
      await refreshSessionMessages();
      $("#chat-progress").textContent = data.progress || "";
    } else if (data.question) {
      await refreshSessionMessages();
      $("#chat-progress").textContent = data.progress || "";
    }
  } catch (e) {
    removeLoadingIndicator(loading);
    appendMessage({ role: "assistant", kind: "follow_up", content: "⚠️ " + e.message });
  } finally {
    state.sending = false;
    $("#send-btn").disabled = !state.canResume;
    if (state.canResume) answerInput.focus();
  }
}

async function skipQuestion() {
  if (state.sending || !state.currentSession) return;
  state.sending = true;
  appendMessage({ role: "user", kind: "answer", content: "（跳过此题）" });
  const loading = addLoadingIndicator();

  try {
    const resp = await fetch(`/api/interviews/${state.currentSession}/skip`, {
      method: "POST",
    });
    const data = await resp.json();
    removeLoadingIndicator(loading);

    if (data.report) {
      appendMessage({ role: "assistant", kind: "report", report: data.report });
      state.canResume = false;
      $("#chat-progress").textContent = "面试结束";
      updateComposer();
      loadHistory();
    } else if (data.question) {
      await refreshSessionMessages();
      $("#chat-progress").textContent = data.progress || "";
    }
  } finally {
    state.sending = false;
    $("#send-btn").disabled = !state.canResume;
  }
}

async function refreshSessionMessages() {
  const resp = await fetch(`/api/interviews/${state.currentSession}`);
  const data = await resp.json();
  renderMessages(data.messages);
}

/* ═══════════════ 加载指示 ═══════════════ */

function addLoadingIndicator() {
  const container = $("#messages");
  const wrapper = document.createElement("div");
  wrapper.className = "msg assistant";
  wrapper.id = "loading-indicator";
  wrapper.innerHTML = `<div class="bubble"><span class="typing-dots"><span></span><span></span><span></span></span></div>`;
  container.appendChild(wrapper);
  scrollToBottom();
  return wrapper;
}

function removeLoadingIndicator(el) {
  if (el) el.remove();
}

/* ═══════════════ 初始化 ═══════════════ */

$("#new-interview-btn").addEventListener("click", () => {
  $("#resume-text").value = "";
  selectedFile = null;
  $("#file-name").textContent = "";
  showLanding();
});

showLanding();
