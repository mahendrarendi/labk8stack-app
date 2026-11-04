(() => {
  const root = document.querySelector(".challenge");
  if (!root) return;

  const slug = root.dataset.challenge;
  const isActive = root.dataset.active === "true";
  const editor = document.getElementById("editor");
  const output = document.getElementById("console");
  const timerEl = document.getElementById("timer");
  const btnApply = document.getElementById("btn-apply");
  const btnVerify = document.getElementById("btn-verify");
  const btnReset = document.getElementById("btn-reset");

  const pad = (n) => String(n).padStart(2, "0");
  const clock = (seconds) => {
    const s = Math.max(0, seconds);
    return `${pad(Math.floor(s / 60))}:${pad(s % 60)}`;
  };

  function print(text, kind) {
    output.textContent = text || "(tidak ada output)";
    output.classList.remove("ok", "bad");
    if (kind) output.classList.add(kind);
  }

  function disableActions() {
    [btnApply, btnVerify, btnReset].forEach((b) => b && (b.disabled = true));
  }

  // ---- timer, dihitung dari sisa waktu yang dikirim server ----
  if (isActive && timerEl) {
    let left = Number(root.dataset.remaining || 0);
    let stopped = false;
    const paint = () => {
      timerEl.textContent = clock(left);
      if (left <= 0) {
        stopped = true;
        timerEl.classList.add("expired");
        print("Waktu habis. Skor attempt ini 0. Muat ulang halaman untuk mulai attempt baru.", "bad");
        disableActions();
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
      data = { ok: false, output: `respons tidak valid (HTTP ${response.status})` };
    }
    return { response, data };
  }

  btnApply?.addEventListener("click", async () => {
    btnApply.disabled = true;
    print("$ kubectl apply -f manifest.yaml\nmenunggu cluster...");
    try {
      const { response, data } = await postJSON(`/api/challenge/${slug}/apply`, {
        yaml: editor.value,
      });
      print(data.output || "(tidak ada output)", response.ok ? "ok" : "bad");
    } catch (err) {
      print(`gagal menghubungi server: ${err}`, "bad");
    } finally {
      btnApply.disabled = false;
    }
  });

  btnVerify?.addEventListener("click", async () => {
    btnVerify.disabled = true;
    print("memverifikasi kondisi cluster...");
    try {
      const { data } = await postJSON(`/api/challenge/${slug}/verify`, {
        yaml: editor.value,
      });

      if (data.passed) {
        print(`✔ LULUS — ${data.score} poin (percobaan ke-${data.tries}, sisa ${clock(data.remaining)}).\nMemuat ulang...`, "ok");
        setTimeout(() => window.location.reload(), 1600);
        return;
      }

      if (data.expired) {
        print(`✘ ${data.message}`, "bad");
        disableActions();
        return;
      }

      print(`✘ ${data.message}\nPercobaan: ${data.tries} · sisa waktu ${clock(data.remaining)}`, "bad");
    } catch (err) {
      print(`gagal memverifikasi: ${err}`, "bad");
    } finally {
      if (btnVerify) btnVerify.disabled = false;
    }
  });

  btnReset?.addEventListener("click", async () => {
    btnReset.disabled = true;
    try {
      const { data } = await postJSON(`/api/challenge/${slug}/reset`);
      print(data.output || "sandbox direset", "ok");
    } catch (err) {
      print(`gagal reset: ${err}`, "bad");
    } finally {
      btnReset.disabled = false;
    }
  });
})();
