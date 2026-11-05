# SPR Pre-Screen

An offline AI pre-screen for Security and Policy Review, built at a hackathon. It reads a research paper against two
screening lists and shows a green, yellow or red result with the evidence, so a reviewer knows exactly where to look.

Two rules shape the whole design:

- **A human decides.** The tool never clears a paper, and even green only means it found nothing.
- **Nothing leaves the computer.** Everything runs locally, including the language model.

This is a prototype and an aid to a human reviewer. It is not an authority on what can be published.

## Run it

You need Docker with Compose, and an NVIDIA GPU with about 8 GB of memory.

**1. Build and start the app**

```bash
docker compose up -d --build
```

**2. Download the model (first run only)**

The model container has no internet route, so connect it to one for the download, then disconnect it:

```bash
docker network connect bridge $(docker compose ps -q ollama)
```

```bash
docker compose exec ollama ollama pull qwen3:8b
```

```bash
docker network disconnect bridge $(docker compose ps -q ollama)
```

**3. Open the app**

Go to <http://127.0.0.1:8000>. It's reachable only from the same computer, and it has no logins. Upload a PDF or Word
file, or use **Bulk check** for a whole folder.

To stop it:

```bash
docker compose down
```

Results and uploaded papers stay in the `spr-data` volume, and the model stays in `ollama-models`.

**Without a GPU:** remove the `deploy:` block from the `ollama` service in `docker-compose.yml`. The model then runs on
the CPU, which will be much slower. This setup hasn't been tested.

### Settings

Set these on the `spr` service in `docker-compose.yml`.

| Variable | Default | What it does |
|---|---|---|
| `SPR_LLM_MODEL` | `qwen3:8b` | Ollama model used to read passages |
| `SPR_LLM_URL` | `http://localhost:11434` | Ollama address (Compose sets this to the `ollama` service) |
| `SPR_LLM` | `1` | `0` turns the model off, so passages that name a list item show as possible matches |
| `SPR_LLM_PARALLEL` | `3` | Model readings at once (match `OLLAMA_NUM_PARALLEL`) |
| `SPR_LLM_VOTES` | `1` | Readings per passage group; `2` adds a second wording and the most cautious answer wins |
| `SPR_LLM_BUDGET` | `900` | Seconds of model time per paper |
| `SPR_MAX_PAGES` | `600` | Longer documents are refused |
| `SPR_LIST1_UNCERTAIN` | `red` | Color of a possible List 1 match |

## Why it works this way

The first version used keywords, patterns and fuzzy matching. On real papers, a paper about drones got a flag on
nearly every paragraph. A word is rarely the problem: what matters is what the sentence reveals. "The squadron flies
F-16s" is public knowledge, while a named unit at a specific place on a specific date may not be, even with no listed
word in it.

So the work is split:

- **True identifiers and real markings** stand on their own, with no model involved.
- **Everything else** is read by a small local model, which answers factual questions about each passage.
- **Fixed rules in code** turn those answers into a color. The model never picks the color.

This means every finding can be checked (a quote that isn't in the paper is thrown away), the color logic is ordinary
code, and a paper can't talk its way out, because its text is treated as data.

## How it works

1. **Upload and queue.** Papers are screened one at a time in upload order, and a restart picks up whatever was left.
   Encrypted files, oversized Word archives and papers over the page limit are refused with a reason.
2. **Read the paper.** Every word is extracted with its position, which makes exact highlights possible. Word files
   are converted to PDF with headers, footers and pictures kept. Pictures are read with Tesseract OCR. Sections such as
   references and acknowledgments are detected, because the same words mean different things there.
3. **Fast checks, List 1 then List 2.** These run on every paper, without the model: exact markings such as `(S//NF)`
   and `CUI`, identifiers such as SSNs (with number rules and checksums), and fuzzy matches for OCR errors.
4. **Structure checks.** These look at the layout around the words: unpublished or marked sources in the notes, family
   members in the acknowledgments, general-officer authors, outside sponsors and named personnel in a deployed unit.
5. **Read in context.** The paper is split into chunks of up to 5,000 characters. For each one, the model first skims
   for list items it might be about, then reads at most four of them closely: it quotes the passage and says whether
   it's specific, public and really about the item. If the model is offline or out of time, passages that name a list
   item still show as possible matches, and the screen says the reading didn't run.
6. **Rules turn answers into a color.** A quote not found in the paper is dropped. Specific and non-public is a clear
   match. A passage called public can't also be a clear match. Public discussion of a sensitive subject counts toward
   one topic card per paper. A passage that says it's confidential is treated as non-public.
7. **The final result.** Findings on the same passage merge into one card, and the worst color wins.

| Result | Meaning |
|---|---|
| **Red** | Anything found on List 1, or a clear List 2 match |
| **Yellow** | A possible match, with the reason the tool is unsure |
| **Green** | Nothing found, and every page was read |
| **Incomplete** | Part of the paper couldn't be read, so it isn't called green |

## Features

**Review page.** The document sits next to its findings, with each flag highlighted in place.

- Accept with one click, or override with a reason
- Add a finding the tool missed
- Passages the tool set aside stay visible under "Not counted", with the reason
- Keyboard shortcuts, title search, and filters by result

**Sign-off.** Release stays disabled until every flag is reviewed and the reviewer attests they reviewed the paper
personally. If part of a paper couldn't be read, they must add a note saying how it was checked. A green paper needs
the same sign-off.

**Bulk check.** Drop in a folder and let it run, with live counts, an estimate of the time left, a results table you
can filter and sort, and CSV export.

**Live screening view.** Watch a paper being screened: a progress bar, an outline around the passage being read, a page
map, possible findings as they appear, and an activity feed. The live view only carries positions, counts and item
names, never the paper's text.

**Updatable lists and custom checks.** Each list item can be edited or removed in the app, and restored to its original
wording at any time. A list always keeps at least one item.

- Add your own checks, and test them on sample text before saving
- Describe a check in a sentence and the model drafts the form for you to review
- Broken or slow regular expressions are refused
- Re-screen every paper that doesn't have a decision yet

**Suggested rewrites.** A reviewer can ask for up to three replacement sentences to pass to the author. The paper is
never changed. Each suggestion is re-checked first, and dropped if it keeps the flagged words or number, matches an
identifier or marking pattern, or hints that something is secret.

**Privacy and record keeping.**

- SSNs and similar numbers are masked until a reviewer reveals them, and each reveal is recorded
- Every screening, review and decision goes into a hash-chained history log, so later changes can be detected
- Deleting a paper removes its files, findings and decision, while the log records that a deletion happened
- The app runs in a read-only container with a strict content security policy, and the model's network has no route to
  the internet

## Where it stands

- **Speed:** about 20 to 75 seconds per paper on one GPU, depending on length and pictures
- **Rules:** no violations across 67 papers and 286 findings, including order, color and evidence
- **Misses:** reading 16 papers by hand found 4 missed reds, which are now caught
- **Seeded tests:** 6 out of 6 small made-up papers came out right

Known limits:

- The small model can give different answers on two runs of the same paper.
- It sometimes mistakes an author's analysis for inside information.
- Testing used small samples, without a labeled answer key, so there's no measured accuracy score.

Ideas for a fork: a labeled answer key, a stronger model or two readings per passage, a memory of what reviewers accept
and override, and a view that compares a revised paper with its earlier flags.

## Layout

| Path | Contents |
|---|---|
| `app/` | FastAPI app and screening pipeline |
| `app/detect/` | Fast checks: exact, pattern, fuzzy and structure |
| `app/judge/` | Reading in context with the local model |
| `app/policy/`, `app/evidence/` | Color rules, merging and evidence checks |
| `criteria/` | List 1 and List 2 as YAML, editable without rebuilding |
| `web/` | The pages, with pdf.js and fonts bundled so it works offline |

## Fork it

This project is released under the [MIT License](LICENSE). You're welcome to fork it, change it and build your own
version.

**Pull requests are not accepted.** This repository won't take outside changes, so please make your improvements in
your own fork.

Bundled third-party files keep their own licenses: pdf.js (Apache 2.0) in `web/vendor/pdfjs/`, and fonts (SIL Open Font
License 1.1) in `web/fonts/`.
