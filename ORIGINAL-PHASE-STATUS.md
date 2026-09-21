# YRD Alpha implementation status

This package is a concrete structure installer, not completion of the 56-section blueprint. No live Discord changes were made while preparing it.

| Phase | Ready in this package | Still required |
| --- | --- | --- |
| 1 | Roles, channels, access policy, welcome/rules/risk text, offline tests, live installer checks | Run with owner authorization; Gatekeeper runtime, secure manual fallback, moderation/raid/phishing controls, native server security configuration, real role tests |
| 2 | Public unreviewed feed, reviewed feed and owner/admin review channels | Scout runtime, new-launch and older-token engines, signed Discord interactions, ID-bound reviewers, persistent candidate state machine, permitted sources, missing-data status |
| 3 | Rug, whale, liquidity, holder, network and token-event channels | On-chain adapters, contract/token-program checks, risk evidence, cluster analysis, freshness, emergency events; never infer safety from unavailable data |
| 4 | Risk/journal/after-action channels and disclosure text | Risk calculator, append-only journal DB, timestamped observations with defined windows, actual reviewer identity, deterministic after-action evidence |
| 5 | Paper channels, community, mentors, challenges and leaderboard area | Paper execution/slippage model, separate fake-money balances, drawdown-aware ranking, community workflows and referral anti-abuse |
| 6 | Private Premium area, support and booking entry channels, English/Spanish preference roles | Private tickets/evidence/P&L, booking availability and reminders, approved FAQ assistant, translation, membership entitlements; no billing until pricing/platform approved |
| 7 | Owner control, analytics, status, incident and update channels; pre-change snapshot | Runtime health, audit capture, periodic backups and restore testing, retention, safe archive, source reliability, deduplication, pause/shutdown, advanced signal aggregation |

## Decisions preserved for implementation

- Use four identities: YRD Alpha Scout, YRD Security, YRD Assistant, YRD System.
- Public Scout candidates may precede approval but must prominently state Yurman has not reviewed them.
- Approvals are research review, not executable buy instructions. Admin reviews must not be falsely attributed to Yurman.
- Reject stale/replayed component interactions. Check guild ID, actor ID, approved-admin allowlist and current candidate state on every review action. Moderators are not reviewers by default.
- Candidate identity is chain + contract, never ticker alone. Distinguish market cap from fully diluted valuation and attach sources/timestamps.
- Unknown contract/social/wallet data lowers evidence coverage; it must not count as a passed check. Scores are heuristic evidence summaries, not win probabilities.
- Provider sources can fail independently. Show unavailable/stale data, stop dependent scoring, and continue only explicitly independent monitoring.
- Journal losses and rejections as well as wins. Keep append-only corrections, fees/slippage assumptions, initial conditions and a fixed evaluation horizon.
- Track approved/watchlisted tokens beyond launch. Use a deduplicated event stream with idempotent updates and escalation for independent evidence, not repeated copies of one source.
- All personal P&L, billing information, evidence and support details must be private by default. Public feedback is not anonymous until an actual privacy-preserving intake exists.
- Do not claim an 80–90% wallet win rate without a verifiable sample, closed-trade accounting, time window, costs and treatment of transfers/open positions.
- No token purchases, wallet signing, real-money trading or return promises are part of this server setup.
