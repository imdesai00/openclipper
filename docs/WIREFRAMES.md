# Low-fi wireframes (Milestone 0)

Desktop-first; dark/light; one accent colour. `[ ]` = control, `░` = media.

## Dashboard
```
┌ sidebar ───┬───────────────────────────────────────────────────────┐
│ Dashboard  │ Projects 4 | Sources 12 | Clips 31 | Jobs 2 running   │
│ Projects   │ AI spend (est.) $0.00 | Storage 41/100 GB [▓▓▓░░░]   │
│ Clips      │ Recent activity ──────────────────────────────────────│
│ Providers  │  • Source "Ep 12" transcribed  2m ago                 │
│ Publishing │ (empty state when nothing exists, no fake zeros)     │
│ Automation │                                                        │
│ Storage    │                                                        │
│ Settings   │                                                        │
└────────────┴───────────────────────────────────────────────────────┘
```
## New import
```
Project: [Podcast ▾]
( ) Upload file  [ drop zone / choose ]  resumable, shows % + resume
( ) Direct URL   [ https://... ]          (SSRF-checked)
( ) YouTube link [ ... ]  ← only visible if admin enabled `ingest`
[x] I own this content or have permission to use it   (required)
[ Start ]
```
## Source details / processing
```
Ep 12 — 1:42:10 — proxy ready ▶ player
Stages: ✔ validate ✔ proxy ✔ audio ◐ transcribe 63% ○ scenes ○ highlights
[Retry stage] [Cancel]        Provider: Ollama (local)
```
## Highlight review
```
Prompt: [ Find the 5 best discussions about AI, 30–60 s ]  [Find]
┌ score 82 "AI relevance score, not an engagement prediction" ───────┐
│ 12:03–12:51 (48s)  Title / Hook (editable)  Category: opinion      │
│ "transcript text pulled from source…"   Why: …   Standalone: yes   │
│ [Preview] [Open in editor] [Reject]                                │
└────────────────────────────────────────────────────────────────────┘
```
## Editor
```
┌ preview (proxy, 9:16 overlay + crop box) ┬ transcript ───────────────┐
│ ░░░░░░░░                                 │ click word = seek         │
│ [ratio 9:16 ▾] [track speaker ☐]         │ select words = set range  │
├──────────────────────────────────────────┴───────────────────────────┤
│ timeline: waveform ▁▂▅▇▅▂▁  scene markers |  [I]n ────── [O]ut      │
├ captions: template [ ▾ ] font size color position  | metadata tab ──┤
│ [Server preview] [Save version] [Render]     J/K/L space I/O keys    │
└──────────────────────────────────────────────────────────────────────┘
```
## Providers
```
[+ Add provider]  Ollama ● reachable   OpenRouter ○ no key (free models: "rate-limited" badge)
Per-task routing: highlight [model ▾] metadata [model ▾] ...  Mode: LOCAL|REMOTE|HYBRID
Keys shown masked only: sk-…abcd
```
## Publishing / Storage / Settings / Automation
Publishing: calendar + queue with states draft→published; account cards with "re-authorize" warnings; unverified-YouTube-project banner ("uploads will be private until audit").
Storage: usage bar, per-kind table, retention form, [Dry run] → list of what would be deleted, [Run cleanup].
Automation: trigger → steps builder, mode selector, global [Emergency pause].
Unimplemented features are hidden, not greyed out.
