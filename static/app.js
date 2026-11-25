const $ = (id) => document.getElementById(id);
const form = $("form"), input = $("url"), go = $("go");

function show(el, on) { el.hidden = !on; }
function setError(msg) { $("error").textContent = msg || ""; show($("error"), !!msg); }

function checkStatus() {
  fetch("/api/status").then((r) => r.json()).then((s) => {
    // Show the Cloudflare link when the app is opened on the computer that runs it
    if (s.public_url && ["localhost", "127.0.0.1", "[::1]"].includes(location.hostname)) {
      $("share-link").href = $("share-link").textContent = s.public_url;
      $("share-copy").onclick = () => navigator.clipboard.writeText(s.public_url)
        .then(() => { $("share-copy").textContent = "Copied!"; });
      show($("share"), true);
    }
    const notes = [];
    if (!s.ffmpeg) notes.push("ffmpeg is missing, so MP3 audio and YouTube downloads won't work — run pip install -r requirements.txt again.");
    if (!s.js_runtime) notes.push("Deno is missing, so YouTube downloads may fail — run pip install -r requirements.txt again.");
    const yt = s.youtube || {};
    const setup = (yt.pot_server_setup || {}).status;
    const settingUp = ["not started", "downloading", "installing", "restarting"].includes(setup);
    if (yt.pot_provider && !yt.pot_provider.reachable) {
      if (settingUp) notes.push("Setting up YouTube bot protection (first start only, about a minute)…");
      else if (!yt.cookies) notes.push("YouTube bot protection couldn't start" +
        (yt.pot_server_setup && yt.pot_server_setup.error ? ` (${yt.pot_server_setup.error})` : "") +
        ", so YouTube may ask to \"confirm you're not a bot\" — see README.");
    }
    if (!s.logged_in) notes.push("Tip: to download Instagram stories or private posts, add login cookies (see README).");
    $("notice").textContent = notes.join(" ");
    show($("notice"), notes.length > 0);
    if (settingUp && yt.pot_provider && !yt.pot_provider.reachable) setTimeout(checkStatus, 5000);
  }).catch(() => {});
}
checkStatus();

$("paste").addEventListener("click", async () => {
  try { input.value = (await navigator.clipboard.readText()).trim(); input.focus(); }
  catch { input.focus(); }
});

// Allow sharing a link straight into the page: /?url=https://instagram.com/...
const preset = new URLSearchParams(location.search).get("url");
if (preset) { input.value = preset; setTimeout(() => form.requestSubmit(), 0); }

function button(href, label, ghost) {
  const a = document.createElement("a");
  a.href = href; a.textContent = label; a.className = "btn" + (ghost ? " ghost" : "");
  a.setAttribute("download", "");
  return a;
}

function fmtDuration(sec) {
  if (!sec) return "";
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
  return (h ? h + ":" + String(m).padStart(2, "0") : m) + ":" + String(s).padStart(2, "0");
}

function el(tag, props = {}, ...children) {
  const e = Object.assign(document.createElement(tag), props);
  e.append(...children);
  return e;
}

function renderYouTube(data) {
  $("owner").textContent = data.title || "YouTube video";
  $("caption").textContent = data.owner || "";
  show($("all"), false);
  const item = data.items[0];

  const thumb = el("div", { className: "thumb" }, el("img", { src: item.thumbnail, alt: "Video thumbnail" }));
  if (data.duration) thumb.append(el("span", { className: "dur", textContent: fmtDuration(data.duration) }));

  const select = el("select", { id: "quality" },
    el("option", { value: "", textContent: "Best available" }),
    ...data.qualities.map((q) => el("option", { value: q, textContent: q + "p" + (q >= 2160 ? " (4K)" : q >= 1440 ? " (2K)" : q >= 720 ? " HD" : "") })));
  const videoBtn = el("button", { type: "button", textContent: "⬇ Video MP4" });
  const audioBtn = el("button", { type: "button", className: "ghost", textContent: "🎵 Audio MP3" });
  const bar = el("div", { className: "bar" }, el("span"));
  const stage = el("span");
  const prog = el("div", { className: "progress", hidden: true }, stage, bar);

  async function start(mode) {
    videoBtn.disabled = audioBtn.disabled = true;
    setError(""); prog.hidden = false; stage.textContent = "Starting…"; bar.classList.add("indeterminate");
    try {
      const res = await fetch("/api/jobs", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token: data.token, index: 0, mode,
                               quality: mode === "video" && select.value ? Number(select.value) : null }),
      });
      let job = await res.json();
      if (!res.ok) throw new Error(job.detail || "Could not start download");
      while (job.status === "queued" || job.status === "running") {
        await new Promise((r) => setTimeout(r, 1000));
        const r = await fetch("/api/jobs/" + job.id);
        job = await r.json();
        if (!r.ok) throw new Error(job.detail || "Download failed");
        const pct = Math.round(job.progress * 100);
        const downloading = job.stage.startsWith("Downloading") && job.progress > 0 && job.progress < 1;
        bar.classList.toggle("indeterminate", !downloading);
        bar.firstChild.style.width = downloading ? pct + "%" : "";
        stage.textContent = job.stage + (downloading ? ` — ${pct}%` : "…");
      }
      if (job.status === "error") throw new Error(job.error);
      bar.classList.remove("indeterminate"); bar.firstChild.style.width = "100%";
      stage.textContent = "Done! Your download should start now.";
      button(job.file_url, "").click();
    } catch (err) {
      setError(err.message); prog.hidden = true;
    } finally {
      videoBtn.disabled = audioBtn.disabled = false;
    }
  }
  videoBtn.onclick = () => start("video");
  audioBtn.onclick = () => start("audio");

  const side = el("div", { className: "side" },
    el("label", {}, "Video quality", select), el("div", { className: "row" }, videoBtn, audioBtn), prog);
  $("grid").replaceChildren(el("div", { className: "yt" }, thumb, side));
  $("grid").className = "";
  show($("result"), true);
}

function render(data) {
  if (data.platform === "youtube") return renderYouTube(data);
  $("grid").className = "grid";
  $("owner").textContent = data.owner ? "@" + data.owner : "Instagram " + data.kind;
  $("caption").textContent = data.caption || "";
  const grid = $("grid");
  grid.replaceChildren();

  data.items.forEach((item, i) => {
    const card = document.createElement("div");
    card.className = "card";
    const media = document.createElement("div");
    media.className = "media";
    if (item.type === "video") {
      const v = document.createElement("video");
      v.controls = true; v.preload = "metadata"; v.playsInline = true; v.src = item.preview_url;
      media.append(v);
    } else {
      const img = document.createElement("img");
      img.loading = "lazy"; img.alt = "Instagram image " + (i + 1); img.src = item.preview_url;
      media.append(img);
    }
    const tag = document.createElement("div");
    tag.className = "tag";
    tag.textContent = `${item.type}${data.items.length > 1 ? " " + (i + 1) + "/" + data.items.length : ""}`;
    const actions = document.createElement("div");
    actions.className = "actions";
    actions.append(button(item.download_url, item.type === "video" ? "⬇ Video" : "⬇ Image"));
    if (item.audio_url) actions.append(button(item.audio_url, "🎵 Audio MP3", true));
    card.append(media, tag, actions);
    grid.append(card);
  });

  const all = $("all");
  show(all, data.items.length > 1);
  all.onclick = () => data.items.forEach((it, i) =>
    setTimeout(() => button(it.download_url, "").click(), i * 600));
  show($("result"), true);
}

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  setError(""); show($("result"), false); show($("loading"), true); go.disabled = true;
  try {
    const res = await fetch("/api/resolve", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url: input.value }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || `Request failed (${res.status})`);
    render(data);
  } catch (err) {
    setError(err.message);
  } finally {
    show($("loading"), false); go.disabled = false;
  }
});
