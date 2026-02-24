const devicesEl = document.getElementById("devices");
const commandsEl = document.getElementById("commands");
const subsEl = document.getElementById("subs");
const eventsEl = document.getElementById("events");
const triggerEl = document.getElementById("trigger_type");
const intervalEl = document.getElementById("interval_seconds");
const conditionWrapEl = document.getElementById("condition-fields");
const cmdHelpEl = document.getElementById("cmd-help");
const cmdFeedbackEl = document.getElementById("cmd-feedback");
const conditionEls = ["condition_topic", "condition_operator", "condition_value"].map((id) => document.getElementById(id));

const state = { devices: [], commands: [], subscriptions: [] };

function setFeedback(message, type = "info") {
  cmdFeedbackEl.textContent = message;
  cmdFeedbackEl.dataset.type = type;
}

function toggleTriggerFields() {
  const trigger = triggerEl.value;
  const intervalMode = trigger === "interval";
  const conditionMode = trigger === "condition";

  intervalEl.disabled = !intervalMode;
  if (!intervalMode) intervalEl.value = "";

  conditionWrapEl.classList.toggle("muted", !conditionMode);
  conditionWrapEl.setAttribute("aria-disabled", String(!conditionMode));
  conditionEls.forEach((el) => {
    el.disabled = !conditionMode;
    if (!conditionMode) el.value = "";
  });

  if (intervalMode) cmdHelpEl.textContent = "Dica: informe o intervalo em segundos para execução automática.";
  else if (conditionMode) cmdHelpEl.textContent = "Dica: preencha tópico, operador e valor para disparo por condição.";
  else cmdHelpEl.textContent = "Dica: para comando manual, basta selecionar 'Manual' e salvar.";
}

function logEvent(evt) {
  const line = `[${new Date().toLocaleTimeString()}] ${JSON.stringify(evt)}`;
  eventsEl.textContent = `${line}\n${eventsEl.textContent}`.slice(0, 6000);
}

function render() {
  devicesEl.innerHTML = state.devices.map((d) => `
    <tr>
      <td>${d.device_id}</td><td>${d.connected ? "🟢 online" : "⚪ offline"}</td>
      <td>${d.packets_received}/${d.packets_sent}</td>
      <td>${d.bytes_received}/${d.bytes_sent}</td>
      <td>${d.last_topic || "-"}</td>
      <td>${d.last_payload || "-"}</td>
    </tr>`).join("");

  commandsEl.innerHTML = state.commands.map((c) => `
    <tr>
      <td>${c.id}</td>
      <td>${c.device_id}</td>
      <td>${c.trigger_type}</td>
      <td>${c.topic}</td>
      <td><button onclick="executeCmd('${c.id}')">Executar</button></td>
    </tr>`).join("");

  subsEl.innerHTML = state.subscriptions.map((s) => `<li>${s}</li>`).join("");
}

async function fetchJson(url, options = {}) {
  const response = await fetch(url, options);
  if (!response.ok) {
    let detail = "Erro inesperado";
    try {
      const body = await response.json();
      detail = body.detail || JSON.stringify(body);
    } catch {
      detail = await response.text();
    }
    throw new Error(detail);
  }
  const contentType = response.headers.get("content-type") || "";
  return contentType.includes("application/json") ? response.json() : {};
}

async function refresh() {
  try {
    const payload = await fetchJson("/api/state");
    Object.assign(state, payload);
    render();
  } catch (error) {
    setFeedback(`Falha ao atualizar estado: ${error.message}`, "error");
  }
}

window.executeCmd = async (id) => {
  try {
    await fetchJson(`/api/commands/${id}/execute`, { method: "POST" });
    setFeedback(`Comando ${id} executado com sucesso.`, "success");
  } catch (error) {
    setFeedback(`Erro ao executar comando ${id}: ${error.message}`, "error");
  }
};

document.getElementById("publish-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const data = Object.fromEntries(new FormData(e.target).entries());
  data.qos = Number(data.qos || 0);
  data.retain = data.retain === "on";
  try {
    await fetchJson("/api/publish", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) });
    setFeedback("Publicação enviada com sucesso.", "success");
    e.target.reset();
  } catch (error) {
    setFeedback(`Erro ao publicar: ${error.message}`, "error");
  }
});

document.getElementById("sub-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const topic = new FormData(e.target).get("topic");
  try {
    await fetchJson(`/api/subscriptions/${encodeURIComponent(topic)}`, { method: "POST" });
    setFeedback(`SUB monitorado adicionado: ${topic}`, "success");
    e.target.reset();
    refresh();
  } catch (error) {
    setFeedback(`Erro ao cadastrar SUB: ${error.message}`, "error");
  }
});

document.getElementById("cmd-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const data = Object.fromEntries(new FormData(e.target).entries());

  if (data.trigger_type === "interval" && !data.interval_seconds) {
    setFeedback("Para gatilho por tempo, informe o intervalo em segundos.", "error");
    return;
  }
  if (data.trigger_type === "condition" && (!data.condition_topic || !data.condition_operator || !data.condition_value)) {
    setFeedback("Para gatilho por condição, preencha tópico, operador e valor esperado.", "error");
    return;
  }

  data.interval_seconds = data.interval_seconds ? Number(data.interval_seconds) : null;
  data.qos = 0;
  data.retained = false;
  data.enabled = true;
  for (const k of ["condition_topic", "condition_operator", "condition_value"]) {
    if (!data[k]) data[k] = null;
  }

  try {
    await fetchJson("/api/commands", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) });
    setFeedback(`Comando ${data.id} cadastrado com sucesso.`, "success");
    e.target.reset();
    toggleTriggerFields();
    refresh();
  } catch (error) {
    setFeedback(`Erro ao cadastrar comando: ${error.message}`, "error");
  }
});

triggerEl.addEventListener("change", toggleTriggerFields);
toggleTriggerFields();
setFeedback("Preencha os passos e cadastre um comando para iniciar.", "info");

const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`);
ws.onmessage = (event) => {
  const data = JSON.parse(event.data);
  logEvent(data);
  if (data.type === "state") {
    state.devices = data.devices;
    state.commands = data.commands;
    state.subscriptions = data.subscriptions;
    return render();
  }
  refresh();
};
ws.onerror = () => setFeedback("WebSocket indisponível no momento. A interface continuará via refresh.", "error");

refresh();
