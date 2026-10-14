/* Light/dark theme. Loaded in <head> so the saved choice applies before
   the page paints (no flash). With no saved choice the page follows the
   system setting (CSS prefers-color-scheme); clicking the toggle saves an
   explicit choice. Storage can be blocked (private windows) -- the toggle
   still works for the current page. */
(function () {
  "use strict";
  var root = document.documentElement;
  try {
    var saved = localStorage.getItem("clearbook-theme");
    if (saved === "light" || saved === "dark") root.setAttribute("data-theme", saved);
    if (localStorage.getItem("clearbook-hide-balance") === "1") root.setAttribute("data-hide-balance", "");
  } catch (e) { /* storage unavailable: follow the system */ }

  function current() {
    var set = root.getAttribute("data-theme");
    if (set) return set;
    return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }

  // Hide total balance (Dashboard) and the balance chart (Trends).
  document.addEventListener("click", function (ev) {
    var btn = ev.target.closest && ev.target.closest("[data-balance-toggle]");
    if (!btn) return;
    var hide = !root.hasAttribute("data-hide-balance");
    if (hide) root.setAttribute("data-hide-balance", "");
    else root.removeAttribute("data-hide-balance");
    try { localStorage.setItem("clearbook-hide-balance", hide ? "1" : "0"); } catch (e) { /* not saved */ }
    window.dispatchEvent(new Event("resize"));  // a revealed chart draws itself
  });

  document.addEventListener("click", function (ev) {
    var btn = ev.target.closest && ev.target.closest("[data-theme-toggle]");
    if (!btn) return;
    var next = current() === "dark" ? "light" : "dark";
    root.setAttribute("data-theme", next);
    try { localStorage.setItem("clearbook-theme", next); } catch (e) { /* not saved */ }
    btn.setAttribute("aria-label", next === "dark" ? "Switch to light theme" : "Switch to dark theme");
  });
})();
