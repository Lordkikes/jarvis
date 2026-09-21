/* ===========================================================================
   Cliente de Jarvis: WebSocket + estado de la interfaz.
   =========================================================================== */
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
    form: $("form"),
    player: $("player"),
    brand: $("brand-name"),
  };

  const STATUS = {
    idle:      { text: "En reposo",   hint: "Di «Hey Jarvis» o pulsa el micrófono" },
    listening: { text: "Te escucho…", hint: "Habla con naturalidad; me callo cuando pares" },
    thinking:  { text: "Pensando…",   hint: "Consultando el modelo y las herramientas" },
    speaking:  { text: "Hablando",    hint: "Háblame encima para cortarme, o pulsa Esc" },
    error:     { text: "Algo falla",  hint: "Revisa la consola del servidor" },
  };

  const orb = new Orb($("orb"));
  let socket = null;
  let retryDelay = 500;
  let currentBubble = null;
  let messageCount = 0;
  let levelTimer = null;

  // --- Conexión -------------------------------------------------------------
  function connect() {
    const protocol = location.protocol === "https:" ? "wss" : "ws";
    socket = new WebSocket(`${protocol}://${location.host}/ws`);

    socket.onopen = () => {
      retryDelay = 500;
      setState("idle");
    };

    socket.onmessage = (event) => handle(JSON.parse(event.data));

    socket.onclose = () => {
      setState("error");
      ui.status.textContent = "Sin conexión";
      ui.hint.textContent = "Reintentando…";
      setTimeout(connect, retryDelay);
      retryDelay = Math.min(retryDelay * 1.7, 8000);
    };
  }

  function send(payload) {
    if (socket && socket.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify(payload));
    }
  }

  // --- Eventos del servidor -------------------------------------------------
  function handle(event) {
    switch (event.type) {
      case "hello":
        ui.brand.textContent = event.name || "Jarvis";
        document.title = event.name || "Jarvis";
        $("chip-model").textContent = event.model || "—";
        $("chip-stt").textContent = "oído: " + short(event.stt);
        $("chip-tts").textContent = "voz: " + short(event.tts);
        $("chip-wake").textContent = event.voice ? "wake: " + short(event.wake) : "sin micro";
        $("chip-barge").textContent = "corte: " + short(event.barge_in)
          + (event.aec && event.aec !== "off" ? " + aec" : "");
        if (event.greeting) addMessage("system", event.greeting);
        if (!event.voice) {
          addMessage("system", "No hay micrófono disponible: usa el cuadro de texto.");
        }
        break;

      case "state":
        setState(event.state);
        break;

      case "level":
        orb.setLevel(event.value);
        clearTimeout(levelTimer);
        levelTimer = setTimeout(() => orb.setLevel(0), 400);
        break;

      case "wake":
        ping();
        break;

      case "user":
        addMessage("user", event.text);
        ui.caption.textContent = "";
        break;

      case "assistant_start":
        currentBubble = addMessage("assistant", "");
        ui.caption.textContent = "";
        break;

      case "assistant_delta":
        if (!currentBubble) currentBubble = addMessage("assistant", "");
        currentBubble.textContent += event.text;
        ui.caption.textContent = tail(currentBubble.textContent);
        scrollLog();
        break;

      case "assistant_done":
        if (currentBubble && !currentBubble.textContent.trim()) {
          currentBubble.textContent = event.text || "";
        }
        if (!currentBubble && event.text) addMessage("assistant", event.text);
        ui.caption.textContent = tail(event.text || "");
        currentBubble = null;
        break;

      case "tool":
        addMessage("tool", "⚙ " + event.name);
        break;

      case "audio":
        playAudio(event.data, event.mime);
        break;

      case "barge_in":
        addMessage("system", "✋ Te he cedido la palabra");
        stopAudio();
        currentBubble = null;
        break;

      case "interrupted":
        stopAudio();
        currentBubble = null;
        break;

      case "timer":
        addMessage("system", "⏰ " + event.message);
        break;

      case "note":
        addMessage("system", "📝 Nota guardada");
        break;

      case "log":
        addMessage("system", event.message);
        break;

      case "muted":
        $("btn-mute").setAttribute("aria-pressed", String(event.value));
        break;

      case "cleared":
        ui.log.innerHTML = "";
        messageCount = 0;
        ui.count.textContent = "0";
        ui.caption.textContent = "";
        break;
    }
  }

  // --- Estado ---------------------------------------------------------------
  function setState(state) {
    const info = STATUS[state] || STATUS.idle;
    ui.body.dataset.state = state;
    ui.status.textContent = info.text;
    ui.hint.textContent = info.hint;
    orb.setState(state);
  }

  function ping() {
    ui.status.textContent = "¡Sí?";
    orb.setLevel(1);
  }

  // --- Conversación ---------------------------------------------------------
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
    ui.count.textContent = String(++messageCount);
    scrollLog();
    return body;
  }

  function scrollLog() {
    ui.log.scrollTop = ui.log.scrollHeight;
  }

  function tail(text) {
    const trimmed = text.trim();
    return trimmed.length > 220 ? "…" + trimmed.slice(-220) : trimmed;
  }

  function short(value) {
    return String(value || "—").replace(/STT|TTS|WakeWord/gi, "").toLowerCase() || "—";
  }

  // --- Audio de respaldo (cuando el servidor no tiene altavoces) -------------
  function stopAudio() {
    // Solo aplica al respaldo del navegador; con altavoces locales corta el servidor.
    ui.player.pause();
    ui.player.removeAttribute("src");
    ui.player.load();
  }

  function playAudio(base64, mime) {
    const bytes = Uint8Array.from(atob(base64), (c) => c.charCodeAt(0));
    const url = URL.createObjectURL(new Blob([bytes], { type: mime || "audio/wav" }));
    ui.player.src = url;
    ui.player.play().catch(() => addMessage("system", "Pulsa en la página para permitir el audio."));
    ui.player.onended = () => URL.revokeObjectURL(url);
  }

  // --- Interacción ----------------------------------------------------------
  ui.form.addEventListener("submit", (event) => {
    event.preventDefault();
    const text = ui.input.value.trim();
    if (!text) return;
    send({ type: "text", text });
    ui.input.value = "";
  });

  $("btn-mic").addEventListener("click", () => send({ type: "activate" }));
  $("btn-stop").addEventListener("click", () => send({ type: "interrupt" }));
  $("btn-clear").addEventListener("click", () => send({ type: "clear" }));

  $("btn-mute").addEventListener("click", (event) => {
    const next = event.currentTarget.getAttribute("aria-pressed") !== "true";
    send({ type: "mute", value: next });
  });

  $("btn-panel").addEventListener("click", () => ui.stage.classList.toggle("stage--open"));

  document.addEventListener("keydown", (event) => {
    const typing = document.activeElement === ui.input;
    if (event.key === "Escape") {
      send({ type: "interrupt" });
    } else if (event.code === "Space" && !typing) {
      event.preventDefault();
      send({ type: "activate" });
    } else if (event.key === "m" && !typing) {
      $("btn-mute").click();
    } else if (event.key === "/" && !typing) {
      event.preventDefault();
      ui.input.focus();
    }
  });

  ui.stage.classList.add("stage--open");
  connect();
})();
