#!/usr/bin/env python3
"""
Weekly scientific-paper digest: NO-API version.

Queries Europe PMC for papers published in the last N days across a set of
topic areas and emails a skimmable digest. Each paper shows as a compact line
(title, authors, journal, date, link); the abstract is tucked inside a
click-to-expand section so the email stays scannable. No Anthropic API, no
API key, no per-run cost. Nothing to summarize, nothing to bill.

Note on the expand/collapse: it uses the native HTML <details> element, which
needs no JavaScript and works in Gmail (web and mobile apps) and most modern
clients. A few older clients (e.g. some Outlook desktop builds) don't support
it and will simply show the abstract already expanded: you see more, not less.

Configuration via environment variables (set as GitHub secrets/variables):
  SMTP_USER    the Gmail address that sends the digest        (secret)
  SMTP_PASS    the 16-char Gmail app password                 (secret)
  MAIL_TO      where to send the digest, comma-separated ok   (secret)

Optional (variables; unset = default):
  ENABLED      "false" to skip the whole run (master off-switch). Default on.
  DAYS_BACK    how many days back to search. Default 7.
  MAX_PAPERS   hard cap on papers per run. Default 60.
  SMTP_HOST    default smtp.gmail.com
  SMTP_PORT    default 465 (SSL)
"""

import os
import sys
import ssl
import html
import smtplib
import datetime as dt
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

import requests

# ----------------------------------------------------------------------------
# CONFIG: edit the topics below to change what gets tracked. Each topic is
# (label, Europe PMC query fragment). Europe PMC query syntax:
#   https://europepmc.org/searchsyntax
# These are the same tuned, human-scoped queries as the main version.
# ----------------------------------------------------------------------------
HUMAN = (
    '(ABSTRACT:"human" OR TITLE:"human" OR ABSTRACT:"people" '
    'OR ABSTRACT:"population" OR TITLE:"population" OR ABSTRACT:"ethnic" '
    'OR TITLE:"ethnic" OR ABSTRACT:"ancestry" OR TITLE:"ancestry" '
    'OR ABSTRACT:"cohort" OR ABSTRACT:"individuals" OR ABSTRACT:"Homo sapiens" '
    'OR ABSTRACT:"patient" OR ABSTRACT:"biobank")'
)
NOT_MICROBIAL = (
    'NOT (TITLE:"isolates" OR TITLE:"strains" OR TITLE:"accessions" '
    'OR ABSTRACT:"bacterial isolates" OR ABSTRACT:"viral genomes" '
    'OR ABSTRACT:"crop" OR ABSTRACT:"livestock" OR ABSTRACT:"wild populations")'
)

TOPICS = [
    ("Population-scale sequencing & initiatives",
     '('
     'TITLE:"population genomics" OR ABSTRACT:"population genomics" '
     'OR TITLE:"population-scale sequencing" OR ABSTRACT:"population-scale sequencing" '
     'OR TITLE:"population-scale genomics" OR ABSTRACT:"population-scale genomics" '
     'OR TITLE:"whole-genome sequencing cohort" OR ABSTRACT:"whole-genome sequencing cohort" '
     'OR TITLE:"national genome project" OR ABSTRACT:"national genome project" '
     'OR TITLE:"genomic atlas" OR ABSTRACT:"genomic atlas" '
     'OR TITLE:"allele frequency reference" OR ABSTRACT:"allele frequency reference" '
     'OR TITLE:"variant catalog" OR ABSTRACT:"variant catalog" '
     'OR TITLE:"variant catalogue" OR ABSTRACT:"variant catalogue" '
     'OR TITLE:"reference cohort" OR ABSTRACT:"reference cohort" '
     'OR ((ABSTRACT:"genomic diversity" OR ABSTRACT:"genetic diversity" '
     'OR ABSTRACT:"human genetic variation") AND (ABSTRACT:"sequencing" OR ABSTRACT:"whole-genome"))'
     f') AND {HUMAN} {NOT_MICROBIAL}'),

    ("Ancestry, admixture & demographic history",
     '('
     'TITLE:"genetic ancestry" OR ABSTRACT:"genetic ancestry" '
     'OR TITLE:"ancestry inference" OR ABSTRACT:"ancestry inference" '
     'OR TITLE:"demographic history" OR ABSTRACT:"demographic history" '
     'OR TITLE:"founder population" OR ABSTRACT:"founder population" '
     'OR TITLE:"underrepresented populations" OR ABSTRACT:"underrepresented populations" '
     'OR ((ABSTRACT:"admixture" OR ABSTRACT:"population structure" '
     'OR ABSTRACT:"population stratification") '
     'AND (ABSTRACT:"whole-genome" OR ABSTRACT:"sequencing" OR ABSTRACT:"SNP" '
     'OR ABSTRACT:"variants" OR ABSTRACT:"genome"))'
     f') AND {HUMAN} {NOT_MICROBIAL}'),

    ("Pangenome, assembly & T2T",
     '('
     'TITLE:"pangenome" OR ABSTRACT:"pangenome" OR TITLE:"pan-genome" OR ABSTRACT:"pan-genome" '
     'OR TITLE:"pangenome reference" OR ABSTRACT:"pangenome reference" '
     'OR TITLE:"genome graph" OR ABSTRACT:"genome graph" '
     'OR TITLE:"graph genome" OR ABSTRACT:"graph genome" '
     'OR TITLE:"telomere-to-telomere" OR ABSTRACT:"telomere-to-telomere" '
     'OR TITLE:"T2T" OR ABSTRACT:"complete genome assembly" '
     'OR TITLE:"human pangenome" OR ABSTRACT:"human pangenome"'
     f') AND {HUMAN} {NOT_MICROBIAL}'),

    ("Sequencing technology evaluation",
     '(('
     'TITLE:"long-read sequencing" OR ABSTRACT:"long-read sequencing" '
     'OR TITLE:"nanopore" OR ABSTRACT:"nanopore" OR TITLE:"PacBio" OR ABSTRACT:"PacBio" '
     'OR ABSTRACT:"HiFi" OR ABSTRACT:"ultra-long reads" OR ABSTRACT:"duplex sequencing"'
     ') AND ('
     'ABSTRACT:"comparison" OR TITLE:"comparison" OR ABSTRACT:"benchmark" OR TITLE:"benchmark" '
     'OR ABSTRACT:"evaluation" OR ABSTRACT:"read accuracy" OR ABSTRACT:"variant calling accuracy" '
     'OR ABSTRACT:"structural variant detection" OR ABSTRACT:"de novo assembly"'
     f')) AND {HUMAN}'),

    ("Precision & population health genomics",
     '(('
     'TITLE:"precision medicine" OR ABSTRACT:"precision medicine" '
     'OR TITLE:"precision health" OR ABSTRACT:"precision health" '
     'OR TITLE:"genomic medicine" OR ABSTRACT:"genomic medicine" '
     'OR TITLE:"clinical genomics" OR ABSTRACT:"clinical genomics" '
     'OR TITLE:"population health genomics" OR ABSTRACT:"population health genomics" '
     'OR TITLE:"genomic newborn screening" OR ABSTRACT:"genomic newborn screening" '
     'OR TITLE:"genomic implementation" OR ABSTRACT:"genomic implementation" '
     'OR ((ABSTRACT:"polygenic risk score" OR ABSTRACT:"polygenic score") '
     'AND (ABSTRACT:"clinical" OR ABSTRACT:"implementation" OR ABSTRACT:"population"))'
     f')) AND {HUMAN} {NOT_MICROBIAL}'),
]


def _flag(name, default=True):
    """Boolean env var. Unset or empty falls through to default (so an unset
    GitHub variable never accidentally disables the run)."""
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() not in ("false", "0", "no", "off")


def _int_env(name, default):
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw.strip())
    except ValueError:
        return default


ENABLED    = _flag("ENABLED", True)
DAYS_BACK  = _int_env("DAYS_BACK", 7)
MAX_PAPERS = _int_env("MAX_PAPERS", 60)   # higher default: entries are cheap now

EPMC_BASE  = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
PER_TOPIC_LIMIT = 20


def epmc_search(query_fragment, date_from, date_to):
    """Return a list of paper dicts from Europe PMC for one topic."""
    query = f'{query_fragment} AND (FIRST_PDATE:[{date_from} TO {date_to}])'
    params = {
        "query": query,
        "format": "json",
        "pageSize": PER_TOPIC_LIMIT,
        "resultType": "core",
        "sort": "P_PDATE_D desc",
    }
    try:
        r = requests.get(EPMC_BASE, params=params, timeout=60)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f"  ! Europe PMC query failed: {e}", file=sys.stderr)
        return []

    papers = []
    for res in data.get("resultList", {}).get("result", []):
        title = (res.get("title") or "").strip()
        if not title:
            continue
        abstract = (res.get("abstractText") or "").strip()
        doi = res.get("doi")
        pmid = res.get("pmid")
        pmcid = res.get("pmcid")
        if doi:
            link = f"https://doi.org/{doi}"
        elif pmid:
            link = f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"
        elif pmcid:
            link = f"https://europepmc.org/article/PMC/{pmcid}"
        else:
            link = "https://europepmc.org/"

        uid = doi or pmid or pmcid or title
        papers.append({
            "uid": uid,
            "title": title,
            "abstract": abstract,
            "authors": (res.get("authorString") or "").strip(),
            "journal": (res.get("journalTitle")
                        or res.get("bookOrReportDetails", {}).get("publisher")
                        or "Preprint / other").strip(),
            "date": (res.get("firstPublicationDate") or "").strip(),
            "link": link,
            "is_preprint": res.get("source") == "PPR",
        })
    return papers


def _clean_abstract(text):
    """Europe PMC abstracts sometimes carry section labels and stray markup.
    Light touch: strip, collapse whitespace, keep it readable."""
    if not text:
        return "No abstract available for this record."
    return " ".join(text.split())


def build_html(sections, date_from, date_to):
    """Assemble the skimmable HTML email with collapsible abstracts."""
    total = sum(len(p) for p in sections.values())
    css = """
    body{font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;
         color:#1a1a1a;line-height:1.45;max-width:720px;margin:0 auto;padding:16px;}
    h1{font-size:20px;margin-bottom:2px;}
    .sub{color:#666;font-size:13px;margin-bottom:8px;}
    .hint{color:#999;font-size:12px;margin-bottom:24px;font-style:italic;}
    h2{font-size:16px;border-bottom:2px solid #e0e0e0;padding-bottom:4px;
       margin-top:30px;color:#0b5;}
    .paper{margin:0 0 14px 0;padding-bottom:12px;border-bottom:1px solid #f2f2f2;}
    .ptitle{font-weight:600;font-size:15px;}
    .ptitle a{color:#1155cc;text-decoration:none;}
    .meta{color:#888;font-size:12px;margin:2px 0 4px 0;}
    details{margin-top:4px;}
    summary{cursor:pointer;color:#1155cc;font-size:13px;
            list-style:none;display:inline-block;}
    summary::-webkit-details-marker{display:none;}
    summary::before{content:"\\25B6  Abstract";}
    details[open] summary::before{content:"\\25BC  Abstract";}
    .abstract{font-size:13.5px;color:#333;margin:8px 0 2px 0;
              padding:10px 12px;background:#fafafa;border-left:3px solid #d9d9d9;
              border-radius:0 4px 4px 0;}
    .tag{display:inline-block;background:#eef6ff;color:#1155cc;font-size:11px;
         padding:1px 6px;border-radius:3px;margin-left:6px;}
    .empty{color:#999;font-style:italic;font-size:13px;}
    .foot{color:#aaa;font-size:11px;margin-top:32px;border-top:1px solid #eee;
          padding-top:12px;}
    """
    out = [f"<html><head><meta charset='utf-8'><style>{css}</style></head><body>"]
    out.append("<h1>Weekly literature digest</h1>")
    out.append(f"<div class='sub'>{total} new papers, {date_from} to {date_to}"
               f" &middot; source: Europe PMC</div>")
    out.append("<div class='hint'>Click &ldquo;Abstract&rdquo; under any paper "
               "to expand it. Titles link to the full text.</div>")

    for label, papers in sections.items():
        out.append(f"<h2>{html.escape(label)} ({len(papers)})</h2>")
        if not papers:
            out.append("<div class='empty'>No new papers this week.</div>")
            continue
        for p in papers:
            tag = "<span class='tag'>preprint</span>" if p["is_preprint"] else ""
            authors = html.escape(p["authors"])
            if len(authors) > 130:
                authors = authors[:130] + "..."
            abstract = html.escape(_clean_abstract(p["abstract"]))
            out.append("<div class='paper'>")
            out.append(f"<div class='ptitle'><a href='{html.escape(p['link'])}'>"
                       f"{html.escape(p['title'])}</a>{tag}</div>")
            out.append(f"<div class='meta'>{authors}<br>"
                       f"{html.escape(p['journal'])} &middot; {html.escape(p['date'])}</div>")
            out.append(f"<details><summary></summary>"
                       f"<div class='abstract'>{abstract}</div></details>")
            out.append("</div>")

    out.append("<div class='foot'>Generated automatically from Europe PMC. "
               "No AI summaries in this version: abstracts are shown as "
               "published. Edit the TOPICS list in weekly_digest_noapi.py to "
               "change coverage.</div>")
    out.append("</body></html>")
    return "\n".join(out)


def send_email(html_body, total, date_from, date_to):
    host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    port = int(os.environ.get("SMTP_PORT", "465"))
    user = os.environ["SMTP_USER"]
    passwd = os.environ["SMTP_PASS"]
    mail_to = os.environ["MAIL_TO"]

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"Literature digest: {total} new papers ({date_from} to {date_to})"
    msg["From"] = user
    msg["To"] = mail_to
    msg.attach(MIMEText("This digest is best viewed as HTML.", "plain"))
    msg.attach(MIMEText(html_body, "html"))

    ctx = ssl.create_default_context()
    with smtplib.SMTP_SSL(host, port, context=ctx) as server:
        server.login(user, passwd)
        server.sendmail(user, [a.strip() for a in mail_to.split(",")], msg.as_string())
    print(f"Sent digest to {mail_to}")


def main():
    if not ENABLED:
        print("ENABLED is false: skipping this run entirely.")
        return

    today = dt.date.today()
    date_from = (today - dt.timedelta(days=DAYS_BACK)).isoformat()
    date_to = today.isoformat()
    print(f"Searching {date_from} to {date_to} (no-API version)")

    sections = {}
    seen = set()
    budget = MAX_PAPERS

    for label, fragment in TOPICS:
        print(f"Topic: {label}")
        papers = epmc_search(fragment, date_from, date_to)
        kept = []
        for p in papers:
            if p["uid"] in seen:
                continue
            if budget <= 0:
                break
            seen.add(p["uid"])
            kept.append(p)
            budget -= 1
        sections[label] = kept
        print(f"  kept {len(kept)}")

    total = sum(len(p) for p in sections.values())
    print(f"Total papers: {total}")

    # By default the digest is sent even on weeks with zero papers, so you know
    # the job ran. To stay silent on empty weeks instead, uncomment these lines:
    # if total == 0:
    #     print("No papers this week; skipping email.")
    #     return

    html_body = build_html(sections, date_from, date_to)
    send_email(html_body, total, date_from, date_to)


if __name__ == "__main__":
    main()
