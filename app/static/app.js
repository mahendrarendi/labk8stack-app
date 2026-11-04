(() => {
  const root = document.querySelector(".challenge");
  if (!root) return;

  const slug = root.dataset.challenge;
  const isActive = root.dataset.active === "true";

  const editor = document.getElementById("editor");
  const consoleBox = document.getElementById("console");
  const termOut = document.getElementById("term-out");
  const termForm = document.getElementById("term-form");
  const termInput = document.getElementById("term-input");
  const timerEl = document.getElementById("timer");

  const btnApply = document.getElementById("btn-apply");
  const btnVerify = document.getElementById("btn-verify");
  const btnReset = document.getElementById("btn-reset");
  const btnTermVerify = document.getElementById("btn-term-verify");
  const btnTermReset = document.getElementById("btn-term-reset");

  let activeTab = "editor";

  const pad = (n) => String(n).padStart(2, "0");
  const clock = (seconds) => {
    const s = Math.max(0, seconds);
    return `${pad(Math.floor(s / 60))}:${pad(s % 60)}`;
  };

  function printEditor(text, kind) {
    consoleBox.textContent = text || "(no output)";
    consoleBox.classList.remove("ok", "bad");
    if (kind) consoleBox.classList.add(kind);
  }

  function printTerminal(text) {
    termOut.textContent += `\n${text}`;
    termOut.scrollTop = termOut.scrollHeight;
  }

  function say(text, kind) {
    if (activeTab === "terminal") printTerminal(text);
    else printEditor(text, kind);
  }

  function disableAll() {
    [btnApply, btnVerify, btnReset, btnTermVerify, btnTermReset, termInput]
      .forEach((element) => element && (element.disabled = true));
  }

  // ------------------------------------------------------------ tabs
  document.querySelectorAll(".tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      activeTab = tab.dataset.tab;
      document.querySelectorAll(".tab").forEach((other) => {
        other.classList.toggle("is-active", other === tab);
      });
      document.querySelectorAll(".pane").forEach((pane) => {
        pane.classList.toggle("is-active", pane.dataset.pane === activeTab);
      });
      if (activeTab === "terminal" && termInput) termInput.focus();
    });
  });

  // ----------------------------------------------------------- timer
  if (isActive && timerEl) {
    let left = Number(root.dataset.remaining || 0);
    let stopped = false;
    const paint = () => {
      timerEl.textContent = clock(left);
      if (left <= 0) {
        stopped = true;
        timerEl.classList.add("expired");
        say("Time is up. This attempt scores 0. Reload to start a new attempt.", "bad");
        disableAll();
        return;
      }
      left -= 1;
    };
    paint();
    const id = setInterval(() => {
      if (stopped) return clearInterval(id);
      paint();
    }, 1000);
  }

  // ------------------------------------------------------------- api
  async function postJSON(path, payload) {
    const response = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload || {}),
    });
    let data = {};
    try {
      data = await response.json();
    } catch (_) {
      data = { ok: false, output: `invalid response (HTTP ${response.status})` };
    }
    return { response, data };
  }

  // ---------------------------------------------------------- editor
  btnApply?.addEventListener("click", async () => {
    btnApply.disabled = true;
    printEditor("$ kubectl apply -f manifest.yaml\nwaiting for the cluster...");
    try {
      const { response, data } = await postJSON(`/api/challenge/${slug}/apply`, {
        yaml: editor.value,
      });
      printEditor(data.output || "(no output)", response.ok ? "ok" : "bad");
    } catch (error) {
      printEditor(`could not reach the server: ${error}`, "bad");
    } finally {
      btnApply.disabled = false;
    }
  });

  editor?.addEventListener("keydown", (event) => {
    if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
      event.preventDefault();
      btnApply?.click();
    }
  });

  // -------------------------------------------------------- terminal
  const history = [];
  let historyIndex = -1;

  termForm?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const command = termInput.value.trim();
    if (!command) return;

    printTerminal(`$ ${command}`);
    termInput.value = "";
    history.push(command);
    historyIndex = history.length;
    termInput.disabled = true;

    try {
      const { data } = await postJSON(`/api/challenge/${slug}/terminal`, { command });
      printTerminal(data.output || "(no output)");
    } catch (error) {
      printTerminal(`could not reach the server: ${error}`);
    } finally {
      termInput.disabled = false;
      termInput.focus();
    }
  });

  termInput?.addEventListener("keydown", (event) => {
    if (event.key === "ArrowUp") {
      event.preventDefault();
      if (historyIndex > 0) historyIndex -= 1;
      termInput.value = history[historyIndex] || "";
    } else if (event.key === "ArrowDown") {
      event.preventDefault();
      if (historyIndex < history.length - 1) {
        historyIndex += 1;
        termInput.value = history[historyIndex];
      } else {
        historyIndex = history.length;
        termInput.value = "";
      }
    }
  });

  // ---------------------------------------------------------- verify
  async function verify(source, button) {
    if (button) button.disabled = true;
    say("verifying cluster state...");

    try {
      const payload = source === "editor"
        ? { yaml: editor.value, source }
        : { source };
      const { data } = await postJSON(`/api/challenge/${slug}/verify`, payload);

      if (data.passed) {
        say(`✔ PASSED — ${data.score} points (attempt #${data.tries}, ${clock(data.remaining)} left).\nReloading...`, "ok");
        setTimeout(() => window.location.reload(), 1600);
        return;
      }

      if (data.expired) {
        say(`✘ ${data.message}`, "bad");
        disableAll();
        return;
      }

      say(`✘ ${data.message}\nAttempt: ${data.tries} · time left ${clock(data.remaining)}`, "bad");
    } catch (error) {
      say(`verification failed: ${error}`, "bad");
    } finally {
      if (button) button.disabled = false;
    }
  }

  btnVerify?.addEventListener("click", () => verify("editor", btnVerify));
  btnTermVerify?.addEventListener("click", () => verify("terminal", btnTermVerify));

  // ----------------------------------------------------------- reset
  async function reset(button, fromTerminal) {
    if (button) button.disabled = true;
    try {
      const { data } = await postJSON(`/api/challenge/${slug}/reset`);
      const text = data.output || "sandbox reset";
      if (fromTerminal) printTerminal(text);
      else printEditor(text, "ok");
    } catch (error) {
      say(`reset failed: ${error}`, "bad");
    } finally {
      if (button) button.disabled = false;
    }
  }

  btnReset?.addEventListener("click", () => reset(btnReset, false));
  btnTermReset?.addEventListener("click", () => reset(btnTermReset, true));
})();
