// patch-visual.js — dyslexia-friendly visual theme for Claude Code 2.1.112.
// Boxes each reply, hides tool noise, colours the prompt and the user-echo bar,
// and stops resize redraw-stacking. Colours are easy to change below.
//
// Usage: node patch-visual.js [path-to-cli.js]
const fs = require("fs");
const path = require("path");
const file = process.argv[2] ||
  path.join(__dirname, "cc-2.1.112/node_modules/@anthropic-ai/claude-code/cli.js");

// ---- COLOURS (edit these hex values to taste) ----
const REPLY_BORDER  = "#50C878"; // emerald box around each assistant reply
const USER_BAR      = "#005FFF"; // background of the bar echoing your message
const PROMPT_BORDER = "#E84545"; // the line around your input prompt
const PROMPT_TEXT   = "#FFFFFF"; // colour of the text you type
const HIDE_TOOLS    = true;      // hide tool calls + their output (set false to show)

let code = fs.readFileSync(file, "utf8");
let patches = 0;

function patch(label, search, replacement) {
  if (code.includes(replacement) && !code.includes(search)) { console.log(`[SKIP] ${label}`); return; }
  if (!code.includes(search)) { console.log(`[WARN] ${label} -- anchor not found`); return; }
  code = code.split(search).join(replacement);
  patches++;
  console.log(`[OK]   ${label}`);
}

if (HIDE_TOOLS) {
  patch('hide tool_use render',
    'case"tool_use":{let v;if(K[10]!==z||K[11]!==A',
    'case"tool_use":{return null;let v;if(K[10]!==z||K[11]!==A');
  patch('hide tool_result render',
    'case"tool_result":{let P=M-5,W;if(K[10]!==X||K[11]!==J',
    'case"tool_result":{return null;let P=M-5,W;if(K[10]!==X||K[11]!==J');
}

// Coloured background on the bar echoing your submitted message
patch('user bar (wrapper D)',
  'flexDirection:"column",marginTop:D,backgroundColor:"userMessageBackground",paddingRight:1',
  `flexDirection:"column",marginTop:D,backgroundColor:"${USER_BAR}",paddingRight:1`);
patch('user bar (wrapper X)',
  'flexDirection:"column",marginTop:X,backgroundColor:"userMessageBackground",paddingRight:1',
  `flexDirection:"column",marginTop:X,backgroundColor:"${USER_BAR}",paddingRight:1`);
patch('user bar (text)',
  'backgroundColor:j?"messageActionsBackground":w?void 0:"userMessageBackground",paddingRight:w?0:1',
  `backgroundColor:j?"messageActionsBackground":w?void 0:"${USER_BAR}",paddingRight:w?0:1`);

// Prompt: text colour + border line colour
patch('prompt text colour',
  'themeText:d7("text",K),columns:q.columns',
  `themeText:"${PROMPT_TEXT}",columns:q.columns`);
patch('prompt border colour',
  'alignItems:"flex-start",justifyContent:"flex-start",borderColor:ru(),borderStyle:"round"',
  `alignItems:"flex-start",justifyContent:"flex-start",borderColor:"${PROMPT_BORDER}",borderStyle:"round"`);

// Box around each reply (assistant prose)
patch('box around reply text',
  'alignItems:"flex-start",flexDirection:"row",marginTop:v,width:"100%",backgroundColor:V',
  `alignItems:"flex-start",flexDirection:"row",marginTop:v,width:"100%",borderStyle:"round",borderColor:"${REPLY_BORDER}",paddingX:1`);
patch('box around tool wrapper (legacy)',
  'flexDirection:"row",justifyContent:"space-between",marginTop:q6,width:"100%",backgroundColor:R',
  `flexDirection:"row",marginTop:q6,width:"100%",borderStyle:"round",borderColor:"${REPLY_BORDER}",paddingX:1`);

// Remove the leading bullet dot so reply text sits cleanly inside the box
patch('kill assistant bullet dot',
  'R=Y&&h_.default.createElement(PJ,{fromLeftEdge:!0',
  'R=!1&&h_.default.createElement(PJ,{fromLeftEdge:!0');

// Allow a multi-line statusline instead of truncating to one line
patch('statusline multi-line wrap',
  'dimColor:!0,wrap:"truncate"},S66.createElement(v5,null,O)',
  'dimColor:!0,wrap:"wrap"},S66.createElement(v5,null,O)');

// Clear the screen on resize so Ink redraws one clean frame instead of stacking
const resizeClear = `
// --- RESIZE CLEAR FIX (injected by patch-visual.js) ---
try {
  if (process.stdout && process.stdout.on) {
    process.stdout.on('resize', () => {
      try { process.stdout.write('\\x1b[3J\\x1b[2J\\x1b[H'); } catch (e) {}
    });
  }
} catch (e) {}
// --- END RESIZE CLEAR FIX ---
`;
if (!code.includes('RESIZE CLEAR FIX')) {
  const shebangEnd = code.indexOf('\n');
  code = code.substring(0, shebangEnd + 1) + resizeClear + code.substring(shebangEnd + 1);
  patches++;
  console.log('[OK]   resize clear fix prepended');
} else { console.log('[SKIP] resize clear fix'); }

if (patches > 0) { fs.writeFileSync(file, code, "utf8"); console.log(`\nSaved ${patches} patches to ${file}`); }
else { console.log("\nNo patches applied"); }
