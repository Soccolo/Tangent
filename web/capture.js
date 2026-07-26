"use strict";

/* ==========================================================================
   Tangent — screen capture

   No video is ever recorded. The display stream is sampled into a canvas at
   intervals, each frame is downscaled and compared against the last kept one,
   and a small batch is POSTed for extraction. Frames live in a JS array and
   are dropped the moment the batch is sent — nothing touches disk, nothing is
   stored server-side, and closing the tab loses everything in flight.

   Exposed as window.TangentCapture for app.js.
   ========================================================================== */

window.TangentCapture = (function () {
  const SAMPLE_MS = 20_000;    // grab a frame this often
  const BATCH_MS = 5 * 60_000; // send what we've kept this often
  const MAX_BATCH = 6;         // hard ceiling per request (server allows 8)
  const FRAME_WIDTH = 1024;    // downscale: cost scales with pixels
  const JPEG_QUALITY = 0.6;
  const DIFF_THRESHOLD = 0.06; // fraction of sampled pixels that must change

  let stream = null;
  let video = null;
  let timerSample = null;
  let timerBatch = null;
  let frames = [];             // data URLs awaiting extraction
  let lastSignature = null;    // coarse fingerprint of the last kept frame
  let listeners = {};
  let stats = { kept: 0, sent: 0, skipped: 0, startedAt: null };

  const emit = (event, payload) => (listeners[event] || []).forEach((f) => f(payload));

  function on(event, fn) {
    (listeners[event] = listeners[event] || []).push(fn);
    return () => { listeners[event] = listeners[event].filter((f) => f !== fn); };
  }

  const isSupported = () =>
    !!(navigator.mediaDevices && navigator.mediaDevices.getDisplayMedia);

  /* A coarse greyscale fingerprint. Cheap enough to run every 20s and good
     enough to tell "still the same spreadsheet" from "switched to the docs". */
  function signature(canvas) {
    const size = 16;
    const small = document.createElement("canvas");
    small.width = small.height = size;
    const ctx = small.getContext("2d", { willReadFrequently: true });
    ctx.drawImage(canvas, 0, 0, size, size);
    const { data } = ctx.getImageData(0, 0, size, size);
    const out = new Uint8Array(size * size);
    for (let i = 0; i < out.length; i++) {
      const p = i * 4;
      out[i] = (data[p] * 0.299 + data[p + 1] * 0.587 + data[p + 2] * 0.114) | 0;
    }
    return out;
  }

  function changed(a, b) {
    if (!a || !b) return true;
    let differing = 0;
    for (let i = 0; i < a.length; i++) if (Math.abs(a[i] - b[i]) > 12) differing++;
    return differing / a.length > DIFF_THRESHOLD;
  }

  function sample() {
    if (!video || video.readyState < 2 || !video.videoWidth) return;
    const scale = Math.min(1, FRAME_WIDTH / video.videoWidth);
    const canvas = document.createElement("canvas");
    canvas.width = Math.round(video.videoWidth * scale);
    canvas.height = Math.round(video.videoHeight * scale);
    canvas.getContext("2d").drawImage(video, 0, 0, canvas.width, canvas.height);

    const sig = signature(canvas);
    if (!changed(lastSignature, sig)) {
      stats.skipped++;
      emit("stats", { ...stats, pending: frames.length });
      return;   // screen hasn't meaningfully moved — sending it would pay twice
    }
    lastSignature = sig;

    if (frames.length >= MAX_BATCH) frames.shift();  // keep the most recent
    frames.push(canvas.toDataURL("image/jpeg", JPEG_QUALITY));
    stats.kept++;
    emit("stats", { ...stats, pending: frames.length });
  }

  async function flush() {
    if (!frames.length) return;
    const batch = frames;
    frames = [];                       // dropped before the await: never resent
    emit("stats", { ...stats, pending: 0 });
    try {
      const res = await fetch("/api/capture/frames", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ frames: batch }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || "Extraction failed");
      stats.sent += batch.length;
      emit("stats", { ...stats, pending: frames.length });
      if (data.observations && data.observations.length) emit("observations", data.observations);
    } catch (err) {
      emit("error", err.message);
    } finally {
      batch.length = 0;
    }
  }

  async function start() {
    if (stream) return true;
    if (!isSupported()) throw new Error("This browser can't share a screen. Try Chrome, Edge or Firefox on a desktop.");

    // The browser's own picker is the consent gate — we never choose for them.
    stream = await navigator.mediaDevices.getDisplayMedia({
      video: { frameRate: 1 },   // we sample stills; no need for smooth video
      audio: false,
    });

    video = document.createElement("video");
    video.srcObject = stream;
    video.muted = true;
    await video.play();

    // Stopping from the browser's own "Stop sharing" bar must stop us too.
    stream.getVideoTracks().forEach((t) => t.addEventListener("ended", () => stop(true)));

    stats = { kept: 0, sent: 0, skipped: 0, startedAt: Date.now() };
    lastSignature = null;
    frames = [];
    sample();
    timerSample = setInterval(sample, SAMPLE_MS);
    timerBatch = setInterval(flush, BATCH_MS);
    emit("started", { ...stats });
    return true;
  }

  async function stop(fromBrowser) {
    if (!stream) return;
    clearInterval(timerSample);
    clearInterval(timerBatch);
    timerSample = timerBatch = null;
    await flush();                       // one last extraction from what we kept
    stream.getTracks().forEach((t) => t.stop());
    stream = null;
    if (video) { video.srcObject = null; video = null; }
    frames = [];
    lastSignature = null;
    emit("stopped", { ...stats, fromBrowser: !!fromBrowser });
  }

  /* Panic button: drop everything held in memory without sending it. */
  function discard() {
    frames = [];
    emit("stats", { ...stats, pending: 0 });
  }

  const isRunning = () => !!stream;

  // A refresh or tab close ends the session; in-memory frames die with it.
  window.addEventListener("beforeunload", () => {
    if (stream) stream.getTracks().forEach((t) => t.stop());
  });

  return { start, stop, discard, on, isRunning, isSupported, flush };
})();
