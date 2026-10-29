# Eval design: is Claude's coaching answer true?

## What is evaluated
Answers Claude gives when it uses the chess-coach MCP tools to answer a player's question.
Not the template commentator.

## Claim types
| Type | Example | Ground truth | Fails when |
|---|---|---|---|
| Numeric | "33 of 59 games" | tool JSON returned in the same run | number absent or different |
| Move | "23. Nf3 was a blunder" | DB grade and best line | grade or best move differs |
| Trend | "you got worse" | trend verdict with noise margin | states a change when the verdict is unclear |
| Advice | "practise converting won positions" | weekly focus from coach_plan | unrelated to the focus (0/1) |

Each claim is labelled: supported / contradicted / unverifiable.

## Metrics
- Primary: hallucination rate = contradicted / checkable claims. Pass threshold: ≤ 5%.
- Secondary:
  - unverifiable share
  - advice relevance (mean of the 0/1 labels)
  - consistency across the 5 runs of the same question (do the numeric claims agree?)
- Comparison: the same metrics per model, plus tokens and wall time per answer.

## Configs (model comparison)
Sonnet and Haiku, same effort (medium), same prompts, same MCP server and DB.
Effort is held equal so the model is the only variable.

## Question set (10)
1. TODO
2. TODO
3. TODO
4. TODO
5. TODO
6. TODO
7. TODO
8. TODO
9. TODO
10. TODO

## Game set (20)
4 not-won games per cause (thrown_win, blunder, time, opening, gradual), used by the
single-game questions. The list is kept in private/eval/games.csv, not committed, because
game IDs identify my account.

## Method
- 10 questions × 5 runs × 2 models = 100 runs, each in a fresh session, run headless with
  Claude Code.
- Each run's answer and its tool calls and outputs are saved to private/eval/runs/.
- Numeric and move claims are checked by code against the saved tool JSON and the DB
  (reusing the number-matching logic in commentary.py where possible).
- I check a random sample of 20 answers by hand to validate the checker.

## Out of scope
- Whether the advice improves play.
- The cause classifier against human judgement (needs a stronger player as labeller).
