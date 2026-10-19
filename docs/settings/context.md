# Project context (for the postdoc)

The postdoc answers your questions about the whole project. What it knows about the project comes from `context/` (git-ignored, never committed; anything in it is sent to your AI provider when the postdoc reads it).

```
context/
  context.md        the project in plain words: aims, hypotheses, samples, what matters
  instructions.md   how the postdoc uses all this: which document is for what, focus, language
  documents/        work plan, project files, papers (PDF, DOCX, Markdown, text); read-only for agents
```

```bash
labpipe context init     # creates the folder and the two files from templates
labpipe context index    # after adding documents: text with page numbers, for citing
```

Write `context.md` yourself, or ask your agent: *let's write the project context*. It drafts, you correct, it saves only with your OK.

## You say → setting

| You say | Setting |
|---|---|
| "the work plan defines the aims" / "papers only for reference values" | `instructions.md` |
| "answer in Croatian" / "always end with next measurements" | `instructions.md` |
| "S-9 was meant as the Pt-rich end member" | `context.md` |
| "here is a new paper" | put it in `documents/`, then `labpipe context index` |

`instructions.md` steers focus and form; it cannot loosen the rules (no invented numbers, observations apart from interpretations).

## After a change

Nothing reruns. The report marks the postdoc's last answer "out of date" until you ask again.
