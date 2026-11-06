window.initChallengePage = () => {
  const root = document.querySelector(".challenge");
  if (!root) return;

  const slug = root.dataset.challenge;
  const isActive = root.dataset.active === "true";

  const editor = document.getElementById("editor");
  const editorState = document.getElementById("editor-state");
  const consoleBox = document.getElementById("console");
  const termOut = document.getElementById("term-out");
  const termScreen = document.getElementById("terminal-screen");
  const termBanner = document.getElementById("term-banner");
  const termClear = document.getElementById("term-clear");
  const termForm = document.getElementById("term-form");
  const termInput = document.getElementById("term-input");
  const timerEl = document.getElementById("timer");
  const advancedHint = document.getElementById("advanced-hint");

  const btnApply = document.getElementById("btn-apply");
  const btnVerify = document.getElementById("btn-verify");
  const btnReset = document.getElementById("btn-reset");
  const btnTermVerify = document.getElementById("btn-term-verify");

  let activeTab = "editor";
  let controlsLocked = !isActive;
  let timerId;
  let refreshTimeout;
  const initialManifest = editor.value;
  const storagePrefix = `labk8stack:${root.dataset.attemptId}:${slug}`;
  const draftKey = `${storagePrefix}:draft`;
  const appliedKey = `${storagePrefix}:applied`;
  const readStored = (key) => {
    try { return sessionStorage.getItem(key); } catch (_) { return null; }
  };
  const store = (key, value) => {
    try {
      if (value === null) sessionStorage.removeItem(key);
      else sessionStorage.setItem(key, value);
    } catch (_) { /* private browsing may disable storage */ }
  };
  const draft = readStored(draftKey);
  if (draft !== null && isActive) editor.value = draft;
  let lastApplied = readStored(appliedKey);

  function updateEditorState() {
    const applied = lastApplied !== null && editor.value === lastApplied;
    editorState.textContent = applied ? "Applied" : "Not applied";
    editorState.classList.toggle("applied", applied);
  }
  updateEditorState();
  editor.addEventListener("input", () => {
    store(draftKey, editor.value);
    updateEditorState();
  });

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

  function scrollTerminal() {
    termScreen.scrollTop = termScreen.scrollHeight;
  }

  function printTerminal(text, kind) {
    const block = document.createElement("pre");
    block.className = `terminal-result${kind === "bad" ? " error" : kind === "ok" ? " success" : ""}`;
    block.textContent = text || "(no output)";
    termOut.appendChild(block);
    scrollTerminal();
  }

  function printCommand(command) {
    const line = document.createElement("div");
    line.className = "terminal-command";
    const prompt = document.createElement("span");
    prompt.className = "prompt";
    prompt.textContent = "lab@labk8stack:~$";
    const value = document.createElement("span");
    value.textContent = ` ${command}`;
    line.append(prompt, value);
    termOut.appendChild(line);
    scrollTerminal();
  }

  function clearTerminal() {
    termOut.replaceChildren();
    termBanner.hidden = true;
    termInput.focus();
  }

  function say(text, kind) {
    if (activeTab === "terminal") printTerminal(text, kind);
    else printEditor(text, kind);
  }

  function disableAll() {
    controlsLocked = true;
    [btnApply, btnVerify, btnReset, btnTermVerify, termInput]
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
      if (activeTab === "terminal" && termInput && !termInput.disabled) termInput.focus();
    });
  });

  // ----------------------------------------------------------- timer
  if (isActive && timerEl) {
    let left = Number(root.dataset.remaining || 0);
    let stopped = false;
    const paint = () => {
      timerEl.textContent = clock(left);
      if (advancedHint && left <= Number(advancedHint.dataset.unlockAt)) {
        advancedHint.classList.add("unlocked");
      }
      if (left <= 0) {
        stopped = true;
        timerEl.classList.add("expired");
        say("Time is up. This attempt scores 0.", "bad");
        disableAll();
        refreshTimeout = setTimeout(() => {
          window.softNavigate?.(window.location.href, { replace: true });
        }, 900);
        return;
      }
      left -= 1;
    };
    paint();
    timerId = setInterval(() => {
      if (stopped) return clearInterval(timerId);
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
    const submittedYaml = editor.value;
    printEditor("$ kubectl apply -f manifest.yaml\nwaiting for the cluster...");
    try {
      const { response, data } = await postJSON(`/api/challenge/${slug}/apply`, {
        yaml: submittedYaml,
      });
      if (response.ok && data.ok) {
        lastApplied = submittedYaml;
        store(appliedKey, submittedYaml);
      }
      updateEditorState();
      printEditor(data.output || "(no output)", response.ok && data.ok ? "ok" : "bad");
    } catch (error) {
      printEditor(`could not reach the server: ${error}`, "bad");
    } finally {
      btnApply.disabled = controlsLocked;
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

  termClear?.addEventListener("click", clearTerminal);
  termScreen?.addEventListener("click", (event) => {
    if (event.target === termScreen && !termInput.disabled) termInput.focus();
  });

  const terminalShortcut = (event) => {
    if (activeTab === "terminal" && (event.ctrlKey || event.metaKey)
        && event.key.toLowerCase() === "l") {
      event.preventDefault();
      clearTerminal();
    }
  };
  document.addEventListener("keydown", terminalShortcut);

  termForm?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const command = termInput.value.trim();
    if (!command) return;

    if (command === "clear") {
      clearTerminal();
      termInput.value = "";
      return;
    }

    printCommand(command);
    termInput.value = "";
    history.push(command);
    historyIndex = history.length;

    if (command === "help") {
      printTerminal("Available commands:\n  kubectl get pods|deploy|svc|ep\n  kubectl describe pod|deployment|service <name>\n  kubectl logs <pod>\n  kubectl set image|env|resources deployment/web ...\n  kubectl scale deployment/web --replicas=<n>\n  kubectl rollout status|resume|pause deployment/web\n  kubectl delete pod <name>\n  kubectl create deployment <name> --image=<image>\n  help · clear");
      termInput.focus();
      return;
    }

    termInput.disabled = true;

    try {
      const { response, data } = await postJSON(`/api/challenge/${slug}/terminal`, { command });
      const output = data.output || "(no output)";
      printTerminal(output, !response.ok || output.startsWith("error:") ? "bad" : undefined);
    } catch (error) {
      printTerminal(`could not reach the server: ${error}`, "bad");
    } finally {
      termInput.disabled = controlsLocked;
      if (!controlsLocked) termInput.focus();
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
    if (source === "editor" && (lastApplied === null || editor.value !== lastApplied)) {
      printEditor("Apply the current manifest before verifying it.", "bad");
      return;
    }
    if (source === "terminal" && termInput.value.trim()) {
      printTerminal("Press Enter to run the command before verifying.", "bad");
      return;
    }
    if (button) button.disabled = true;
    say("verifying cluster state...");

    try {
      const payload = source === "editor"
        ? { yaml: editor.value, source }
        : { source };
      const { response, data } = await postJSON(`/api/challenge/${slug}/verify`, payload);

      if (!response.ok) {
        say(`✘ ${data.message || data.output || "Verification failed."}`, "bad");
        return;
      }

      if (data.passed) {
        disableAll();
        say(`✔ PASSED — ${data.score} points (attempt #${data.tries}, ${clock(data.remaining)} left).`, "ok");
        store(draftKey, null);
        store(appliedKey, null);
        refreshTimeout = setTimeout(() => {
          window.softNavigate?.(window.location.href, { replace: true });
        }, 900);
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
      if (button) button.disabled = controlsLocked;
    }
  }

  btnVerify?.addEventListener("click", () => verify("editor", btnVerify));
  btnTermVerify?.addEventListener("click", () => verify("terminal", btnTermVerify));

  // ----------------------------------------------------------- reset
  async function reset() {
    const button = btnReset;
    if (button) button.disabled = true;
    try {
      const { data } = await postJSON(`/api/challenge/${slug}/reset`);
      const text = data.output || "Lab reset.";
      if (data.ok) {
        editor.value = initialManifest;
        lastApplied = null;
        store(draftKey, null);
        store(appliedKey, null);
        updateEditorState();
        termOut.replaceChildren();
        termBanner.hidden = false;
        termInput.value = "";
        history.length = 0;
        historyIndex = -1;
      }
      if (activeTab === "terminal") printTerminal(text, data.ok ? "ok" : "bad");
      else printEditor(text, data.ok ? "ok" : "bad");
    } catch (error) {
      say(`reset failed: ${error}`, "bad");
    } finally {
      if (button) button.disabled = controlsLocked;
    }
  }

  btnReset?.addEventListener("click", reset);

  window.disposeChallengePage = () => {
    if (timerId) clearInterval(timerId);
    if (refreshTimeout) clearTimeout(refreshTimeout);
    document.removeEventListener("keydown", terminalShortcut);
  };
};

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", window.initChallengePage, { once: true });
} else {
  window.initChallengePage();
}
