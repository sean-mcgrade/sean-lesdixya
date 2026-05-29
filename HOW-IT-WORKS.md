# How it works — and a prompt to recreate it

This is the technical story behind **Sean Lesdixya**, written so anyone can read
it, share it, or hand it to an AI assistant to reproduce the work. It contains
**no secrets** — no API keys, no IP addresses, no machine names.

## The problem

Reading long blocks of monospace terminal text is hard with dyslexia. The goal
was to make Claude Code visually navigable **without** dropping to a weaker model:

- every reply in a coloured box
- the text you type in its own colour, on a coloured prompt line
- your last message echoed back in a bold coloured bar
- tool calls / raw command output hidden
- the **Opus 4.8** model with **extended thinking ON** — not a stripped chatbot

## The core obstacle

Claude Code changed how it ships:

- Up to **v2.1.112** it shipped as editable JavaScript (`cli.js`) you can patch.
- From **v2.1.120** it ships as a compiled binary — no editable JS.
- The newest models (e.g. Opus 4.8) use a new **adaptive** thinking request format.
  The older v2.1.112 client sends the old `thinking.type: "enabled"` format,
  which the new model rejects with a 400 error telling you to use
  `thinking.type: "adaptive"` plus `output_config.effort`.

So you're squeezed: the only **patchable** client is too old to speak the new
model's thinking format, and the only client that speaks it natively can't be
patched for the UI. The trick is to bridge that gap.

## The approach

1. **Install the last patchable version** (v2.1.112) into a local folder — not a
   global install.
2. **Patch the request** so the old client speaks the new thinking format.
   At the SDK's request-builder entry, when a request carries
   `thinking.type:"enabled"`, rewrite it to `{type:"adaptive"}` and attach
   `output_config:{effort:"high"}`. Also fix the small token-counting pre-flight
   requests that hardcode the old format. Result: Opus 4.8 + thinking works on
   the old client.
3. **Patch the UI** with targeted string replacements on `cli.js`:
   - wrap the assistant message component in a rounded coloured border (the box)
   - set the input text colour and the prompt border colour
   - set a background colour on the bar that echoes your submitted message
   - return `null` from the tool-use / tool-result render branches to hide the
     "programming"
   - change the statusline from truncate to wrap (multi-line dashboards)
   - on terminal resize, clear the screen so the UI redraws one clean frame
     instead of stacking duplicate copies
4. **Add an accessibility system prompt** so every answer opens with a bold
   coloured echo of your words and a one-line plain-language summary, hides code
   from chat, and keeps prose short.

All patches are **idempotent** and only edit the local `cli.js`. Colours live as
plain hex constants at the top of the visual patch so anyone can change them.

## Important caveat

Everything here is pinned to **Claude Code v2.1.112**. The exact code anchors the
patches target are specific to that version and **will not match newer releases**.
That pin is the price of getting model + thinking + a readable UI in one place.

---

## A reusable prompt

You can paste this to an AI assistant to recreate the approach in your own setup:

> I have dyslexia and find Claude Code's plain terminal output hard to read. I
> want each reply wrapped in a coloured box, the text I type in its own colour on
> a coloured prompt line, my last message echoed in a bold coloured bar, tool
> output hidden, and it must run the latest model **with extended thinking on** —
> not a weaker fallback.
>
> Constraints I've found: Claude Code ships as editable JavaScript only up to
> v2.1.112 (compiled binary after). v2.1.112 sends the old `thinking.type:"enabled"`
> format, which the newest model rejects in favour of `thinking.type:"adaptive"`
> plus `output_config.effort`.
>
> Please: (1) install v2.1.112 locally, (2) patch its request builder so
> `enabled` thinking is rewritten to `adaptive` + `output_config.effort`,
> (3) patch the renderer to add the boxes/colours and hide tool output, and
> (4) add a short accessibility system prompt. Verify with a non-interactive
> run that the model actually used is the new one and that real thinking happens,
> before claiming it works. Keep all patches idempotent and editable.

That's the whole method — no secrets required.
