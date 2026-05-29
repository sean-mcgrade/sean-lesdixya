# Sean Lesdixya — a dyslexia-friendly theme for Claude Code

Walls of monospace text are hard to read with dyslexia. This is a small set of
patches that make Claude Code visually navigable:

- **Every reply is wrapped in a coloured box** (emerald by default) so you can
  see where one answer ends and the next begins.
- **The text you type is coloured** and your input prompt has a coloured border.
- **Your last message is echoed back in a bold coloured bar** ("YOU SAID: …") so
  you always know what Claude is responding to.
- **Tool calls and their raw output are hidden** — you see plain answers, not
  programming. (Toggle off in `patch-visual.js`.)
- **Resize no longer stacks duplicate copies** of the conversation.
- Runs on the **Opus 4.8** model with **extended thinking ON** — not a stripped
  chatbot.

All colours are plain hex values at the top of `patch-visual.js` — change them to
whatever you can read best.

## Important: this is pinned to Claude Code v2.1.112

Claude Code switched from editable JavaScript to a compiled binary at v2.1.120.
**v2.1.112 is the last version we can patch.** These patches are written against
that exact version and **will not work on newer ones** without being redone.
That is the trade-off for getting the model + thinking + a readable UI together.

## Requirements

- Windows + PowerShell (the launchers are `.ps1`; the patches are plain Node and
  work anywhere)
- [Node.js](https://nodejs.org) (v18+)
- A Claude account / `ANTHROPIC_API_KEY` with access to Opus 4.8
- [Windows Terminal](https://aka.ms/terminal) recommended (truecolor + box chars)

## Install

```
.\setup.ps1
```

This installs a private copy of Claude Code 2.1.112 into `./cc-2.1.112` and
applies the patches. It does **not** touch any global Claude install.

## Run

```
.\launch.ps1
```

Set `ANTHROPIC_API_KEY` first (or be logged in to Claude).

## Customise

Open `patch-visual.js`, edit the colour constants at the top, then re-run
`.\setup.ps1`.

| Setting | Default | Meaning |
|---|---|---|
| `REPLY_BORDER` | `#50C878` | box around each reply |
| `USER_BAR` | `#005FFF` | bar echoing your message |
| `PROMPT_BORDER` | `#E84545` | border around your input |
| `PROMPT_TEXT` | `#FFFFFF` | colour of the text you type |
| `HIDE_TOOLS` | `true` | hide tool calls + output |

## How it works

`patch-thinking.js` rewrites the API request so the older 2.1.112 client speaks
Opus 4.8's `adaptive` thinking format. `patch-visual.js` does targeted string
replacements on Claude Code's `cli.js` to add the borders, colours, and layout
fixes. Both are idempotent and only edit the local `cli.js`.

## Licence

MIT — see [LICENSE](LICENSE). Made for accessibility; use it however helps.
