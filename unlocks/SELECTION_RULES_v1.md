# Token-unlock event list: selection rules (v1)

Approved by the user 2026-11-05 02:14 (UTC+5), before any unlock-event price was
examined. Changes after this point create v2.

## Universe
All Bybit USDT-perpetual instruments with launchTime between 2022-07-01 and
2024-03-31 that were still Trading on 2026-11-05 (instruments_linear_usdt.json).
Count: 86.

## Filters (each exclusion is logged with its reason before prices are examined)
1. New token: public trading of the token began at most 120 days before its Bybit
   perp launch. Excludes older tokens that were simply listed late.
2. Vesting: the token has a documented team and/or investor allocation with a cliff.
   Excludes tokens without such vesting (most memecoins, mined coins).

## Event definition
- One event per token: the FIRST cliff unlock of team and/or investor allocations
  (date = TGE + cliff, as documented).
- Size = tokens unlocked at that cliff as % of circulating supply on that date.
- Keep only events with size >= 2% of circulating supply.
- Monthly linear releases are ignored.

## Sourcing and quality
- Sources: official tokenomics docs, whitepapers, project blog posts; reputable news
  reporting the specific unlock as corroboration. Link recorded per entry.
- Grade: CLEAR (date and size directly stated) or INFERRED (derived from a schedule).
  The primary test uses CLEAR entries only.
- Flag any token whose schedule was changed after TGE.

## Split
Events dated before 2025-01-01: development. From 2025-01-01: holdout.

## Known limits (stated in advance)
- Survivorship: only perps still trading in Nov 2026.
- The researcher (Claude) has general background knowledge of how some of these
  tokens performed; the rule-based list limits, but cannot remove, that exposure.
