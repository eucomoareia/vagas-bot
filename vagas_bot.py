#!/usr/bin/env python3
"""Busca vagas remotas júnior (Java/Spring Boot, Full Stack Web) no Brasil e envia ao Discord.

Uso:
    DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/... python3 vagas_bot.py
    python3 vagas_bot.py --dry-run     # só imprime, não envia nem grava histórico
    python3 vagas_bot.py --limpar      # apaga do canal as mensagens enviadas pelo bot

Só usa a biblioteca padrão. Vagas já enviadas ficam em seen.json (sem repetição).
"""
import argparse
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
SEEN_FILE = HERE / "seen.json"
MESSAGES_FILE = HERE / "messages.json"  # IDs das mensagens no Discord, para --limpar
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130 Safari/537.36"
BRT = timezone(timedelta(hours=-3))
DISCORD_UA = "DiscordBot (https://github.com/eucomoareia/vagas-bot, 1.0)"  # a API de bots recusa UA de navegador

# Termos usados nas fontes que aceitam busca por palavra-chave
TERMS = ["java", "spring boot", "full stack", "fullstack", "desenvolvedor", "react",
         "node", "javascript", "typescript", "angular", "back-end", "front-end"]

# --- Filtros ---------------------------------------------------------------
# A vaga precisa citar Java/Spring, web full stack ou ser de desenvolvimento genérico
STACK = re.compile(
    r"(java(?!script)|spring|kotlin|full[\s-]?stack|fullstack|react|angular|vue|node|"
    r"javascript|typescript|front[\s-]?end|back[\s-]?end|\bweb\b|desenvolv|developer|"
    r"programador|engenheir[oa] de software|software engineer|\bdev\b)",
    re.I,
)
JAVA_OR_WEB = re.compile(r"(java|spring|kotlin|full[\s-]?stack|react|angular|vue|node|typescript)", re.I)
OTHER_STACK = re.compile(
    r"(\.net|asp\.net|c#|\bphp\b|python|\bruby\b|\bgolang\b|\bgo\b|flutter|android|\bios\b|"
    r"delphi|abap|\bsap\b|salesforce|cobol|dados|\bdata\b|\bqa\b|testes?|tester|quality|"
    r"suporte|support|infra|devops|sre\b|mobile|unity|game|design|ux|ui\b|vendas|sales|marketing)",
    re.I,
)
JUNIOR = re.compile(
    r"(j[uú]nior|\bjr\b\.?|entry[\s-]?level|trainee|est[aá]gi|intern\b|internship|"
    r"iniciante|associate|graduate|primeiro emprego)",
    re.I,
)
SENIOR = re.compile(
    r"(s[eê]nior|\bsr\b\.?|pleno|\bpl\b|mid[\s-]?level|\blead\b|\bstaff\b|principal|\bhead\b|"
    r"\bii\b|\biii\b|\biv\b|expert|l[ií]der|especialista|specialist|coordenador|gerente|"
    r"manager|arquitet|architect|tech lead)",
    re.I,
)
BR_OK = re.compile(r"(brazil|brasil|latam|latin america|south america|americas|worldwide|anywhere|global)", re.I)
# Modelo de trabalho
NOT_REMOTE = re.compile(
    r"(h[ií]brid|hybrid|presencial|on[\s-]?site|in[\s-]?office|"
    r"dias? (no|na) (escrit|empresa|cliente|sede))",
    re.I,
)
FULLY_REMOTE = re.compile(
    r"(100\s*%\s*remot|totalmente remot|full[\s-]?remote|fully remote|remote[\s-]?first|"
    r"100\s*%\s*home\s*office|trabalho 100\s*%|modelo remoto|regime remoto)",
    re.I,
)


def get(url, headers=None, raw=False):
    req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = r.read().decode("utf-8", "replace")
    return data if raw else json.loads(data)


def strip_html(s):
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", s or "")).split())


def job(source, title, url, company="", location="", extra="", text="", level_ok=False,
        br_ok=True, remote_ok=False, level=""):
    """level_ok: a própria fonte já filtrou por júnior/estágio.
    br_ok: a vaga já é sabidamente aberta a quem está no Brasil.
    remote_ok: a fonte garante que é 100% remota (senão o texto é checado)."""
    return {"id": url.split("?")[0].rstrip("/"), "source": source, "title": " ".join(title.split()),
            "company": (company or "").strip(), "url": url, "location": location or "",
            "extra": extra or "", "text": text or "", "level_ok": level_ok, "br_ok": br_ok,
            "remote_ok": remote_ok, "level": level}


def warn(msg):
    print(f"[aviso] {msg}", file=sys.stderr)


# --- Fontes brasileiras ----------------------------------------------------
GITHUB_REPOS = ["backend-br/vagas", "frontendbr/vagas", "soujava/vagas-java",
                "react-brasil/vagas", "vuejs-br/vagas"]


def from_github():
    """Repos de vagas da comunidade BR (issues abertas e remotas)."""
    headers = {"Accept": "application/vnd.github+json"}
    if os.getenv("GITHUB_TOKEN"):
        headers["Authorization"] = f"Bearer {os.environ['GITHUB_TOKEN']}"
    out = []
    for repo in GITHUB_REPOS:
        for page in range(1, 6):
            q = urllib.parse.urlencode({"state": "open", "per_page": 100, "page": page})
            try:
                issues = get(f"https://api.github.com/repos/{repo}/issues?{q}", headers)
            except Exception as e:
                warn(f"{repo}: {e}")
                break
            for it in issues:
                if "pull_request" in it:
                    continue
                labels = [l["name"] for l in it.get("labels", [])]
                lab = " ".join(labels)
                if not re.search(r"remot", f"{it['title']} {lab}", re.I):
                    continue
                if re.search(r"h[ií]brido|presencial|alocado", lab, re.I):
                    continue
                junior_label = any(re.search(r"j[uú]nior|estagi", l, re.I) for l in labels)
                out.append(job(f"GitHub {repo}", it["title"], it["html_url"], location="Brasil",
                               extra=lab, text=f"{lab} {(it.get('body') or '')[:2000]}",
                               level_ok=junior_label))
            if len(issues) < 100:
                break
    return out


def from_remotar():
    """remotar.com.br: vagas remotas no Brasil (algumas são híbridas: type == 'hybrid')."""
    out = []
    for term in TERMS:
        for page in range(1, 11):
            q = urllib.parse.urlencode({"search": term, "page": page})
            try:
                d = get(f"https://api.remotar.com.br/jobs?{q}")
            except Exception as e:
                warn(f"remotar {term}: {e}")
                break
            for j in d.get("data", []):
                if j.get("type") != "remote":
                    continue
                tags = " ".join((t.get("tag") or {}).get("name", "") for t in j.get("jobTags") or [])
                company = (j.get("company") or {}).get("name") or j.get("companyDisplayName") or ""
                url = j.get("externalLink") or f"https://remotar.com.br/job/{j['id']}"
                out.append(job("Remotar", j["title"], url, company, "Remoto (Brasil)",
                               extra=j.get("subtitle") or "",
                               text=f"{j.get('subtitle') or ''} {tags} {strip_html(j.get('description'))[:2000]}",
                               level_ok=bool(re.search(r"j[uú]nior|est[aá]gi|trainee", tags, re.I))))
            if page >= d.get("meta", {}).get("last_page", 1):
                break
    return out


def from_programathor():
    out = []
    for skill in ("java", "spring", "react", "javascript", "typescript", "angular"):
        q = urllib.parse.urlencode({"expertise": "Júnior", "remote": "true"})
        try:
            h = get(f"https://programathor.com.br/jobs-{skill}?{q}", raw=True)
        except Exception as e:
            warn(f"programathor {skill}: {e}")
            continue
        for m in re.finditer(r'href="(/jobs/\d+[^"]*)">(.*?)</a>', h, re.S):
            card = m.group(2)
            t = re.search(r"<h3[^>]*>(.*?)</h3>", card, re.S)
            if not t or "Vencida" in card:
                continue
            spans = [strip_html(s) for s in re.findall(r"<span>(.*?)</span>", card, re.S)]
            if not any("remot" in s.lower() for s in spans):
                continue
            out.append(job("ProgramaThor", strip_html(t.group(1)), "https://programathor.com.br" + m.group(1),
                           spans[0] if spans else "", "Remoto (Brasil)",
                           extra=" · ".join(spans[2:4]), level_ok=True, level="Júnior"))
    return out


def from_linkedin():
    """Busca pública (sem login) do LinkedIn: Brasil, remoto, estágio + júnior, últimos 30 dias.
    Os filtros do LinkedIn vazam, então cada vaga é conferida depois em linkedin_details()."""
    out = []
    for term in TERMS:
        for start in range(0, 100, 10):
            q = urllib.parse.urlencode({"keywords": term, "geoId": "106057199", "f_WT": "2",
                                        "f_E": "1,2", "f_TPR": "r2592000", "start": start})
            try:
                h = get(f"https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search?{q}", raw=True)
            except Exception as e:
                warn(f"linkedin {term}: {e}")
                break
            cards = h.split("base-card relative")[1:]
            for c in cards:
                t = re.search(r'base-search-card__title">\s*(.*?)\s*<', c, re.S)
                u = re.search(r'href="(https://[a-z]+\.linkedin\.com/jobs/view/[^"?]+)', c)
                if not t or not u:
                    continue
                co = re.search(r'base-search-card__subtitle">.*?>\s*(.*?)\s*<', c, re.S)
                loc = re.search(r'job-search-card__location">\s*(.*?)\s*<', c, re.S)
                out.append(job("LinkedIn", html.unescape(t.group(1)), u.group(1),
                               html.unescape(co.group(1)) if co else "",
                               html.unescape(loc.group(1)) if loc else "Brasil", level_ok=True))
            if len(cards) < 10:
                break
            time.sleep(1)
    return out


def linkedin_details(j):
    """Abre a vaga do LinkedIn e preenche nível e descrição. False = descartar."""
    m = re.search(r"(\d{8,})$", j["id"])
    if not m:
        return True
    try:
        h = get(f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{m.group(1)}", raw=True)
    except Exception as e:
        warn(f"linkedin detalhe {m.group(1)}: {e}")
        return True  # sem detalhe, confia no filtro de remoto da busca
    crit = dict(re.findall(r'job-criteria-subheader">\s*(.*?)\s*<.*?job-criteria-text[^>]*>\s*(.*?)\s*<', h, re.S))
    level = crit.get("Seniority level", "")
    if re.search(r"mid-senior|director|executive", level, re.I):
        return False
    j["level"] = {"Entry level": "Entry level", "Internship": "Estágio"}.get(level, "")
    d = re.search(r'show-more-less-html__markup[^>]*>(.*?)</div>', h, re.S)
    j["text"] = strip_html(d.group(1) if d else "")[:4000]
    return True


# --- Fontes internacionais (filtradas por Brasil/LATAM) -------------------
def from_himalayas():
    out = []
    for term in TERMS:
        for page in range(1, 6):
            q = urllib.parse.urlencode({"q": term, "country": "Brazil",
                                        "seniority": "Entry-level", "page": page})
            try:
                d = get(f"https://himalayas.app/jobs/api/search?{q}")
            except Exception as e:
                warn(f"himalayas {term}: {e}")
                break
            jobs = d.get("jobs", [])
            for j in jobs:
                locs = ", ".join(j.get("locationRestrictions") or []) or "Worldwide"
                out.append(job("Himalayas", j["title"], j["applicationLink"], j.get("companyName", ""),
                               locs, text=strip_html(j.get("excerpt")), level_ok=True,
                               remote_ok=True, level="Entry level"))
            if len(jobs) < 20:
                break
    return out


def from_remotive():
    out = []
    for term in ("java", "spring", "full stack", "junior", "react", "node"):
        try:
            d = get("https://remotive.com/api/remote-jobs?" + urllib.parse.urlencode(
                {"search": term, "category": "software-dev"}))
        except Exception as e:
            warn(f"remotive {term}: {e}")
            continue
        for j in d.get("jobs", []):
            loc = j.get("candidate_required_location", "")
            out.append(job("Remotive", j["title"], j["url"], j["company_name"], loc,
                           text=f"{' '.join(j.get('tags') or [])} {strip_html(j.get('description'))[:600]}",
                           br_ok=bool(BR_OK.search(loc)), remote_ok=True))
    return out


def from_jobicy():
    out = []
    for geo in ("brazil", "latam", "anywhere"):
        for tag in ("java", "full-stack", "spring", "react", "node", "javascript", "typescript"):
            try:
                d = get(f"https://jobicy.com/api/v2/remote-jobs?count=50&geo={geo}&tag={tag}")
            except Exception as e:
                warn(f"jobicy {geo}/{tag}: {e}")
                continue
            for j in d.get("jobs", []):
                out.append(job("Jobicy", j["jobTitle"], j["url"], j.get("companyName", ""),
                               j.get("jobGeo", ""),
                               text=f"{j.get('jobLevel', '')} {strip_html(j.get('jobExcerpt'))}",
                               br_ok=bool(BR_OK.search(j.get("jobGeo", ""))), remote_ok=True))
    return out


def from_remoteok():
    out = []
    for tag in ("java", "junior", "full-stack", "react", "node"):
        try:
            d = get(f"https://remoteok.com/api?tag={tag}")
        except Exception as e:
            warn(f"remoteok {tag}: {e}")
            continue
        for j in d[1:]:
            loc = j.get("location") or ""
            out.append(job("RemoteOK", j.get("position", ""), j["url"], j.get("company", ""), loc,
                           text=" ".join(j.get("tags") or []), br_ok=bool(BR_OK.search(loc)),
                           remote_ok=True))
        time.sleep(1)
    return out


SOURCES = [from_github, from_remotar, from_programathor, from_linkedin,
           from_himalayas, from_remotive, from_jobicy, from_remoteok]


# --- Filtragem -------------------------------------------------------------
def matches(j):
    """Filtro pelo título/fonte (barato, antes de abrir detalhes)."""
    title = j["title"]
    head = f"{title} {j['extra']} {j['text'][:400]}"
    if not j["br_ok"]:
        return False
    if not STACK.search(title):
        return False
    # Outra stack no título (ex.: .NET, Python, QA) só passa se citar Java/web também
    if OTHER_STACK.search(title) and not JAVA_OR_WEB.search(title):
        return False
    if SENIOR.search(title) and not JUNIOR.search(title):
        return False
    if not j["level_ok"] and not JUNIOR.search(head):
        return False
    if NOT_REMOTE.search(title) and not FULLY_REMOTE.search(title):
        return False
    return True


def is_remote(j):
    """Descarta vagas cujo texto fala em híbrido/presencial sem dizer que é 100% remota."""
    if j["remote_ok"]:
        return True
    text = f"{j['extra']} {j['text']}"
    return not NOT_REMOTE.search(text) or bool(FULLY_REMOTE.search(text))


def collect(seen):
    by_id = {}
    for src in SOURCES:
        try:
            jobs = src()
        except Exception as e:
            warn(f"{src.__name__}: {e}")
            continue
        n = 0
        for j in jobs:
            if j["id"] not in by_id and j["id"] not in seen and matches(j):
                by_id[j["id"]] = j
                n += 1
        print(f"{src.__name__}: {len(jobs)} lidas, {n} candidatas", file=sys.stderr)

    out, keys, dropped = [], set(), 0
    for j in by_id.values():
        # Mesma vaga em fontes diferentes: dedupe por título+empresa
        k = (re.sub(r"\W+", "", j["title"].lower()), re.sub(r"\W+", "", j["company"].lower()))
        if k in keys:
            continue
        keys.add(k)
        if j["source"] == "LinkedIn":
            ok = linkedin_details(j)
            time.sleep(0.7)
            if not ok:
                dropped += 1
                continue
        if not is_remote(j):
            dropped += 1
            continue
        out.append(j)
    print(f"{dropped} descartadas por não serem remotas/júnior após checar a descrição", file=sys.stderr)
    return out


# --- Apresentação ----------------------------------------------------------
# (nome, emoji, cor, regex) — a primeira que casar define a categoria
CATEGORIES = [
    ("Java", "☕", 0xE76F00, re.compile(r"java(?!script)|spring|kotlin", re.I)),
    ("Full Stack", "🧩", 0x9B59B6, re.compile(r"full[\s-]?stack|fullstack", re.I)),
    ("Front-end", "🎨", 0x3498DB, re.compile(r"front[\s-]?end|react|angular|vue", re.I)),
    ("Back-end", "⚙️", 0x2ECC71, re.compile(r"back[\s-]?end|node|api", re.I)),
    ("Dev geral", "💻", 0x95A5A6, re.compile(r".")),
]
TECHS = [("Java", r"java(?!script)"), ("Spring", r"spring"), ("Kotlin", r"kotlin"),
         ("React", r"react"), ("Angular", r"angular"), ("Vue", r"\bvue"), ("Node", r"node"),
         ("TypeScript", r"typescript"), ("JavaScript", r"javascript"), ("SQL", r"\bsql\b")]


def category(j):
    for name, emoji, color, rx in CATEGORIES:
        if rx.search(j["title"]):
            return name, emoji, color
    return CATEGORIES[-1][:3]


def techs(j):
    blob = f"{j['title']} {j['extra']} {j['text'][:1500]}"
    return [n for n, rx in TECHS if re.search(rx, blob, re.I)]


def level(j):
    t = j["title"]
    if re.search(r"est[aá]gi|intern", t, re.I):
        return "Estágio"
    if re.search(r"trainee", t, re.I):
        return "Trainee"
    if re.search(r"j[uú]nior|\bjr\b", t, re.I):
        return "Júnior"
    return j["level"] or "Júnior"


def embed(j, n, total, today):
    name, emoji, color = category(j)
    fields = [
        {"name": "📊 Nível", "value": level(j), "inline": True},
        {"name": "🏠 Modelo", "value": "Remoto", "inline": True},
        {"name": "🔎 Fonte", "value": j["source"].replace("GitHub ", "GitHub · "), "inline": True},
    ]
    t = techs(j)
    if t:
        fields.insert(0, {"name": "🛠️ Stack", "value": " · ".join(t), "inline": False})
    if j["location"] and j["location"] not in ("Brasil", "Remoto (Brasil)"):
        fields.append({"name": "📍 Local", "value": j["location"][:100], "inline": True})
    e = {"title": f"{emoji} {j['title']}"[:250], "url": j["url"], "color": color,
         "fields": fields, "footer": {"text": f"{name} · vaga {n}/{total} · {today}"}}
    if j["company"]:
        e["author"] = {"name": f"🏢 {j['company']}"[:250]}
    return e


def header(jobs, today):
    counts = {}
    for j in jobs:
        name, emoji, _ = category(j)
        counts[(name, emoji)] = counts.get((name, emoji), 0) + 1
    resumo = " · ".join(f"{e} {n}: **{c}**" for (n, e), c in counts.items())
    return (f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📅 **{today} — {len(jobs)} vaga(s) júnior remota(s) nova(s)**\n{resumo}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━")


# --- Discord ---------------------------------------------------------------
def discord(url, method="POST", payload=None, headers=None):
    """Chama a API do Discord com retry em caso de rate limit (429)."""
    data = json.dumps(payload).encode() if payload is not None else None
    for _ in range(5):
        req = urllib.request.Request(url, data=data, method=method,
                                     headers={"Content-Type": "application/json", "User-Agent": DISCORD_UA,
                                              **(headers or {})})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                body = r.read()
            return json.loads(body) if body else None
        except urllib.error.HTTPError as e:
            if e.code != 429:
                raise
            time.sleep(float(json.loads(e.read() or b"{}").get("retry_after", 2)) + 0.5)
    raise RuntimeError("Discord continuou limitando (429)")


def send(webhook, payload):
    """Envia e devolve o ID da mensagem (wait=true faz o Discord responder com ela)."""
    sep = "&" if "?" in webhook else "?"
    return discord(f"{webhook}{sep}wait=true", payload=payload)["id"]


class Bot:
    """Envio pelo bot do Discord: permite botões nos cards (o webhook comum não permite)."""
    API = "https://discord.com/api/v10"

    def __init__(self, token, channel):
        self.auth = {"Authorization": f"Bot {token}"}
        self.channel = channel

    def call(self, path, method="POST", payload=None):
        return discord(f"{self.API}{path}", method, payload, self.auth)

    def send(self, payload):
        return self.call(f"/channels/{self.channel}/messages", payload=payload)["id"]

    def clean(self):
        """Apaga todas as mensagens de bots/webhooks do canal (inclusive as antigas do webhook)."""
        ids, before = [], None
        while True:
            q = "limit=100" + (f"&before={before}" if before else "")
            msgs = self.call(f"/channels/{self.channel}/messages?{q}", "GET")
            if not msgs:
                break
            ids += [m["id"] for m in msgs if m["author"].get("bot") or m.get("webhook_id")]
            before = msgs[-1]["id"]
        # bulk-delete só aceita mensagens com menos de 14 dias
        cutoff = (int(time.time() * 1000) - 1420070400000 - 13 * 86400 * 1000) << 22
        recent = [i for i in ids if int(i) > cutoff]
        old = [i for i in ids if int(i) <= cutoff]
        for k in range(0, len(recent), 100):
            part = recent[k:k + 100]
            if len(part) == 1:
                old += part
            else:
                self.call(f"/channels/{self.channel}/messages/bulk-delete", payload={"messages": part})
        for i in old:
            self.call(f"/channels/{self.channel}/messages/{i}", "DELETE")
            time.sleep(0.5)
        MESSAGES_FILE.write_text("[]")
        print(f"{len(ids)} mensagem(ns) apagada(s)")


def buttons(j):
    # Os cliques são tratados em discord-botoes/worker.js
    row = [{"type": 2, "style": 3, "label": "Me candidatei", "emoji": {"name": "✅"}, "custom_id": "aplicar"},
           {"type": 2, "style": 4, "label": "Sem interesse", "emoji": {"name": "🗑️"}, "custom_id": "ignorar"}]
    if len(j["url"]) <= 512:
        row.append({"type": 2, "style": 5, "label": "Abrir vaga", "url": j["url"]})
    return [{"type": 1, "components": row}]


def get_bot():
    token, channel = os.getenv("DISCORD_BOT_TOKEN"), os.getenv("DISCORD_CHANNEL_ID")
    return Bot(token, channel) if token and channel else None


def load_messages():
    return json.loads(MESSAGES_FILE.read_text()) if MESSAGES_FILE.exists() else []


def clean(webhook):
    """Apaga do canal todas as mensagens que o bot enviou (o webhook só apaga as próprias)."""
    ids = load_messages()
    left = []
    for i, mid in enumerate(ids):
        try:
            discord(f"{webhook.split('?')[0]}/messages/{mid}", method="DELETE")
        except urllib.error.HTTPError as e:
            if e.code != 404:  # 404 = já tinha sido apagada à mão
                warn(f"não consegui apagar {mid}: {e}")
                left.append(mid)
        if i % 4 == 3:
            time.sleep(1.5)
    MESSAGES_FILE.write_text(json.dumps(left))
    print(f"{len(ids) - len(left)} mensagem(ns) apagada(s)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limpar", action="store_true", help="apaga do canal as mensagens enviadas pelo bot")
    args = ap.parse_args()

    bot = get_bot()
    if args.limpar:
        if bot:
            bot.clean()
        else:
            clean(os.getenv("DISCORD_WEBHOOK_URL") or sys.exit("Defina DISCORD_WEBHOOK_URL"))
        return

    seen = set(json.loads(SEEN_FILE.read_text())) if SEEN_FILE.exists() else set()
    new = collect(seen)
    order = [c[0] for c in CATEGORIES]
    new.sort(key=lambda j: (order.index(category(j)[0]), j["title"].lower()))
    print(f"{len(new)} vaga(s) nova(s)")
    today = datetime.now(BRT).strftime("%d/%m/%Y")

    if args.dry_run:
        for n, j in enumerate(new, 1):
            print(f"- {category(j)[1]} [{j['source']}] {j['title']} | {j['company']} | "
                  f"{level(j)} | {', '.join(techs(j))}\n  {j['url']}")
        return

    webhook = os.getenv("DISCORD_WEBHOOK_URL")
    if not bot and not webhook:
        sys.exit("Defina DISCORD_BOT_TOKEN + DISCORD_CHANNEL_ID (com botões) ou DISCORD_WEBHOOK_URL")
    if not new:
        return
    sent, msg_ids = set(), []
    try:
        if bot:
            # Uma mensagem por vaga, cada uma com seus botões
            msg_ids.append(bot.send({"content": header(new, today)}))
            for n, j in enumerate(new, 1):
                msg_ids.append(bot.send({"embeds": [embed(j, n, len(new), today)], "components": buttons(j)}))
                sent.add(j["id"])
                time.sleep(1.1)
        else:
            msg_ids.append(send(webhook, {"content": header(new, today)}))
            for i in range(0, len(new), 10):  # máx. 10 embeds por mensagem
                chunk = new[i:i + 10]
                embeds = [embed(j, i + k + 1, len(new), today) for k, j in enumerate(chunk)]
                msg_ids.append(send(webhook, {"embeds": embeds}))
                sent |= {j["id"] for j in chunk}
                time.sleep(1.5)
    finally:
        # grava o que já foi enviado mesmo se der erro no meio
        if msg_ids:
            SEEN_FILE.write_text(json.dumps(sorted(seen | sent), indent=0))
            MESSAGES_FILE.write_text(json.dumps(load_messages() + msg_ids))


if __name__ == "__main__":
    main()
