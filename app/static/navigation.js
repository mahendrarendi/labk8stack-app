(() => {
  let pending = null;

  function fallback(target, options) {
    if (!options.method || options.method === "GET") {
      window.location.assign(target.href);
      return;
    }
    let notice = document.getElementById("navigation-error");
    if (!notice) {
      notice = document.createElement("p");
      notice.id = "navigation-error";
      notice.className = "alert";
      notice.setAttribute("role", "alert");
      document.querySelector("main.wrap")?.prepend(notice);
    }
    notice.textContent = "The page could not load. Please try again.";
  }

  async function softNavigate(url, options = {}) {
    const target = new URL(url, window.location.href);
    if (target.origin !== window.location.origin) {
      window.location.assign(target.href);
      return;
    }

    pending?.abort();
    const controller = new AbortController();
    pending = controller;
    document.body.classList.add("is-navigating");

    try {
      const response = await fetch(target.href, {
        method: options.method || "GET",
        body: options.body,
        credentials: "same-origin",
        headers: { "X-LabK8stack-Navigation": "1" },
        signal: controller.signal,
      });
      if (!response.headers.get("content-type")?.includes("text/html")) {
        fallback(target, options);
        return;
      }

      const page = new DOMParser().parseFromString(await response.text(), "text/html");
      const nextMain = page.querySelector("main.wrap");
      const nextNav = page.querySelector("nav.nav");
      if (!nextMain || !nextNav) {
        fallback(target, options);
        return;
      }

      window.disposeChallengePage?.();
      window.disposeChallengePage = null;
      window.disposeLandingPreview?.();
      window.disposeLandingPreview = null;
      window.disposeLandingAuth?.();
      window.disposeLandingAuth = null;
      document.querySelector("main.wrap").replaceWith(nextMain);
      document.querySelector("nav.nav").replaceWith(nextNav);
      document.title = page.title;

      const finalUrl = new URL(response.url);
      const historyMethod = options.replace || finalUrl.href === window.location.href
        ? "replaceState" : "pushState";
      window.history[historyMethod]({}, "", finalUrl.href);
      window.initChallengePage?.();
      window.initLandingPreview?.();
      window.initLandingAuth?.();
      window.scrollTo(0, 0);
      nextMain.querySelector("[autofocus]")?.focus();
    } catch (error) {
      if (error.name !== "AbortError") fallback(target, options);
    } finally {
      if (pending === controller) {
        pending = null;
        document.body.classList.remove("is-navigating");
      }
    }
  }

  window.softNavigate = softNavigate;

  document.addEventListener("click", (event) => {
    const link = event.target instanceof Element
      ? event.target.closest("a[href]") : null;
    if (!link || event.defaultPrevented || event.button !== 0 ||
        event.metaKey || event.ctrlKey || event.shiftKey || event.altKey ||
        link.target || link.hasAttribute("download")) return;
    const target = new URL(link.href);
    if (target.origin !== window.location.origin ||
        (target.pathname === window.location.pathname &&
         target.search === window.location.search && target.hash)) return;
    event.preventDefault();
    softNavigate(target.href);
  });

  document.addEventListener("submit", (event) => {
    const form = event.target;
    if (!(form instanceof HTMLFormElement) ||
        form.method.toLowerCase() !== "post" || form.dataset.fullReload === "true") return;
    const action = new URL(form.action, window.location.href);
    if (action.origin !== window.location.origin) return;
    event.preventDefault();
    const data = new FormData(form);
    if (event.submitter?.name) data.set(event.submitter.name, event.submitter.value);
    softNavigate(action.href, { method: "POST", body: data });
  });

  window.addEventListener("popstate", () => {
    softNavigate(window.location.href, { replace: true });
  });
})();
