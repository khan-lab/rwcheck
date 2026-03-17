# Zotero Workflow

RWCheck can screen an entire Zotero library for retracted papers in a few steps.
The workflow exports your library as BibTeX, runs `rwcheck bib`, and gives you
an HTML report listing every retracted reference with its retraction date, reason,
and journal.

---

## Step 1 — Export your Zotero library as BibTeX

**Zotero desktop app**

    1. Select the collection or library you want to check (e.g. *My Library*
       or a specific folder).
    2. **File → Export Library…** (or right-click the collection → *Export Collection…*).
    3. Choose format **BibTeX**.
    4. Tick **Export Notes** if you want to preserve notes; leave everything else at
       defaults.
    5. Save the file — for example as `my_library.bib`.

**Better BibTeX plugin (recommended)**

    [Better BibTeX](https://retorque.re/zotero-better-bibtex/) gives you stable
    cite keys and richer DOI/PMID export.

    1. Install the plugin from [https://retorque.re/zotero-better-bibtex/](https://retorque.re/zotero-better-bibtex/).
    2. Select your library or collection.
    3. **File → Export Library…** → format **Better BibTeX**.
    4. Optionally tick **Keep updated** for an auto-syncing export.
    5. Save as `my_library.bib`.

> **Tip:** The DOI field is populated automatically for most journal articles.
> Papers without a DOI or PMID will be listed as *unchecked* in the report.

---

## Step 2 — Build the Retraction Watch database (first run only)

```bash
pip install rwcheck
rwcheck update
```

This downloads the Retraction Watch CSV and builds a local SQLite database
(`~/.rwcheck/rw.sqlite`). It takes ~30 seconds on first run and is skipped on
subsequent runs if the dataset has not changed.

---

## Step 3 — Run the check

```bash
rwcheck bib my_library.bib
```

**Console output**

```
  Total references         1234
  Retracted                   3
  Clean (not found)        1198
  Unchecked (no DOI/PMID)    33

⚠ Retracted entries:
  ✗ [stapel2011] Stapel et al. 2011 — Retraction | Science
  ✗ [fujii2012]  Fujii et al. 2012  — Retraction | Anesthesiology
  ✗ [boldt2009]  Boldt et al. 2009  — Retraction | Anesthesia & Analgesia

Reports written:
  Markdown → my_library_rwcheck.md
  HTML     → my_library_rwcheck.html
  JSON     → my_library_rwcheck.json
```

---

## Step 4 — Review the report

Open `my_library_rwcheck.html` in your browser for the full interactive report.
Each retracted entry includes:

- Retraction date and nature (Retraction / Correction / Expression of Concern)
- Retraction reason (data fabrication, image manipulation, etc.)
- Journal and publisher
- DOI links to the original paper and retraction notice

---

## Step 5 — Remove or annotate retracted items in Zotero

For each flagged item:

1. In Zotero, search for the paper by title or DOI.
2. Add a **tag** such as `RETRACTED` so it stands out in future exports.
3. Add a **note** with the retraction date and reason for your records.
4. Optionally delete the item if you no longer need it.

---

## Automating with a keep-updated export

If you use Better BibTeX's **Keep updated** option, your `.bib` file stays in sync
with your Zotero library automatically. You can then schedule a nightly cron job:

```bash
# crontab entry — run every night at 01:00
0 1 * * * cd ~/papers && rwcheck update && rwcheck bib my_library.bib \
           --report-dir ~/papers/rw-reports/
```

This ensures newly added references are screened without any manual steps.

---

## Using the REST API instead of the local database

If you have a shared [rwcheck server](rest-api.md) (e.g. for a research group),
pass `--api` to delegate lookups:

```bash
rwcheck bib my_library.bib --api https://rwcheck.khanlab.bio
```

No local database is needed — all queries are forwarded to the remote API.

---

## Mendeley

Mendeley uses the same export format:

1. **File → Export…** → select **BibTeX (.bib)**.
2. Choose *All References* or a specific group.
3. Save and run `rwcheck bib` as above.

---

## Paperpile
You can also export references to a BibTeX file from the Paperpile using the follwoing setps.

  - Hold down the Shift key and click to select multiple references or click the checkbox next to each title to select one or more references from the list.
  - Choose Export from the three-dot menu in the toolbar.
  - Select BibTeX from the options and click Export.

---

## Using a RIS export instead of BibTeX

Zotero, Mendeley, and Paperpile can all export references as RIS (`.ris`) files.
Use `rwcheck ris` to check a RIS export directly — no conversion needed:

```bash
rwcheck ris my_library.ris
```

The output format and report files are identical to `rwcheck bib` (Markdown, HTML, and JSON).
