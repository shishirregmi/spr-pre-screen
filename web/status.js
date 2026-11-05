/* Header badge on every page: is the local model ready? (The screen never sends papers anywhere else.) */
(async () => {
  const badge = document.getElementById("iso-badge");
  if (!badge) return;
  const set = (ok, text, title) => {
    badge.hidden = false;
    badge.classList.toggle("warn", !ok);
    badge.querySelector(".iso-text").textContent = text;
    badge.title = title;
  };
  try {
    const h = await (await fetch("/api/health")).json();
    if (h.llm_ready) set(true, "Local model · nothing leaves this machine", `Model ${h.llm} runs on this computer; no cloud service is used.`);
    else set(false, "Model offline · named terms only", "The local model is not reachable, so passages are not read in context. Results show named terms as possible matches.");
  } catch (_) {
    set(false, "Server not reachable", "The screening server did not answer.");
  }
})();
