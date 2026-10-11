# Chess Coach: project plan

A personal chess coach built on the chess.com public API and Stockfish. It turns a player's own
games into measured, explained, trainable feedback. It is also a learning project: every claim the
app makes is meant to be backed by ground truth and an evaluation.

## Principles
- **Measure, then claim.** Every result carries its sample size; differences are called real only when
  they exceed sampling noise. Heuristics are labelled as heuristics.
- **Ground truth first.** Components are validated against an independent reference (deeper engine
  search, chess.com's own labels, peer benchmarks) before they are trusted. See `scripts/`.
- **Facts are computed, words are generated.** Numbers, moves and evaluations come from deterministic
  code. A language model may only phrase facts the code supplies, and its output is checked against them.
- **Reusable artifacts.** Outputs are structured (JSON/JSONL) so they can feed retrieval, evaluation
  sets and, later, fine-tuning of a local model.

## Layers
1. **Analysis**: sync, two-pass Stockfish analysis, win-probability move grading (`analysis.py`, `classify.py`).
2. **Reports**: causes of non-wins, trends with noise margins, openings, clock, tilt, peers (`coach.py`, `patterns.py`, `peers.py`).
3. **Interfaces**: MCP server (`server.py`, 14 tools) and a local web dashboard with a game viewer and
   trainer (`web.py`, `web_static/`).
4. **Knowledge** (planned): principles from coaching literature, in the project's own words, linked to
   the data that can confirm or refute them.
5. **Commentary** (planned): a `Commentator` interface with interchangeable backends (templates, a hosted
   model API, a local model), compared by an evaluation harness for faithfulness to the supplied facts.
6. **Agent** (planned): a coach agent that calls the MCP tools, evaluated on scripted scenarios.

## Knowledge pipeline (planned)
```
books (private, never committed) -> OCR / text extraction (local) -> Markdown per page
  -> distilled principles (own words, page references) -> data-backed validation
  -> retrieval index + evaluation set + fine-tuning data
```
Principle record (one JSON object per line):

| field | meaning |
|---|---|
| `id` | stable identifier, e.g. `thr-001` |
| `claim` | the principle, in the project's own words |
| `kind` | principle, heuristic, diagnostic, process_step, exercise_type, misconception, curriculum_level |
| `source` | book key and page range (no quoted passages beyond a few words) |
| `audience` | rating range the author addresses |
| `evidence` | author's claim, worked example, or empirical study |
| `conflicts_with` | ids of principles from other sources that disagree |
| `testable_prediction` | what the player's own data should show if the principle holds, or `null` |
| `app_hooks` | features that use it (report, drill, explanation) |
| `status` | candidate, supported_by_data, refuted_by_data, untestable |

Rules: no book text is copied into the repository or the app; the source files stay on the author's
machine; every principle that makes a testable prediction gets an evaluation before it drives a feature.

## Evaluation plan
- Engine depth agreement (done): two-pass analysis vs depth-20 reference, at about 800 and 1500-2300 rating.
- Peer benchmarks (done): per rating band, 3+0 blitz, from public games.
- chess.com brilliant labels (done, negative result): our rule cannot reproduce them; reported as such.
- OCR accuracy (planned): scanned pages vs books that carry a clean text layer.
- Commentary faithfulness (planned): every number, move and claim in generated text is checked against the facts supplied.
- Principle tests (planned): do the book-derived predictions hold in the player's data?

## Data and licensing notes
- The author's games, opponents' public games, and the local analysis databases are not published.
- Stockfish is GPL-licensed and is downloaded by the user, not redistributed.
- Reference books are used for private study; they are not part of this repository.
