"use strict";

/* ==========================================================================
   Tangent — celebration layer
   Additive: this file makes NO assumptions about app.js beyond the markup it
   already renders, and app.js needs no changes. It watches #view, and when a
   lesson-finish screen appears it fires confetti and, if the level went up,
   injects a "Level up!" badge (styled by .levelup in styles.css).
   ========================================================================== */

(function () {
  var layer = document.getElementById("confetti");
  var view = document.getElementById("view");
  if (!layer || !view) return;

  var LEVEL_KEY = "tangent.lastLevel";
  var COLORS = ["#9184d9", "#b5abfc", "#d2cefd", "#e8b657", "#a7a1db", "#7c6fc7"];

  var reduced = window.matchMedia
    && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  function burst(count) {
    if (reduced) return;
    for (var i = 0; i < count; i++) {
      var piece = document.createElement("i");
      var w = 6 + Math.random() * 9;
      piece.style.left = (Math.random() * 100) + "%";
      piece.style.width = w + "px";
      piece.style.height = (w * 0.55) + "px";
      piece.style.background = COLORS[i % COLORS.length];
      piece.style.animationDuration = (1.7 + Math.random() * 1.5) + "s";
      piece.style.animationDelay = (Math.random() * 0.45) + "s";
      layer.appendChild(piece);
      removeLater(piece);
    }
  }

  function removeLater(el) {
    setTimeout(function () {
      if (el.parentNode) el.parentNode.removeChild(el);
    }, 4200);
  }

  function num(text) {
    var m = String(text == null ? "" : text).match(/-?\d+/);
    return m ? parseInt(m[0], 10) : null;
  }

  function storedLevel() {
    try { return num(localStorage.getItem(LEVEL_KEY)); } catch (e) { return null; }
  }
  function storeLevel(level) {
    try { localStorage.setItem(LEVEL_KEY, String(level)); } catch (e) { /* private mode */ }
  }

  /* The finish screen renders three .reward tiles: XP earned, streak, level. */
  function celebrate(card) {
    var tiles = card.querySelectorAll(".reward .n");
    var awarded = tiles.length > 0 ? num(tiles[0].textContent) : null;
    var level = tiles.length > 2 ? num(tiles[2].textContent) : null;

    var score = card.querySelector(".bigscore");
    var parts = score ? String(score.textContent).split("/") : [];
    var perfect = parts.length === 2 && num(parts[0]) !== null
      && num(parts[0]) === num(parts[1]) && num(parts[1]) > 0;

    burst(perfect ? 96 : 64);
    if (perfect) setTimeout(function () { burst(40); }, 450);

    if (level === null) return;

    /* Prefer the level we recorded last time; fall back to deriving the
       pre-lesson level from the header XP minus what this lesson awarded. */
    var before = storedLevel();
    if (before === null && awarded !== null) {
      var xpNow = num((document.getElementById("xpNum") || {}).textContent);
      /* The app defines level as floor(xp / 100) + 1, so the +1 is required —
         without it a first-ever lesson derives level 0 and reports a level-up
         to 1 that never happened. */
      if (xpNow !== null) before = Math.floor((xpNow - awarded) / 100) + 1;
    }

    if (before !== null && level > before) {
      var badge = document.createElement("div");
      badge.className = "levelup";
      badge.textContent = "✦ Level up! You reached level " + level;
      card.insertBefore(badge, card.firstChild);
      setTimeout(function () { burst(48); }, 550);
    }
    storeLevel(level);
  }

  /* Keep the recorded level fresh from the Progress screen too, so the very
     first level-up after install is still caught. */
  function noteProgressLevel() {
    var heads = view.querySelectorAll("h2");
    for (var i = 0; i < heads.length; i++) {
      var m = String(heads[i].textContent).match(/^\s*Level\s+(\d+)/i);
      if (m) { storeLevel(parseInt(m[1], 10)); return; }
    }
  }

  var celebrated = false;
  function check() {
    var score = view.querySelector(".bigscore");
    if (score) {
      if (!celebrated) {
        celebrated = true;
        var card = score.closest(".card") || score.parentNode;
        celebrate(card);
      }
    } else {
      celebrated = false;
      noteProgressLevel();
    }
  }

  new MutationObserver(check).observe(view, { childList: true, subtree: true });
  check();
})();
