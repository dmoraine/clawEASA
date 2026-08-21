# EASA answering notes

## Retrieval-first workflow

1. If the user gives an exact reference, run `claw-easa lookup <REF>`, or
   `claw-easa lookup <REF> --json` when you are going to quote the provision.
2. If the user gives a topic or phrase, start with `refs`, `snippets`, or `hybrid`.
3. Use `ask` only after retrieval when a synthesized answer is useful.

## Output discipline

- Prefer exact wording when the user asks for the content of a regulation point.
- Separate clearly:
  - binding regulation text
  - AMC/GM guidance
  - FAQ material
- Include the reference identifier in the answer.
- If you only found related material, say that explicitly.
- If nothing relevant was retrieved, say so explicitly.

## Citing from `lookup --json`

One JSON document is emitted, whatever the outcome, and `status` says which:

- `found` — `entry` holds the whole provision. `text` is what to quote;
  `slug`, `revision`, `page_url`, `part`, `subpart` and `regulation` are what
  to cite it with. Any of them may be `null`, meaning the corpus does not
  record it — say so rather than filling the gap.
- `ambiguous` — the reference is held by more than one corpus and `entry` is
  null. Do not pick one: `matches` lists the candidates, so re-run with
  `--slug <corpus>`, or tell the user which corpora state it.
- `not_found` — the corpus does not hold the reference. Say so; do not answer
  from a neighbouring or truncated reference.
