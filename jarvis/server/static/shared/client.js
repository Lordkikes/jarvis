/* ===========================================================================
   Cliente compartido por todos los temas: conexión, eventos, audio y atajos.
   Cada tema solo se ocupa de pintar.
   =========================================================================== */
(function (global) {
  "use strict";

  const THEMES = [
    { id: "orb", name: "Orbe" },
    { id: "hud", name: "HUD" },
    { id: "paper", name: "Papel" },
    { id: "terminal", name: "Terminal" },
    { id: "wave", name: "Onda" },
  ];

  const STATUS = {
    idle:      { text: "En reposo",   hint: "Di «Hey Jarvis» o pulsa el micrófono" },
    listening: { text: "Te escucho…", hint: "Habla con naturalidad; me callo cuando pares" },
    thinking:  { text: "Pensando…",   hint: "Consultando el modelo y las herramientas" },
    speaking:  { text: "Hablando",    hint: "Háblame encima para cortarme, o pulsa Esc" },
    error:     { text: "Sin conexión", hint: "Reintentando…" },
  };

  class JarvisClient {
    constructor() {
      this.handlers = new Map();
      this.socket = null;
      this.retryDelay = 500;
      this.audio = new Audio();
      this.levelTimer = null;
    }

    // -- eventos -----------------------------------------------------------
    on(type, handler) {
      if (!this.handlers.has(type)) this.handlers.set(type, []);
      this.handlers.get(type).push(handler);
      return this;
    }

    emit(type, event) {
      for (const handler of this.handlers.get(type) || []) handler(event);
      for (const handler of this.handlers.get("*") || []) handler(event);
    }

    // -- conexión ----------------------------------------------------------
    connect() {
      const protocol = location.protocol === "https:" ? "wss" : "ws";
      this.socket = new WebSocket(`${protocol}://${location.host}/ws`);

      this.socket.onopen = () => { this.retryDelay = 500; };

      this.socket.onmessage = (message) => {
        const event = JSON.parse(message.data);
        if (event.type === "audio") this.play(event.data, event.mime);
        if (event.type === "barge_in" || event.type === "interrupted") this.stopAudio();
        if (event.type === "level") {
          // Si dejan de llegar niveles, el visualizador debe volver a cero.
          clearTimeout(this.levelTimer);
          this.levelTimer = setTimeout(() => this.emit("level", { value: 0 }), 400);
        }
        this.emit(event.type, event);
      };

      this.socket.onclose = () => {
        this.emit("state", { type: "state", state: "error" });
        setTimeout(() => this.connect(), this.retryDelay);
        this.retryDelay = Math.min(this.retryDelay * 1.7, 8000);
      };
      return this;
    }

    send(payload) {
      if (this.socket && this.socket.readyState === WebSocket.OPEN) {
        this.socket.send(JSON.stringify(payload));
      }
    }

    say(text) { this.send({ type: "text", text }); }
    activate() { this.send({ type: "activate" }); }
    interrupt() { this.send({ type: "interrupt" }); }
    clear() { this.send({ type: "clear" }); }
    mute(value) { this.send({ type: "mute", value }); }

    // -- audio de respaldo (cuando el servidor no tiene altavoces) ---------
    play(base64, mime) {
      const bytes = Uint8Array.from(atob(base64), (c) => c.charCodeAt(0));
      const url = URL.createObjectURL(new Blob([bytes], { type: mime || "audio/wav" }));
      this.audio.src = url;
      this.audio.play().catch(() => this.emit("log", {
        type: "log", message: "Pulsa en la página para permitir el audio.",
      }));
      this.audio.onended = () => URL.revokeObjectURL(url);
    }

    stopAudio() {
      this.audio.pause();
      this.audio.removeAttribute("src");
      this.audio.load();
    }

    // -- atajos de teclado -------------------------------------------------
    shortcuts(input) {
      document.addEventListener("keydown", (event) => {
        const typing = document.activeElement === input;
        if (event.key === "Escape") this.interrupt();
        else if (event.code === "Space" && !typing) { event.preventDefault(); this.activate(); }
        else if (event.key === "m" && !typing) this.emit("toggle_mute", {});
        else if (event.key === "/" && !typing && input) { event.preventDefault(); input.focus(); }
      });
      return this;
    }
  }

  // -- selector de temas ---------------------------------------------------
  function currentTheme() {
    return new URLSearchParams(location.search).get("theme") || document.body.dataset.theme;
  }

  function themePicker(container, label = "Tema") {
    if (!container) return;
    const select = document.createElement("select");
    select.className = "theme-picker";
    select.setAttribute("aria-label", label);
    for (const theme of THEMES) {
      const option = document.createElement("option");
      option.value = theme.id;
      option.textContent = theme.name;
      option.selected = theme.id === currentTheme();
      select.appendChild(option);
    }
    select.addEventListener("change", () => {
      location.search = "?theme=" + select.value;
    });
    container.appendChild(select);
  }

  global.JarvisClient = JarvisClient;
  global.JarvisStatus = STATUS;
  global.JarvisThemes = THEMES;
  global.jarvisThemePicker = themePicker;
})(window);
