// Cloudflare Worker que recebe os cliques nos botões dos cards de vaga
// (Interactions Endpoint URL do app no Discord).
//   aplicar        -> card fica verde, status "Candidatei"
//   ignorar        -> card fica vermelho, status "Sem interesse"
//   desfazer:<cor> -> volta o card ao normal
// GET /spacer.png -> imagem transparente que deixa todos os cards com a mesma largura
// /search -> dispara a busca no GitHub Actions   /wipe -> limpa o canal
// Variáveis (wrangler.toml): DISCORD_PUBLIC_KEY, DISCORD_APP_ID, GITHUB_REPO
// Secrets (wrangler secret put): DISCORD_BOT_TOKEN, GITHUB_TOKEN

const STATUS = "📌 Status";
const MARKS = /^(✅|❌) /;
const STATES = {
  aplicar: { mark: "✅", color: 0x2ecc71, status: (t) => `✅ Candidatei <t:${t}:d>` },
  ignorar: { mark: "❌", color: 0xe74c3c, status: () => "❌ Sem interesse" },
};

// PNG 1000x1 transparente
const SPACER = Uint8Array.from(
  atob("iVBORw0KGgoAAAANSUhEUgAAA+gAAAABCAYAAABNAIQzAAAAG0lEQVR42u3BMQEAAADCoPVPbQwfoAAAAIC7AQ+hAAEipZISAAAAAElFTkSuQmCC"),
  (c) => c.charCodeAt(0)
);

const hex = (s) => Uint8Array.from(s.match(/../g).map((h) => parseInt(h, 16)));

async function verify(req, body, publicKey) {
  const sig = req.headers.get("x-signature-ed25519");
  const ts = req.headers.get("x-signature-timestamp");
  if (!sig || !ts) return false;
  const key = await crypto.subtle.importKey("raw", hex(publicKey), { name: "Ed25519" }, false, ["verify"]);
  return crypto.subtle.verify("Ed25519", key, hex(sig), new TextEncoder().encode(ts + body));
}

const json = (data) => new Response(JSON.stringify(data), { headers: { "Content-Type": "application/json" } });

// Mesmos botões que o vagas_bot.py coloca no card
function buttons(url, marked, color = 0) {
  const row = marked
    ? [{ type: 2, style: 2, label: "Desfazer", emoji: { name: "↩️" }, custom_id: `desfazer:${color}` }]
    : [
        { type: 2, style: 3, label: "Me candidatei", emoji: { name: "✅" }, custom_id: "aplicar" },
        { type: 2, style: 4, label: "Sem interesse", emoji: { name: "❌" }, custom_id: "ignorar" },
      ];
  if (url) row.push({ type: 2, style: 5, label: "Abrir vaga", url });
  return [{ type: 1, components: row }];
}

// Troca o valor do campo Status mantendo a posição (o card não muda de tamanho)
function withStatus(fields, value) {
  const out = fields.map((f) => (f.name === STATUS ? { ...f, value } : f));
  return out.some((f) => f.name === STATUS) ? out : [...out, { name: STATUS, value, inline: true }];
}

function handleClick(i) {
  const id = i.data.custom_id;
  const embed = i.message.embeds?.[0] ?? {};
  const url = embed.url ?? "";
  const title = (embed.title ?? "").replace(MARKS, "");
  const fields = embed.fields ?? [];

  const state = STATES[id];
  if (state) {
    const now = Math.floor(Date.now() / 1000);
    const updated = {
      ...embed,
      title: `${state.mark} ${title}`.slice(0, 256),
      color: state.color,
      fields: withStatus(fields, state.status(now)),
    };
    return json({ type: 7, data: { embeds: [updated], components: buttons(url, true, embed.color ?? 0) } });
  }

  if (id.startsWith("desfazer:")) {
    const restored = {
      ...embed,
      title,
      color: Number(id.split(":")[1]) || 0x5865f2,
      fields: withStatus(fields, "🆕 Nova"),
    };
    return json({ type: 7, data: { embeds: [restored], components: buttons(url, false) } });
  }

  return json({ type: 6 });
}

// --- Comandos /search e /wipe -----------------------------------------------
const API = "https://discord.com/api/v10";
const ephemeral = (content) => json({ type: 4, data: { content, flags: 64 } });

async function search(env) {
  // Dispara o workflow do GitHub Actions; ele posta as vagas no canal quando terminar
  const r = await fetch(`https://api.github.com/repos/${env.GITHUB_REPO}/actions/workflows/vagas.yml/dispatches`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${env.GITHUB_TOKEN}`,
      Accept: "application/vnd.github+json",
      "User-Agent": "vagas-bot-worker",
    },
    body: JSON.stringify({ ref: "main", inputs: { avisar: true } }),
  });
  return r.ok
    ? ephemeral("🔎 Busca iniciada! As vagas novas aparecem aqui em uns 5 minutos.")
    : ephemeral(`⚠️ Não consegui iniciar a busca (GitHub respondeu ${r.status}).`);
}

// Apaga todas as mensagens de bots/webhooks do canal (as suas ficam)
async function wipe(channel, env) {
  const bot = { Authorization: `Bot ${env.DISCORD_BOT_TOKEN}`, "Content-Type": "application/json" };
  const ids = [];
  let before = "";
  for (let page = 0; page < 10; page++) {
    const r = await fetch(`${API}/channels/${channel}/messages?limit=100${before && `&before=${before}`}`, { headers: bot });
    const msgs = r.ok ? await r.json() : [];
    if (!msgs.length) break;
    ids.push(...msgs.filter((m) => m.author?.bot || m.webhook_id).map((m) => m.id));
    before = msgs[msgs.length - 1].id;
  }
  // bulk-delete só aceita mensagens com menos de 14 dias; as antigas vão uma a uma
  const cutoff = (BigInt(Date.now() - 1420070400000 - 13 * 86400000)) << 22n;
  const recent = ids.filter((id) => BigInt(id) > cutoff);
  const old = ids.filter((id) => BigInt(id) <= cutoff);
  let deleted = 0;
  for (let k = 0; k < recent.length; k += 100) {
    const part = recent.slice(k, k + 100);
    if (part.length === 1) { old.push(part[0]); continue; }
    const r = await fetch(`${API}/channels/${channel}/messages/bulk-delete`, {
      method: "POST", headers: bot, body: JSON.stringify({ messages: part }),
    });
    if (r.ok) deleted += part.length;
  }
  for (const id of old.slice(0, 25)) { // limite de subrequests do Worker
    const r = await fetch(`${API}/channels/${channel}/messages/${id}`, { method: "DELETE", headers: bot });
    if (r.ok) deleted++;
  }
  const rest = Math.max(0, old.length - 25);
  return `🧹 ${deleted} mensagem(ns) apagada(s).` + (rest ? ` Faltaram ${rest} antigas, rode /wipe de novo.` : "");
}

function handleCommand(i, env, ctx) {
  if (i.data.name === "search") return search(env);
  if (i.data.name === "wipe") {
    // Responde "pensando..." só para você e edita a resposta quando terminar
    ctx.waitUntil(
      wipe(i.channel_id, env).then((content) =>
        fetch(`${API}/webhooks/${env.DISCORD_APP_ID}/${i.token}/messages/@original`, {
          method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ content }),
        })
      )
    );
    return json({ type: 5, data: { flags: 64 } });
  }
  return ephemeral("Comando desconhecido.");
}

export default {
  async fetch(req, env, ctx) {
    if (req.method === "GET" && new URL(req.url).pathname === "/spacer.png") {
      return new Response(SPACER, { headers: { "Content-Type": "image/png", "Cache-Control": "public, max-age=31536000" } });
    }
    if (req.method !== "POST") return new Response("ok");
    const body = await req.text();
    if (!(await verify(req, body, env.DISCORD_PUBLIC_KEY))) return new Response("assinatura inválida", { status: 401 });
    const i = JSON.parse(body);
    if (i.type === 1) return json({ type: 1 }); // PING de validação do Discord
    if (i.type === 2) return handleCommand(i, env, ctx); // /search, /wipe
    if (i.type === 3) return handleClick(i); // clique em botão
    return json({ type: 6 });
  },
};
