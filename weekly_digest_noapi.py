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
# --- Human-scoping, rewritten to cut pathogen / animal / plant contamination ---
#
# Two problems in the first version let non-human work through:
#   1. "patient" in the human signal matched infectious-disease papers about
#      pathogens (e.g. a Burkholderia pseudomallei study mentions patients).
#   2. "population structure" is shared vocabulary with microbial phylogenetics.
#
# Fix, in layers:
#   HUMAN         a positive signal: a MeSH "Humans" tag OR clear human-context
#                 words. We keep this as an OR (not a hard MeSH gate) because
#                 preprints often have no MeSH terms yet, and a hard gate would
#                 silently drop them. "patient" is deliberately removed.
#   NOT_NONHUMAN  a much broader exclusion of organism and pathogen-genomics
#                 terms. This is the workhorse that removes camels, Drosophila,
#                 bacteria, viruses, plants, etc.
#
# Applied together as: (topic) AND HUMAN NOT_NONHUMAN

HUMAN = (
    '(MESH:"Humans" '
    'OR ABSTRACT:"human" OR TITLE:"human" '
    'OR ABSTRACT:"people" OR ABSTRACT:"population" OR TITLE:"population" '
    'OR ABSTRACT:"ethnic" OR TITLE:"ethnic" '
    'OR ABSTRACT:"ancestry" OR TITLE:"ancestry" '
    'OR ABSTRACT:"Homo sapiens" OR ABSTRACT:"biobank" '
    'OR TITLE:"UK Biobank" OR ABSTRACT:"UK Biobank")'
)

# Broad exclusion. Grouped for readability; all OR-ed inside one NOT.
NOT_NONHUMAN = (
    'NOT ('
    # microbes / pathogens
    'ABSTRACT:"bacterial" OR ABSTRACT:"bacterium" OR ABSTRACT:"pathogen" '
    'OR TITLE:"pathogen" OR ABSTRACT:"virus" OR TITLE:"virus" OR ABSTRACT:"viral" '
    'OR ABSTRACT:"antimicrobial" OR ABSTRACT:"antibiotic" OR ABSTRACT:"outbreak" '
    'OR ABSTRACT:"isolates" OR TITLE:"isolates" OR ABSTRACT:"strains" OR TITLE:"strains" '
    'OR ABSTRACT:"serotype" OR ABSTRACT:"phylogenomic" OR TITLE:"phylogenomic" '
    'OR ABSTRACT:"E. coli" OR ABSTRACT:"Salmonella" OR ABSTRACT:"Mycobacterium" '
    'OR ABSTRACT:"Burkholderia" OR ABSTRACT:"Klebsiella" OR ABSTRACT:"Pseudomonas" '
    'OR ABSTRACT:"Staphylococcus" OR ABSTRACT:"Streptococcus" '
    # model organisms / animals / plants
    'OR TITLE:"Drosophila" OR ABSTRACT:"Drosophila" OR ABSTRACT:"zebrafish" '
    'OR ABSTRACT:"mouse" OR ABSTRACT:"murine" OR ABSTRACT:"mice" '
    'OR ABSTRACT:"cattle" OR ABSTRACT:"bovine" OR ABSTRACT:"camel" '
    'OR ABSTRACT:"livestock" OR ABSTRACT:"poultry" OR ABSTRACT:"swine" OR ABSTRACT:"porcine" '
    'OR ABSTRACT:"crop" OR ABSTRACT:"plant" OR ABSTRACT:"maize" OR ABSTRACT:"rice" '
    'OR ABSTRACT:"wild populations" OR ABSTRACT:"accessions" '
    # veterinary / non-human MeSH
    'OR MESH:"Animals, Wild" OR MESH:"Plants" OR MESH:"Bacteria"'
    ')'
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
     f') AND {HUMAN} {NOT_NONHUMAN}'),

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
     f') AND {HUMAN} {NOT_NONHUMAN}'),

    ("Pangenome, assembly & T2T",
     '('
     'TITLE:"pangenome" OR ABSTRACT:"pangenome" OR TITLE:"pan-genome" OR ABSTRACT:"pan-genome" '
     'OR TITLE:"pangenome reference" OR ABSTRACT:"pangenome reference" '
     'OR TITLE:"genome graph" OR ABSTRACT:"genome graph" '
     'OR TITLE:"graph genome" OR ABSTRACT:"graph genome" '
     'OR TITLE:"telomere-to-telomere" OR ABSTRACT:"telomere-to-telomere" '
     'OR TITLE:"T2T" OR ABSTRACT:"complete genome assembly" '
     'OR TITLE:"human pangenome" OR ABSTRACT:"human pangenome"'
     f') AND {HUMAN} {NOT_NONHUMAN}'),

    ("Sequencing technology evaluation",
     '(('
     'TITLE:"long-read sequencing" OR ABSTRACT:"long-read sequencing" '
     'OR TITLE:"nanopore" OR ABSTRACT:"nanopore" OR TITLE:"PacBio" OR ABSTRACT:"PacBio" '
     'OR ABSTRACT:"HiFi" OR ABSTRACT:"ultra-long reads" OR ABSTRACT:"duplex sequencing"'
     ') AND ('
     'ABSTRACT:"comparison" OR TITLE:"comparison" OR ABSTRACT:"benchmark" OR TITLE:"benchmark" '
     'OR ABSTRACT:"evaluation" OR ABSTRACT:"read accuracy" OR ABSTRACT:"variant calling accuracy" '
     'OR ABSTRACT:"structural variant detection" OR ABSTRACT:"de novo assembly"'
     f')) AND {HUMAN} {NOT_NONHUMAN}'),

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
     f')) AND {HUMAN} {NOT_NONHUMAN}'),
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


import re

# Formatting tags Europe PMC uses that are safe and meaningful to keep
# (italic species names, sub/superscripts in formulae, bold). Everything else
# is escaped so it can never inject markup.
_SAFE_TAGS = ("i", "b", "sub", "sup", "em", "strong")
_TAG_RE = re.compile(r"</?([a-zA-Z0-9]+)[^>]*>")


def _sanitize_inline(text):
    """Escape the abstract for HTML, but let a small safelist of formatting
    tags render instead of showing as literal <i> text. Approach: escape the
    whole string first (so all real markup is neutralised), then selectively
    un-escape the safelisted tags. This is safe because only the exact tag
    forms we re-enable can come back; anything else stays escaped."""
    escaped = html.escape(text)
    for tag in _SAFE_TAGS:
        # Re-enable <i>, </i>, <i/> style forms only.
        escaped = escaped.replace(f"&lt;{tag}&gt;", f"<{tag}>")
        escaped = escaped.replace(f"&lt;/{tag}&gt;", f"</{tag}>")
        escaped = escaped.replace(f"&lt;{tag}/&gt;", f"<{tag}>")
        # Capitalised variants occasionally appear.
        T = tag.upper()
        escaped = escaped.replace(f"&lt;{T}&gt;", f"<{tag}>")
        escaped = escaped.replace(f"&lt;/{T}&gt;", f"</{tag}>")
    return escaped


def _clean_abstract(text):
    """Strip, collapse whitespace. Returns a placeholder when empty."""
    if not text:
        return ""
    return " ".join(text.split())


def _preview(text, limit=280):
    """Return (preview_html, was_truncated). Truncates on a word boundary near
    `limit` characters, sanitises inline formatting, and closes any safelisted
    tag left open by the cut so italics don't bleed into the rest of the email."""
    clean = _clean_abstract(text)
    if not clean:
        return ("No abstract available for this record.", False)

    truncated = len(clean) > limit
    if truncated:
        cut = clean[:limit]
        # back up to the last space so we don't slice a word in half
        sp = cut.rfind(" ")
        if sp > limit - 60:
            cut = cut[:sp]
        snippet = cut.rstrip(" ,;:.") + "\u2026"   # ellipsis
    else:
        snippet = clean

    safe = _sanitize_inline(snippet)

    # If truncation left an unclosed safelisted tag open, close it.
    for tag in _SAFE_TAGS:
        opens = len(re.findall(f"<{tag}>", safe))
        closes = len(re.findall(f"</{tag}>", safe))
        if opens > closes:
            safe += f"</{tag}>" * (opens - closes)
    return (safe, truncated)


def build_html(sections, date_from, date_to):
    """Assemble the skimmable HTML email.

    Design constraints learned the hard way:
      - Gmail strips <style> blocks and interactive HTML (<details>), so there
        is NO reliable in-email collapse. All styling is therefore INLINE, and
        each abstract shows as a short truncated preview with the full text one
        click away via the title link. This renders the same everywhere.
      - Europe PMC abstracts contain <i> etc. for species names; those are
        preserved via a safelist, everything else is escaped.
    """
    total = sum(len(p) for p in sections.values())

    # Inline style fragments (Gmail-safe). Kept as named constants for reuse.
    S_BODY   = ("font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,"
                "sans-serif;color:#1a1a1a;line-height:1.45;max-width:720px;"
                "margin:0 auto;padding:16px;")
    S_H1     = "font-size:20px;margin:0 0 2px 0;"
    S_SUB    = "color:#666;font-size:13px;margin-bottom:20px;"
    S_H2     = ("font-size:16px;border-bottom:2px solid #e0e0e0;"
                "padding-bottom:4px;margin:30px 0 12px 0;color:#0a8f4f;")
    S_PAPER  = "margin:0 0 14px 0;padding-bottom:12px;border-bottom:1px solid #f2f2f2;"
    S_TITLE  = "font-weight:600;font-size:15px;"
    S_LINK   = "color:#1155cc;text-decoration:none;"
    S_META   = "color:#888;font-size:12px;margin:2px 0 6px 0;"
    S_ABS    = ("font-size:13.5px;color:#333;margin:6px 0 2px 0;padding:10px 12px;"
                "background:#fafafa;border-left:3px solid #d9d9d9;")
    S_MORE   = "color:#1155cc;text-decoration:none;font-size:12px;white-space:nowrap;"
    S_TAG    = ("background:#eef6ff;color:#1155cc;font-size:11px;padding:1px 6px;"
                "border-radius:3px;margin-left:6px;")
    S_EMPTY  = "color:#999;font-style:italic;font-size:13px;"
    S_FOOT   = ("color:#aaa;font-size:11px;margin-top:32px;border-top:1px solid "
                "#eee;padding-top:12px;")

    out = ["<html><head><meta charset='utf-8'></head>"
           f"<body style=\"{S_BODY}\">"]
    out.append(f"<h1 style=\"{S_H1}\">Weekly literature digest</h1>")
    out.append(f"<div style=\"{S_SUB}\">{total} new papers, {date_from} to "
               f"{date_to} &middot; source: Europe PMC. Each entry shows the "
               f"start of the abstract; the title links to the full text.</div>")

    for label, papers in sections.items():
        out.append(f"<h2 style=\"{S_H2}\">{html.escape(label)} ({len(papers)})</h2>")
        if not papers:
            out.append(f"<div style=\"{S_EMPTY}\">No new papers this week.</div>")
            continue
        for p in papers:
            tag = (f"<span style=\"{S_TAG}\">preprint</span>"
                   if p["is_preprint"] else "")
            authors = html.escape(p["authors"])
            if len(authors) > 130:
                authors = authors[:130] + "\u2026"
            preview, truncated = _preview(p["abstract"])
            link = html.escape(p["link"])
            more = (f" <a href=\"{link}\" style=\"{S_MORE}\">read more &rsaquo;</a>"
                    if truncated else "")
            out.append(f"<div style=\"{S_PAPER}\">")
            out.append(f"<div style=\"{S_TITLE}\"><a href=\"{link}\" "
                       f"style=\"{S_LINK}\">{html.escape(p['title'])}</a>{tag}</div>")
            out.append(f"<div style=\"{S_META}\">{authors}<br>"
                       f"{html.escape(p['journal'])} &middot; "
                       f"{html.escape(p['date'])}</div>")
            out.append(f"<div style=\"{S_ABS}\">{preview}{more}</div>")
            out.append("</div>")

    out.append(f"<div style=\"{S_FOOT}\">Generated automatically from Europe PMC. "
               "Abstracts are shown as published, trimmed to a preview. Edit the "
               "TOPICS list in weekly_digest_noapi.py to change coverage.</div>")
    out.append("</body></html>")
    return "\n".join(out)


def _plaintext_version(sections, date_from, date_to):
    """A real plain-text alternative. A message whose only text part says
    'best viewed as HTML' looks spammy to filters; a genuine text version that
    mirrors the content scores better and is a good fallback."""
    lines = [f"Weekly literature digest ({date_from} to {date_to})",
             "Source: Europe PMC", ""]
    for label, papers in sections.items():
        lines.append(f"== {label} ({len(papers)}) ==")
        if not papers:
            lines.append("  No new papers this week.")
        for p in papers:
            pre = _clean_abstract(p["abstract"])
            if len(pre) > 240:
                pre = pre[:240].rsplit(" ", 1)[0] + "\u2026"
            lines.append(f"- {p['title']}")
            lines.append(f"  {p['journal']} | {p['date']}"
                         + ("  [preprint]" if p["is_preprint"] else ""))
            if pre:
                lines.append(f"  {pre}")
            lines.append(f"  {p['link']}")
        lines.append("")
    return "\n".join(lines)


def send_email(html_body, plain_body, total, date_from, date_to):
    from email.utils import formatdate, make_msgid, formataddr

    host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    port = int(os.environ.get("SMTP_PORT", "465"))
    user = os.environ["SMTP_USER"]
    passwd = os.environ["SMTP_PASS"]
    mail_to = os.environ["MAIL_TO"]

    msg = MIMEMultipart("alternative")
    msg["Subject"] = (f"Literature digest: {total} new papers "
                      f"({date_from} to {date_to})")
    # A display name plus proper Date and Message-ID are basic legitimacy
    # signals; their absence is a common reason strict filters flag mail.
    msg["From"] = formataddr(("Literature Digest", user))
    msg["To"] = mail_to
    msg["Reply-To"] = user
    msg["Date"] = formatdate(localtime=True)
    # Message-ID domain should match the sender domain (e.g. gmail.com).
    sender_domain = user.split("@")[-1] if "@" in user else "localhost"
    msg["Message-ID"] = make_msgid(domain=sender_domain)
    msg["Auto-Submitted"] = "auto-generated"   # marks it as an automated report

    # Order matters: least-preferred (plain) first, best (html) last.
    msg.attach(MIMEText(plain_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    ctx = ssl.create_default_context()
    with smtplib.SMTP_SSL(host, port, context=ctx) as server:
        server.login(user, passwd)
        server.sendmail(user, [a.strip() for a in mail_to.split(",")],
                        msg.as_string())
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
    plain_body = _plaintext_version(sections, date_from, date_to)
    send_email(html_body, plain_body, total, date_from, date_to)


if __name__ == "__main__":
    main()
