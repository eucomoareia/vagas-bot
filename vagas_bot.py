#!/usr/bin/env python3
"""Busca vagas remotas júnior (Java/Spring Boot, Full Stack) no Brasil e envia ao Discord.

Uso:
    DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/... python3 vagas_bot.py
    python3 vagas_bot.py --dry-run     # só imprime, não envia nem grava histórico

Só usa a biblioteca padrão. Vagas já enviadas ficam em seen.json (sem repetição).
"""
import argparse
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
SEEN_FILE = HERE / "seen.json"
UA = "Mozilla/5.0 (vagas-bot)"

# --- Filtros ---------------------------------------------------------------
TECH = re.compile(
    r"\b(java|spring|spring\s*boot|full[\s-]?stack|fullstack|kotlin|react|angular|vue|node|typescript)\b",
    re.I,
)
# Precisa citar Java/Spring OU full stack para entrar
CORE = re.compile(r"\b(java(?!script)|spring|full[\s-]?stack|fullstack)\b", re.I)
JUNIOR = re.compile(r"\b(j[uú]nior|jr\.?|junior|entry[\s-]?level|trainee|est[aá]gio|iniciante|pleno/j[uú]nior)\b", re.I)
OTHER_STACK = re.compile(r"(\.net|asp\.net|c#|\bphp\b|python|\bruby\b|\bgolang\b|\bgo\b|\bflutter\b|\bandroid\b|\bios\b)", re.I)
SENIOR =re.compile(r"\b(s[eê]nior|sr\.?|lead|staff|principal|head|especialista|specialist)\b", re.I)
REMOTE = re.compile(r"\b(remot[oa]|remote|home\s*office|anywhere)\b", re.I)
BR_OK = re.compile(r"\b(brazil|brasil|latam|latin america|worldwide|anywhere|global)\b", re.I)

GITHUB_REPOS = ["backend-br/vagas", "frontendbr/vagas"]


def get_json(url, headers=None):
    req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.load(r)


def job(source, title, company, url, location="", extra=""):
    return {"id": url, "source": source, "title": title.strip(), "company": company.strip(),
            "url": url, "location": location, "extra": extra}


# --- Fontes ----------------------------------------------------------------
def from_github():
    """Repos brasileiros de vagas (issues). Labels trazem nível e regime."""
    out = []
    headers = {"Accept": "application/vnd.github+json"}
    if os.getenv("GITHUB_TOKEN"):
        headers["Authorization"] = f"Bearer {os.environ['GITHUB_TOKEN']}"
    for repo in GITHUB_REPOS:
        q = urllib.parse.urlencode({"state": "open", "labels": "Remoto", "per_page": 100})
        try:
            issues = get_json(f"https://api.github.com/repos/{repo}/issues?{q}", headers)
        except Exception as e:
            print(f"[aviso] {repo}: {e}", file=sys.stderr)
            continue
        for it in issues:
            if "pull_request" in it:
                continue
            labels = " ".join(l["name"] for l in it.get("labels", []))
            blob = f"{it['title']} {labels}"
            body = (it.get("body") or "")[:1500]
            out.append(job("GitHub " + repo.split("/")[0], it["title"], "", it["html_url"],
                           "Brasil", labels) | {"_blob": blob, "_body": body})
    return out


def from_remotive():
    out = []
    for term in ("java", "spring boot", "full stack"):
        try:
            data = get_json("https://remotive.com/api/remote-jobs?" + urllib.parse.urlencode(
                {"search": term, "category": "software-dev"}))
        except Exception as e:
            print(f"[aviso] remotive {term}: {e}", file=sys.stderr)
            continue
        for j in data.get("jobs", []):
            out.append(job("Remotive", j["title"], j["company_name"], j["url"],
                           j.get("candidate_required_location", ""), j.get("job_type", ""))
                       | {"_blob": f"{j['title']} {j.get('tags', '')}",
                          "_body": re.sub(r"<[^>]+>", " ", j.get("description", ""))[:1500]})
    return out


def from_jobicy():
    out = []
    for tag in ("java", "full-stack", "spring"):
        try:
            data = get_json(f"https://jobicy.com/api/v2/remote-jobs?count=50&tag={tag}")
        except Exception as e:
            print(f"[aviso] jobicy {tag}: {e}", file=sys.stderr)
            continue
        for j in data.get("jobs", []):
            out.append(job("Jobicy", j["jobTitle"], j.get("companyName", ""), j["url"],
                           j.get("jobGeo", ""), j.get("jobLevel", ""))
                       | {"_blob": f"{j['jobTitle']} {j.get('jobLevel', '')}",
                          "_body": re.sub(r"<[^>]+>", " ", j.get("jobExcerpt", ""))})
    return out


SOURCES = [from_github, from_remotive, from_jobicy]


# --- Filtragem -------------------------------------------------------------
def matches(j):
    blob = j["_blob"]
    full = f"{blob} {j['_body']}"
    international = not j["source"].startswith("GitHub")
    if not CORE.search(blob):  # título/labels devem citar a stack
        return False
    if OTHER_STACK.search(j["title"]) and not CORE.search(j["title"]):
        return False
    if SENIOR.search(j["title"]) and not JUNIOR.search(j["title"]):
        return False
    if not JUNIOR.search(blob + " " + j["_body"][:300]):
        return False
    if international:
        # vagas internacionais: só se aceitam Brasil/LATAM/worldwide
        if not BR_OK.search(j["location"]):
            return False
    return True


def collect():
    seen_urls, found = set(), []
    for src in SOURCES:
        try:
            for j in src():
                if j["id"] in seen_urls or not matches(j):
                    continue
                seen_urls.add(j["id"])
                found.append(j)
        except Exception as e:
            print(f"[aviso] {src.__name__}: {e}", file=sys.stderr)
    return found


# --- Discord ---------------------------------------------------------------
def post_discord(webhook, jobs):
    for i in range(0, len(jobs), 10):  # máx. 10 embeds por mensagem
        embeds = []
        for j in jobs[i:i + 10]:
            desc = f"**{j['company']}**\n" if j["company"] else ""
            desc += f"📍 {j['location'] or 'Remoto'}"
            if j["extra"]:
                desc += f"\n🏷️ {j['extra'][:200]}"
            embeds.append({"title": j["title"][:250], "url": j["url"], "description": desc,
                           "color": 0x5865F2, "footer": {"text": j["source"]}})
        payload = {"content": "🆕 **Novas vagas júnior remotas**" if i == 0 else None, "embeds": embeds}
        req = urllib.request.Request(webhook, data=json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json", "User-Agent": UA})
        urllib.request.urlopen(req, timeout=25).read()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    seen = set(json.loads(SEEN_FILE.read_text())) if SEEN_FILE.exists() else set()
    new = [j for j in collect() if j["id"] not in seen]
    print(f"{len(new)} vaga(s) nova(s)")

    if args.dry_run:
        for j in new:
            print(f"- [{j['source']}] {j['title']} | {j['company']} | {j['location']}\n  {j['url']}")
        return

    webhook = os.getenv("DISCORD_WEBHOOK_URL")
    if not webhook:
        sys.exit("Defina DISCORD_WEBHOOK_URL")
    if new:
        post_discord(webhook, new)
        SEEN_FILE.write_text(json.dumps(sorted(seen | {j["id"] for j in new})))


if __name__ == "__main__":
    main()
