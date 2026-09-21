/* Tema Terminal: todo es una línea de texto. */
(function () {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const client = new JarvisClient();
  const log = $("log");
  let line = null;

  const STATES = {
    idle: "EN REPOSO", listening: "ESCUCHANDO", thinking: "PROCESANDO",
    speaking: "HABLANDO", error: "DESCONECTADO",
  };
  const TAGS = {
    user: "tu>", assistant: "jarvis>", tool: "  ·", system: "  #",
  };

  function stamp() {
    return new Date().toTimeString().slice(0, 8);
  }

  function addLine(role, text) {
    const element = document.createElement("p");
    element.className = "line line--" + role;
    const tag = document.createElement("span");
    tag.className = "line__tag";
    tag.textContent = `[${stamp()}] ${TAGS[role] || ">"} `;
    const body = document.createElement("span");
    body.textContent = text;
    element.append(tag, body);
    log.appendChild(element);
    scrollToPrompt();
    return body;
  }

  function scrollToPrompt() {
    const screen = document.querySelector(".screen");
    screen.scrollTop = screen.scrollHeight;
  }

  function meter(level) {
    // Medidor ASCII: 10 casillas que se llenan con la voz.
    const filled = Math.round(Math.max(0, Math.min(1, level)) * 10);
    return "[" + "#".repeat(filled) + ".".repeat(10 - filled) + "]";
  }

  client
    .on("hello", (e) => {
      document.title = (e.name || "jarvis").toLowerCase() + " — tty";
      $("boot").textContent =
        `enlace establecido · modelo ${e.model} · oído ${e.stt} · voz ${e.tts} · `
        + (e.voice ? `despertar ${e.wake}` : "sin micrófono")
        + (e.aec && e.aec !== "off" ? " · eco cancelado" : "");
      $("s-model").textContent = e.model || "—";
      $("s-audio").textContent = e.voice ? `${e.stt}/${e.tts}` : "solo texto";
      if (e.greeting) addLine("system", e.greeting);
      if (!e.voice) addLine("system", "sin micrófono: escribe la orden y pulsa intro");
    })
    .on("state", (e) => {
      document.body.dataset.state = e.state;
      $("status-text").textContent = STATES[e.state] || e.state.toUpperCase();
    })
    .on("level", (e) => { $("meter").textContent = meter(e.value); })
    .on("wake", () => addLine("system", "palabra de activación detectada"))
    .on("user", (e) => addLine("user", e.text))
    .on("assistant_start", () => { line = addLine("assistant", ""); })
    .on("assistant_delta", (e) => {
      if (!line) line = addLine("assistant", "");
      line.textContent += e.text;
      scrollToPrompt();
    })
    .on("assistant_done", (e) => {
      if (line && !line.textContent.trim()) line.textContent = e.text || "";
      else if (!line && e.text) addLine("assistant", e.text);
      line = null;
    })
    .on("tool", (e) => addLine("tool", `exec ${e.name}`))
    .on("barge_in", () => { addLine("system", "^C palabra cedida"); line = null; })
    .on("interrupted", () => { addLine("system", "^C interrumpido"); line = null; })
    .on("timer", (e) => addLine("system", "alarma: " + e.message))
    .on("note", () => addLine("system", "nota guardada"))
    .on("log", (e) => addLine("system", e.message))
    .on("muted", (e) => addLine("system", e.value ? "micrófono apagado" : "micrófono activo"))
    .on("cleared", () => { log.innerHTML = ""; })
    .on("toggle_mute", () => {
      muted = !muted;
      client.mute(muted);
    });

  let muted = false;

  $("form").addEventListener("submit", (event) => {
    event.preventDefault();
    const text = $("input").value.trim();
    if (!text) return;
    client.say(text);
    $("input").value = "";
  });

  // En una terminal, el clic en cualquier parte devuelve el foco al prompt.
  document.addEventListener("click", (event) => {
    if (!event.target.closest("select")) $("input").focus();
  });

  // Expuesto para depurar desde la consola del navegador.
  window.jarvis = client;

  jarvisThemePicker($("theme-slot"));
  client.shortcuts($("input")).connect();
  $("input").focus();
})();
