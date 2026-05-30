// patch-thinking.js — make Claude Code 2.1.112 speak Opus 4.8's adaptive-thinking
// format. 2.1.112 is the last version shipped as editable JavaScript; it sends the
// old thinking.type:"enabled" which Opus 4.8 rejects. We rewrite it to "adaptive".
//
// Usage: node patch-thinking.js [path-to-cli.js]
const fs = require("fs");
const path = require("path");
const file = process.argv[2] ||
  path.join(__dirname, "cc-2.1.112/node_modules/@anthropic-ai/claude-code/cli.js");

let code = fs.readFileSync(file, "utf8");
let patches = 0;

function patch(label, search, replacement) {
  if (code.includes(replacement) && !code.includes(search)) { console.log(`[SKIP] ${label}`); return; }
  if (!code.includes(search)) { console.log(`[WARN] ${label} -- anchor not found`); return; }
  code = code.split(search).join(replacement);
  patches++;
  console.log(`[OK]   ${label}`);
}

// Normalize thinking -> adaptive (+ output_config.effort) at the SDK create() entry,
// for any model (the built-in gate excludes newer models like 4.8).
// Also strip stale thinking/redacted_thinking blocks from prior assistant turns:
// the old client mangles them on replay, causing "thinking blocks cannot be
// modified" 400s. Removing them avoids that (not needed once a tool loop ends).
patch('normalize thinking->adaptive + strip history (Y create)',
  'let A=this._client._options.timeout;if(!Y.stream',
  'if(Y.thinking&&Y.thinking.type==="enabled"){Y.thinking={type:"adaptive"};Y.output_config=Object.assign({effort:"high"},Y.output_config)}if(Y.thinking&&Y.thinking.type==="adaptive"&&Array.isArray(Y.messages))Y.messages.forEach(m=>{if(m&&m.role==="assistant"&&Array.isArray(m.content))m.content=m.content.filter(b=>!(b&&(b.type==="thinking"||b.type==="redacted_thinking")))});let A=this._client._options.timeout;if(!Y.stream');
patch('normalize thinking->adaptive + strip history (q create)',
  'let _=this._client._options.timeout;if(!q.stream',
  'if(q.thinking&&q.thinking.type==="enabled"){q.thinking={type:"adaptive"};q.output_config=Object.assign({effort:"high"},q.output_config)}if(q.thinking&&q.thinking.type==="adaptive"&&Array.isArray(q.messages))q.messages.forEach(m=>{if(m&&m.role==="assistant"&&Array.isArray(m.content))m.content=m.content.filter(b=>!(b&&(b.type==="thinking"||b.type==="redacted_thinking")))});let _=this._client._options.timeout;if(!q.stream');
// Token-counter pre-flight literals.
patch('token-counter literal -> adaptive',
  '{type:"enabled",budget_tokens:Jz7}',
  '{type:"adaptive"}');

if (patches > 0) { fs.writeFileSync(file, code, "utf8"); console.log(`\nSaved ${patches} patches to ${file}`); }
else { console.log("\nNo patches applied"); }
