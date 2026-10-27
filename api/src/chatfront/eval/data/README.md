# Language evaluation set (feature 121, part of 111)

One file per Tier 1 language: ten text conversations and two voice notes, in the way people
in that country write (Roman Urdu with English words, Arabizi with 3 and 7, and so on).

- The messages were drafted with a model and still need a native speaker to read them; replace
  any line that no real customer would write.
- Voice notes: `voice/<id>.ogg` is a recording of the transcript, in a natural voice. Until the
  file exists the case is reported as "pending" and does not count.
- `python -m chatfront.eval.languages` runs it against the mock (routing and detection only);
  `--live` runs it against the real models and is the only run that can mark a language
  "supported". Results go to `i18n/eval-results.json`.
