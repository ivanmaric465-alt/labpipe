# Agent instructions of this lab

Add to an agent role, or add a role of your own. After a change, run `labpipe project sync`.
labpipe's own roles come with the code (`.labpipe/agents/`, read-only). The roles are
`supervisor`, `postdoc` and one `<method>-analyst` per method.

**Add instructions to a role.** Create `lab/agents/<role>.md` with the role's name, e.g.
`lab/agents/xrd-analyst.md`, containing plain sentences:

```markdown
In notes, call the support "Vulcan" and give lattice parameters in Å.
```

They are appended to that role's instructions, under "This lab's additions". The file name
must be an existing role: a name that matches no role is reported by `labpipe project sync`.

**Add a role.** Create `lab/agents/<new-role>.md` that starts with frontmatter, like the
files in `.labpipe/agents/`:

```markdown
---
name: tem-reader
role: analyst
description: Reads TEM image descriptions and writes short notes.
capabilities: [shell, read, write]
---
Instructions of the role.
```

For all agents at once, use `lab/LAB.md`.
