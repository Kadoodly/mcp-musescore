// Serves the plugin, running on the mock MuseScore API, over stdin/stdout:
// one JSON request per line in, one JSON reply per line out, as the
// websocket would. Used by the Python end-to-end tests (tests/test_end_to_end.py)
// and for dry runs of the live tests.
//
//   node tests/js/mock_server.js '{"nstaves": 3, "bars": 8}'
//
// A line {"__mock": "<js>"} evaluates <js> with `ms` (the MockMuseScore) in
// scope and answers {"mock": <value>}: tests use it to act as the user
// editing in MuseScore, or to look at the mock's state.

'use strict';

const readline = require('readline');
const { MockMuseScore } = require('./mock_musescore.js');

const ms = new MockMuseScore(JSON.parse(process.argv[2] || '{}'));
ms.loadPlugin();

const rl = readline.createInterface({ input: process.stdin });
rl.on('line', line => {
    if (!line.trim()) return;
    let out;
    try {
        const msg = JSON.parse(line);
        if (msg && typeof msg.__mock === 'string') {
            // eslint-disable-next-line no-new-func
            const value = new Function('ms', 'return (' + msg.__mock + ');')(ms);
            out = { mock: value === undefined ? null : value };
        } else {
            ms.plugin.get('processMessage')(line, 1);
            out = ms.replies.pop();
        }
    } catch (e) {
        out = { status: 'error', message: 'mock_server: ' + e.message };
    }
    process.stdout.write(JSON.stringify(out) + '\n');
});
