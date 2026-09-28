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
const { MockMuseScore, WHOLE, Element, Tid, ClefType, LayoutBreak, DynamicType, SymId, symName } = require('./mock_musescore.js');

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
    fails(ms.call('getScore', { format: 'json' }), /getScore: unknown parameter 'format' \(allowed: startMeasure, endMeasure\)/);
    assert.strictEqual(ms.snapshot(), before);
    assert.strictEqual(ms.counters.startCmd, 0);
});

test('unknown top-level command field is an error', () => {
    const ms = fresh();
    fails(ms.raw({ action: 'ping', params: {}, atomic: true }), /Command: unknown parameter 'atomic'/);
    fails(ms.call('getCursorInfo', { expectedVersion: 1 }), /unknown parameter 'expectedVersion'/);
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
    assert.ok(ms.counters.firstMeasure <= 6, 'bar-list walks (a few per request, not per note): ' + ms.counters.firstMeasure);
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

test('writeVoice: starting inside a held note splits it; tuplets are refused', () => {
    const ms = fresh();
    ok(ms.call('writeVoice', { measure: 1, events: [notes([60], '1/2'), notes([62], '1/4'), notes([64], '1/4')] }));
    ok(ms.call('writeVoice', { measure: 1, voice: 1, events: [notes([48], '1/1')] }));
    // voice 2 at the half: the whole note keeps its first half
    let r = ok(ms.call('writeVoice', { tick: 960, voice: 1, events: [notes([50], '1/4')] }));
    assert.match(r.warnings.join(' '), /Split the note\/chord \[48\] at tick 0/);
    expectTrack(ms, 1, 0, WHOLE, [[0, 960, [48]], [960, 480, [50]], [1440, 480, 'rest']]);
    // voice 1 inside the half note at tick 240
    ok(ms.call('writeVoice', { tick: 240, voice: 0, events: [notes([50], '1/4')] }));
    expectTrack(ms, 0, 0, 960, [[0, 240, [60]], [240, 480, [50]], [720, 240, 'rest']]);
    // a head that is not one note value: half + eighth, tied
    ok(ms.call('writeVoice', { measure: 3, voice: 0, events: [notes([60], '1/1')] }));
    const undoBefore = ms.undoStack.length;
    ok(ms.call('writeVoice', { measure: 3, offset: '5/8', voice: 0, events: [notes([50], '1/8')] }));
    assert.strictEqual(ms.undoStack.length, undoBefore + 1);
    expectTrack(ms, 0, 2 * WHOLE, 3 * WHOLE, [[3840, 960, [60], [60]], [4800, 240, [60], [], [60]], [5040, 240, [50]], [5280, 480, 'rest']]);

    ok(ms.call('addTuplet', { measure: 2, voice: 0, duration: { numerator: 1, denominator: 4 }, ratio: { numerator: 3, denominator: 2 } }));
    const before2 = ms.snapshot();
    fails(ms.call('writeVoice', { measure: 2, voice: 0, events: [notes([60], '1/2')] }), /inside a tuplet/);
    fails(ms.call('writeVoice', { tick: WHOLE - 480, voice: 0, events: [notes([60], '1/2')] }), /There is a tuplet at tick 1920/);
    fails(ms.call('writeVoice', { tick: WHOLE + 160, voice: 0, events: [notes([60], '1/8')] }), /inside a tuplet/);
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
    assert.ok(ms.counters.firstMeasure <= 6, 'bar-list walks (a few per request, not per note): ' + ms.counters.firstMeasure);
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
        { action: 'addNote', params: { pitch: 60, duration: '1/12', tick: 240 } },
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
// Score versions
// ---------------------------------------------------------------------------

test('versions: edits raise the version, reads do not', () => {
    const ms = fresh();
    const v0 = ok(ms.call('getVersion')).version;
    assert.ok(Number.isInteger(v0) && v0 >= 100000);
    ok(ms.call('getScore'));
    ok(ms.call('getCursorInfo'));
    assert.strictEqual(ok(ms.call('getVersion')).version, v0);
    ok(ms.call('writeVoice', { measure: 1, events: [notes([60], '1/4')] }));
    assert.strictEqual(ms.lastVersion, v0 + 1);
    // a failed edit that changed nothing does not count
    fails(ms.call('writeVoice', { measure: 1, events: [notes([60], '1/4', true), { rest: true, duration: '1/4' }] }));
    assert.strictEqual(ok(ms.call('getVersion')).version, v0 + 1);
});

test('versions: expectedVersion refuses an edit on a changed score', () => {
    const ms = fresh();
    const v0 = ok(ms.call('getVersion')).version;
    ok(ms.call('writeVoice', { measure: 1, events: [notes([60], '1/4')], expectedVersion: v0 }));
    const before = ms.snapshot();
    fails(ms.call('writeVoice', { measure: 2, events: [notes([62], '1/4')], expectedVersion: v0 }),
          new RegExp('changed since version ' + v0 + ' \\(it is now version ' + (v0 + 1) + '\\)\\. Nothing was done'));
    assert.strictEqual(ms.snapshot(), before);
    // on a read it is not allowed
    fails(ms.call('getScore', { expectedVersion: v0 + 1 }), /unknown parameter 'expectedVersion'/);
    // inside a sequence only the sequence may carry it
    fails(ms.call('processSequence', { sequence: [{ action: 'addRest', params: { duration: '1/4', expectedVersion: 1 } }] }),
          /unknown parameter 'expectedVersion'/);
    ok(ms.call('processSequence', { expectedVersion: v0 + 1, sequence: [{ action: 'addRest', params: { duration: '1/4', measure: 3 } }] }));
});

test('versions: edits made in MuseScore are noticed, with their bars', () => {
    const ms = fresh();
    const v0 = ok(ms.call('getVersion')).version;
    ok(ms.call('writeVoice', { measure: 1, events: [notes([60], '1/1')] }));
    ms.userEdit(e => e.setNoteRest(2 * WHOLE, 0, 67, 480));        // bar 3
    fails(ms.call('addRest', { duration: '1/4', measure: 4, expectedVersion: v0 + 1 }), /changed since version/);
    const ch = ok(ms.call('getChangesSince', { version: v0 }));
    assert.strictEqual(ch.version, v0 + 2);
    assert.strictEqual(ch.complete, true);
    assert.deepStrictEqual(ch.changes.map(c => [c.source, c.bars]), [['mcp', [[1, 1]]], ['user', [[3, 3]]]]);
    assert.deepStrictEqual(ch.changedBars, [[1, 1], [3, 3]]);
    assert.deepStrictEqual(ok(ms.call('getChangesSince', { version: v0 + 2 })).changes, []);
    // a version from the future (another run of the plugin) can't be answered
    assert.strictEqual(ok(ms.call('getChangesSince', { version: v0 + 50 })).complete, false);
});

test('versions: bar-level digests ignore tempo; the log says which bars an edit touched', () => {
    const ms = fresh({ bars: 6 });
    const v0 = ok(ms.call('getVersion')).version;
    ok(ms.call('processSequence', { atomic: true, sequence: [
        { action: 'writeVoice', params: { measure: 2, events: [notes([60], '1/1')] } },
        { action: 'writeVoice', params: { measure: 5, staff: 1, events: [notes([48], '1/2'), notes([50], '1/2')] } },
    ] }));
    const ch = ok(ms.call('getChangesSince', { version: v0 }));
    assert.deepStrictEqual(ch.changedBars, [[2, 2], [5, 5]]);
    // a tempo mark changes the playback time of later bars but not their music
    ok(ms.call('setTempo', { bpm: 90, measure: 1 }));
    assert.deepStrictEqual(ok(ms.call('getChangesSince', { version: v0 + 1 })).changedBars, [[1, 1]]);
});

test('versions: a getScore step inside an editing sequence does not blame the user', () => {
    const ms = fresh();
    const v0 = ok(ms.call('getVersion')).version;
    ok(ms.call('processSequence', { sequence: [
        { action: 'writeVoice', params: { measure: 2, events: [notes([60], '1/1')] } },
        { action: 'getScore', params: { startMeasure: 2, endMeasure: 2 } },
    ] }));
    const ch = ok(ms.call('getChangesSince', { version: v0 }));
    assert.deepStrictEqual(ch.changes.map(c => [c.source, c.action, c.bars]), [['mcp', 'processSequence', [[2, 2]]]]);
});

test('versions: another score being opened makes the log incomplete', () => {
    const ms = fresh();
    const v0 = ok(ms.call('getVersion')).version;
    ms.plugin.set('lastScore', {});       // as if the user switched scores
    const ch = ok(ms.call('getChangesSince', { version: v0 }));
    assert.strictEqual(ch.complete, false);
    assert.strictEqual(ch.changes[0].action, 'another score was opened');
});

// ---------------------------------------------------------------------------
// Positions, spelling, markings, tuplets
// ---------------------------------------------------------------------------

test('offset: a position inside a bar', () => {
    const ms = fresh();
    const r = ok(ms.call('writeVoice', { measure: 2, offset: '1/4', events: [notes([60], '1/4')] }));
    assert.strictEqual(r.startTick, WHOLE + Q);
    expectTrack(ms, 0, WHOLE, 2 * WHOLE, [[WHOLE, Q, 'rest'], [WHOLE + Q, Q, [60]], [WHOLE + 2 * Q, 2 * Q, 'rest']]);
    assert.ok(!r.warnings, 'shortening a rest is not a warning');
    ok(ms.call('writeVoice', { measure: 2, offset: '0', events: [notes([59], '1/8')] }));
    fails(ms.call('writeVoice', { offset: '1/4', events: [notes([60], '1/4')] }), /offset needs measure/);
    fails(ms.call('writeVoice', { tick: 0, offset: '1/4', events: [notes([60], '1/4')] }), /offset with measure, not with tick/);
    fails(ms.call('writeVoice', { measure: 1, offset: '1/1', events: [notes([60], '1/4')] }), /past the end of bar 1/);
    fails(ms.call('writeVoice', { measure: 1, offset: '1/12', events: [notes([60], '1/4')] }), /needs a tuplet/);
    ok(ms.call('setCursor', { measure: 3, offset: '3/8' }));
    assert.strictEqual(ok(ms.call('getCursorInfo')).cursor.tick, 2 * WHOLE + 720);
});

test('note names keep their spelling', () => {
    const ms = fresh();
    ok(ms.call('writeVoice', { measure: 1, events: [
        { pitches: ['Gb4'], duration: '1/4' }, { pitches: ['F#4', 'A#4', 'C#5'], duration: '1/4' }, { pitches: [66], duration: '1/2' },
    ] }));
    const tpcs = ms.trackCrs(0).slice(0, 3).map(c => c.notes.map(n => n.tpc1));
    assert.deepStrictEqual(tpcs, [[8], [20, 24, 21], [20]]);
    const a = ok(ms.call('getScore', { startMeasure: 1, endMeasure: 1 })).analysis;
    assert.deepStrictEqual(a.measures[0].elements.staff0[0].notes.map(n => n.pitchName), ['Gb']);
    ok(ms.call('addNote', { pitch: 'Bb3', duration: '1/4', measure: 2 }));
    assert.deepStrictEqual([ms.crAt(0, WHOLE).notes[0].pitch, ms.crAt(0, WHOLE).notes[0].tpc1], [58, 12]);
    ok(ms.call('addNote', { pitch: 'D5', addToChord: true }));
    assert.deepStrictEqual(ms.crAt(0, WHOLE).notes.map(n => n.pitch).sort(), [58, 74]);
    fails(ms.call('writeVoice', { measure: 1, events: [{ pitches: ['H4'], duration: '1/4' }] }), /not a MIDI pitch or a note name/);
    fails(ms.call('writeVoice', { measure: 1, events: [{ pitches: ['F#4', 'Gb4'], duration: '1/4' }] }), /lists pitch 66 twice/);
    // split notes keep the spelling on every piece
    ok(ms.call('writeVoice', { measure: 3, events: [{ pitches: ['Eb4'], duration: '5/8' }, { rest: true, duration: '3/8' }] }));
    assert.deepStrictEqual(ms.trackCrs(0).filter(c => c.tick >= 2 * WHOLE && !c.rest).map(c => c.notes[0].tpc1), [11, 11]);
});

test('event markings: dynamic, articulations, fermata, lyric, text, chord symbol', () => {
    const ms = fresh();
    ok(ms.call('writeVoice', { measure: 1, events: [
        { pitches: [60], duration: '1/4', dynamic: 'mf', articulations: ['staccato', 'accent'], lyric: 'Hel-', chord: 'C7', text: 'dolce' },
        { pitches: [62], duration: '1/4', lyric: 'lo' },
        { pitches: [64], duration: '1/4', lyric: '_' },
        { rest: true, duration: '1/4', articulations: ['fermata'] },
    ] }));
    const at0 = ms.annotationsAt(0);
    assert.deepStrictEqual(at0.map(a => a.type).sort(), [Element.DYNAMIC, Element.EXPRESSION, Element.HARMONY].sort());
    assert.strictEqual(at0.find(a => a.type === Element.DYNAMIC).dynamicType, DynamicType.MF);
    // written above; MuseScore puts them below a low note (stem up)
    assert.deepStrictEqual(ms.crAt(0, 0).arts.map(a => symName(a.symbol)), ['articStaccatoBelow', 'articAccentBelow']);
    assert.deepStrictEqual(ms.crAt(0, 0).lyrics.map(l => [l.text, l.syllabic]), [['Hel', 1]]);
    assert.deepStrictEqual(ms.crAt(0, Q).lyrics.map(l => [l.text, l.syllabic]), [['lo', 2]]);
    assert.deepStrictEqual(ms.crAt(0, 2 * Q).lyrics, []);
    assert.deepStrictEqual(ms.annotationsAt(3 * Q).map(a => a.type), [Element.FERMATA]);
    const bar = ok(ms.call('getScore', { startMeasure: 1, endMeasure: 1 })).analysis.measures[0];
    // symbol names, not the translated display names ("Staccato below")
    assert.deepStrictEqual(bar.elements.staff0[0].articulations, ['articStaccatoBelow', 'articAccentBelow']);
    assert.deepStrictEqual(bar.markings.map(m => m.type).sort(), ['chordSymbol', 'dynamic', 'fermata', 'text']);
    // writing a dynamic again replaces it
    ok(ms.call('writeVoice', { measure: 1, events: [{ pitches: [60], duration: '1/4', dynamic: 'pp' }] }));
    assert.deepStrictEqual(ms.annotationsAt(0).filter(a => a.type === Element.DYNAMIC).map(a => a.dynamicType), [DynamicType.PP]);
    ok(ms.call('writeVoice', { measure: 2, events: [{ pitches: [60], duration: '1/4', articulations: ['trill', 'up-bow', 'short-trill'] }] }));
    assert.deepStrictEqual(ms.crAt(0, WHOLE).arts.map(a => [a.type, symName(a.symbol)]),
                           [[Element.ORNAMENT, 'ornamentTrill'], [Element.ARTICULATION, 'stringsUpBow'], [Element.ORNAMENT, 'ornamentShortTrill']]);
    // One the tools don't know (added in MuseScore): its display name
    ms.userEdit(m => m.crAt(0, WHOLE).arts.push({ id: m.id(), type: Element.ARTICULATION, symbol: SymId.articMarcatoTenutoAbove }));
    assert.deepStrictEqual(ok(ms.call('getScore', { startMeasure: 2, endMeasure: 2 })).analysis.measures[0].elements.staff0[0].articulations,
                           ['ornamentTrill', 'stringsUpBow', 'ornamentShortTrill', 'Marcato tenuto above']);
    fails(ms.call('writeVoice', { measure: 2, events: [{ pitches: [60], duration: '1/4', dynamic: 'loud' }] }), /unknown dynamic 'loud'/);
    fails(ms.call('writeVoice', { measure: 2, events: [{ rest: true, duration: '1/4', articulations: ['staccato'] }] }), /a rest can only have a fermata/);
    fails(ms.call('writeVoice', { measure: 2, events: [{ pitches: [60], duration: '1/4', articulations: ['wobble'] }] }), /unknown articulation 'wobble'/);
    fails(ms.call('writeVoice', { measure: 2, events: [{ rest: true, duration: '1/4', lyric: 'la' }] }), /a rest can't have a lyric/);
});

test('lyrics of several verses: one per verse, each continuing its own words', () => {
    const ms = fresh();
    ok(ms.call('writeVoice', { measure: 1, events: [
        { pitches: [60], duration: '1/4', lyric: ['Hel-', 'Good'] },
        { pitches: [62], duration: '1/4', lyric: ['lo', null, 'three'] },
        { pitches: [64], duration: '1/4', lyric: 'end' },
    ] }));
    const lyr = tick => ms.crAt(0, tick).lyrics.map(l => [l.verse, l.text, l.syllabic]).sort();
    assert.deepStrictEqual(lyr(0), [[0, 'Hel', 1], [1, 'Good', 0]]);
    assert.deepStrictEqual(lyr(Q), [[0, 'lo', 2], [2, 'three', 0]]);
    assert.deepStrictEqual(lyr(2 * Q), [[0, 'end', 0]]);
    fails(ms.call('writeVoice', { measure: 2, events: [{ pitches: [60], duration: '1/4', lyric: [null] }] }), /one per verse/);
    fails(ms.call('writeVoice', { measure: 2, events: [{ pitches: [60], duration: '1/4', lyric: ['la', ''] }] }), /one per verse/);
});

test('tuplets inside writeVoice, with ties in and out', () => {
    const ms = fresh();
    const r = ok(ms.call('writeVoice', { measure: 1, events: [
        { pitches: [60], duration: '1/4', tie: true },
        { tuplet: '3:2', events: [notes([60], '1/8'), notes([62], '1/8'), notes([64], '1/8', true)] },
        notes([64], '1/2'),
    ] }));
    assert.strictEqual(r.endTick, WHOLE);
    const d = ms.dump(0, 0, WHOLE);
    assert.deepStrictEqual(d.map(c => [c.tick, c.actual, c.tuplet, c.pitches[0], c.tiedForward.length > 0]),
        [[0, 480, false, 60, true], [480, 160, true, 60, false], [640, 160, true, 62, false], [800, 160, true, 64, true], [960, 960, false, 64, false]]);
    fails(ms.call('writeVoice', { measure: 2, offset: '7/8', events: [{ tuplet: '3:2', events: [notes([60], '1/8'), notes([60], '1/8'), notes([60], '1/8')] }] }),
          /would cross a barline/);
    fails(ms.call('writeVoice', { measure: 2, events: [{ tuplet: '3:2', events: [notes([60], '5/8')] }] }), /one plain or dotted value/);
    fails(ms.call('writeVoice', { measure: 2, events: [{ tuplet: '3:2', events: [notes([60], '1/8'), notes([60], '1/8')] }] }), /must add up to 3 times one note value.*got 1\/4/);
    fails(ms.call('writeVoice', { measure: 2, events: [{ tuplet: '3', events: [notes([60], '1/8')] }] }), /must look like "3:2"/);
    fails(ms.call('writeVoice', { measure: 2, events: [{ tuplet: '3:2', events: [{ tuplet: '3:2', events: [] }] }] }), /unknown parameter 'tuplet'/);
});

// ---------------------------------------------------------------------------
// Range operations
// ---------------------------------------------------------------------------

test('transpose: pitches and spelling; tied notes outside the range follow', () => {
    const ms = fresh();
    ok(ms.call('writeVoice', { measure: 1, events: [{ pitches: [60, 64, 67], duration: '1/2' }, { pitches: [62], duration: '1/2', tie: true },
                                                    notes([62], '1/1')] }));
    ok(ms.call('writeVoice', { measure: 1, staff: 1, events: [notes([48], '1/1')] }));
    const r = ok(ms.call('transpose', { semitones: 2, startMeasure: 1, endMeasure: 1, staves: [0] }));
    assert.match(r.warnings[0], /Tied notes outside the range moved too \(ticks 1920\)/);
    expectTrack(ms, 0, 0, 2 * WHOLE, [[0, 960, [62, 66, 69]], [960, 960, [64], [64]], [WHOLE, WHOLE, [64], [], [64]]]);
    assert.deepStrictEqual(ms.crAt(0, 0).notes.map(n => n.tpc1), [16, 20, 17]);    // D F# A
    expectTrack(ms, 4, 0, WHOLE, [[0, WHOLE, [48]]]);                                   // other staff untouched
    ok(ms.call('transpose', { semitones: -1, startTick: 0, endTick: 960, staves: [0] }));
    assert.deepStrictEqual(ms.crAt(0, 0).notes.map(n => [n.pitch, n.tpc1]), [[61, 21], [65, 25], [68, 22]]);  // C# E# G# (D major -> C# major)
    fails(ms.call('transpose', { semitones: 0, startMeasure: 1 }), /must not be 0/);
    fails(ms.call('transpose', { semitones: 70, startMeasure: 1 }), /semitones must be an integer -48-48/);
    fails(ms.call('transpose', { semitones: 2 }), /Give the range/);
    fails(ms.call('transpose', { semitones: 2, startMeasure: 1, startTick: 0, endTick: 10 }), /not both/);
    ok(ms.call('writeVoice', { measure: 3, events: [notes([120], '1/1')] }));
    const before = ms.snapshot();
    fails(ms.call('transpose', { semitones: 12, startMeasure: 3 }), /leaves MIDI 0-127/);
    assert.strictEqual(ms.snapshot(), before);
    ok(ms.call('addChordSymbol', { text: 'C', measure: 4 }));
    ok(ms.call('writeVoice', { measure: 4, events: [notes([60], '1/1')] }));
    assert.match(ok(ms.call('transpose', { semitones: 2, startMeasure: 4, chordSymbols: false })).warnings.join(),
                 /1 chord symbol\(s\) in the range were not transposed/);
});

test('transpose: chord symbols and key signatures move too', () => {
    const ms = fresh({ bars: 6 });
    ok(ms.call('writeVoice', { measure: 2, events: [{ pitches: ['F#4'], duration: '1/2', chord: 'F#m7b5/A' }, { pitches: ['B4'], duration: '1/2', chord: 'B7' }] }));
    ok(ms.call('addChordSymbol', { text: 'Ebmaj7', measure: 3 }));
    ok(ms.call('addChordSymbol', { text: 'N.C.', measure: 3, offset: '1/2' }));
    ok(ms.call('setKeySignature', { fifths: 2, measure: 2 }));
    const r = ok(ms.call('transpose', { semitones: 2, startMeasure: 2, endMeasure: 3, keySignatures: true }));
    const chords = t => ms.annotationsAt(t).filter(a => a.type === Element.HARMONY).map(a => a.text);
    assert.deepStrictEqual([chords(WHOLE), chords(WHOLE + 960), chords(2 * WHOLE), chords(2 * WHOLE + 960)],
                           [['G#m7b5/B'], ['C#7'], ['Fmaj7'], ['N.C.']]);
    assert.match(r.warnings.join(), /Chord symbol 'N.C.' .* was not transposed/);
    // D major -> E major in bars 2-3 on both staves; bar 4 goes back to D major
    assert.deepStrictEqual(ms.state.keys[0], [{ tick: 0, fifths: 0 }, { tick: WHOLE, fifths: 4 }, { tick: 3 * WHOLE, fifths: 2 }]);
    assert.deepStrictEqual(ms.state.keys[1], ms.state.keys[0]);
    // a whole-step down from C major (with a key change inside) over the whole score
    const ms2 = fresh({ bars: 4 });
    ok(ms2.call('setKeySignature', { fifths: -1, measure: 3 }));
    ok(ms2.call('transpose', { semitones: -2, startMeasure: 1, endMeasure: 4, keySignatures: true, staves: [0] }));
    assert.deepStrictEqual(ms2.state.keys[0], [{ tick: 0, fifths: -2 }, { tick: 2 * WHOLE, fifths: -3 }]);
    assert.deepStrictEqual(ms2.state.keys[1], [{ tick: 0, fifths: 0 }, { tick: 2 * WHOLE, fifths: -1 }]);
    fails(ms2.call('transpose', { semitones: 2, startTick: 0, endTick: 960, keySignatures: true }), /keySignatures needs whole bars/);
});

test('clearRange: notes, voices, markings; held notes are cut, not re-struck', () => {
    const ms = fresh();
    ok(ms.call('writeVoice', { measure: 1, events: [{ pitches: [60], duration: '1/4', dynamic: 'f' }, notes([62], '1/4'), notes([64], '1/2')] }));
    ok(ms.call('writeVoice', { measure: 1, voice: 1, events: [notes([48], '1/2'), notes([50], '1/2')] }));
    ok(ms.call('writeVoice', { measure: 2, voice: 0, events: [notes([65], '1/1')] }));
    let r = ok(ms.call('clearRange', { startMeasure: 1, endMeasure: 1 }));
    assert.match(r.message, /Cleared bars 1-1 .*5 note\(s\)\/chord\(s\), 1 marking\(s\)/);
    expectTrack(ms, 0, 0, WHOLE, [[0, WHOLE, 'rest']]);                 // one whole-bar rest
    assert.deepStrictEqual(ms.dump(1, 0, WHOLE), []);                   // voice 2 is gone
    assert.deepStrictEqual(ms.annotationsAt(0), []);
    expectTrack(ms, 0, WHOLE, 2 * WHOLE, [[WHOLE, WHOLE, [65]]]);        // bar 2 untouched
    // a note held across the start keeps its head; one lasting past the end is removed
    ok(ms.call('writeVoice', { measure: 3, voice: 0, events: [notes([60], '1/2'), notes([62], '1/2')] }));
    r = ok(ms.call('clearRange', { startTick: WHOLE + Q, endTick: 2 * WHOLE + Q }));
    assert.match(r.warnings.join(' '), /Split the note\/chord \[65\]/);
    assert.match(r.warnings.join(' '), /at tick 3840 .* lasted until tick 4800; it was removed entirely/);
    expectTrack(ms, 0, WHOLE, WHOLE + Q, [[WHOLE, Q, [65]]]);
    assert.ok(ms.dump(0, 2 * WHOLE, 3 * WHOLE).slice(0, 1).every(c => c.rest));
    // markings: false keeps the dynamics
    ok(ms.call('writeVoice', { measure: 4, voice: 0, events: [{ pitches: [60], duration: '1/1', dynamic: 'p' }] }));
    ok(ms.call('clearRange', { startMeasure: 4, markings: false }));
    assert.strictEqual(ms.annotationsAt(3 * WHOLE).length, 1);
    // one voice of one staff only
    ok(ms.call('writeVoice', { measure: 4, voice: 0, events: [notes([60], '1/1')] }));
    ok(ms.call('writeVoice', { measure: 4, voice: 1, events: [notes([55], '1/1')] }));
    ok(ms.call('clearRange', { startMeasure: 4, staves: [0], voices: [1] }));
    expectTrack(ms, 0, 3 * WHOLE, 4 * WHOLE, [[3 * WHOLE, WHOLE, [60]]]);
    fails(ms.call('clearRange', { startMeasure: 4, voices: [4] }), /voices entry must be an integer 0-3/);
    fails(ms.call('clearRange', { startMeasure: 4, staves: [0, 0] }), /lists 0 twice/);
});

test('replaceSection: new music for bars, all parts in one undo step', () => {
    const ms = fresh();
    ok(ms.call('writeVoice', { measure: 1, events: [notes([60], '1/1'), notes([62], '1/1')] }));
    ok(ms.call('writeVoice', { measure: 1, voice: 2, events: [notes([40], '1/1')] }));
    const undo = ms.undoStack.length;
    const r = ok(ms.call('replaceSection', { startMeasure: 1, endMeasure: 2, parts: [
        { staff: 0, voice: 1, events: [notes([55], '1/1'), { rest: true, duration: '1/1' }] },
        { staff: 0, events: [notes([72], '3/4'), notes([74], '1/4', true), notes([74], '1/2'), notes([76], '1/2')] },
        { staff: 1, events: [notes([36], '2/1')] },
    ] }));
    assert.match(r.message, /^Wrote bars 1-2 with 3 part\(s\)/);
    assert.strictEqual(ms.undoStack.length, undo + 1);
    expectTrack(ms, 0, 0, 2 * WHOLE, [[0, 1440, [72]], [1440, Q, [74], [74]], [WHOLE, 960, [74], [], [74]], [WHOLE + 960, 960, [76]]]);
    assert.deepStrictEqual(ms.dump(1, 0, 2 * WHOLE).map(c => [c.tick, c.rest ? 'r' : c.pitches[0]]), [[0, 55], [WHOLE, 'r']]);
    assert.deepStrictEqual(ms.dump(2, 0, 2 * WHOLE), []);                  // voice 3 was cleared
    expectTrack(ms, 4, 0, 2 * WHOLE, [[0, WHOLE, [36], [36]], [WHOLE, WHOLE, [36], [], [36]]]);
    fails(ms.call('replaceSection', { startMeasure: 1, parts: [{ staff: 0, events: [notes([60], '1/2')] }] }),
          /lasts 1\/2, but bars 1-1 last 1\/1/);
    fails(ms.call('replaceSection', { startMeasure: 1, parts: [{ staff: 0, events: [notes([60], '1/1')] }, { staff: 0, voice: 0, events: [notes([60], '1/1')] }] }),
          /given twice/);
    fails(ms.call('replaceSection', { startMeasure: 1, parts: [{ staff: 0, events: [notes([60], '1/1')], color: 'red' }] }), /unknown parameter 'color'/);
    // bars past the end are appended
    const n = ms.state.measures.length;
    const r2 = ok(ms.call('replaceSection', { startMeasure: n + 1, endMeasure: n + 2, parts: [
        { staff: 0, events: [notes([60], '1/1'), notes([62], '1/1')] }, { staff: 1, events: [notes([48], '2/1')] }] }));
    assert.match(r2.message, /^Appended 2 bar\(s\); wrote bars 5-6/);
    assert.strictEqual(ms.state.measures.length, n + 2);
    fails(ms.call('replaceSection', { startMeasure: n + 4, parts: [{ staff: 0, events: [notes([60], '1/1')] }] }), /startMeasure must be an integer 1-7/);
    // clearOtherVoices: false keeps the other voices
    ok(ms.call('replaceSection', { startMeasure: 1, clearOtherVoices: false, parts: [{ staff: 0, events: [notes([60], '1/1')] }] }));
    assert.deepStrictEqual(ms.dump(1, 0, WHOLE).map(c => c.pitches[0]), [55]);
});

test('copyMeasures: to another staff, transposed; inserted or over existing bars', () => {
    const ms = fresh({ bars: 4 });
    ok(ms.call('writeVoice', { measure: 1, events: [notes([60], '1/2', true), notes([60], '1/4'), notes([64], '1/4')] }));
    let r = ok(ms.call('copyMeasures', { startMeasure: 1, toMeasure: 3, insert: false, staff: 0, toStaff: 1, transpose: -12 }));
    assert.strictEqual(r.undoSteps, 2);
    expectTrack(ms, 4, 2 * WHOLE, 3 * WHOLE, [[2 * WHOLE, 960, [48], [48]], [2 * WHOLE + 960, Q, [48], [], [48]], [2 * WHOLE + 1440, Q, [52]]]);
    expectTrack(ms, 0, 2 * WHOLE, 3 * WHOLE, [[2 * WHOLE, WHOLE, 'rest']]);
    // insert: new bars before bar 2
    r = ok(ms.call('copyMeasures', { startMeasure: 1, toMeasure: 2, staff: 0 }));
    assert.strictEqual(ms.state.measures.length, 5);
    expectTrack(ms, 0, WHOLE, 2 * WHOLE, [[WHOLE, 960, [60], [60]], [WHOLE + 960, Q, [60], [], [60]], [WHOLE + 1440, Q, [64]]]);
    fails(ms.call('copyMeasures', { startMeasure: 1, toMeasure: 5, insert: false, endMeasure: 2 }), /Not enough bars/);
    fails(ms.call('copyMeasures', { startMeasure: 1, toMeasure: 2, staff: 0, toStaff: 2 }), /toStaff must be an integer 0-1/);
    // undo takes back the paste and the transposition as separate steps
    const n = ms.undoStack.length;
    ok(ms.call('copyMeasures', { startMeasure: 1, toMeasure: 4, insert: false, staff: 0, transpose: 5 }));
    assert.strictEqual(ms.undoStack.length, n + 2);
});

// ---------------------------------------------------------------------------
// Text, chord symbols, pedal, clefs, layout, tempo
// ---------------------------------------------------------------------------

test('addText, addChordSymbol, addPedalMarks', () => {
    const ms = fresh();
    ok(ms.call('addText', { text: 'cresc. poco a poco', kind: 'expression', measure: 1, offset: '1/2' }));
    ok(ms.call('addText', { text: 'Tutti', kind: 'system', measure: 2 }));
    ok(ms.call('addText', { text: 'solo', measure: 2, staff: 1 }));
    assert.deepStrictEqual(ms.annotationsAt(960).map(a => [a.type, a.text]), [[Element.EXPRESSION, 'cresc. poco a poco']]);
    assert.deepStrictEqual(ms.annotationsAt(WHOLE).map(a => [a.type, a.text, a.track]).sort(),
                           [[Element.STAFF_TEXT, 'solo', 4], [Element.SYSTEM_TEXT, 'Tutti', 0]].sort());
    fails(ms.call('addText', { text: 'x', kind: 'title' }), /kind must be staff, system or expression/);
    fails(ms.call('addText', { text: '' }), /text must be a non-empty string/);
    ok(ms.call('addChordSymbol', { text: 'Am7', measure: 3 }));
    ok(ms.call('addChordSymbol', { text: 'D7/F#', measure: 3 }));
    assert.deepStrictEqual(ms.annotationsAt(2 * WHOLE).map(a => a.text), ['D7/F#']);
    ok(ms.call('addPedalMarks', { startMeasure: 1, endMeasure: 2, staff: 1 }));
    assert.deepStrictEqual(ms.annotationsAt(0).map(a => a.text), ['<sym>keyboardPedalPed</sym>']);
    assert.deepStrictEqual(ms.annotationsAt(2 * WHOLE).map(a => a.text).sort(), ['<sym>keyboardPedalUp</sym>', 'D7/F#'].sort());
    fails(ms.call('addPedalMarks', { startMeasure: 3, endMeasure: 4 }), /release must be before the end of the score/);
    const bar1 = ok(ms.call('getScore', { startMeasure: 1, endMeasure: 1 })).analysis.measures[0];
    assert.ok(bar1.markings.some(m => m.type === 'text' && m.text === 'cresc. poco a poco'));
});

test('addClef, addLayoutBreak, setMeasuresPerSystem', () => {
    const ms = fresh();
    ok(ms.call('addClef', { type: 'bass', measure: 2, staff: 0 }));
    ok(ms.call('addClef', { type: 'treble', measure: 3, offset: '1/2', staff: 0 }));
    assert.deepStrictEqual(ms.state.clefs[0], [{ tick: 0, type: ClefType.G }, { tick: WHOLE, type: ClefType.F }, { tick: 2 * WHOLE + 960, type: ClefType.G }]);
    const hdr = ok(ms.call('getScore')).analysis;
    assert.deepStrictEqual(hdr.staves[0].clefChanges, [{ measure: 2, clef: 'bass' }, { measure: 4, clef: 'treble' }]);
    assert.strictEqual(hdr.staves[1].clef, 'bass');
    fails(ms.call('addClef', { type: 'violin' }), /Unknown clef 'violin'/);
    ok(ms.call('addLayoutBreak', { type: 'line', measure: 2 }));
    assert.match(ok(ms.call('addLayoutBreak', { type: 'line', measure: 2 })).message, /already has a line break/);
    assert.deepStrictEqual(ms.state.measures[1].elements.map(e => [e.type, e.layoutBreakType]), [[Element.LAYOUT_BREAK, LayoutBreak.LINE]]);
    fails(ms.call('addLayoutBreak', { type: 'column', measure: 2 }), /type must be line, page or section/);
    ok(ms.call('setMeasuresPerSystem', { count: 4 }));
    assert.deepStrictEqual(ms.state.locks, { interval: 4, start: 0, end: ms.endTick });   // the whole score
    ok(ms.call('setMeasuresPerSystem', { count: 0 }));
    assert.strictEqual(ms.state.locks, null);
    fails(ms.call('processSequence', { atomic: true, sequence: [{ action: 'setMeasuresPerSystem', params: { count: 4 } }] }), /can't be part of an atomic sequence/);
});

test('ranges that reach the end of the score include the last bar', () => {
    // Selection.selectRange ends at the segment at or before endTick: at the very end of
    // the score that is the last note, which would be left out (seen live: deleting the
    // test bars left the last one)
    const ms = fresh();
    ok(ms.call('writeVoice', { measure: 4, events: [notes([60], '1/4'), notes([62], '1/4'), notes([64], '1/4'), notes([65], '1/4')] }));
    ok(ms.call('copyMeasures', { startMeasure: 4, toMeasure: 1, insert: false }));
    assert.strictEqual(ms.brief(0, 0, WHOLE), ms.brief(0, 3 * WHOLE, 4 * WHOLE));
    ok(ms.call('selectCustomRange', { startTick: 2 * WHOLE, endTick: 4 * WHOLE, startStaff: 0, endStaff: 1 }));
    const sel = ok(ms.call('getSelection'));
    assert.deepStrictEqual([sel.startMeasure, sel.endMeasure], [3, 4]);
    ok(ms.call('deleteMeasures', { startMeasure: 3, endMeasure: 4 }));
    assert.strictEqual(ms.state.measures.length, 2);
});

test('setTempo: beat units and text; the tempo map is reported', () => {
    const ms = fresh();
    ok(ms.call('setTempo', { bpm: 60, beatUnit: '3/8', text: 'Allegretto', measure: 2 }));
    const t = ms.annotationsAt(WHOLE).find(a => a.type === Element.TEMPO_TEXT);
    assert.strictEqual(t.tempo, 1.5);
    assert.strictEqual(t.text, 'Allegretto <sym>metNoteQuarterUp</sym><sym>metAugmentationDot</sym> = 60');
    const a = ok(ms.call('getScore')).analysis;
    assert.deepStrictEqual(a.tempos.map(x => [x.measure, x.bpm]), [[2, 90]]);
    assert.strictEqual(a.measures[1].tempoBpm, 90);
    fails(ms.call('setTempo', { bpm: 60, beatUnit: '1/32' }), /beatUnit must be a plain or dotted note/);
    ok(ms.call('setTempo', { bpm: 72, measure: 2 }));
    assert.strictEqual(ms.annotationsAt(WHOLE).filter(x => x.type === Element.TEMPO_TEXT).length, 1);
});

// ---------------------------------------------------------------------------
// Score info, export, save, redo, selection, check
// ---------------------------------------------------------------------------

test('setScoreInfo replaces the title texts and sets the metadata', () => {
    const ms = fresh();
    ok(ms.call('setScoreInfo', { title: 'Nocturne', composer: 'Claude', lyricist: 'A. Poet' }));
    const texts = () => ms.state.frame.elements.map(e => [e.subStyle, e.text]).sort();
    assert.deepStrictEqual(texts(), [[Tid.TITLE, 'Nocturne'], [Tid.COMPOSER, 'Claude'], [Tid.POET, 'A. Poet']].sort());
    assert.strictEqual(ms.meta.workTitle, 'Nocturne');
    ok(ms.call('setScoreInfo', { lyricist: 'B. Poet', composer: '' }));
    assert.deepStrictEqual(texts(), [[Tid.TITLE, 'Nocturne'], [Tid.POET, 'B. Poet']].sort());
    assert.strictEqual(ok(ms.call('getScore', { startMeasure: 1, endMeasure: 1 })).analysis.title, 'Nocturne');
    fails(ms.call('setScoreInfo', {}), /Give at least one/);
    fails(ms.call('setScoreInfo', { title: 3 }), /title must be a string/);
    fails(ms.call('setScoreInfo', { arranger: 'x' }), /unknown parameter 'arranger'/);
});

test('exportScore and saveScore', () => {
    const ms = fresh();
    const r = ok(ms.call('exportScore', { path: '/tmp/out/score', format: 'PDF' }));
    assert.strictEqual(r.path, '/tmp/out/score.pdf');
    assert.deepStrictEqual(ms.exports, [{ path: '/tmp/out/score', ext: 'pdf' }]);
    assert.strictEqual(ok(ms.call('exportScore', { path: 'C:/x/song.mid', format: '.mid' })).path, 'C:/x/song.mid');
    fails(ms.call('exportScore', { path: '/missing-folder/x', format: 'pdf' }), /could not export/);
    fails(ms.call('exportScore', { path: '/tmp/x', format: 'p d f' }), /format must be one of/);
    fails(ms.call('exportScore', { path: '/tmp/x', format: 'mp3' }), /audio: File > Export/);
    const v = ok(ms.call('getVersion')).version;
    ok(ms.call('saveScore'));
    assert.ok(ms.log.includes('file-save'));
    assert.strictEqual(ok(ms.call('getVersion')).version, v);        // not an edit
});

test('setInstrumentName renames a part', () => {
    const ms = fresh();
    ok(ms.call('setInstrumentName', { staff: 0, name: 'Violin I', shortName: 'Vln. I' }));
    const hdr = ok(ms.call('getScore', { startMeasure: 1, endMeasure: 1 })).analysis;
    assert.deepStrictEqual([hdr.staves[0].instrument, hdr.staves[0].shortName], ['Violin I', 'Vln. I']);
    fails(ms.call('setInstrumentName', { staff: 0 }), /Give name and\/or shortName/);
    fails(ms.call('addInstrument', { instrumentId: 'flute', position: 5 }), /position must be an integer 0-1/);
});

test('instruments: added at a position (appended, then moved), replaced, removed, undone', () => {
    const ms = fresh();
    ok(ms.call('writeVoice', { measure: 1, staff: 1, voice: 0, events: [notes([48], '1/1')] }));
    let r = ok(ms.call('addInstrument', { instrumentId: 'violin' }));
    assert.deepStrictEqual([r.part.index, r.part.staves, r.part.instrumentId], [1, [2], 'violin']);
    assert.strictEqual(r.undoSteps, undefined);
    // insertPart would scramble the staves in 4.7.5 (the mock throws if it is used)
    r = ok(ms.call('addInstrument', { instrumentId: 'flute', position: 0 }));
    assert.deepStrictEqual([r.part.index, r.part.staves, r.part.instrumentId, r.undoSteps], [0, [0], 'flute', 2]);
    let a = ok(ms.call('getScore', { startMeasure: 1, endMeasure: 1 })).analysis;
    assert.deepStrictEqual(a.staves.map(st => [st.instrumentId, st.part]), [['flute', 0], ['piano', 1], ['piano', 1], ['violin', 2]]);
    assert.deepStrictEqual(ms.brief(2 * 4, 0, WHOLE), '48:1920');         // the piano's left hand moved down with it
    assert.deepStrictEqual(ms.state.clefs[2].map(c => c.type), [ClefType.F]);
    // an unknown id: MuseScore adds a default instrument (warned)
    r = ok(ms.call('addInstrument', { instrumentId: 'kazoo-xyz', position: 1 }));
    assert.match(r.warning, /not found/);
    ok(ms.call('undo', { steps: 2 }));
    // an instrument with a position can't be in an atomic batch (two undo steps)
    fails(ms.call('processSequence', { atomic: true, sequence: [{ action: 'addInstrument', params: { instrumentId: 'cello', position: 0 } }] }),
          /atomic/);
    ok(ms.call('processSequence', { atomic: true, sequence: [{ action: 'addInstrument', params: { instrumentId: 'cello' } }] }));
    assert.deepStrictEqual(ms.state.parts.map(p => p.instrumentId), ['flute', 'piano', 'violin', 'cello']);
    ok(ms.call('setInstrumentSound', { staff: 4, instrumentId: 'voice' }));
    fails(ms.call('setInstrumentSound', { staff: 4, instrumentId: 'kazoo-xyz' }), /not found/);
    ok(ms.call('removeInstrument', { staff: 1 }));
    a = ok(ms.call('getScore', { startMeasure: 1, endMeasure: 1 })).analysis;
    assert.deepStrictEqual(a.staves.map(st => st.instrumentId), ['flute', 'violin', 'voice']);
    // undo: back to the piano with its music
    ok(ms.call('undo', { steps: 6 }));
    assert.deepStrictEqual(ms.state.parts.map(p => p.instrumentId), ['piano']);
    assert.deepStrictEqual(ms.brief(1 * 4, 0, WHOLE), '48:1920');
});

test('openScore refuses while a score is open (MuseScore would open another window)', () => {
    const ms = fresh();
    fails(ms.call('openScore', { path: '/x/song.mscz' }), /A score is already open/);
    assert.ok(!ms.log.some(l => l.startsWith('readScore')));
});

test('redo after undo restores the edit and the cursor', () => {
    const ms = fresh();
    ok(ms.call('writeVoice', { measure: 1, events: [notes([60], '1/4'), notes([62], '1/4')] }));
    const after = ms.snapshot();
    const cursorAfter = ok(ms.call('getCursorInfo')).cursor.tick;
    ok(ms.call('undo'));
    assert.notStrictEqual(ms.snapshot(), after);
    ok(ms.call('redo'));
    assert.strictEqual(ms.snapshot(), after);
    assert.strictEqual(ok(ms.call('getCursorInfo')).cursor.tick, cursorAfter);
    // a new edit clears the redo stack
    ok(ms.call('undo'));
    ok(ms.call('addRest', { duration: '1/4', measure: 2 }));
    const now = ms.snapshot();
    ok(ms.call('redo'));
    assert.strictEqual(ms.snapshot(), now);
});

test('getSelection: what the user selected, and whether the plugin made it', () => {
    const ms = fresh();
    ok(ms.call('writeVoice', { measure: 1, events: [notes([60], '1/4'), notes([62], '1/4')] }));
    let s = ok(ms.call('getSelection'));
    assert.strictEqual(s.kind, 'range');
    assert.strictEqual(s.fromUser, false);
    // the user clicks a note in MuseScore
    ms.selection = { kind: 'list', els: [{ kind: 'note', id: ms.crAt(0, Q).notes[0].id }], range: null };
    s = ok(ms.call('getSelection'));
    assert.strictEqual(s.fromUser, true);
    assert.deepStrictEqual(s.elements, [{ type: 'Note', tick: Q, staff: 0, voice: 0, pitch: 62, measure: 1 }]);
    // the user selects bars 2-3 on both staves
    ms.selection = { kind: 'range', els: [], range: { start: WHOLE, end: 3 * WHOLE, s0: 0, s1: 2 } };
    s = ok(ms.call('getSelection'));
    assert.deepStrictEqual([s.fromUser, s.startMeasure, s.endMeasure, s.startStaff, s.endStaff], [true, 2, 3, 0, 1]);
});

test('checkScore finds bars whose voices do not add up', () => {
    const ms = fresh();
    assert.strictEqual(ok(ms.call('checkScore')).ok, true);
    ms.removeCr(ms.crAt(4, WHOLE));
    ms.insertCr(ms.newCr(1, 2 * WHOLE, 3 * WHOLE, 3 * WHOLE, 60));       // a voice 2 note longer than bar 3
    let r = ok(ms.call('checkScore'));
    assert.deepStrictEqual([r.ok, r.corrupted], [false, [
        { measure: 2, staff: 1, voice: 0, problem: 'incomplete', found: '0/1', expected: '1/1' },
        { measure: 3, staff: 0, voice: 1, problem: 'too long', found: '3/1', expected: '1/1' }]]);
    r = ok(ms.call('checkScore', { startMeasure: 3, endMeasure: 4 }));
    assert.deepStrictEqual(r.corrupted.map(c => c.measure), [3]);
    fails(ms.call('checkScore', { startMeasure: 3, endMeasure: 2 }), /endMeasure/);
});

test('voices 2-4: cleared rests become gaps, and are not reported as rests', () => {
    const ms = fresh();
    ok(ms.call('writeVoice', { measure: 1, voice: 1, events: [notes([55], '1/2'), { rest: true, duration: '1/2' }] }));
    ok(ms.call('clearRange', { startTick: 0, endTick: 960, voices: [1] }));
    // the rest of the bar still holds the rest written, so the cleared part is a gap
    assert.deepStrictEqual(ms.dump(1, 0, WHOLE).map(c => [c.tick, c.rest, !!c.gap]), [[0, true, true], [960, true, false]]);
    const els = ok(ms.call('getScore', { startMeasure: 1, endMeasure: 1 })).analysis.measures[0].elements.staff0;
    assert.deepStrictEqual(els.filter(e => e.voice === 1).map(e => e.startTick), [960]);
    // writing into the gap splits it without a warning
    const r = ok(ms.call('writeVoice', { measure: 1, offset: '1/4', voice: 1, events: [notes([57], '1/4')] }));
    assert.ok(!r.warnings);
    assert.deepStrictEqual(ms.dump(1, 0, WHOLE).map(c => [c.tick, c.rest ? (c.gap ? 'g' : 'r') : c.pitches[0]]),
                           [[0, 'g'], [480, 57], [960, 'r']]);
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
        readOnlyActions: ms.plugin.get('readOnlyActions'),
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
