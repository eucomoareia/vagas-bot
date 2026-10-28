// Cloudflare Worker que recebe os cliques nos botões dos cards de vaga
// (Interactions Endpoint URL do app no Discord).
//   aplicar        -> card fica verde, marcado como "candidatura enviada"
//   desfazer:<cor> -> volta o card ao normal
//   ignorar        -> apaga a mensagem
// Variáveis (wrangler.toml): DISCORD_PUBLIC_KEY, DISCORD_APP_ID

const STATUS = "✅ Status";
const PREFIX = "✅ CANDIDATADO · ";

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
function buttons(url, applied, color = 0) {
  const row = applied
    ? [{ type: 2, style: 2, label: "Desfazer", emoji: { name: "↩️" }, custom_id: `desfazer:${color}` }]
    : [
        { type: 2, style: 3, label: "Me candidatei", emoji: { name: "✅" }, custom_id: "aplicar" },
        { type: 2, style: 4, label: "Sem interesse", emoji: { name: "🗑️" }, custom_id: "ignorar" },
      ];
  if (url) row.push({ type: 2, style: 5, label: "Abrir vaga", url });
  return [{ type: 1, components: row }];
}

function handleClick(i, env, ctx) {
  const id = i.data.custom_id;
  const embed = i.message.embeds?.[0] ?? {};
  const url = embed.url ?? "";
  const fields = (embed.fields ?? []).filter((f) => f.name !== STATUS);

  if (id === "aplicar") {
    const when = Math.floor(Date.now() / 1000);
    const updated = {
      ...embed,
      title: (PREFIX + (embed.title ?? "")).slice(0, 256),
      color: 0x2ecc71,
      fields: [{ name: STATUS, value: `Candidatura enviada <t:${when}:R> (<t:${when}:d>)`, inline: false }, ...fields],
    };
    return json({ type: 7, data: { embeds: [updated], components: buttons(url, true, embed.color ?? 0) } });
  }

  if (id.startsWith("desfazer:")) {
    const restored = {
      ...embed,
      title: (embed.title ?? "").replace(PREFIX, ""),
      color: Number(id.split(":")[1]) || 0x5865f2,
      fields,
    };
    return json({ type: 7, data: { embeds: [restored], components: buttons(url, false) } });
  }

  if (id === "ignorar") {
    // Responde "ok" na hora e apaga a mensagem logo depois, usando o token da interação
    ctx.waitUntil(
      new Promise((r) => setTimeout(r, 500)).then(() =>
        fetch(`https://discord.com/api/v10/webhooks/${env.DISCORD_APP_ID}/${i.token}/messages/@original`, {
          method: "DELETE",
        })
      )
    );
    return json({ type: 6 });
  }

  return json({ type: 6 });
}

export default {
  async fetch(req, env, ctx) {
    if (req.method !== "POST") return new Response("ok");
    const body = await req.text();
    if (!(await verify(req, body, env.DISCORD_PUBLIC_KEY))) return new Response("assinatura inválida", { status: 401 });
    const i = JSON.parse(body);
    if (i.type === 1) return json({ type: 1 }); // PING de validação do Discord
    if (i.type === 3) return handleClick(i, env, ctx); // clique em botão
    return json({ type: 6 });
  },
};
