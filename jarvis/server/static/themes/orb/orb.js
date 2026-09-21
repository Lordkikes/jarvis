/* ===========================================================================
   Orbe reactivo. Dibuja tres anillos deformados por ruido sinusoidal cuya
   amplitud depende del nivel de micrófono y del estado del asistente.
   =========================================================================== */
(function (global) {
  "use strict";

  const TAU = Math.PI * 2;

  // Cada estado tiene su propio "carácter": velocidad, ondulación y pulso.
  const MOODS = {
    idle:      { spin: 0.10, wobble: 0.030, pulse: 0.035, rings: 3, reactive: 0.15 },
    listening: { spin: 0.28, wobble: 0.075, pulse: 0.060, rings: 4, reactive: 1.00 },
    thinking:  { spin: 0.95, wobble: 0.110, pulse: 0.050, rings: 5, reactive: 0.20 },
    speaking:  { spin: 0.45, wobble: 0.130, pulse: 0.080, rings: 4, reactive: 0.85 },
    error:     { spin: 0.05, wobble: 0.020, pulse: 0.120, rings: 2, reactive: 0.10 },
  };

  function readAccent() {
    const styles = getComputedStyle(document.body);
    return {
      a: styles.getPropertyValue("--accent").trim() || "#7c9cff",
      b: styles.getPropertyValue("--accent-2").trim() || "#9d7cff",
    };
  }

  class Orb {
    constructor(canvas) {
      this.canvas = canvas;
      this.ctx = canvas.getContext("2d");
      this.state = "idle";
      this.level = 0;        // objetivo (0..1)
      this.smooth = 0;       // nivel suavizado, evita saltos bruscos
      this.time = 0;
      this.phase = 0;
      this.colors = readAccent();
      this.particles = Array.from({ length: 34 }, () => ({
        angle: Math.random() * TAU,
        radius: 0.72 + Math.random() * 0.42,
        speed: (0.1 + Math.random() * 0.5) * (Math.random() < 0.5 ? -1 : 1),
        size: 0.6 + Math.random() * 1.9,
      }));
      this._resize();
      window.addEventListener("resize", () => this._resize());
      requestAnimationFrame((t) => this._loop(t));
    }

    setState(state) {
      this.state = MOODS[state] ? state : "idle";
      // El color lo define el CSS; lo releemos tras la transición de la hoja.
      setTimeout(() => { this.colors = readAccent(); }, 60);
    }

    setLevel(value) {
      this.level = Math.max(0, Math.min(1, value));
    }

    _resize() {
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      const rect = this.canvas.getBoundingClientRect();
      const size = Math.max(160, Math.min(rect.width, rect.height));
      this.canvas.width = size * dpr;
      this.canvas.height = size * dpr;
      this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      this.size = size;
    }

    _loop(timestamp) {
      const dt = Math.min(0.05, (timestamp - (this._last || timestamp)) / 1000);
      this._last = timestamp;
      this.time += dt;

      // Subida rápida y bajada lenta: así el orbe "respira" con la voz.
      const rise = this.level > this.smooth ? 0.35 : 0.08;
      this.smooth += (this.level - this.smooth) * rise;

      const mood = MOODS[this.state];
      this.phase += dt * mood.spin;
      this._draw(mood);
      requestAnimationFrame((t) => this._loop(t));
    }

    _draw(mood) {
      const ctx = this.ctx;
      const size = this.size;
      const cx = size / 2;
      const cy = size / 2;
      const base = size * 0.30;
      const energy = this.smooth * mood.reactive;
      const breath = 1 + Math.sin(this.time * 1.6) * mood.pulse + energy * 0.22;

      ctx.clearRect(0, 0, size, size);
      ctx.globalCompositeOperation = "lighter";

      // Halo
      const halo = ctx.createRadialGradient(cx, cy, base * 0.2, cx, cy, base * 2.3);
      halo.addColorStop(0, this._alpha(this.colors.a, 0.30 + energy * 0.25));
      halo.addColorStop(0.5, this._alpha(this.colors.b, 0.10));
      halo.addColorStop(1, "rgba(0,0,0,0)");
      ctx.fillStyle = halo;
      ctx.fillRect(0, 0, size, size);

      // Anillos deformados
      for (let i = 0; i < mood.rings; i++) {
        const t = i / Math.max(1, mood.rings - 1);
        const radius = base * breath * (0.72 + t * 0.55);
        const amplitude = radius * (mood.wobble + energy * 0.16) * (1 - t * 0.35);
        const lobes = 3 + i;
        const drift = this.phase * (i % 2 === 0 ? 1 : -1.35) + i * 0.8;

        ctx.beginPath();
        for (let a = 0; a <= TAU + 0.05; a += 0.045) {
          const wave =
            Math.sin(a * lobes + drift) * amplitude +
            Math.sin(a * (lobes + 3) - drift * 1.7) * amplitude * 0.45;
          const r = radius + wave;
          const x = cx + Math.cos(a) * r;
          const y = cy + Math.sin(a) * r;
          a === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
        }
        ctx.closePath();

        const stroke = ctx.createLinearGradient(cx - radius, cy - radius, cx + radius, cy + radius);
        stroke.addColorStop(0, this._alpha(this.colors.a, 0.85 - t * 0.5));
        stroke.addColorStop(1, this._alpha(this.colors.b, 0.75 - t * 0.5));
        ctx.strokeStyle = stroke;
        ctx.lineWidth = Math.max(0.8, (size * 0.005) * (1 - t * 0.5));
        ctx.stroke();

        if (i === 0) {
          ctx.fillStyle = this._alpha(this.colors.a, 0.10 + energy * 0.16);
          ctx.fill();
        }
      }

      // Núcleo
      const coreR = base * 0.30 * (1 + energy * 0.5);
      const core = ctx.createRadialGradient(cx, cy, 0, cx, cy, coreR);
      core.addColorStop(0, this._alpha("#ffffff", 0.95));
      core.addColorStop(0.35, this._alpha(this.colors.a, 0.75));
      core.addColorStop(1, "rgba(0,0,0,0)");
      ctx.fillStyle = core;
      ctx.beginPath();
      ctx.arc(cx, cy, coreR, 0, TAU);
      ctx.fill();

      // Partículas en órbita
      for (const p of this.particles) {
        p.angle += p.speed * 0.006 * (1 + energy * 2);
        const r = base * breath * p.radius * (1 + energy * 0.12);
        const x = cx + Math.cos(p.angle) * r;
        const y = cy + Math.sin(p.angle) * r * 0.96;
        ctx.fillStyle = this._alpha(this.colors.b, 0.35 + energy * 0.4);
        ctx.beginPath();
        ctx.arc(x, y, p.size * (0.7 + energy), 0, TAU);
        ctx.fill();
      }

      ctx.globalCompositeOperation = "source-over";
    }

    _alpha(color, alpha) {
      const hex = color.replace("#", "").trim();
      if (hex.length !== 6) return color;
      const r = parseInt(hex.slice(0, 2), 16);
      const g = parseInt(hex.slice(2, 4), 16);
      const b = parseInt(hex.slice(4, 6), 16);
      return `rgba(${r},${g},${b},${Math.max(0, Math.min(1, alpha))})`;
    }
  }

  global.Orb = Orb;
})(window);
