// Progressive enhancement only: every action also works without JavaScript,
// and the server enforces all rules (including duplicate protection).
document.addEventListener("submit", function (event) {
  var form = event.target;
  var confirmText = form.getAttribute("data-confirm");
  if (confirmText && !window.confirm(confirmText)) {
    event.preventDefault();
    return;
  }
  if (form.dataset.submitted === "1") {
    event.preventDefault(); // stop double taps from sending twice
    return;
  }
  form.dataset.submitted = "1";
  var buttons = form.querySelectorAll("button[type=submit], input[type=submit]");
  buttons.forEach(function (b) {
    // keep the clicked button's name/value in the submission
    if (event.submitter === b && b.name) {
      var hidden = document.createElement("input");
      hidden.type = "hidden";
      hidden.name = b.name;
      hidden.value = b.value;
      form.appendChild(hidden);
    }
    b.disabled = true;
  });
});

// Live, clearly provisional timer on the clock screen.
(function () {
  var el = document.querySelector("[data-live-since]");
  if (!el) return;
  var since = Date.parse(el.getAttribute("data-live-since"));
  var offset = parseInt(el.getAttribute("data-live-offset") || "0", 10);
  function tick() {
    var secs = Math.max(0, Math.floor((Date.now() - since) / 1000) - offset);
    var h = Math.floor(secs / 3600), m = Math.floor((secs % 3600) / 60);
    el.textContent = h + "h " + (m < 10 ? "0" : "") + m + "m";
  }
  tick();
  setInterval(tick, 30000);
})();

// Location switcher: choosing a location submits the form straight away.
document.addEventListener("change", function (e) {
  if (e.target.matches("select[data-autosubmit]")) { e.target.form.submit(); }
});
