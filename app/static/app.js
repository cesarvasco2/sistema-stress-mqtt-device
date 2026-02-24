const devicesEl = document.getElementById("devices");
const commandsEl = document.getElementById("commands");
const subsEl = document.getElementById("subs");
const eventsEl = document.getElementById("events");

const state = { devices: [], commands: [], subscriptions: [] };

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

async function refresh() {
  const resp = await fetch("/api/state");
  Object.assign(state, await resp.json());
  render();
}

window.executeCmd = async (id) => {
  await fetch(`/api/commands/${id}/execute`, { method: "POST" });
};

document.getElementById("publish-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const data = Object.fromEntries(new FormData(e.target).entries());
  data.qos = Number(data.qos || 0);
  data.retain = data.retain === "on";
  await fetch("/api/publish", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) });
  e.target.reset();
});

document.getElementById("sub-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const topic = new FormData(e.target).get("topic");
  await fetch(`/api/subscriptions/${encodeURIComponent(topic)}`, { method: "POST" });
  e.target.reset();
  refresh();
});

document.getElementById("cmd-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const data = Object.fromEntries(new FormData(e.target).entries());
  data.interval_seconds = data.interval_seconds ? Number(data.interval_seconds) : null;
  data.qos = 0;
  data.retained = false;
  data.enabled = true;
  for (const k of ["condition_topic", "condition_operator", "condition_value"]) {
    if (!data[k]) data[k] = null;
  }
  await fetch("/api/commands", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) });
  e.target.reset();
  refresh();
});

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

refresh();
