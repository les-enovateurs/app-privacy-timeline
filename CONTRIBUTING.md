# Contributing

## Language

English for everything in the code: identifiers, comments, docstrings, test names.

Existing modules are in French. Don't translate them wholesale — convert a docstring or
a comment when you're already editing that code, leave the rest alone. Mixed is the
transitional state; new code is never French.

User-facing strings (CLI output, error messages) stay French: the operator is French.

## Comments

Short. One or two lines. A comment earns its place by saying *why*, never *what* — the
code already says what.

Write the reason, not the essay:

```python
# Google Play serves only today's version; asking for a numbered one gets a sentence
# back. Version comes from the manifest instead.
```

Not a page of measurement history. If a finding needs three paragraphs, it belongs in
`docs/journal.md`, and the comment points at it.

Docstrings: one line for most functions. Add a second paragraph only when a caller would
get it wrong without it.

## Code

Open-source expectations — someone reads this without knowing the project:

- Obvious names over clever ones. No abbreviations that aren't universal.
- Pure functions where the work is a transformation; impure boundaries thin and named
  (`gitlog`/`backfill_git`, `exodusweb`/`backfill_exodus`).
- Every rejection is recorded, never silent. An absence that reads as a fact is the
  failure mode this project exists to avoid.
- No command writes without `--write`.

## Tests

`pytest`. Test names say the behaviour, in English:
`test_a_sentence_is_not_a_version`, not `test_parse_versions_2`.

External binaries and network are injected, never touched by the suite.

## Data and extensions

Data (corpus, snapshots, caches) is read from the current directory; reference files
(`signatures/*.txt`, `corrections.json`, `schema/`) from this checkout. Other packages can
add subcommands through the `apklab.commands` entry point.

## Secrets

Never a token in argv typed by hand, in a run report, or in a fixture.
