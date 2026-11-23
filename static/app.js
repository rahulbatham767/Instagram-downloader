const $ = (id) => document.getElementById(id);
const form = $("form"), input = $("url"), go = $("go");

function show(el, on) { el.hidden = !on; }
function setError(msg) { $("error").textContent = msg || ""; show($("error"), !!msg); }

fetch("/api/status").then((r) => r.json()).then((s) => {
  const notes = [];
  if (!s.logged_in) notes.push("Stories and private posts need login cookies — see README to enable them.");
  if (!s.ffmpeg) notes.push("ffmpeg is not installed, so MP3 audio extraction is disabled.");
  if (notes.length) { $("notice").textContent = notes.join(" "); show($("notice"), true); }
}).catch(() => {});

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

function render(data) {
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
