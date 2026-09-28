// Offline tests of the plugin's JavaScript against the mock MuseScore API
// (tests/js/mock_musescore.js). Run: node tests/js/test_plugin.js
// They check the plugin's own logic (validation, splitting, the write/verify
// loop, tie handling, batching); what MuseScore itself does is only checked by
// the live test.
//
// With --plans, reads JSON cases from stdin and prints the plugin's duration
// parsing and piece planning for them (used by tests/test_plugin_js.py to
// compare with src/utils/durations.py).

'use strict';

const assert = require('assert');
const { MockMuseScore, WHOLE } = require('./mock_musescore.js');

const Q = 480;   // quarter note in ticks

function fresh(opts) {
    const ms = new MockMuseScore(opts);
    ms.loadPlugin();
    return ms;
}

function notes(pitches, duration, tie) {
    const ev = { pitches, duration };
    if (tie !== undefined) ev.tie = tie;
    return ev;
}

function ok(result) {
    assert.ok(result && !result.error, 'expected success, got ' + JSON.stringify(result && result.error));
    return result;
}

function fails(result, pattern) {
    assert.ok(result && result.error, 'expected an error, got ' + JSON.stringify(result).slice(0, 300));
    if (pattern) assert.match(result.error, pattern);
    return result;
}

// Asserts [ticks, pitches|'rest', tiedForward?] rows for a track.
function expectTrack(ms, track, from, to, rows) {
    const got = ms.dump(track, from, to).map(c => [c.tick, c.ticks, c.rest ? 'rest' : c.pitches, c.tiedForward, c.tiedBack]);
    const want = rows.map(r => [r[0], r[1], r[2], r[3] || [], r[4] || []]);
    assert.deepStrictEqual(got, want);
}

const tests = [];
function test(name, fn) { tests.push({ name, fn }); }

// ---------------------------------------------------------------------------
// Validation
// ---------------------------------------------------------------------------

test('unknown action is an error', () => {
    const ms = fresh();
    fails(ms.call('addNotes', { pitch: 60 }), /Unknown command: addNotes/);
});

test('unknown param is an error and nothing is written', () => {
    const ms = fresh();
    const before = ms.snapshot();
    fails(ms.call('addNote', { pitch: 60, duration: '1/4', tied: true }), /addNote: unknown parameter 'tied'/);
    fails(ms.call('addRest', { duration: '1/4', tie: true }), /addRest: unknown parameter 'tie'/);
    fails(ms.call('getScore', { format: 'json' }), /getScore: unknown parameter 'format' \(it takes no parameters\)/);
    assert.strictEqual(ms.snapshot(), before);
    assert.strictEqual(ms.counters.startCmd, 0);
});

test('unknown top-level command field is an error', () => {
    const ms = fresh();
    fails(ms.raw({ action: 'ping', params: {}, atomic: true }), /Command: unknown parameter 'atomic'/);
    fails(ms.raw({ action: 'ping', params: [1] }), /params must be an object/);
    assert.strictEqual(ms.raw({ action: 'ping' }), 'pong');
});

test('every whitelisted action is dispatched, and every dispatched action whitelisted', () => {
    const fs = require('fs');
    const path = require('path');
    const qml = fs.readFileSync(path.join(__dirname, '..', '..', 'musescore-mcp-websocket.qml'), 'utf8');
    const body = qml.slice(qml.indexOf('function processCommand('), qml.indexOf('// UTILITY FUNCTIONS'));
    const cases = Array.from(body.matchAll(/case "(\w+)":/g)).map(m => m[1]).sort();
    const ms = fresh();
    const allowed = Object.keys(ms.plugin.get('actionParams')).sort();
    assert.deepStrictEqual(cases, allowed);
    for (const a of ms.plugin.get('sequenceCommands')) assert.ok(allowed.includes(a), a);
});

test('sequence steps are all checked before any runs', () => {
    const ms = fresh();
    const before = ms.snapshot();
    const r = ms.call('processSequence', { sequence: [
        { action: 'addNote', params: { pitch: 60, duration: '1/4' } },
        { action: 'addNote', params: { pitch: 62, duration: '1/4', bogus: 1 } },
    ] });
    fails(r, /Step 1: addNote: unknown parameter 'bogus'/);
    assert.strictEqual(ms.snapshot(), before);
});

test('bad durations are errors before any command opens', () => {
    const ms = fresh();
    const before = ms.snapshot();
    for (const [d, pat] of [
        ['1/12', /needs a tuplet/], ['5/24', /needs a tuplet/], ['1/256', /multiple of 1\/128/],
        ['0/4', /greater than zero/], ['1/0', /zero denominator/], ['abc', /must look like/], ['1.5/4', /must look like/],
        [{ numerator: 1, denominator: 4, dots: 1 }, /unknown parameter 'dots'/], [{ numerator: 1.5, denominator: 4 }, /integers/],
        [0.25, /must be "n\/d"/],
    ]) {
        fails(ms.call('addNote', { pitch: 60, duration: d }), pat);
        fails(ms.call('writeVoice', { events: [notes([60], d)] }), pat);
    }
    assert.strictEqual(ms.snapshot(), before);
    assert.strictEqual(ms.counters.startCmd, 0);
});

test('bad writeVoice events are errors and nothing is written', () => {
    const ms = fresh();
    const before = ms.snapshot();
    const bad = [
        [[], /non-empty list/],
        [[{ pitches: [128], duration: '1/4' }], /0-127/],
        [[{ pitches: [60, 60], duration: '1/4' }], /twice/],
        [[{ pitches: [], duration: '1/4' }], /non-empty list of MIDI pitches/],
        [[{ duration: '1/4' }], /give "pitches"/],
        [[{ rest: true, pitches: [60], duration: '1/4' }], /rest can't have pitches/],
        [[{ rest: true, duration: '1/4', tie: true }], /rest can't be tied/],
        [[{ pitches: [60], duration: '1/4', tied: true }], /Event 0: unknown parameter 'tied'/],
        [[{ pitches: [60], duration: '1/4', tie: [62] }], /not in its pitches/],
        [[notes([60], '1/4', true), { rest: true, duration: '1/4' }], /event 1 is a rest/],
        [[notes([60, 64], '1/4', true), notes([60, 65], '1/4')], /ties pitch 64, which event 1 doesn't contain/],
        [[{ pitches: [60] }], /missing duration/],
    ];
    for (const [events, pat] of bad) fails(ms.call('writeVoice', { events }), pat);
    fails(ms.call('writeVoice', { events: [notes([60], '1/4')], voice: 4 }), /Invalid voice/);
    fails(ms.call('writeVoice', { events: [notes([60], '1/4')], staff: 9 }), /Invalid staff/);
    fails(ms.call('writeVoice', { events: [notes([60], '1/4')], staff: '1' }), /Invalid staff/);
    fails(ms.call('writeVoice', { events: [notes([60], '1/4')], measure: 99 }), /Invalid measure/);
    fails(ms.call('addNote', { pitch: 60, duration: '1/4', tie: 'yes' }), /tie must be true or false/);
    assert.strictEqual(ms.snapshot(), before);
});

// ---------------------------------------------------------------------------
// Duration planning
// ---------------------------------------------------------------------------

test('durations split at barlines, then greedily', () => {
    const ms = fresh();
    const plan = ms.plugin.get('planPieces');
    const bars = [{ startTick: 0, endTick: WHOLE }, { startTick: WHOLE, endTick: 2 * WHOLE }];
    // JSON round trip: arrays made inside the plugin's sandbox have another prototype
    const texts = (start, len) => JSON.parse(JSON.stringify(plan(start, len, bars).map(p => [p.tick, p.ticks])));
    assert.deepStrictEqual(texts(0, 1200), [[0, 960], [960, 240]]);            // 5/8 = 1/2 + 1/8
    assert.deepStrictEqual(texts(0, 540), [[0, 480], [480, 60]]);              // 9/32 = 1/4 + 1/32
    assert.deepStrictEqual(texts(3 * Q, 2 * Q), [[3 * Q, Q], [WHOLE, Q]]);     // half from beat 4
    assert.deepStrictEqual(texts(0, 720), [[0, 720]]);                         // dotted quarter
    assert.deepStrictEqual(texts(0, 840), [[0, 840]]);                         // double-dotted quarter
    assert.deepStrictEqual(texts(Q, 1440), [[Q, 1440]]);                        // dotted half from beat 2
    assert.deepStrictEqual(texts(2 * Q, 1440), [[2 * Q, 960], [WHOLE, 480]]);  // dotted half from beat 3
});

// ---------------------------------------------------------------------------
// writeVoice
// ---------------------------------------------------------------------------

test('writeVoice: 128 notes in one call, one command, one undo step, one UI update', () => {
    const ms = fresh({ bars: 2 });
    const scale = [60, 62, 64, 65, 67, 69, 71, 72];
    const events = [];
    for (let i = 0; i < 128; i++) events.push(notes([scale[i % 8]], '1/16'));
    const undoBefore = ms.undoStack.length;
    const r = ok(ms.call('writeVoice', { events, staff: 0, voice: 0, measure: 1 }));
    assert.strictEqual(r.written, 128);
    assert.strictEqual(r.endTick, 128 * 120);
    assert.strictEqual(r.startMeasure, 1);
    assert.strictEqual(r.endMeasure, 8);                    // 6 bars appended
    assert.strictEqual(ms.state.measures.length, 8);
    assert.strictEqual(ms.counters.startCmd, 1);
    assert.strictEqual(ms.undoStack.length, undoBefore + 1);
    assert.strictEqual(ms.counters.selClear, 1);            // the view is updated once (showCursor)
    assert.ok(ms.counters.firstMeasure <= 3, 'whole-score walks: ' + ms.counters.firstMeasure);
    const got = ms.dump(0);
    assert.strictEqual(got.length, 128);
    got.forEach((c, i) => {
        assert.strictEqual(c.tick, i * 120);
        assert.strictEqual(c.ticks, 120);
        assert.deepStrictEqual(c.pitches, [scale[i % 8]]);
    });
    assert.strictEqual(r.cursor.tick, 128 * 120);
});

test('writeVoice: chords, rests, dotted notes', () => {
    const ms = fresh();
    ok(ms.call('writeVoice', { measure: 1, events: [
        notes([48, 52, 55], '3/8'), notes([50], '1/8'), { rest: true, duration: '1/4' }, notes([60, 64], '7/32'), notes([62], '1/32'),
    ] }));
    expectTrack(ms, 0, 0, WHOLE, [
        [0, 720, [48, 52, 55]], [720, 240, [50]], [960, 480, 'rest'], [1440, 420, [60, 64]], [1860, 60, [62]],
    ]);
});

test('writeVoice: 5/8 and 9/32 are split and tied, never shortened', () => {
    const ms = fresh();
    const r = ok(ms.call('writeVoice', { measure: 1, events: [notes([60], '5/8'), notes([62], '9/32'), notes([64], '3/32')] }));
    assert.deepStrictEqual(r.split, [
        { event: 0, duration: '5/8', writtenAs: ['1/2', '1/8'] },
        { event: 1, duration: '9/32', writtenAs: ['1/4', '1/32'] },
    ]);
    expectTrack(ms, 0, 0, WHOLE, [
        [0, 960, [60], [60]], [960, 240, [60], [], [60]],
        [1200, 480, [62], [62]], [1680, 60, [62], [], [62]],
        [1740, 180, [64]],
    ]);
});

test('writeVoice: a note crossing a barline is split at the barline and tied', () => {
    const ms = fresh();
    ok(ms.call('writeVoice', { measure: 1, events: [notes([60], '3/4'), notes([67, 72], '1/2'), notes([65], '3/4'), notes([64], '1/1')] }));
    expectTrack(ms, 0, 0, 4320, [
        [0, 1440, [60]],
        [1440, 480, [67, 72], [67, 72]], [1920, 480, [67, 72], [], [67, 72]],
        [2400, 1440, [65]],                                   // dotted half from beat 2 fits its bar
        [3840, 1920, [64]],
    ]);
    ok(ms.call('writeVoice', { measure: 1, events: [notes([60], '3/4'), notes([62], '1/1')] }));
    expectTrack(ms, 0, 0, 3360, [[0, 1440, [60]], [1440, 480, [62], [62]], [1920, 1440, [62], [], [62]]]);
});

test('writeVoice: ties within a bar, across a barline, repeated, and on chords', () => {
    const ms = fresh();
    ok(ms.call('writeVoice', { measure: 1, events: [
        notes([60], '1/4', true), notes([60], '1/4', true), notes([60], '1/4', true), notes([60], '1/4', true),   // C~C~C~C~
        notes([60, 64, 67], '1/2', [60, 64]), notes([60, 64, 69], '1/2'),                                       // partial chord tie
    ] }));
    expectTrack(ms, 0, 0, 2 * WHOLE, [
        [0, 480, [60], [60]], [480, 480, [60], [60], [60]], [960, 480, [60], [60], [60]], [1440, 480, [60], [60], [60]],
        [1920, 960, [60, 64, 67], [60, 64], [60]], [2880, 960, [60, 64, 69], [], [60, 64]],
    ]);
    // every tie ends on the same pitch, same voice, at the next chord
    for (const c of ms.dump(0)) for (const t of c.tieTargets) {
        assert.strictEqual(t.track, 0);
        assert.strictEqual(t.pitch2, t.pitch);
        assert.strictEqual(t.tick, c.tick + c.actual);
    }
});

test('writeVoice: ties combined with unusual durations', () => {
    const ms = fresh();
    ok(ms.call('writeVoice', { measure: 1, events: [notes([62], '5/8', true), notes([62], '9/32', true), notes([62], '3/32')] }));
    expectTrack(ms, 0, 0, WHOLE, [
        [0, 960, [62], [62]], [960, 240, [62], [62], [62]], [1200, 480, [62], [62], [62]], [1680, 60, [62], [62], [62]],
        [1740, 180, [62], [], [62]],
    ]);
});

test('writeVoice: an empty second voice, and two voices with ties', () => {
    const ms = fresh();
    ok(ms.call('writeVoice', { measure: 1, voice: 0, events: [notes([72], '1/2', true), notes([72], '1/2')] }));
    ok(ms.call('writeVoice', { measure: 1, voice: 1, events: [notes([48], '3/4', true), notes([48], '1/2'), notes([43], '1/1')] }));
    expectTrack(ms, 0, 0, WHOLE, [[0, 960, [72], [72]], [960, 960, [72], [], [72]]]);
    expectTrack(ms, 1, 0, 2 * WHOLE + 480, [
        [0, 1440, [48], [48]], [1440, 480, [48], [48], [48]], [1920, 480, [48], [], [48]],
        [2400, 1440, [43], [43]], [3840, 480, [43], [], [43]],
    ]);
    // voice-2 ties stay in voice 2
    for (const c of ms.dump(1)) for (const t of c.tieTargets) assert.strictEqual(t.track, 1);
});

test('writeVoice: other staves, and voices 3 and 4 of an empty bar', () => {
    const ms = fresh({ nstaves: 2 });
    ok(ms.call('writeVoice', { measure: 2, staff: 1, events: [notes([36], '1/1')] }));
    ok(ms.call('writeVoice', { measure: 2, staff: 1, voice: 2, events: [{ rest: true, duration: '1/2' }, notes([40], '1/2')] }));
    ok(ms.call('writeVoice', { measure: 2, staff: 1, voice: 3, events: [notes([31], '1/4'), { rest: true, duration: '3/4' }] }));
    expectTrack(ms, 4, WHOLE, 2 * WHOLE, [[WHOLE, WHOLE, [36]]]);
    expectTrack(ms, 6, WHOLE, 2 * WHOLE, [[WHOLE, 960, 'rest'], [WHOLE + 960, 960, [40]]]);
    expectTrack(ms, 7, WHOLE, 2 * WHOLE, [[WHOLE, 480, [31]], [WHOLE + 480, 1440, 'rest']]);
    expectTrack(ms, 0, WHOLE, 2 * WHOLE, [[WHOLE, WHOLE, 'rest']]);   // staff 0 untouched
});

test('writeVoice: the next call continues after the passage, same staff and voice', () => {
    const ms = fresh();
    ok(ms.call('writeVoice', { measure: 1, staff: 1, voice: 1, events: [notes([50], '3/4')] }));
    const r = ok(ms.call('writeVoice', { events: [notes([52], '1/4'), notes([53], '1/2')] }));
    assert.strictEqual(r.startTick, 1440);
    expectTrack(ms, 5, 0, 2 * WHOLE, [[0, 1440, [50]], [1440, 480, [52]], [WHOLE, 960, [53]], [WHOLE + 960, 960, 'rest']]);
});

test('writeVoice: a tie on the last event ties into the existing next note', () => {
    const ms = fresh();
    ok(ms.call('writeVoice', { measure: 2, events: [notes([65, 69], '1/2'), notes([67], '1/2')] }));
    ok(ms.call('writeVoice', { measure: 1, events: [notes([60], '1/2'), notes([65], '1/2', true)] }));
    expectTrack(ms, 0, 0, 2 * WHOLE, [[0, 960, [60]], [960, 960, [65], [65]], [WHOLE, 960, [65, 69], [], [65]], [WHOLE + 960, 960, [67]]]);
});

test('writeVoice: a tie with no matching next note fails and writes nothing', () => {
    const ms = fresh();
    ok(ms.call('writeVoice', { measure: 2, events: [notes([67], '1/1')] }));
    const before = ms.snapshot();
    const undo = ms.undoStack.length;
    fails(ms.call('writeVoice', { measure: 1, events: [notes([60], '1/2'), notes([65], '1/2', true)] }),
          /tie on pitch 65 .*the next chord \(tick 1920\) has pitches \[67\], not 65\. Nothing was written/);
    fails(ms.call('writeVoice', { measure: 3, events: [notes([60], '1/1', true)] }), /a rest follows it/);
    fails(ms.call('writeVoice', { measure: 4, events: [notes([60], '1/1', true)] }), /end of score/);
    assert.strictEqual(ms.snapshot(), before);
    assert.strictEqual(ms.undoStack.length, undo);
    assert.strictEqual(ms.counters.tieCmd, 0);          // MuseScore's tie action was never reached
});

test('writeVoice: no tie across a repeat barline', () => {
    const ms = fresh();
    ms.state.measures[0].repeatEnd = true;
    ok(ms.call('writeVoice', { measure: 2, events: [notes([60], '1/1')] }));
    const before = ms.snapshot();
    fails(ms.call('writeVoice', { measure: 1, events: [notes([60], '1/1', true)] }), /repeat barline/);
    fails(ms.call('writeVoice', { measure: 1, events: [notes([60], '3/2')] }), /repeat barline/);
    assert.strictEqual(ms.snapshot(), before);
});

test('writeVoice: leaves note-input mode before tying (else MuseScore adds a note)', () => {
    const ms = fresh();
    ms.noteEntryMode = true;
    ok(ms.call('writeVoice', { measure: 1, events: [notes([60], '1/2', true), notes([60], '1/2')] }));
    assert.strictEqual(ms.noteEntryMode, false);
    expectTrack(ms, 0, 0, WHOLE, [[0, 960, [60], [60]], [960, 960, [60], [], [60]]]);
});

test('writeVoice: a note that is already tied correctly is left alone', () => {
    const ms = fresh();
    ok(ms.call('processSequence', { atomic: true, sequence: [
        { action: 'writeVoice', params: { measure: 1, events: [notes([60], '1/2', true), notes([60], '1/2')] } },
        { action: 'addNote', params: { pitch: 60, duration: '1/2', tie: true, measure: 1 } },
    ] }));
    expectTrack(ms, 0, 0, WHOLE, [[0, 960, [60], [60]], [960, 960, [60], [], [60]]]);
});

test('writeVoice: refuses to start inside a held note or to overwrite a tuplet', () => {
    const ms = fresh();
    ok(ms.call('writeVoice', { measure: 1, events: [notes([60], '1/2'), notes([62], '1/4'), notes([64], '1/4')] }));
    ok(ms.call('writeVoice', { measure: 1, voice: 1, events: [notes([48], '1/1')] }));
    const before = ms.snapshot();
    fails(ms.call('writeVoice', { tick: 960, voice: 1, events: [notes([50], '1/4')] }), /inside a note\/rest of staff 0 voice 1 that starts at tick 0/);
    fails(ms.call('writeVoice', { tick: 480, voice: 0, events: [notes([50], '1/4')] }), /No note or rest starts at tick 480/);
    assert.strictEqual(ms.snapshot(), before);

    ok(ms.call('addTuplet', { measure: 2, voice: 0, duration: { numerator: 1, denominator: 4 }, ratio: { numerator: 3, denominator: 2 } }));
    const before2 = ms.snapshot();
    fails(ms.call('writeVoice', { measure: 2, voice: 0, events: [notes([60], '1/2')] }), /inside a tuplet/);
    fails(ms.call('writeVoice', { tick: WHOLE - 480, voice: 0, events: [notes([60], '1/2')] }), /There is a tuplet at tick 1920/);
    assert.strictEqual(ms.snapshot(), before2);
});

test('writeVoice: cutting into a held note is reported', () => {
    const ms = fresh();
    ok(ms.call('writeVoice', { measure: 1, events: [notes([60], '1/1')] }));
    const r = ok(ms.call('writeVoice', { measure: 1, events: [notes([62], '1/4')] }));
    assert.match(r.warnings[0], /lasted until tick 1920; after tick 480 it is now a rest/);
    const bar = ms.dump(0, 0, WHOLE);
    assert.deepStrictEqual(bar[0].pitches, [62]);
    assert.ok(bar.slice(1).every(c => c.rest));
    assert.strictEqual(bar.slice(1).reduce((n, c) => n + c.ticks, 0), 1440);
});

test('writeVoice: a wrong duration from MuseScore is caught and rolled back', () => {
    const ms = fresh();
    const before = ms.snapshot();
    // Make the mock's Cursor.setDuration misbehave like an unexpected engine change
    const orig = ms.newCursor.bind(ms);
    ms.newCursor = function() { const c = orig(); const sd = c.setDuration; c.setDuration = (z, n) => sd.call(c, z, n * 2); return c; };
    fails(ms.call('writeVoice', { measure: 1, events: [notes([60], '1/4'), notes([62], '1/4')] }),
          /(wrote something else|moved the write position).*Nothing was written/);
    assert.strictEqual(ms.snapshot(), before);
});

// ---------------------------------------------------------------------------
// addNote / addRest
// ---------------------------------------------------------------------------

test('addNote: 5/8 is split and tied; the cursor advances by 5/8', () => {
    const ms = fresh();
    const r = ok(ms.call('addNote', { pitch: 60, duration: '5/8', measure: 1 }));
    assert.match(r.message, /written as tied notes: 5\/8 = 1\/2 \+ 1\/8/);
    assert.strictEqual(r.cursor.tick, 1200);
    expectTrack(ms, 0, 0, WHOLE, [[0, 960, [60], [60]], [960, 240, [60], [], [60]], [1200, 720, 'rest']]);
    fails(ms.call('addNote', { pitch: 64, addToChord: true }), /written as 2 tied notes/);
});

test('addNote: legacy {numerator, denominator} durations still work', () => {
    const ms = fresh();
    ok(ms.call('addNote', { pitch: 60, duration: { numerator: 3, denominator: 8 }, measure: 1 }));
    expectTrack(ms, 0, 0, 720, [[0, 720, [60]]]);
});

test('addNote tie=true needs the next note to exist when the call ends', () => {
    const ms = fresh();
    const before = ms.snapshot();
    fails(ms.call('addNote', { pitch: 60, duration: '1/4', tie: true, measure: 1 }), /tie on pitch 60 at tick 0 .*a rest follows it/);
    assert.strictEqual(ms.snapshot(), before);
    ok(ms.call('writeVoice', { measure: 1, events: [notes([62], '1/4'), notes([60], '1/4')] }));
    ok(ms.call('addNote', { pitch: 60, duration: '1/4', tie: true, measure: 1 }));   // rewrites 62 as a tied 60
    expectTrack(ms, 0, 0, 960, [[0, 480, [60], [60]], [480, 480, [60], [], [60]]]);
});

test('addNote tie=true in an atomic sequence ties to a note written by a later step', () => {
    const ms = fresh();
    const undo = ms.undoStack.length;
    const r = ok(ms.call('processSequence', { atomic: true, sequence: [
        { action: 'setCursor', params: { measure: 1, staff: 0, voice: 0 } },
        { action: 'addNote', params: { pitch: 67, duration: '1/2', tie: true } },
        { action: 'addNote', params: { pitch: 67, duration: '1/4' } },
        { action: 'addNote', params: { pitch: 64, duration: '1/4' } },
    ] }));
    assert.strictEqual(r.results.length, 4);
    assert.ok(!('cursor' in r.results[1]));
    assert.strictEqual(ms.undoStack.length, undo + 1);
    expectTrack(ms, 0, 0, WHOLE, [[0, 960, [67], [67]], [960, 480, [67], [], [67]], [1440, 480, [64]]]);
});

test('addNote tie=true: a missing target rolls back the whole atomic sequence', () => {
    const ms = fresh();
    const before = ms.snapshot();
    fails(ms.call('processSequence', { atomic: true, sequence: [
        { action: 'addNote', params: { pitch: 67, duration: '1/2', tie: true, measure: 1 } },
        { action: 'addNote', params: { pitch: 65, duration: '1/2' } },
    ] }), /tie on pitch 67 .*not 67.*Nothing from this sequence was kept/);
    assert.strictEqual(ms.snapshot(), before);
});

test('addNote add_to_chord: duration must match, tie supported', () => {
    const ms = fresh();
    ok(ms.call('writeVoice', { measure: 2, events: [notes([64], '1/4')] }));
    ok(ms.call('addNote', { pitch: 60, duration: '1/4', measure: 1 }));
    fails(ms.call('addNote', { pitch: 64, duration: '1/2', addToChord: true }), /is 1\/4 long, not 1\/2/);
    ok(ms.call('addNote', { pitch: 64, addToChord: true }));
    ok(ms.call('addNote', { pitch: 67, duration: '1/4', addToChord: true }));
    expectTrack(ms, 0, 0, 480, [[0, 480, [60, 64, 67]]]);
    ok(ms.call('addNote', { pitch: 64, duration: '1/4', tick: 480 }));
    ok(ms.call('addNote', { pitch: 60, duration: '1/4', measure: 1 }));
    ok(ms.call('addNote', { pitch: 64, addToChord: true, tie: true }));
    expectTrack(ms, 0, 0, 960, [[0, 480, [60, 64], [64]], [480, 480, [64], [], [64]]]);
});

test('addNote fills an existing tuplet; tuplet-only durations are rejected', () => {
    const ms = fresh();
    ok(ms.call('addTuplet', { measure: 1, duration: { numerator: 1, denominator: 4 }, ratio: { numerator: 3, denominator: 2 } }));
    for (const p of [60, 62, 64]) ok(ms.call('addNote', { pitch: p, duration: '1/8' }));
    const got = ms.dump(0, 0, 480);
    assert.deepStrictEqual(got.map(c => [c.tick, c.ticks, c.actual, c.tuplet, c.pitches]),
                           [[0, 240, 160, true, [60]], [160, 240, 160, true, [62]], [320, 240, 160, true, [64]]]);
    fails(ms.call('addNote', { pitch: 60, duration: '1/12', measure: 1 }), /needs a tuplet/);
    fails(ms.call('addNote', { pitch: 60, duration: '5/8', measure: 1 }), /Inside a tuplet the duration must be one plain or dotted note value/);
});

test('addRest: 5/8 is written as rests that add up to 5/8', () => {
    const ms = fresh();
    ok(ms.call('writeVoice', { measure: 1, events: [notes([60], '1/1')] }));
    const r = ok(ms.call('addRest', { duration: '5/8', measure: 1 }));
    assert.deepStrictEqual(r.split, [{ event: 0, duration: '5/8', writtenAs: ['1/2', '1/8'] }]);
    expectTrack(ms, 0, 0, 1200, [[0, 960, 'rest'], [960, 240, 'rest']]);
});

// ---------------------------------------------------------------------------
// Batches and UI work
// ---------------------------------------------------------------------------

test('atomic batch of 100 addNote: one command, one UI update, no score walks per note', () => {
    const ms = fresh({ bars: 30 });
    const seq = [{ action: 'setCursor', params: { measure: 1, staff: 0, voice: 0 } }];
    for (let i = 0; i < 100; i++) seq.push({ action: 'addNote', params: { pitch: 60 + (i % 12), duration: '1/8' } });
    const undo = ms.undoStack.length;
    const r = ok(ms.call('processSequence', { atomic: true, sequence: seq }));
    assert.strictEqual(r.results.length, 101);
    assert.strictEqual(ms.counters.startCmd, 1);
    assert.strictEqual(ms.undoStack.length, undo + 1);
    assert.strictEqual(ms.counters.selectRange, 1);
    assert.ok(ms.counters.firstMeasure <= 3, 'whole-score walks: ' + ms.counters.firstMeasure);
    assert.strictEqual(ms.dump(0).filter(c => !c.rest).length, 100);
    assert.strictEqual(r.cursor.tick, 100 * 240);
});

test('non-atomic sequence: one command per step, one UI update at the end', () => {
    const ms = fresh();
    const seq = [];
    for (let i = 0; i < 10; i++) seq.push({ action: 'addNote', params: { pitch: 60 + i, duration: '1/8', staff: 1 } });
    ok(ms.call('processSequence', { sequence: seq }));
    assert.strictEqual(ms.counters.startCmd, 10);
    assert.strictEqual(ms.undoStack.length, 10);
    assert.strictEqual(ms.counters.selectRange, 1);
});

test('deleteSelection in a sequence deletes at the cursor, not a stale selection', () => {
    const ms = fresh();
    ok(ms.call('writeVoice', { measure: 1, events: [notes([60], '1/4'), notes([62], '1/4')] }));
    ms.counters.selectRange = 0;
    ok(ms.call('processSequence', { sequence: [
        { action: 'setCursor', params: { tick: 480 } },
        { action: 'deleteSelection' },
    ] }));
    // the selection was moved to the cursor (tick 480) right before the delete
    assert.ok(ms.counters.selectRange >= 1);
    assert.ok(ms.log.includes('action://notation/delete'));
});

test('a failed step leaves no undo step and the cursor where it was', () => {
    const ms = fresh();
    ok(ms.call('setCursor', { measure: 2 }));
    const r = fails(ms.call('processSequence', { atomic: true, sequence: [
        { action: 'writeVoice', params: { measure: 1, events: [notes([60], '1/4')] } },
        { action: 'addNote', params: { pitch: 60, duration: '1/4', tick: 240 } },
    ] }), /Step 1 \(addNote\) failed/);
    assert.strictEqual(r.cursor.tick, WHOLE);
    assert.strictEqual(ms.undoStack.length, 0);
});

test('undo restores the state before a writeVoice (one step)', () => {
    const ms = fresh();
    const before = ms.snapshot();
    ok(ms.call('writeVoice', { measure: 1, events: [notes([60], '5/8', true), notes([60], '3/8'), notes([62], '1/1')] }));
    ok(ms.call('undo', { steps: 1 }));
    assert.strictEqual(ms.snapshot(), before);
});

test('getScore reports the written ties', () => {
    const ms = fresh();
    ok(ms.call('writeVoice', { measure: 1, events: [notes([60], '1/2', true), notes([60], '1/2')] }));
    const a = ok(ms.call('getScore')).analysis;
    const els = a.measures[0].elements.staff0;
    assert.deepStrictEqual(els.map(e => [e.startTick, e.notes[0].tiedForward, e.notes[0].tiedBack]), [[0, true, false], [960, false, true]]);
});

// ---------------------------------------------------------------------------

function runPlans(input) {
    const ms = fresh();
    const parse = ms.plugin.get('parseDuration');
    const plan = ms.plugin.get('planPieces');
    return input.map(c => {
        try {
            if (c.kind === 'parse') return { ticks: parse(c.value, 'duration') };
            return { pieces: plan(c.start, c.length, c.bars).map(p => [p.tick, p.ticks]) };
        } catch (e) {
            return { error: e.message };
        }
    });
}

if (process.argv.includes('--plans')) {
    const input = JSON.parse(require('fs').readFileSync(0, 'utf8'));
    process.stdout.write(JSON.stringify(runPlans(input)));
} else if (process.argv.includes('--tables')) {
    const ms = fresh();
    process.stdout.write(JSON.stringify({
        actionParams: ms.plugin.get('actionParams'),
        sequenceCommands: ms.plugin.get('sequenceCommands'),
        nonAtomicCommands: ms.plugin.get('nonAtomicCommands'),
        noteValueTicks: ms.plugin.get('noteValueTicks'),
    }));
} else {
    let failed = 0;
    for (const t of tests) {
        try {
            t.fn();
            console.log('ok   ' + t.name);
        } catch (e) {
            failed++;
            console.log('FAIL ' + t.name + '\n     ' + (e.stack || e).toString().split('\n').slice(0, 6).join('\n     '));
        }
    }
    console.log((tests.length - failed) + '/' + tests.length + ' passed');
    process.exit(failed ? 1 : 0);
}
