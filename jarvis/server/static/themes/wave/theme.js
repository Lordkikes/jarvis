/* Tema Onda: conversación tipo mensajería + barras de voz. */
(function () {
  "use strict";

  const $ = (id) => document.getElementById(id);

  // --- Barras ---------------------------------------------------------------
  class Bars {
    constructor(canvas, count = 42) {
      this.canvas = canvas;
      this.ctx = canvas.getContext("2d");
      this.values = new Array(count).fill(0);
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
      this.width = Math.max(200, rect.width);
      this.height = Math.max(30, rect.height);
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
      this.smooth += (this.level - this.smooth) * (this.level > this.smooth ? 0.4 : 0.09);
      this.draw();
      requestAnimationFrame((t) => this.loop(t));
    }

    draw() {
      const { ctx, width: w, height: h } = this;
      const styles = getComputedStyle(document.body);
      const accent = styles.getPropertyValue("--accent").trim();
      const accent2 = styles.getPropertyValue("--accent-2").trim();
      const n = this.values.length;
      const gap = 3;
      const barWidth = Math.max(2, (w - gap * (n - 1)) / n);
      const mid = h / 2;
      // Pensando: onda que recorre las barras. Hablando/escuchando: nivel real.
      const thinking = this.state === "thinking";
      const energy = thinking ? 0.32 : this.smooth;

      ctx.clearRect(0, 0, w, h);
      const gradient = ctx.createLinearGradient(0, 0, w, 0);
      gradient.addColorStop(0, accent);
      gradient.addColorStop(1, accent2);
      ctx.fillStyle = gradient;

      for (let i = 0; i < n; i++) {
        const centred = 1 - Math.abs(i / (n - 1) - 0.5) * 1.5;   // más altas en el centro
        const wobble = thinking
          ? Math.max(0, Math.sin(i * 0.45 - this.time * 5))
          : Math.abs(Math.sin(i * 1.1 + this.time * 7) * Math.cos(i * 0.4 - this.time * 4));
        const target = 0.08 + energy * centred * (0.35 + 0.65 * wobble);
        this.values[i] += (target - this.values[i]) * 0.28;
        const barHeight = Math.max(3, this.values[i] * h);
        const x = i * (barWidth + gap);
        const radius = barWidth / 2;
        ctx.beginPath();
        ctx.roundRect(x, mid - barHeight / 2, barWidth, barHeight, radius);
        ctx.fill();
      }
    }
  }

  // --- Interfaz -------------------------------------------------------------
  const bars = new Bars($("bars"));
  const client = new JarvisClient();
  const chat = $("log");
  let bubble = null;

  const STATES = {
    idle: "En reposo", listening: "Te escucho…", thinking: "Pensando…",
    speaking: "Hablando", error: "Sin conexión",
  };

  function setState(state) {
    document.body.dataset.state = state;
    $("status-text").textContent = STATES[state] || state;
    $("status-hint").textContent = (JarvisStatus[state] || JarvisStatus.idle).hint;
    bars.setState(state);
  }

  function addBubble(role, text) {
    const element = document.createElement("div");
    element.className = "bubble bubble--" + role;
    element.textContent = text;
    chat.appendChild(element);
    chat.scrollTop = chat.scrollHeight;
    return element;
  }

  client
    .on("hello", (e) => {
      $("brand-name").textContent = e.name || "Jarvis";
      document.title = e.name || "Jarvis";
      if (e.greeting) addBubble("system", e.greeting);
      if (!e.voice) addBubble("system", "Sin micrófono: escríbeme por aquí.");
    })
    .on("state", (e) => setState(e.state))
    .on("level", (e) => bars.setLevel(e.value))
    .on("wake", () => { $("status-text").textContent = "¿Sí?"; bars.setLevel(1); })
    .on("user", (e) => addBubble("user", e.text))
    .on("assistant_start", () => { bubble = addBubble("assistant", ""); })
    .on("assistant_delta", (e) => {
      if (!bubble) bubble = addBubble("assistant", "");
      bubble.textContent += e.text;
      chat.scrollTop = chat.scrollHeight;
    })
    .on("assistant_done", (e) => {
      if (bubble && !bubble.textContent.trim()) bubble.textContent = e.text || "";
      else if (!bubble && e.text) addBubble("assistant", e.text);
      bubble = null;
    })
    .on("tool", (e) => addBubble("tool", "⚙ " + e.name))
    .on("barge_in", () => { addBubble("system", "✋ Te he cedido la palabra"); bubble = null; })
    .on("interrupted", () => { bubble = null; })
    .on("timer", (e) => addBubble("system", "⏰ " + e.message))
    .on("note", () => addBubble("system", "📝 Nota guardada"))
    .on("log", (e) => addBubble("system", e.message))
    .on("muted", (e) => $("btn-mute").setAttribute("aria-pressed", String(e.value)))
    .on("cleared", () => { chat.innerHTML = ""; })
    .on("toggle_mute", () => $("btn-mute").click());

  $("form").addEventListener("submit", (event) => {
    event.preventDefault();
    const text = $("input").value.trim();
    if (!text) return;
    client.say(text);
    $("input").value = "";
  });

  // El botón grande hace lo que toca en cada momento: activar o cortar.
  $("btn-mic").addEventListener("click", () => {
    const state = document.body.dataset.state;
    if (state === "speaking" || state === "thinking") client.interrupt();
    else client.activate();
  });
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
