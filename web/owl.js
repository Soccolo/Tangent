"use strict";

/* ==========================================================================
   Tangent — the owl, as a character

   owl.svg stays the favicon; this is the same bird rebuilt inline so its parts
   can be animated and given expressions. Everything is CSS-driven off a mood
   class, so a mood change is one attribute write and the browser does the rest.

   Owl.render({size, mood})   -> markup string, for templating into a view
   Owl.setMood(el, mood)      -> change expression in place
   Owl.say(el, text, opts)    -> speech bubble, optionally typed out
   ========================================================================== */

window.Owl = (function () {
  const MOODS = ["idle", "happy", "proud", "oops", "thinking", "curious", "wave"];

  /* One SVG, parts tagged for animation. Colours come from the palette the
     redesign established, not from theme tokens — the owl should look like
     itself in light and dark alike. */
  function accessoryKind(value) {
    const key = String(value || "").toLowerCase();
    if (!key) return "";
    if (key.includes("star") && key.includes("glass")) return "star-glasses";
    if (key.includes("cap") || key.includes("scholar")) return "cap";
    if (key.includes("crown")) return "crown";
    if (key.includes("scarf")) return "scarf";
    if (key.includes("monocle")) return "monocle";
    if (key.includes("bow")) return "bow";
    return "pin";
  }

  function svg(size = 96, mood = "idle", accessory) {
    const equipped = accessory === undefined
      ? document.documentElement.dataset.owlCosmetic
      : accessory;
    const safeAccessory = String(equipped || "").toLowerCase().replace(/[^a-z0-9_-]/g, "");
    const kind = accessoryKind(safeAccessory);
    return `
<svg class="owl-svg is-${mood}" viewBox="0 0 120 128" width="${size}" height="${size * 128 / 120}"
     role="img" aria-label="Tangent, the owl" focusable="false"
     data-accessory="${safeAccessory}" data-accessory-kind="${kind}">
  <g class="owl-all">
    <!-- wings, animated for waving and celebrating -->
    <ellipse class="owl-wing owl-wing-l" cx="30" cy="80" rx="9" ry="17" fill="#5d5294"/>
    <ellipse class="owl-wing owl-wing-r" cx="90" cy="80" rx="9" ry="17" fill="#5d5294"/>

    <!-- ear tufts -->
    <path d="M32 56 L28 38 L46 48 Z" fill="#5d5294"/>
    <path d="M88 56 L92 38 L74 48 Z" fill="#5d5294"/>

    <!-- body -->
    <ellipse cx="60" cy="76" rx="35" ry="33" fill="#7c6fc7"/>
    <path d="M25 76a35 33 0 0 0 70 0z" fill="#5d5294" opacity=".45"/>
    <ellipse cx="60" cy="88" rx="17" ry="19" fill="#d2cefd" opacity=".5"/>

    <!-- feet -->
    <path d="M48 106q-6 6-11 6M48 106q-2 7-2 9M48 106q5 5 7 8"
          stroke="#e8b657" stroke-width="3.5" stroke-linecap="round" fill="none"/>
    <path d="M72 106q6 6 11 6M72 106q2 7 2 9M72 106q-5 5-7 8"
          stroke="#e8b657" stroke-width="3.5" stroke-linecap="round" fill="none"/>

    <g class="owl-head">
      <!-- eyes -->
      <circle cx="46" cy="68" r="13" fill="#f3f5fe"/>
      <circle cx="74" cy="68" r="13" fill="#f3f5fe"/>
      <g class="owl-pupils">
        <circle cx="48" cy="69" r="5.5" fill="#161826"/>
        <circle cx="76" cy="69" r="5.5" fill="#161826"/>
        <circle cx="50" cy="66.5" r="1.8" fill="#ffffff"/>
        <circle cx="78" cy="66.5" r="1.8" fill="#ffffff"/>
      </g>

      <!-- lids: scaleY from the top for blinking, from the bottom for a smile -->
      <ellipse class="owl-lid" cx="46" cy="68" rx="13.4" ry="13.4" fill="#7c6fc7"/>
      <ellipse class="owl-lid" cx="74" cy="68" rx="13.4" ry="13.4" fill="#7c6fc7"/>
      <path class="owl-smile-l" d="M34 68a12 12 0 0 0 24 0z" fill="#7c6fc7"/>
      <path class="owl-smile-r" d="M62 68a12 12 0 0 0 24 0z" fill="#7c6fc7"/>

      <!-- glasses -->
      <g fill="none" stroke="#161826" stroke-width="3.2">
        <circle cx="46" cy="68" r="14.5"/>
        <circle cx="74" cy="68" r="14.5"/>
        <path d="M60.5 68h-1"/>
        <path d="M31.5 65 L22 61"/>
        <path d="M88.5 65 L98 61"/>
      </g>

      <!-- brows: the main carrier of expression -->
      <path class="owl-brow owl-brow-l" d="M36 49 L56 53" stroke="#4a4080"
            stroke-width="3.4" stroke-linecap="round" fill="none"/>
      <path class="owl-brow owl-brow-r" d="M84 49 L64 53" stroke="#4a4080"
            stroke-width="3.4" stroke-linecap="round" fill="none"/>

      <!-- beak -->
      <path class="owl-beak" d="M60 78 L53 86 L67 86 Z" fill="#e8b657"/>

      <!-- mortarboard -->
      <path d="M44 44h32v6a16 16 0 0 1-32 0z" fill="#292b31"/>
      <path d="M60 20 L100 36 L60 52 L20 36 Z" fill="#3f424d"/>
      <path d="M60 20 L100 36 L60 52 Z" fill="#595d6c"/>
      <g class="owl-tassel">
        <path d="M96 37.6v14" stroke="#e8b657" stroke-width="3" stroke-linecap="round"/>
        <circle cx="96" cy="55" r="4.5" fill="#e8b657"/>
      </g>
    </g>

    <!-- Workshop accessories. Only the equipped group is revealed in CSS. -->
    <g class="owl-accessory owl-accessory-scarf">
      <path d="M37 90q23 10 46 0l-3 10q-20 9-40 0z" fill="#e78aa8"/>
      <path d="M75 96l9 1-3 24-10-2z" fill="#cf6c91"/>
      <path d="M75 106h7M74 112h7" stroke="#ffd0df" stroke-width="2"/>
    </g>
    <g class="owl-accessory owl-accessory-crown">
      <path d="M43 28l5-13 12 10 12-10 5 13z" fill="#f2c45f" stroke="#fff0a8" stroke-width="2"/>
      <circle cx="48" cy="18" r="2.5" fill="#d67a9c"/>
      <circle cx="60" cy="24" r="2.5" fill="#9184d9"/>
      <circle cx="72" cy="18" r="2.5" fill="#5cb5b0"/>
    </g>
    <g class="owl-accessory owl-accessory-cap">
      <path d="M60 20l34 14-34 14-34-14z" fill="#7768c4" stroke="#c8c1ff" stroke-width="1.5"/>
      <path d="M44 43h32v6q-16 10-32 0z" fill="#5d5294"/>
      <path d="M91 35v17" stroke="#f2c45f" stroke-width="2.4"/>
      <circle cx="91" cy="55" r="3.5" fill="#f2c45f"/>
    </g>
    <g class="owl-accessory owl-accessory-star-glasses" fill="#f2c45f"
       stroke="#fff0a8" stroke-width="1.4" stroke-linejoin="round">
      <path d="M46 51l4.8 9.5 10.5 1.5-7.6 7.4 1.8 10.5-9.5-5-9.5 5 1.8-10.5-7.6-7.4 10.5-1.5z"/>
      <path d="M74 51l4.8 9.5 10.5 1.5-7.6 7.4 1.8 10.5-9.5-5-9.5 5 1.8-10.5-7.6-7.4 10.5-1.5z"/>
      <circle cx="46" cy="67" r="8" fill="#f3f5fe" stroke="#e8b657"/>
      <circle cx="74" cy="67" r="8" fill="#f3f5fe" stroke="#e8b657"/>
    </g>
    <g class="owl-accessory owl-accessory-monocle" fill="none" stroke="#f2c45f">
      <circle cx="75" cy="68" r="17" stroke-width="2.5"/>
      <path d="M90 79q7 14 1 31" stroke-width="2"/>
    </g>
    <g class="owl-accessory owl-accessory-bow">
      <path d="M52 91q-14-10-15 5 1 14 15 3zM68 91q14-10 15 5-1 14-15 3z" fill="#5cb5b0"/>
      <circle cx="60" cy="96" r="7" fill="#86dcd6"/>
    </g>
    <g class="owl-accessory owl-accessory-pin">
      <path d="M77 85l2.2 4.6 5 .7-3.6 3.5.9 5-4.5-2.4-4.5 2.4.9-5-3.6-3.5 5-.7z"
            fill="#f2c45f" stroke="#fff0a8" stroke-width="1.2"/>
    </g>
  </g>
</svg>`;
  }

  function render({ size = 96, mood = "idle", bubble = "", accessory } = {}) {
    return `<div class="owl" data-mood="${mood}">
      ${svg(size, mood, accessory)}
      ${bubble ? `<div class="owl-bubble">${bubble}</div>` : ""}
    </div>`;
  }

  function setMood(root, mood) {
    if (!root) return;
    const el = root.querySelector(".owl-svg") || root;
    MOODS.forEach((m) => el.classList.remove(`is-${m}`));
    el.classList.add(`is-${MOODS.includes(mood) ? mood : "idle"}`);
    const wrap = root.closest?.(".owl") || root;
    if (wrap.dataset) wrap.dataset.mood = mood;
  }

  /* Types text out unless the user prefers reduced motion, in which case it
     just appears — a typewriter is decoration, and decoration shouldn't be the
     only way to receive information. */
  const reduced = () =>
    window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  function say(bubbleEl, text, { typed = true, speed = 18, done } = {}) {
    if (!bubbleEl) return () => {};
    bubbleEl.classList.remove("hidden");
    if (!typed || reduced()) {
      bubbleEl.textContent = text;
      done && done();
      return () => {};
    }
    bubbleEl.textContent = "";
    let i = 0;
    const timer = setInterval(() => {
      // Emit whole words, not characters: a word at a time reads far better
      // than a stuttering letter crawl and finishes in a fraction of the time.
      const next = text.indexOf(" ", i + 1);
      i = next === -1 ? text.length : next;
      bubbleEl.textContent = text.slice(0, i);
      if (i >= text.length) {
        clearInterval(timer);
        done && done();
      }
    }, speed * 4);
    return () => clearInterval(timer);
  }

  return { render, setMood, say, svg, MOODS };
})();
