/* Tema "Orbe": orbe reactivo sobre fondo con aurora. */
(function () {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const ui = {
    body: document.body,
    stage: document.querySelector(".stage"),
    status: $("status-text"),
    hint: $("status-hint"),
    caption: $("caption"),
    log: $("log"),
    count: $("panel-count"),
    input: $("input"),
  };

  const orb = new Orb($("orb"));
  const client = new JarvisClient();
  let bubble = null;
  let messages = 0;

  function setState(state) {
    const info = JarvisStatus[state] || JarvisStatus.idle;
    ui.body.dataset.state = state;
    ui.status.textContent = info.text;
    ui.hint.textContent = info.hint;
    orb.setState(state === "error" ? "error" : state);
  }

  function addMessage(role, text) {
    const labels = { user: "Tú", assistant: "Jarvis", tool: "herramienta", system: "" };
    const wrapper = document.createElement("div");
    wrapper.className = "msg msg--" + role;
    if (labels[role]) {
      const who = document.createElement("div");
      who.className = "msg__who";
      who.textContent = labels[role];
      wrapper.appendChild(who);
    }
    const body = document.createElement("div");
    body.className = "msg__text";
    body.textContent = text;
    wrapper.appendChild(body);
    ui.log.appendChild(wrapper);
    ui.count.textContent = String(++messages);
    ui.log.scrollTop = ui.log.scrollHeight;
    return body;
  }

  const tail = (text) => {
    const trimmed = (text || "").trim();
    return trimmed.length > 220 ? "…" + trimmed.slice(-220) : trimmed;
  };
  const short = (value) => String(value || "—").toLowerCase();

  client
    .on("hello", (e) => {
      $("brand-name").textContent = e.name || "Jarvis";
      document.title = e.name || "Jarvis";
      $("chip-model").textContent = e.model || "—";
      $("chip-stt").textContent = "oído: " + short(e.stt);
      $("chip-tts").textContent = "voz: " + short(e.tts);
      $("chip-wake").textContent = e.voice ? "wake: " + short(e.wake) : "sin micro";
      $("chip-barge").textContent = "corte: " + short(e.barge_in)
        + (e.aec && e.aec !== "off" ? " + aec" : "");
      if (e.greeting) addMessage("system", e.greeting);
      if (!e.voice) addMessage("system", "No hay micrófono: usa el cuadro de texto.");
    })
    .on("state", (e) => setState(e.state))
    .on("level", (e) => orb.setLevel(e.value))
    .on("wake", () => { ui.status.textContent = "¿Sí?"; orb.setLevel(1); })
    .on("user", (e) => { addMessage("user", e.text); ui.caption.textContent = ""; })
    .on("assistant_start", () => { bubble = addMessage("assistant", ""); })
    .on("assistant_delta", (e) => {
      if (!bubble) bubble = addMessage("assistant", "");
      bubble.textContent += e.text;
      ui.caption.textContent = tail(bubble.textContent);
      ui.log.scrollTop = ui.log.scrollHeight;
    })
    .on("assistant_done", (e) => {
      if (bubble && !bubble.textContent.trim()) bubble.textContent = e.text || "";
      else if (!bubble && e.text) addMessage("assistant", e.text);
      ui.caption.textContent = tail(e.text);
      bubble = null;
    })
    .on("tool", (e) => addMessage("tool", "⚙ " + e.name))
    .on("barge_in", () => { addMessage("system", "✋ Te he cedido la palabra"); bubble = null; })
    .on("interrupted", () => { bubble = null; })
    .on("timer", (e) => addMessage("system", "⏰ " + e.message))
    .on("note", () => addMessage("system", "📝 Nota guardada"))
    .on("log", (e) => addMessage("system", e.message))
    .on("muted", (e) => $("btn-mute").setAttribute("aria-pressed", String(e.value)))
    .on("cleared", () => {
      ui.log.innerHTML = "";
      messages = 0;
      ui.count.textContent = "0";
      ui.caption.textContent = "";
    })
    .on("toggle_mute", () => $("btn-mute").click());

  $("form").addEventListener("submit", (event) => {
    event.preventDefault();
    const text = ui.input.value.trim();
    if (!text) return;
    client.say(text);
    ui.input.value = "";
  });
  $("btn-mic").addEventListener("click", () => client.activate());
  $("btn-stop").addEventListener("click", () => client.interrupt());
  $("btn-clear").addEventListener("click", () => client.clear());
  $("btn-mute").addEventListener("click", (event) => {
    client.mute(event.currentTarget.getAttribute("aria-pressed") !== "true");
  });
  $("btn-panel").addEventListener("click", () => ui.stage.classList.toggle("stage--open"));

  // Expuesto para depurar desde la consola del navegador.
  window.jarvis = client;

  jarvisThemePicker($("theme-slot"));
  ui.stage.classList.add("stage--open");
  client.shortcuts(ui.input).connect();
  setState("idle");
})();
