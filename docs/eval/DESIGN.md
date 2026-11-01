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
Questions 1–9 are adapted from real player posts (chess.com forums, r/chess) so they can be
answered from the player's own data.

| # | Question | Source |
|---|---|---|
| 1 | I play hundreds of games but keep losing. What is the main reason, and what should I work on this week? | [Can someone help me with finding what I should improve on?](https://www.chess.com/forum/view/for-beginners/can-someone-help-me-with-finding-what-i-should-improve-on) |
| 2 | Why can't I stop blundering? What kind of blunders do I make most? | [Why do I keep blundering?](https://www.chess.com/forum/view/general/why-do-i-keep-blundering-1) |
| 3 | Why do I keep losing winning positions? | [Why Do I Keep Losing Winning Positions?](https://www.chess.com/forum/view/general/why-do-i-keep-losing-winning-positions) |
| 4 | I'm losing more games with Black. Is that true, and which opening is the problem? | [im losing more games with black](https://www.chess.com/forum/view/for-beginners/im-losing-more-games-with-black) |
| 5 | Do I play worse after losing a game? Should I stop after a loss? | [Why does our brain hate stopping on a loss, even when we know tilt will destroy our rating?](https://www.chess.com/forum/view/general/why-does-our-brain-hate-stopping-on-a-loss-even-when-we-know-tilt-will-destroy-our-rating) |
| 6 | I keep running out of time. How many games do I lose on the clock, and where does my time go? | [I keep run out of time what should I do?](https://www.chess.com/forum/view/general/i-keep-run-out-of-time-what-should-i-do) |
| 7 | How good am I compared to players at my rating? | [How good am I?](https://www.chess.com/forum/view/general/how-good-am-i) |
| 8 | Pick apart my game <a not-won game with cause = time>: where did it go wrong? | [dropped from 900 to 700 in blitz in 2 months please pick apart my games and help me](https://www.chess.com/forum/view/game-analysis/dropped-from-900-to-700-in-blitz-in-2-months-please-pick-apart-my-games-and-help-me) |
| 9 | Pick apart my game <a not-won game with cause = thrown_win>: where did it go wrong? | [dropped from 900 to 700 in blitz in 2 months please pick apart my games and help me](https://www.chess.com/forum/view/game-analysis/dropped-from-900-to-700-in-blitz-in-2-months-please-pick-apart-my-games-and-help-me) |
| 10 | Am I better at classical chess than at blitz? | designed trap |

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
