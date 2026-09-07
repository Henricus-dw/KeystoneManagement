// Login -> holographic grand entrance.
// On success the login card falls away, the white holographic logo animates
// over the still-playing video, then we navigate into the app.
(function () {
  // Some browsers refuse muted-autoplay until nudged — kick it on load + first input.
  const video = document.getElementById("bgVideo");
  if (video) {
    const play = () => { video.play().catch(() => {}); };
    video.addEventListener("loadeddata", play);
    play();
    document.addEventListener("pointerdown", play, { once: true });
  }

  const form = document.getElementById("loginForm");
  const btn = document.getElementById("loginBtn");
  const errorEl = document.getElementById("loginError");
  if (!form) return;

  function showError(msg) {
    errorEl.textContent = msg;
    errorEl.classList.add("show");
    document.body.classList.remove("authing");
    btn.disabled = false;
  }

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    errorEl.classList.remove("show");
    btn.disabled = true;
    document.body.classList.add("authing");

    const data = new FormData(form);
    let res;
    try {
      res = await fetch("/login", {
        method: "POST",
        headers: { "X-Requested-With": "fetch" },
        body: data,
      });
    } catch (err) {
      showError("Network error — check the server is running.");
      return;
    }

    const payload = await res.json().catch(() => ({}));
    if (!res.ok || !payload.ok) {
      showError(payload.error || "Sign-in failed.");
      return;
    }

    // Success — play the entrance, then go.
    runEntrance(payload.next || "/dashboard");
  });

  function runEntrance(next) {
    // 1) card falls away (CSS via .authing already running), reveal holo stage
    document.body.classList.add("entering");

    // 2) hold the grand entrance, then dissolve into the app
    const HOLD = 2600;   // time the logo is on screen
    const FADE = 650;    // dissolve duration
    setTimeout(() => {
      document.body.classList.add("leaving");
      setTimeout(() => { window.location.href = next; }, FADE);
    }, HOLD);
  }
})();
