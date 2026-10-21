// Shared motion helpers for HTML Motion Scenes. Local file, zero CDN, zero GSAP.
// Every value is a closed-form function of time sampled into WAAPI keyframes.
// The HyperFrames runtime seeks each Animation to the exact frame time.
(function () {
  const RATIOS = {
    "9:16": [1080, 1920],
    "16:9": [1920, 1080],
    "1:1": [1080, 1080],
    "4:5": [1080, 1350]
  };

  let _cachedBounce = 0.15;

  // Near-critically damped spring (zeta 0.9, omega 8) matching screen_ui physics,
  // settling by t = 1, overshoot under 0.2% of travel.
  // When zeta >= 1.0 (critically or overdamped), uses closed-form decay without division by zero.
  function spring(t, zeta = null, omega = 8) {
    if (t <= 0) return 0;
    if (t >= 1) return 1;
    if (zeta === null || zeta === undefined) {
      zeta = _cachedBounce <= 0 ? 1.0 : (1.0 - _cachedBounce);
    }
    if (zeta >= 1.0) {
      const decay = Math.exp(-omega * Math.max(0, t));
      return 1 - decay * (1 + omega * Math.max(0, t));
    }
    const wd = omega * Math.sqrt(1 - zeta * zeta);
    const decay = Math.exp(-zeta * omega * Math.max(0, t));
    return 1 - decay * (Math.cos(wd * t) + ((zeta * omega) / wd) * Math.sin(wd * t));
  }

  const clamp = (t) => Math.max(0, Math.min(1, t));
  const easeOut = (t) => 1 - Math.pow(1 - clamp(t), 3);
  const easeInOut = (t) => {
    t = clamp(t);
    return t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;
  };
  const win = (t, a, b) => clamp((t - a) / (b - a));
  const lerp = (a, b, t) => a + (b - a) * t;

  // Perceptual exponential scale interpolation s0 * (s1 / s0)^p for constant zoom velocity.
  function perceptualScale(fromScale, toScale, p) {
    if (typeof fromScale !== "number" || typeof toScale !== "number" || fromScale <= 0 || toScale <= 0) {
      throw new RangeError("perceptualScale requires positive scale factors");
    }
    return fromScale * Math.pow(toScale / fromScale, clamp(p));
  }

  // Sample fn(p) -> keyframe object over [startS, endS] into one WAAPI animation.
  // fill: both holds the first value before start and last value after end.
  function track(el, startS, endS, fn, n = 48) {
    if (!el || typeof el.animate !== "function") return null;
    const frames = [];
    for (let i = 0; i <= n; i++) {
      frames.push({ ...fn(i / n), offset: i / n });
    }
    return el.animate(frames, {
      duration: Math.max(0, (endS - startS) * 1000),
      delay: startS * 1000,
      fill: "both",
      easing: "linear",
    });
  }

  // Multi-phase sequence compiled into a SINGLE WAAPI animation to prevent backwards-fill clobbering.
  // Automatically inserts intermediate hold keyframes across idle gaps so values hold strictly without drift.
  function sequence(el, phases, totalDurationS) {
    if (!el || typeof el.animate !== "function" || !Array.isArray(phases) || phases.length === 0 || !totalDurationS || totalDurationS <= 0) {
      return null;
    }
    const sorted = [...phases].sort((a, b) => a.startS - b.startS);
    const frames = [];

    // If initial phase starts after 0s, insert hold frame from t=0s to startS
    if (sorted[0].startS > 0) {
      const initialProps = sorted[0].fn(0);
      frames.push({ ...initialProps, offset: 0 });
      frames.push({ ...initialProps, offset: sorted[0].startS / totalDurationS });
    }

    for (let k = 0; k < sorted.length; k++) {
      const ph = sorted[k];
      const n = ph.n || 32;
      const durationS = ph.endS - ph.startS;

      for (let i = 0; i <= n; i++) {
        const p = i / n;
        const t = ph.startS + p * durationS;
        const offset = Math.max(0, Math.min(1, t / totalDurationS));
        if (frames.length > 0 && Math.abs(frames[frames.length - 1].offset - offset) < 1e-5) {
          Object.assign(frames[frames.length - 1], ph.fn(p));
        } else {
          frames.push({ ...ph.fn(p), offset });
        }
      }

      // If gap exists before the next phase, insert hold keyframes
      if (k < sorted.length - 1) {
        const nextPh = sorted[k + 1];
        if (nextPh.startS > ph.endS) {
          const lastProps = ph.fn(1);
          const gapMidS = (ph.endS + nextPh.startS) / 2;
          const midOffset = Math.max(0, Math.min(1, gapMidS / totalDurationS));
          const holdOffset = Math.max(0, Math.min(1, nextPh.startS / totalDurationS));
          frames.push({ ...lastProps, offset: midOffset });
          frames.push({ ...lastProps, offset: holdOffset });
        }
      }
    }

    // If final phase ends before totalDurationS, hold terminal values to t=1.0
    const lastPh = sorted[sorted.length - 1];
    if (lastPh.endS < totalDurationS) {
      const finalProps = lastPh.fn(1);
      frames.push({ ...finalProps, offset: 1.0 });
    }

    frames.sort((a, b) => a.offset - b.offset);

    return el.animate(frames, {
      duration: totalDurationS * 1000,
      fill: "both",
      easing: "linear",
    });
  }

  // Safe kinetic word splitting using DOM elements and text nodes (XSS safe, zero innerHTML).
  function splitText(el, opts = { by: "words" }) {
    if (!el) return [];
    const originalText = el.textContent || "";
    el.setAttribute("aria-label", originalText);

    const words = originalText.trim().split(/\s+/).filter(Boolean);
    el.textContent = "";

    const units = [];
    for (let i = 0; i < words.length; i++) {
      if (i > 0) {
        el.appendChild(document.createTextNode(" "));
      }
      const span = document.createElement("span");
      span.className = "gtm-split-unit";
      span.textContent = words[i];
      el.appendChild(span);
      units.push(span);
    }
    return units;
  }

  // Stage size initialization: reads variables or stamps attributes and styles.
  function stage(rootId) {
    const vars = (typeof window !== "undefined" && window.__hyperframes && window.__hyperframes.getVariables()) || {};
    const root = (typeof document !== "undefined" && document.getElementById(rootId)) || (typeof document !== "undefined" && document.body);
    const rawRatio = vars.ratio || (root && root.dataset && root.dataset.ratio) || "9:16";
    const ratio = RATIOS[rawRatio] ? rawRatio : "9:16";
    const defaultDims = RATIOS[ratio];
    const W = parseInt((root && root.dataset && root.dataset.width) || defaultDims[0], 10);
    const H = parseInt((root && root.dataset && root.dataset.height) || defaultDims[1], 10);

    if (root && root.dataset) {
      root.dataset.width = String(W);
      root.dataset.height = String(H);
    }
    if (typeof document !== "undefined") {
      for (const el of [document.documentElement, document.body, root]) {
        if (el) {
          el.style.width = W + "px";
          el.style.height = H + "px";
        }
      }
      if (typeof getComputedStyle === "function" && document.documentElement) {
        try {
          const val = getComputedStyle(document.documentElement).getPropertyValue("--motion-bounce").trim();
          if (val) {
            const parsed = parseFloat(val);
            if (!isNaN(parsed) && isFinite(parsed)) {
              _cachedBounce = Math.max(0, Math.min(1, parsed));
            }
          }
        } catch (_) {}
      }
    }
    return { W, H, ratio, root };
  }

  // Camera push-in on a full-frame layer (transform-origin 0 0).
  function zoomTrack(el, a, b, easeS, c, F, K, holdToEnd = false, easeOutS = easeS) {
    return track(el, a, b, (p) => {
      const t = a + p * (b - a);
      const e = easeInOut(win(t, a, a + easeS)) * (holdToEnd ? 1 : 1 - easeInOut(win(t, b - easeOutS, b)));
      const k = perceptualScale(1, K, e);
      const px = lerp(c[0], F[0], e);
      const py = lerp(c[1], F[1], e);
      return { transform: `translate(${px - k * c[0]}px, ${py - k * c[1]}px) scale(${k})` };
    }, 96);
  }

  const autoZoom = (W, H, bw, bh, cap = 2.5) => Math.max(1.15, Math.min((0.88 * W) / bw, (0.5 * H) / bh, cap));

  const GtmMotion = {
    spring,
    easeOut,
    easeInOut,
    clamp,
    win,
    lerp,
    track,
    sequence,
    stage,
    zoomTrack,
    autoZoom,
    perceptualScale,
    splitText,
  };

  if (typeof window !== "undefined") {
    window.GtmMotion = GtmMotion;
  }
  if (typeof globalThis !== "undefined") {
    globalThis.GtmMotion = GtmMotion;
  }
  if (typeof module !== "undefined" && module.exports) {
    module.exports = GtmMotion;
  }
})();
