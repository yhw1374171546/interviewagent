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
  codeQuestion: null,     // 当前正在编辑的编程题（含判题元数据）
};

const TYPE_LABELS = {
  technical: "🔧 技术基础",
  scenario: "🏗️ 场景设计",
  project: "📂 项目深挖",
  behavioral: "💬 行为面试",
  coding: "💻 代码实操",
};

/* ═══════════════ 视图切换 ═══════════════ */

function showChat() {
  $("#chat-app").hidden = false;
  loadHistory();
}

function showNewInterview() {
  // 新建面试面板: 主区显示表单，侧边栏历史常驻可点
  state.currentSession = null;
  state.currentMeta = null;
  $("#chat-title").textContent = "新的面试";
  $("#chat-progress").textContent = "";
  $("#new-interview-panel").hidden = false;
  $("#messages").hidden = true;
  updateComposer();
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
      showNewInterview();
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

  // 进入会话视图: 隐藏新建面试面板，显示消息区
  $("#new-interview-panel").hidden = true;
  $("#messages").hidden = false;

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

function appendMessage(m, animate = false) {
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

    case "code":
      wrapper.appendChild(codeBubble(m.content));
      break;

    case "evaluation":
      wrapper.appendChild(evalCard(m.evaluation));
      break;

    case "follow_up":
      wrapper.appendChild(
        animate
          ? typewriterBubble("🔍 " + m.content)
          : assistantBubble("🔍 " + m.content)
      );
      break;

    case "report":
      wrapper.appendChild(reportCard(m.report));
      break;

    case "metrics":
      wrapper.appendChild(metricsCard(m.metrics));
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

function codeBubble(text) {
  const b = document.createElement("div");
  b.className = "bubble code-bubble";
  const label = document.createElement("div");
  label.className = "code-bubble-label";
  label.textContent = "💻 代码";
  const pre = document.createElement("pre");
  pre.textContent = text;
  b.appendChild(label);
  b.appendChild(pre);
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

  // 编程题 → 附「写代码」按钮，点击打开代码编辑器（无论有无判题元数据）
  if (q.type === "coding") {
    const btn = document.createElement("button");
    btn.className = "btn-code";
    btn.textContent = "💻 写代码";
    btn.addEventListener("click", () => openCodeModal(q));
    card.appendChild(btn);
  }
  return card;
}

function evalCard(ev) {
  const card = document.createElement("div");
  card.className = "eval-card";
  // 代码题 → LeetCode 式判题结果（AC/WA + 用例数 + 耗时），不套用四维度评分
  if (ev.code_judge) {
    card.innerHTML = codeJudgeCard(ev.code_judge);
    return card;
  }
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

// LeetCode 式判题结果卡片：AC/WA/TLE/CE/RE/SE + 通过用例数 + 耗时 + 逐用例
const VERDICT = {
  AC: { icon: "✅", label: "Accepted", cls: "ac" },
  WA: { icon: "❌", label: "Wrong Answer", cls: "wa" },
  TLE: { icon: "⏱", label: "Time Limit Exceeded", cls: "tle" },
  CE: { icon: "🔧", label: "Compilation Error", cls: "ce" },
  RE: { icon: "💥", label: "Runtime Error", cls: "re" },
  SE: { icon: "🛡", label: "Security Error", cls: "re" },
  REVIEW: { icon: "🤖", label: "AI Code Review", cls: "review" },
};

function codeJudgeCard(j) {
  const v = VERDICT[j.verdict] || (j.passed ? VERDICT.AC : VERDICT.WA);
  const timeText = j.execution_time_ms ? ` · ${j.execution_time_ms} ms` : "";
  // AI 代码评审（无自动判题用例的题）→ 直接展示评语，不套用例列表
  if (j.verdict === "REVIEW") {
    return `
      <div class="judge-verdict ${v.cls}">${v.icon} ${v.label}</div>
      <div class="judge-meta">本题无自动判题用例，由 AI 评审代码质量</div>
      ${j.comment ? `<div class="eval-comment">💬 ${j.comment}</div>` : ""}`;
  }
  const rows = (j.details || []).map((d) => {
    const icon = d.passed ? "✅" : "❌";
    const extra = d.passed
      ? ""
      : d.error
        ? ` — ${d.error}`
        : ` — expected ${d.expected ?? ""}, actual ${d.got ?? ""}`;
    return `<div class="judge-case ${d.passed ? "pass" : "fail"}">${icon} ${d.name}${extra}</div>`;
  }).join("");
  // CE/TLE/RE/SE 不显示"用例通过"（编译/超时/崩溃时用例数无意义），直接看错误详情
  const meta = ["CE", "TLE", "RE", "SE"].includes(j.verdict)
    ? ""
    : `${j.passed_tests}/${j.total_tests} 用例通过${timeText}`;
  return `
    <div class="judge-verdict ${v.cls}">${v.icon} ${v.label}</div>
    ${meta ? `<div class="judge-meta">${meta}</div>` : ""}
    <div class="judge-box">${rows}</div>`;
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
      </div>` : ""}
    ${(r.reference_answers || []).length ? `
      <div class="report-section">
        <h4>📖 参考答案</h4>
        ${r.reference_answers.map((ra) => `
          <div class="ref-answer">
            <div class="ref-q">${esc(ra.question || "")}</div>
            <div class="ref-a">${esc(ra.answer || "")}</div>
          </div>`).join("")}
      </div>` : ""}`;
  return card;
}

function metricsCard(m) {
  const card = document.createElement("div");
  card.className = "metrics-card";
  const total = (m.prompt_tokens || 0) + (m.completion_tokens || 0);
  const t = m.timings || {};
  const totalSec = Object.values(t).reduce((a, b) => a + b, 0);
  card.innerHTML = `
    <div class="metrics-title">📊 本场统计</div>
    <div class="metrics-row">
      <span>⏱ 总耗时 ${totalSec.toFixed(0)}s</span>
      <span>🔤 Token ${total.toLocaleString()}</span>
      <span>💰 约 ¥${(m.cost_yuan || 0).toFixed(3)}</span>
    </div>
    <div class="metrics-detail">
      解析 ${t.jd_parse || 0}s · 出题+暖场 ${t["question_gen+warmup"] || 0}s ·
      评估 ${t.evaluate || 0}s · 报告 ${t.report || 0}s
    </div>`;
  return card;
}

function scrollToBottom() {
  const container = $("#messages");
  container.scrollTop = container.scrollHeight;
}

/* ═══════════════ 流式报告（SSE） ═══════════════ */

// 追问/报告文字逐字显示: 打字机效果（历史回放不触发动画）
function typewriterBubble(text) {
  const b = document.createElement("div");
  b.className = "bubble";
  b.textContent = "";
  let i = 0;
  const step = () => {
    b.textContent = text.slice(0, i);
    i += 1;
    scrollToBottom();
    if (i <= text.length) setTimeout(step, 18);
  };
  step();
  return b;
}

// 报告生成中的占位气泡（typing-dots + 流式文本）
function addStreamingReport() {
  const container = $("#messages");
  const wrapper = document.createElement("div");
  wrapper.className = "msg assistant";
  wrapper.id = "streaming-report";
  const bubble = document.createElement("div");
  bubble.className = "bubble report-streaming";
  const dots = document.createElement("span");
  dots.className = "typing-dots";
  dots.innerHTML = "<span></span><span></span><span></span>";
  const text = document.createElement("span");
  text.className = "report-stream-text";
  text.textContent = "正在生成报告…";
  bubble.appendChild(dots);
  bubble.appendChild(text);
  wrapper.appendChild(bubble);
  container.appendChild(wrapper);
  scrollToBottom();
  return text;
}

// 解析单条 SSE 帧（event:/data: 行）
function parseSSE(raw) {
  let event = "message";
  const dataLines = [];
  for (const line of raw.split("\n")) {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
  }
  return { event, data: dataLines.join("\n") };
}

// 用 fetch ReadableStream 消费 SSE，报告文字逐字显示
async function streamReport(sessionId) {
  const textEl = addStreamingReport();
  let accumulated = "";

  const handleEvent = (raw) => {
    const { event, data } = parseSSE(raw);
    if (event === "stats") {
      textEl.textContent = "分析完成，正在生成建议…";
    } else if (event === "delta") {
      try {
        accumulated += JSON.parse(data).text;
      } catch (e) {
        accumulated += data;
      }
      textEl.textContent = accumulated;
      scrollToBottom();
    } else if (event === "done") {
      const el = $("#streaming-report");
      if (el) el.remove();
      // 服务器已持久化 report + metrics 消息，重拉一次渲染完整卡片
      refreshSessionMessages().then(scrollToBottom);
      finishInterview();
    }
  };

  try {
    const resp = await fetch(`/api/interviews/${sessionId}/report/stream`);
    if (!resp.ok || !resp.body) throw new Error("报告流加载失败");

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let idx;
      while ((idx = buffer.indexOf("\n\n")) !== -1) {
        const raw = buffer.slice(0, idx);
        buffer = buffer.slice(idx + 2);
        handleEvent(raw);
      }
    }
  } catch (e) {
    const el = $("#streaming-report");
    if (el) el.remove();
    appendMessage({ role: "assistant", kind: "follow_up", content: "⚠️ 报告生成失败：" + e.message });
    finishInterview();
  }
}

// 面试结束的统一收尾（禁用输入、刷新侧边栏）
function finishInterview() {
  state.canResume = false;
  $("#chat-progress").textContent = "面试结束";
  updateComposer();
  loadHistory();
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
  const composer = document.querySelector(".composer");
  // 未选中任何会话 → 整个输入区隐藏（空状态页）
  if (!state.currentSession) {
    composer.hidden = true;
    return;
  }
  composer.hidden = false;

  // 仅进行中的会话可输入；已结束 → 禁用
  const disabled = !state.canResume;
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
  $("#send-btn").disabled = true;

  await postAnswer(text);
}

// 统一提交回答（文字或代码都走 /answer 端点）
async function postAnswer(answer) {
  const loading = addLoadingIndicator();
  try {
    const resp = await fetch(`/api/interviews/${state.currentSession}/answer`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ answer }),
    });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.detail || "提交失败");

    removeLoadingIndicator(loading);
    await handleAnswerResponse(data);
  } catch (e) {
    removeLoadingIndicator(loading);
    appendMessage({ role: "assistant", kind: "follow_up", content: "⚠️ " + e.message });
  } finally {
    state.sending = false;
    $("#send-btn").disabled = !state.canResume;
    if (state.canResume) answerInput.focus();
  }
}

async function handleAnswerResponse(data) {
  if (data.evaluation) {
    appendMessage({ role: "assistant", kind: "evaluation", evaluation: data.evaluation });
  }
  if (data.report) {
    appendMessage({ role: "assistant", kind: "report", report: data.report });
    finishInterview();
  } else if (data.stream_report) {
    // 报告改为 SSE 流式：逐字显示，完成后由 streamReport 收尾
    await streamReport(state.currentSession);
  } else if (data.phase === "follow_up") {
    // 追问文字逐字显示（打字机），历史回放不会重放动画
    appendMessage({ role: "assistant", kind: "follow_up", content: data.message }, true);
    $("#chat-progress").textContent = data.progress || "";
  } else if (data.question) {
    await refreshSessionMessages();
    $("#chat-progress").textContent = data.progress || "";
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
      finishInterview();
    } else if (data.stream_report) {
      await streamReport(state.currentSession);
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

/* ═══════════════ 代码编辑器（编程题专用） ═══════════════ */

function openCodeModal(q) {
  if (!state.currentSession || !state.canResume) {
    appendMessage({ role: "assistant", kind: "follow_up", content: "本场面试已结束，无法提交代码。" });
    return;
  }
  state.codeQuestion = q;
  const code = q.code || {};
  $("#code-lang").textContent = code.language || "python";
  const mode = code.mode || "core";
  const modeEl = $("#code-mode");
  modeEl.textContent = mode === "acm" ? "ACM 完整程序" : "核心代码";
  modeEl.className = "code-mode-badge " + (mode === "acm" ? "acm" : "core");
  // 无判题元数据的题（如线程池）→ 提示提交后由面试官评估
  $("#code-signature").textContent = code.function_signature
    || "（本题暂无自动判题用例，提交后由 AI 代码评审评估质量）";
  // 无自动判题用例 → 禁用「运行」按钮（自测需要用例；代码质量由 AI 评审）
  const hasCases = (code.test_cases || []).length > 0;
  $("#code-run").disabled = !hasCases;
  $("#code-run").title = hasCases ? "" : "本题无自动判题用例，无法自测；提交后由 AI 代码评审";
  renderCodeCases(code.test_cases, mode);
  $("#code-run-result").hidden = true;
  $("#code-run-result").innerHTML = "";
  $("#code-editor").value = "";
  $("#code-editor").placeholder = mode === "acm"
    ? "编写完整程序：自行读取标准输入（stdin），结果输出到标准输出（stdout）…"
    : "在这里编写你的代码（只需写函数/类定义，无需写 main）…";
  $("#code-modal").hidden = false;
  $("#code-editor").focus();
}

// 测试用例展示（LeetCode 式：输入 / 期望输出）
function renderCodeCases(testCases, mode) {
  const container = $("#code-cases");
  if (!testCases || !testCases.length) {
    container.hidden = true;
    return;
  }
  container.hidden = false;
  const inputLabel = mode === "acm" ? "stdin" : "输入";
  container.innerHTML = testCases.map((tc, i) => `
    <div class="code-case">
      <div class="code-case-head">用例 ${i + 1}${tc.name ? "：" + tc.name : ""}</div>
      <div class="code-case-row">
        <span class="code-case-label">${inputLabel}</span>
        <pre>${esc(tc.input_code ?? "")}</pre>
      </div>
      <div class="code-case-row">
        <span class="code-case-label">期望输出</span>
        <pre>${esc(tc.expected ?? "")}</pre>
      </div>
    </div>
  `).join("");
}

// HTML 转义（测试用例里可能含 < > 等）
function esc(s) {
  return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function closeCodeModal() {
  $("#code-modal").hidden = true;
}

// 运行自测：跑测试用例看结果，不进面试、不评分
async function runCode() {
  const code = $("#code-editor").value;
  const q = state.codeQuestion;
  if (!code.trim() || !q) return;
  const meta = q.code || {};
  const box = $("#code-run-result");
  box.hidden = false;
  box.innerHTML = `<div class="judge-summary">⏳ 运行中…</div>`;

  try {
    const resp = await fetch("/api/code/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        code,
        language: meta.language || "python",
        mode: meta.mode || "core",
        test_cases: meta.test_cases || [],
      }),
    });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.detail || "运行失败");
    renderRunResult(data);
  } catch (e) {
    box.innerHTML = `<div class="judge-summary fail">❌ ${e.message}</div>`;
  }
}

function renderRunResult(j) {
  const box = $("#code-run-result");
  const rows = (j.details || []).map((d) => {
    const icon = d.passed ? "✅" : "❌";
    const extra = d.passed
      ? ""
      : d.error
        ? ` — ${d.error}`
        : ` — 期望 ${d.expected ?? ""}，实际 ${d.got ?? ""}`;
    return `<div class="judge-case ${d.passed ? "pass" : "fail"}">${icon} ${d.name}${extra}</div>`;
  }).join("");
  box.innerHTML = `
    <div class="judge-summary ${j.passed ? "pass" : "fail"}">🧪 自测结果：通过 ${j.passed_tests}/${j.total_tests}</div>
    ${rows}`;
}

async function submitCode() {
  const code = $("#code-editor").value;
  if (!code.trim() || state.sending || !state.currentSession) return;

  state.sending = true;
  closeCodeModal();
  appendMessage({ role: "user", kind: "code", content: code });
  $("#send-btn").disabled = true;

  await postAnswer(code);
}

/* ═══════════════ 能力画像 ═══════════════ */

async function openProfile() {
  $("#profile-modal").hidden = false;
  const body = $("#profile-body");
  body.innerHTML = `<div class="profile-loading">加载中…</div>`;
  try {
    const resp = await fetch("/api/profile");
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.detail || "加载失败");
    renderProfile(data);
  } catch (e) {
    body.innerHTML = `<div class="profile-empty">加载失败：${e.message}</div>`;
  }
}

function closeProfile() {
  $("#profile-modal").hidden = true;
}

function renderProfile(p) {
  const body = $("#profile-body");
  if (!p.skills || !p.skills.length) {
    body.innerHTML = `<div class="profile-empty">还没有足够的答题记录<br>完成几次面试后再来看看你的能力画像吧</div>`;
    return;
  }

  const tag = (label, list, cls) => `
    <div class="profile-tags-row">
      <span class="profile-tags-label">${label}</span>
      ${list.map((s) => `<span class="profile-tag ${cls}">${esc(s)}</span>`).join("")}
    </div>`;

  const skillRows = p.skills.map((s) => {
    const trend = s.trend > 0
      ? `<span class="profile-trend up">▲ +${s.trend}</span>`
      : s.trend < 0
        ? `<span class="profile-trend down">▼ ${s.trend}</span>`
        : `<span class="profile-trend flat">— 持平</span>`;
    return `
      <div class="profile-skill">
        <div class="profile-skill-head">
          <span class="profile-skill-name">${esc(s.category)}</span>
          <span class="profile-skill-score">${s.avg_score}</span>
          <span class="profile-skill-meta">答 ${s.attempts} 次</span>
          ${s.attempts >= 2 ? trend : ""}
        </div>
        <div class="profile-dims">
          <span>正确性 ${s.avg_correctness}</span>
          <span>深度 ${s.avg_depth}</span>
          <span>结构 ${s.avg_structure}</span>
          <span>相关性 ${s.avg_relevance}</span>
        </div>
      </div>`;
  }).join("");

  body.innerHTML = `
    <div class="profile-summary">
      已面 <b>${p.total_sessions}</b> 场 · 累计答题 <b>${p.total_attempts}</b> 次
    </div>
    ${p.weakest.length ? tag("🎯 弱项", p.weakest, "weak") : ""}
    ${p.strongest.length ? tag("💪 强项", p.strongest, "strong") : ""}
    <div class="profile-skills">${skillRows}</div>`;
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
  showNewInterview();
});

// 代码编辑器弹窗
$("#code-close").addEventListener("click", closeCodeModal);
$("#code-cancel").addEventListener("click", closeCodeModal);
$("#code-run").addEventListener("click", runCode);
$("#code-submit").addEventListener("click", submitCode);

// 能力画像弹窗
$("#profile-btn").addEventListener("click", openProfile);
$("#profile-close").addEventListener("click", closeProfile);

function init() {
  // DeepSeek 式单视图: 始终进入聊天界面，侧边栏历史常驻，
  // 主区显示新建面试表单（未选中会话时）
  showChat();
  showNewInterview();
}

init();
