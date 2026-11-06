window.initLandingPreview = () => {
  const preview = document.querySelector(".landing-preview");
  if (!preview) return;

  const output = preview.querySelector("#landing-preview-output");
  const typed = preview.querySelector("#landing-preview-typed");
  if (!output || !typed) return;

  let timer;
  let disposed = false;
  const schedule = (callback, delay) => {
    timer = window.setTimeout(() => {
      if (!disposed && preview.isConnected) callback();
    }, delay);
  };

  const appendLine = (parts, className) => {
    const line = document.createElement("div");
    if (className) line.className = className;
    for (const [value, partClass] of parts) {
      const span = document.createElement("span");
      span.textContent = value;
      if (partClass) span.className = partClass;
      line.append(span);
    }
    output.append(line);
  };

  const typeCommand = (command, index, next) => {
    typed.textContent = command.slice(0, index);
    if (index < command.length) {
      schedule(() => typeCommand(command, index + 1, next), 85);
    } else {
      schedule(next, 420);
    }
  };

  const showPods = () => {
    appendLine([["lab@labk8stack:~$", "preview-prompt"], [" kubectl get pods"]]);
    const table = document.createElement("table");
    table.className = "landing-preview-table";
    const header = table.createTHead().insertRow();
    const row = table.createTBody().insertRow();
    for (const [index, heading] of ["NAME", "READY", "STATUS", "RESTARTS", "AGE"].entries()) {
      const th = document.createElement("th");
      th.textContent = heading;
      header.append(th);
      const td = row.insertCell();
      td.textContent = ["web-7f9c-abc12", "0/1", "ImagePullBackOff", "0", "4m"][index];
      if (heading === "STATUS") td.className = "preview-warning";
    }
    output.append(table);
    typed.textContent = "";
    schedule(() => typeCommand("clear", 0, clearPreview), 1800);
  };

  const clearPreview = () => {
    output.replaceChildren();
    typed.textContent = "";
    schedule(start, 450);
  };

  const start = () => {
    output.replaceChildren();
    typed.textContent = "";
    schedule(() => typeCommand("kubectl get pods", 0, showPods), 500);
  };

  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const onVisibilityChange = () => {
    window.clearTimeout(timer);
    if (!document.hidden) start();
  };

  if (!reducedMotion) {
    document.addEventListener("visibilitychange", onVisibilityChange);
    if (!document.hidden) start();
  }

  window.disposeLandingPreview = () => {
    disposed = true;
    window.clearTimeout(timer);
    document.removeEventListener("visibilitychange", onVisibilityChange);
  };
};

window.initLandingAuth = () => {
  const tabs = document.querySelector(".landing-auth-tabs");
  if (!tabs) return;

  const loginTab = tabs.querySelector('[data-auth-tab="login"]');
  const registerTab = tabs.querySelector('[data-auth-tab="register"]');
  const loginPanel = document.querySelector("#auth-panel-login");
  const registerPanel = document.querySelector("#auth-panel-register");
  if (!loginTab || !registerTab || !loginPanel || !registerPanel) return;

  const activate = (mode, focusInput = false) => {
    const registering = mode === "register";
    loginPanel.hidden = registering;
    registerPanel.hidden = !registering;
    loginTab.setAttribute("aria-selected", String(!registering));
    registerTab.setAttribute("aria-selected", String(registering));
    loginTab.tabIndex = registering ? -1 : 0;
    registerTab.tabIndex = registering ? 0 : -1;
    if (focusInput) (registering ? registerPanel : loginPanel).querySelector('input[name="name"]')?.focus();
  };

  const onLoginClick = () => activate("login", true);
  const onRegisterClick = () => activate("register", true);
  const onKeyDown = (event) => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    const next = event.key === "Home" ? loginTab
      : event.key === "End" ? registerTab
      : event.target === loginTab ? registerTab : loginTab;
    activate(next.dataset.authTab);
    next.focus();
  };

  loginTab.addEventListener("click", onLoginClick);
  registerTab.addEventListener("click", onRegisterClick);
  tabs.addEventListener("keydown", onKeyDown);
  window.disposeLandingAuth = () => {
    loginTab.removeEventListener("click", onLoginClick);
    registerTab.removeEventListener("click", onRegisterClick);
    tabs.removeEventListener("keydown", onKeyDown);
  };
};

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", () => {
    window.initLandingPreview();
    window.initLandingAuth();
  }, { once: true });
} else {
  window.initLandingPreview();
  window.initLandingAuth();
}
