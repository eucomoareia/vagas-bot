// Cloudflare Worker que recebe os cliques nos botões dos cards de vaga
// (Interactions Endpoint URL do app no Discord).
//   aplicar        -> card fica verde, status "Candidatei"
//   ignorar        -> card fica vermelho, status "Sem interesse"
//   desfazer:<cor> -> volta o card ao normal
// GET /spacer.png -> imagem transparente que deixa todos os cards com a mesma largura
// Variáveis (wrangler.toml): DISCORD_PUBLIC_KEY, DISCORD_APP_ID

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

export default {
  async fetch(req, env) {
    if (req.method === "GET" && new URL(req.url).pathname === "/spacer.png") {
      return new Response(SPACER, { headers: { "Content-Type": "image/png", "Cache-Control": "public, max-age=31536000" } });
    }
    if (req.method !== "POST") return new Response("ok");
    const body = await req.text();
    if (!(await verify(req, body, env.DISCORD_PUBLIC_KEY))) return new Response("assinatura inválida", { status: 401 });
    const i = JSON.parse(body);
    if (i.type === 1) return json({ type: 1 }); // PING de validação do Discord
    if (i.type === 3) return handleClick(i); // clique em botão
    return json({ type: 6 });
  },
};
