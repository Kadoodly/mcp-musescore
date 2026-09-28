// Syntax-checks the plugin's JavaScript with node.
//
// The .qml file isn't plain JavaScript, so it is converted first: imports are
// dropped, the `MuseScore { ... }` object becomes a function body,
// `property <type> x: value` becomes `var x = value`, `onRun: {` becomes
// `function onRun() {`, and the object's own settings (id, menuPath, ...) go.
// The result is written next to the system temp dir and checked with
// `node --check`.
//
//   node syntax_check.js [plugin.qml]
//
// qmlToJs() is also used by the offline plugin tests (tests/js/).

const fs = require('fs');
const os = require('os');
const path = require('path');
const { execFileSync } = require('child_process');

function qmlToJs(qml) {
    const lines = qml.split('\n');
    const out = [];
    let depth = 0;          // brace depth, to know which lines are top-level members
    let opened = false;
    for (const line of lines) {
        let text = line;
        if (!opened) {
            if (/^\s*import\s/.test(text)) { out.push(''); continue; }
            if (/^\s*MuseScore\s*\{\s*$/.test(text)) {
                out.push('function MuseScorePlugin() {');
                opened = true;
                depth = 1;
                continue;
            }
            out.push(text);
            continue;
        }
        if (depth === 1) {
            // Members of the MuseScore object
            text = text
                .replace(/^(\s*)(?:readonly\s+)?property\s+[\w<>]+\s+(\w+)\s*:\s*/, '$1var $2 = ')
                .replace(/^(\s*)onRun\s*:\s*\{/, '$1function onRun() {');
            if (/^\s*(id|menuPath|description|version|pluginType|requiresScore|title|categoryCode|thumbnailName)\s*:/.test(text)) {
                out.push('');
                continue;
            }
        }
        // Track depth, ignoring braces inside strings and comments (strings
        // first: "action://notation/paste" is not a comment).
        const code = text.replace(/"(?:[^"\\]|\\.)*"/g, '""').replace(/'(?:[^'\\]|\\.)*'/g, "''").replace(/\/\/.*$/, '');
        for (const ch of code) {
            if (ch === '{') depth++;
            else if (ch === '}') depth--;
        }
        out.push(text);
    }
    return out.join('\n');
}

function checkFile(qmlPath) {
    const js = qmlToJs(fs.readFileSync(qmlPath, 'utf8'));
    const jsPath = path.join(os.tmpdir(), path.basename(qmlPath) + '.js');
    fs.writeFileSync(jsPath, js);
    execFileSync(process.execPath, ['--check', jsPath], { stdio: 'inherit' });
    return jsPath;
}

module.exports = { qmlToJs };

if (require.main === module) {
    const qmlPath = process.argv[2] || path.join(__dirname, 'musescore-mcp-websocket.qml');
    try {
        const jsPath = checkFile(qmlPath);
        console.log('No syntax errors (node --check ' + jsPath + ')');
    } catch (e) {
        console.error('Syntax check failed');
        process.exit(1);
    }
}
