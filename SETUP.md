# Weekly paper digest: setup guide

This runs entirely on GitHub's servers for free. No server of your own, no
command line needed. Follow these steps once and you'll get an email every
Monday. Total time: about 15 minutes.

---

## Two versions: pick one

There are two scripts. They search the same journals the same way; they differ
only in what the email contains.

- **`weekly_digest_noapi.py` (no API, fully free).** Each paper shows as a
  compact line with a click-to-expand **Abstract** toggle, so you skim titles
  and open only the ones you want. No Anthropic API key, no credit, no cost
  beyond the (free) GitHub run. If you want zero billing and are happy reading
  abstracts, use this one. It ignores the AI-related setup below: you only need
  the three email secrets.

- **`weekly_digest.py` (AI summaries).** Adds a short plain-language summary
  under each paper, written by Claude via the Anthropic API. This needs an API
  key with a few dollars of credit (roughly a few dollars per year at weekly
  cadence). Billing note: the Anthropic API is separate from any Claude Pro
  subscription and cannot draw on it; the key bills its own API account.

You can start with the no-API version and switch later: the deploy steps are
identical apart from the summary-only extras (API key secret). When following
the steps below, just upload whichever script you chose (and its matching
workflow reference), and skip the Anthropic API key step if you picked no-API.

---

## What you'll need before starting

1. A GitHub account (free): https://github.com/signup
2. An Anthropic API key: https://console.anthropic.com  (Settings > API Keys)
   *(AI-summary version only. Skip for the no-API version.)*
3. A Gmail account to send from, with 2-Step Verification turned on.

---

## Step 1: Create the repository

1. Go to https://github.com/new
2. Name it something like `paper-digest`.
3. Set it to **Private** (recommended) or Public. Both are free for this.
4. Click **Create repository**.

## Step 2: Upload the files

First, know which two files are "yours" based on the version you picked in the
section above:

| Your version        | Script to upload           | Workflow filename                       |
|---------------------|----------------------------|-----------------------------------------|
| No-API (free)       | `weekly_digest_noapi.py`   | `.github/workflows/weekly_noapi.yml`    |
| AI summaries        | `weekly_digest.py`         | `.github/workflows/weekly.yml`          |

Then:

1. On your new repo's page, click **Add file > Upload files**.
2. Drag in **your chosen script** (from the table above), plus
   `requirements.txt` and `SETUP.md`. You do not need to upload the other
   version's script, though it does no harm if you do: only the workflow you
   create in the next step decides which one actually runs.
3. For the workflow file, GitHub needs the folder structure. Easiest way:
   click **Add file > Create new file**, and in the name box type exactly your
   workflow filename from the table, for example:
   `.github/workflows/weekly_noapi.yml`
   (typing the slashes creates the folders). Paste the contents of the matching
   `.yml` file, then commit.
4. Commit all changes.

Note: upload only ONE workflow file. If you create both `weekly_noapi.yml` and
`weekly.yml`, both will run on schedule and you'll get two emails a week.

## Step 3: Get a Gmail app password

An app password is a one-off 16-character token that lets this script send
mail through your Gmail. It is safer than using your real password: it only
works for sending mail, and you can revoke it any time without changing your
main password.

1. Turn on 2-Step Verification if you haven't:
   https://myaccount.google.com/security
2. Then go to: https://myaccount.google.com/apppasswords
3. Give it a name like "paper digest" and click Create.
4. Copy the 16-character password it shows (spaces don't matter). You won't
   see it again, so paste it somewhere temporary for the next step.

## Step 4: Add your secrets to GitHub

Secrets are encrypted. They are never shown in logs and can't be read by
anyone browsing the repo.

1. In your repo, go to **Settings > Secrets and variables > Actions**.
2. Click **New repository secret** and add each of these (name, then value).
   The first row is only for the AI-summary version: **skip it if you deployed
   the no-API version**, which needs just the three email secrets.

   | Name                | Value                                              |
   |---------------------|----------------------------------------------------|
   | `ANTHROPIC_API_KEY` | (AI version only) your key, starts with `sk-ant-`  |
   | `SMTP_USER`         | your full Gmail address, e.g. you@gmail.com        |
   | `SMTP_PASS`         | the 16-char app password from Step 3               |
   | `MAIL_TO`           | where to send the digest (can be any address; for  |
   |                     | multiple, separate with commas)                    |

## Step 5: Test it right now (don't wait for Monday)

1. Go to the **Actions** tab in your repo.
2. If prompted, click the green button to enable workflows.
3. In the left list, click your workflow: **Weekly paper digest (no API)** for
   the free version, or **Weekly paper digest** for the AI version.
4. Click **Run workflow > Run workflow** (the manual trigger).
5. Wait about a minute, then refresh. A green check means it ran. Check your
   inbox. A red X means something failed: click into the run to read the log,
   which will name the problem (usually a mistyped secret).

That's it. From now on it runs automatically every Monday at 14:00 Malaysia
time.

---

## Customizing

In this section, "your script" means whichever one you deployed
(`weekly_digest_noapi.py` or `weekly_digest.py`), and "your workflow" means its
matching `.yml` from the table in Step 2.

Note: the two scripts filter for human studies differently. The no-API version
filters *after* retrieval using Europe PMC's organism annotations (it drops any
paper tagged with a non-human organism), so its `TOPICS` queries contain only
subject phrases. The AI version still filters within the query. Editing the
subject phrases in `TOPICS` works the same way in both.

**Change the topics:** open your script, edit the `TOPICS` list near the top.
Each entry is a label plus a Europe PMC search query. Syntax reference:
https://europepmc.org/searchsyntax . Commit the change and it takes effect on
the next run.

**Change the day/time:** edit the `cron` line in your workflow file. The time is
in UTC. Malaysia is UTC+8, so subtract 8 hours from your desired local time.
Example: for 09:00 Monday MYT, use `0 1 * * 1`.

**Change how far back it looks:** default is 7 days. To change, add a repository
**variable** (Settings > Secrets and variables > Actions > Variables tab) named
`DAYS_BACK` with a different number.

**Silence empty weeks:** by default it emails even if zero papers were found,
so you know it ran. To skip empty weeks, open your script and follow the
comment near the bottom of the `main()` function.

---

## Safeguards and off-switches

Important: this script uses the **Anthropic API** (an API key from
console.anthropic.com), which is billed separately from any Claude.ai chat
subscription. What matters here is the credit on that API account, not your
chat plan.

**What happens if API credit runs out:** nothing breaks. Each summary call is
wrapped so that if it fails for any reason (no credit, rate limit, an outage),
that paper simply shows its raw abstract instead. The digest still arrives on
schedule with all papers and links intact, and a yellow banner at the top tells
you some summaries fell back, so you are never left guessing.

**Cost ceiling (AI-summary version only):** the optional variable
`MAX_API_CALLS` caps how many Claude calls a single run can make (it defaults to
the `MAX_PAPERS` value). Once the ceiling is hit, remaining papers use their raw
abstract. This bounds the worst-case cost of any one week. Note: `MAX_API_CALLS`,
`MAX_PAPERS`, and `DAYS_BACK` are pre-listed but commented out in
`.github/workflows/weekly.yml`; to use one, remove the `#` in front of its line
there, then set the variable. (The no-API version has no API cost, so this
whole paragraph doesn't apply to it.)

**Three ways to turn things off**, in increasing order of "off":

1. **Pause AI summaries only, keep the digest.** Go to **Settings > Secrets and
   variables > Actions > Variables tab > New repository variable**, name it
   `SUMMARIES`, value `false`. You still get the weekly email with papers and
   links, just with raw abstracts and zero API cost. To turn summaries back on,
   change the value to `true` or delete the variable.

2. **Pause the whole thing, keep it installed.** Same Variables tab, add a
   variable named `ENABLED` with value `false`. The job still fires on schedule
   but exits immediately: no searches, no API calls, no email. Change it back to
   `true` any time. This is the cleanest temporary off-switch.

   These two are **variables, not secrets**, on purpose: they are not sensitive,
   and variables can be edited inline in a couple of clicks. Leaving either one
   unset is fine: the script defaults to ON, so an empty or missing variable
   never accidentally disables anything.

3. **Stop it running at all.** In the repo, go to the **Actions** tab, click
   **Weekly paper digest** on the left, then the **...** menu on the right and
   choose **Disable workflow**. Re-enable from the same place whenever you like.
   Nothing is deleted.

To remove it entirely, delete the repository, and revoke the Gmail app password
at https://myaccount.google.com/apppasswords and the API key at
https://console.anthropic.com so no credentials are left active.

## Troubleshooting

- **Email step fails with authentication error:** the app password is wrong or
  2-Step Verification isn't on. Regenerate the app password and update the
  `SMTP_PASS` secret.
- **No papers found:** normal on quiet weeks for narrow topics. Broaden a query
  or increase `DAYS_BACK`.
- **Anthropic error:** check the key is valid and your account has credit. If a
  single summary fails, the script falls back to showing the raw abstract, so
  the digest still arrives.
- **Nothing happens on schedule:** GitHub disables scheduled workflows on repos
  with no activity for 60 days. Any commit re-activates them. A manual run also
  counts as activity.
