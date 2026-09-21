/* Tema HUD: reactor de arcos concéntricos + telemetría. */
(function () {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const TAU = Math.PI * 2;

  const MOODS = {
    idle:      { spin: 0.12, bars: 0.10, pulse: 0.03 },
    listening: { spin: 0.30, bars: 1.00, pulse: 0.06 },
    thinking:  { spin: 1.10, bars: 0.35, pulse: 0.05 },
    speaking:  { spin: 0.50, bars: 0.85, pulse: 0.07 },
    error:     { spin: 0.04, bars: 0.05, pulse: 0.14 },
  };

  // --- Reactor --------------------------------------------------------------
  class Reactor {
    constructor(canvas) {
      this.canvas = canvas;
      this.ctx = canvas.getContext("2d");
      this.state = "idle";
      this.level = 0;
      this.smooth = 0;
      this.time = 0;
      this.phase = 0;
      this.bars = new Array(64).fill(0);
      this.resize();
      window.addEventListener("resize", () => this.resize());
      requestAnimationFrame((t) => this.loop(t));
    }

    resize() {
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      const rect = this.canvas.getBoundingClientRect();
      this.size = Math.max(180, Math.min(rect.width, rect.height));
      this.canvas.width = this.size * dpr;
      this.canvas.height = this.size * dpr;
      this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    }

    setState(state) { this.state = MOODS[state] ? state : "idle"; }
    setLevel(value) { this.level = Math.max(0, Math.min(1, value)); }

    accent() {
      return getComputedStyle(document.body).getPropertyValue("--accent").trim() || "#00e5ff";
    }

    loop(timestamp) {
      const dt = Math.min(0.05, (timestamp - (this.last || timestamp)) / 1000);
      this.last = timestamp;
      this.time += dt;
      this.smooth += (this.level - this.smooth) * (this.level > this.smooth ? 0.4 : 0.08);
      const mood = MOODS[this.state];
      this.phase += dt * mood.spin;
      this.draw(mood);
      requestAnimationFrame((t) => this.loop(t));
    }

    draw(mood) {
      const ctx = this.ctx;
      const size = this.size;
      const c = size / 2;
      const r = size * 0.42;
      const color = this.accent();
      const energy = this.smooth * mood.bars;
      const breath = 1 + Math.sin(this.time * 2) * mood.pulse;

      ctx.clearRect(0, 0, size, size);
      ctx.lineCap = "butt";

      // Marcas exteriores
      ctx.save();
      ctx.translate(c, c);
      ctx.rotate(this.phase * 0.3);
      for (let i = 0; i < 72; i++) {
        const major = i % 6 === 0;
        ctx.strokeStyle = this.fade(color, major ? 0.55 : 0.2);
        ctx.lineWidth = major ? 1.6 : 1;
        ctx.beginPath();
        ctx.moveTo(r, 0);
        ctx.lineTo(r - (major ? size * 0.035 : size * 0.016), 0);
        ctx.stroke();
        ctx.rotate(TAU / 72);
      }
      ctx.restore();

      // Arcos concéntricos a distintas velocidades
      const arcs = [
        { radius: 0.86, from: 0.05, to: 0.42, speed: 1.0, width: 2.2 },
        { radius: 0.86, from: 0.55, to: 0.78, speed: 1.0, width: 2.2 },
        { radius: 0.72, from: 0.15, to: 0.85, speed: -0.6, width: 1.2 },
        { radius: 0.60, from: 0.0, to: 0.24, speed: 1.9, width: 3 },
        { radius: 0.60, from: 0.5, to: 0.62, speed: 1.9, width: 3 },
        { radius: 0.48, from: 0.3, to: 0.95, speed: -1.3, width: 1 },
      ];
      for (const arc of arcs) {
        const radius = r * arc.radius * breath;
        ctx.strokeStyle = this.fade(color, 0.28 + energy * 0.5);
        ctx.lineWidth = arc.width;
        ctx.beginPath();
        ctx.arc(c, c, radius, this.phase * arc.speed + arc.from * TAU,
                this.phase * arc.speed + arc.to * TAU);
        ctx.stroke();
      }

      // Barras radiales: el espectro de tu voz alrededor del núcleo
      const inner = r * 0.30;
      for (let i = 0; i < this.bars.length; i++) {
        const target = energy * (0.35 + 0.65 * Math.abs(
          Math.sin(i * 0.7 + this.time * 6) * Math.cos(i * 0.31 - this.time * 3)));
        this.bars[i] += (target - this.bars[i]) * 0.25;
        const angle = (i / this.bars.length) * TAU;
        const length = inner * 0.18 + this.bars[i] * r * 0.30;
        ctx.strokeStyle = this.fade(color, 0.3 + this.bars[i] * 0.7);
        ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.moveTo(c + Math.cos(angle) * inner, c + Math.sin(angle) * inner);
        ctx.lineTo(c + Math.cos(angle) * (inner + length), c + Math.sin(angle) * (inner + length));
        ctx.stroke();
      }

      // Núcleo
      const core = ctx.createRadialGradient(c, c, 0, c, c, inner);
      core.addColorStop(0, "rgba(255,255,255,0.95)");
      core.addColorStop(0.3, this.fade(color, 0.8));
      core.addColorStop(1, this.fade(color, 0.05));
      ctx.fillStyle = core;
      ctx.beginPath();
      ctx.arc(c, c, inner * (0.9 + energy * 0.25), 0, TAU);
      ctx.fill();

      // Hexágono interior
      ctx.strokeStyle = this.fade("#ffffff", 0.5);
      ctx.lineWidth = 1;
      ctx.beginPath();
      for (let i = 0; i <= 6; i++) {
        const angle = (i / 6) * TAU - this.phase * 0.5;
        const x = c + Math.cos(angle) * inner * 0.52;
        const y = c + Math.sin(angle) * inner * 0.52;
        i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
      }
      ctx.stroke();

      // Retícula
      ctx.strokeStyle = this.fade(color, 0.18);
      ctx.lineWidth = 1;
      for (const angle of [0, Math.PI / 2]) {
        ctx.beginPath();
        ctx.moveTo(c + Math.cos(angle) * r * 0.95, c + Math.sin(angle) * r * 0.95);
        ctx.lineTo(c - Math.cos(angle) * r * 0.95, c - Math.sin(angle) * r * 0.95);
        ctx.stroke();
      }
    }

    fade(color, alpha) {
      const hex = color.replace("#", "").trim();
      if (hex.length !== 6) return color;
      const [r, g, b] = [0, 2, 4].map((i) => parseInt(hex.slice(i, i + 2), 16));
      return `rgba(${r},${g},${b},${Math.max(0, Math.min(1, alpha))})`;
    }
  }

  // --- Interfaz -------------------------------------------------------------
  const reactor = new Reactor($("reactor"));
  const client = new JarvisClient();
  const log = $("log");
  let entry = null;
  let count = 0;

  const STATES = {
    idle: "EN REPOSO", listening: "ESCUCHANDO", thinking: "PROCESANDO",
    speaking: "RESPONDIENDO", error: "SIN ENLACE",
  };
  const HINTS = {
    idle: "DI «HEY JARVIS»", listening: "ADELANTE", thinking: "CONSULTANDO",
    speaking: "HABLA PARA CORTARME", error: "REINTENTANDO",
  };

  function setState(state) {
    document.body.dataset.state = state;
    $("status-text").textContent = STATES[state] || state.toUpperCase();
    $("status-hint").textContent = HINTS[state] || "";
    $("head-state").textContent = STATES[state] || state.toUpperCase();
    reactor.setState(state);
  }

  function addEntry(role, text) {
    const tags = { user: "OPERADOR", assistant: "JARVIS", tool: "HERRAMIENTA", system: "SISTEMA" };
    const wrapper = document.createElement("div");
    wrapper.className = "entry entry--" + role;
    const tag = document.createElement("span");
    tag.className = "entry__tag";
    const now = new Date().toTimeString().slice(0, 8);
    tag.textContent = `${now} · ${tags[role] || role}`;
    const body = document.createElement("div");
    body.className = "entry__text";
    body.textContent = text;
    wrapper.append(tag, body);
    log.appendChild(wrapper);
    $("log-count").textContent = String(++count);
    log.scrollTop = log.scrollHeight;
    return body;
  }

  const up = (value) => String(value || "—").toUpperCase();

  client
    .on("hello", (e) => {
      $("brand-name").textContent = (e.name || "Jarvis").toUpperCase().split("").join(".") + ".";
      document.title = e.name || "Jarvis";
      $("t-model").textContent = up(e.model);
      $("t-stt").textContent = up(e.stt);
      $("t-tts").textContent = up(e.tts);
      $("t-wake").textContent = e.voice ? up(e.wake) : "SIN MICRO";
      $("t-barge").textContent = up(e.barge_in);
      $("t-aec").textContent = up(e.aec === "off" ? "inactivo" : e.aec);
      if (e.greeting) addEntry("system", e.greeting);
    })
    .on("state", (e) => setState(e.state))
    .on("level", (e) => {
      reactor.setLevel(e.value);
      $("meter").style.width = Math.round(e.value * 100) + "%";
      $("t-level").textContent = Math.round(e.value * 100) + "%";
    })
    .on("wake", () => { $("status-text").textContent = "A LA ESCUCHA"; reactor.setLevel(1); })
    .on("user", (e) => addEntry("user", e.text))
    .on("assistant_start", () => { entry = addEntry("assistant", ""); })
    .on("assistant_delta", (e) => {
      if (!entry) entry = addEntry("assistant", "");
      entry.textContent += e.text;
      log.scrollTop = log.scrollHeight;
    })
    .on("assistant_done", (e) => {
      if (entry && !entry.textContent.trim()) entry.textContent = e.text || "";
      else if (!entry && e.text) addEntry("assistant", e.text);
      entry = null;
    })
    .on("tool", (e) => addEntry("tool", "▸ " + e.name))
    .on("barge_in", () => { addEntry("system", "PALABRA CEDIDA AL OPERADOR"); entry = null; })
    .on("interrupted", () => { entry = null; })
    .on("timer", (e) => addEntry("system", "⏱ " + e.message))
    .on("log", (e) => addEntry("system", e.message))
    .on("muted", (e) => $("btn-mute").setAttribute("aria-pressed", String(e.value)))
    .on("cleared", () => { log.innerHTML = ""; count = 0; $("log-count").textContent = "0"; })
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

  setInterval(() => {
    $("clock").textContent = new Date().toTimeString().slice(0, 8);
  }, 1000);

  // Expuesto para depurar desde la consola del navegador.
  window.jarvis = client;

  jarvisThemePicker($("theme-slot"));
  client.shortcuts($("input")).connect();
  setState("idle");
})();
