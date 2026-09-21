/* Tema Papel: una sola línea de onda, tipografía grande y transcripción limpia. */
(function () {
  "use strict";

  const $ = (id) => document.getElementById(id);

  // --- Línea de onda --------------------------------------------------------
  class WaveLine {
    constructor(canvas) {
      this.canvas = canvas;
      this.ctx = canvas.getContext("2d");
      this.level = 0;
      this.smooth = 0;
      this.time = 0;
      this.state = "idle";
      this.resize();
      window.addEventListener("resize", () => this.resize());
      requestAnimationFrame((t) => this.loop(t));
    }

    resize() {
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      const rect = this.canvas.getBoundingClientRect();
      this.width = Math.max(240, rect.width);
      this.height = Math.max(60, rect.height);
      this.canvas.width = this.width * dpr;
      this.canvas.height = this.height * dpr;
      this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    }

    setLevel(value) { this.level = Math.max(0, Math.min(1, value)); }
    setState(state) { this.state = state; }

    loop(timestamp) {
      const dt = Math.min(0.05, (timestamp - (this.last || timestamp)) / 1000);
      this.last = timestamp;
      this.time += dt;
      this.smooth += (this.level - this.smooth) * (this.level > this.smooth ? 0.35 : 0.06);
      this.draw();
      requestAnimationFrame((t) => this.loop(t));
    }

    draw() {
      const { ctx, width: w, height: h } = this;
      const mid = h / 2;
      const color = getComputedStyle(document.body).getPropertyValue("--accent").trim();
      // En reposo casi no se mueve; al hablar, la línea respira.
      const base = this.state === "thinking" ? 0.16 : 0.05;
      const amplitude = (base + this.smooth * 0.75) * (h * 0.42);
      const speed = this.state === "thinking" ? 3.2 : 1.6;

      ctx.clearRect(0, 0, w, h);
      ctx.strokeStyle = color;
      ctx.lineWidth = 1.6;
      ctx.lineCap = "round";
      ctx.beginPath();
      for (let x = 0; x <= w; x += 2) {
        const p = x / w;
        // Ventana que apaga la onda en los extremos: parece dibujada a mano.
        const window_ = Math.sin(Math.PI * p) ** 1.6;
        const y = mid
          + Math.sin(p * 13 + this.time * speed) * amplitude * window_
          + Math.sin(p * 27 - this.time * speed * 1.7) * amplitude * 0.35 * window_;
        x === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
      }
      ctx.stroke();

      // Punto que marca el centro cuando está en silencio
      if (this.smooth < 0.04) {
        ctx.fillStyle = color;
        ctx.globalAlpha = 0.5;
        ctx.beginPath();
        ctx.arc(w / 2, mid, 2.4, 0, Math.PI * 2);
        ctx.fill();
        ctx.globalAlpha = 1;
      }
    }
  }

  // --- Interfaz -------------------------------------------------------------
  const wave = new WaveLine($("wave"));
  const client = new JarvisClient();
  const log = $("log");
  let turn = null;

  const STATES = {
    idle: "En reposo", listening: "Te escucho", thinking: "Pensando",
    speaking: "Hablando", error: "Sin conexión",
  };

  function setState(state) {
    document.body.dataset.state = state;
    $("status-text").textContent = STATES[state] || state;
    $("status-hint").textContent = (JarvisStatus[state] || JarvisStatus.idle).hint;
    wave.setState(state);
  }

  function addTurn(role, text) {
    const labels = { user: "Tú", assistant: "Jarvis", tool: "Herramienta", system: "" };
    const wrapper = document.createElement("article");
    wrapper.className = "turn turn--" + role;
    if (labels[role]) {
      const who = document.createElement("div");
      who.className = "turn__who";
      who.textContent = labels[role];
      wrapper.appendChild(who);
    }
    const body = document.createElement("p");
    body.className = "turn__text";
    body.textContent = text;
    wrapper.appendChild(body);
    log.appendChild(wrapper);
    log.scrollTop = log.scrollHeight;
    return body;
  }

  client
    .on("hello", (e) => {
      $("brand-name").textContent = e.name || "Jarvis";
      document.title = e.name || "Jarvis";
      $("m-model").textContent = e.model || "—";
      $("m-voice").textContent = e.voice ? `voz · ${e.tts}` : "solo texto";
      if (e.greeting) addTurn("system", e.greeting);
    })
    .on("state", (e) => setState(e.state))
    .on("level", (e) => wave.setLevel(e.value))
    .on("wave", () => {})
    .on("user", (e) => addTurn("user", e.text))
    .on("assistant_start", () => { turn = addTurn("assistant", ""); })
    .on("assistant_delta", (e) => {
      if (!turn) turn = addTurn("assistant", "");
      turn.textContent += e.text;
      log.scrollTop = log.scrollHeight;
    })
    .on("assistant_done", (e) => {
      if (turn && !turn.textContent.trim()) turn.textContent = e.text || "";
      else if (!turn && e.text) addTurn("assistant", e.text);
      turn = null;
    })
    .on("tool", (e) => addTurn("tool", "consultando " + e.name))
    .on("barge_in", () => { addTurn("system", "Te he cedido la palabra"); turn = null; })
    .on("interrupted", () => { turn = null; })
    .on("timer", (e) => addTurn("system", e.message))
    .on("log", (e) => addTurn("system", e.message))
    .on("muted", (e) => {
      const button = $("btn-mute");
      button.setAttribute("aria-pressed", String(e.value));
      button.textContent = e.value ? "Micro apagado" : "Silenciar";
    })
    .on("cleared", () => { log.innerHTML = ""; })
    .on("toggle_mute", () => $("btn-mute").click());

  $("form").addEventListener("submit", (event) => {
    event.preventDefault();
    const text = $("input").value.trim();
    if (!text) return;
    client.say(text);
    $("input").value = "";
  });
  $("btn-mic").addEventListener("click", () => client.activate());
  $("btn-stop").addEventListener("click", () => client.interrupt());
  $("btn-clear").addEventListener("click", () => client.clear());
  $("btn-mute").addEventListener("click", (event) => {
    client.mute(event.currentTarget.getAttribute("aria-pressed") !== "true");
  });

  // Expuesto para depurar desde la consola del navegador.
  window.jarvis = client;

  jarvisThemePicker($("theme-slot"));
  client.shortcuts($("input")).connect();
  setState("idle");
})();
