// A small model of the MuseScore 4.7 plugin API, enough to run the plugin's
// note writing, tie, batch and validation code offline with node.
//
// It follows the engine behaviour the plugin relies on, as read in the
// MuseScore v4.7.5 source (Cursor with its own input state, Score::setNoteRest /
// makeGap / expandVoice, InputState::moveToNextInputPos, the plugin command
// locking the undo stack, Score::cmdToggleTie falling back to cmdAddTie,
// TDuration(Fraction) truncating). Where the plugin must never go it is
// stricter than MuseScore and throws (a duration crossing a barline, a write
// starting inside a held note, findSegmentAtTick past the end of the score).
//
// This is NOT MuseScore. It tests the plugin's own logic; only the live test
// (tests/live/test_step1.py) shows what MuseScore really does.

'use strict';

const vm = require('vm');
const fs = require('fs');
const path = require('path');
const { qmlToJs } = require('../../syntax_check.js');

const WHOLE = 1920;
const VOICES = 4;

const Element = {
    INVALID: 0, NOTE: 20, REST: 21, CHORD: 93, TIE: 30, DYNAMIC: 43, TEMPO_TEXT: 60, FERMATA: 51,
    STAFF_TEXT: 45, SYSTEM_TEXT: 46, EXPRESSION: 47, HARMONY: 53, REHEARSAL_MARK: 49, BREATH: 42,
    TRIPLET_FEEL: 48, SLUR: 100, HAIRPIN: 101, GRADUAL_TEMPO_CHANGE: 102, VOLTA: 103, OTTAVA: 104,
    PEDAL: 105, TRILL: 106, TEXTLINE: 107, LET_RING: 108, MARKER: 70, JUMP: 71, LYRICS: 72,
    KEYSIG: 73, TIMESIG: 74, SEGMENT: 90, MEASURE: 91,
};
const SegmentType = { ChordRest: 128, All: 0xffffff };

// Longest TDuration (up to 4 dots) that fits `ticks`, else a quarter:
// what Cursor.setDuration() does with a duration that isn't one value.
function tdurationTicks(ticks) {
    for (let base = WHOLE * 4; base >= WHOLE / 1024; base /= 2) {
        for (let dots = 4; dots >= 0; dots--) {
            let v = base, add = base;
            for (let d = 0; d < dots; d++) { add /= 2; v += add; }
            if (v <= ticks) return v;
        }
    }
    return WHOLE / 4;
}

const NOTE_VALUES = [6720, 5760, 3840, 3360, 2880, 1920, 1680, 1440, 960, 840, 720, 480, 420, 360, 240, 210, 180, 120, 105, 90, 60, 45, 30, 15];
function toDurationList(ticks) {
    const out = [];
    while (ticks > 0) {
        const v = NOTE_VALUES.find(x => x <= ticks);
        if (!v) throw new Error('mock: ' + ticks + ' ticks can\'t be split');
        out.push(v);
        ticks -= v;
    }
    return out;
}

class MockMuseScore {
    constructor(opts = {}) {
        const nstaves = opts.nstaves || 2;
        const bars = opts.bars || 4;
        const ts = opts.timesig || [4, 4];
        this.state = { nstaves, measures: [], crs: {}, ties: {}, nextId: 1 };
        for (let t = 0; t < nstaves * VOICES; t++) this.state.crs[t] = [];
        this.appendMeasures(bars, ts);
        this.undoStack = [];
        this.open = null;          // snapshot of the open command
        this.noteEntryMode = false;
        this.inputDuration = 480;  // the score's own input state (used by cmdAddTie)
        this.selection = { kind: 'none', els: [], range: null };
        this.log = [];             // cmd() calls
        this.counters = { selectRange: 0, selClear: 0, select: 0, firstMeasure: 0, startCmd: 0, endCmd: 0, tieCmd: 0 };
        this.replies = [];
    }

    // ---------------- state helpers ----------------
    id() { return this.state.nextId++; }
    get endTick() { const m = this.state.measures; return m.length ? m[m.length - 1].tick + m[m.length - 1].ticks : 0; }
    measureIndexAt(tick) { return this.state.measures.findIndex(m => tick >= m.tick && tick < m.tick + m.ticks); }
    measureAt(tick) { const i = this.measureIndexAt(tick); return i >= 0 ? this.state.measures[i] : null; }
    trackCrs(track) { return this.state.crs[track]; }
    crAt(track, tick) { return this.trackCrs(track).find(c => c.tick === tick) || null; }
    crCovering(track, tick) { return this.trackCrs(track).find(c => c.tick <= tick && tick < c.tick + c.actual) || null; }
    segmentTicks() {
        const set = new Set();
        for (const t in this.state.crs) for (const c of this.state.crs[t]) set.add(c.tick);
        return Array.from(set).sort((a, b) => a - b);
    }
    hasSegment(tick) { for (const t in this.state.crs) if (this.crAt(+t, tick)) return true; return false; }
    findNote(noteId) {
        for (const t in this.state.crs) for (const c of this.state.crs[t]) for (const n of c.notes) if (n.id === noteId) return { cr: c, note: n };
        return null;
    }
    snapshot() { return JSON.stringify(this.state); }
    restore(s) { this.state = JSON.parse(s); }

    appendMeasures(n, ts) {
        const ms = this.state.measures;
        const last = ms[ms.length - 1];
        const [num, den] = ts || (last ? [last.num, last.den] : [4, 4]);
        for (let i = 0; i < n; i++) {
            const tick = this.endTick;
            const ticks = WHOLE * num / den;
            ms.push({ id: this.id(), tick, ticks, num, den, repeatStart: false, repeatEnd: false });
            for (let s = 0; s < this.state.nstaves; s++) {
                this.insertCr({ id: this.id(), track: s * VOICES, tick, ticks, actual: ticks, rest: true, notes: [], tuplet: null });
            }
        }
    }

    insertCr(cr) {
        const list = this.trackCrs(cr.track);
        list.push(cr);
        list.sort((a, b) => a.tick - b.tick);
    }

    removeCr(cr) {
        for (const n of cr.notes) {
            if (n.tieFor) this.removeTie(n.tieFor);
            if (n.tieBack) this.removeTie(n.tieBack);
        }
        const list = this.trackCrs(cr.track);
        list.splice(list.indexOf(cr), 1);
    }

    removeTie(tieId) {
        const tie = this.state.ties[tieId];
        if (!tie) return;
        const a = this.findNote(tie.start), b = this.findNote(tie.end);
        if (a) a.note.tieFor = null;
        if (b) b.note.tieBack = null;
        delete this.state.ties[tieId];
    }

    addTie(startId, endId) {
        const id = this.id();
        this.state.ties[id] = { id, start: startId, end: endId };
        this.findNote(startId).note.tieFor = id;
        this.findNote(endId).note.tieBack = id;
        return id;
    }

    addRests(track, from, to) {
        let pos = from;
        for (const v of toDurationList(to - from)) {
            this.insertCr({ id: this.id(), track, tick: pos, ticks: v, actual: v, rest: true, notes: [], tuplet: null });
            pos += v;
        }
    }

    // Score::expandVoice: an empty voice gets rests around the position.
    expandVoice(tick, track) {
        if (this.crAt(track, tick)) return;
        const m = this.measureAt(tick);
        const held = this.crCovering(track, tick);
        if (held) throw new Error('mock: write at tick ' + tick + ' starts inside a held note/rest of track ' + track + ' (MuseScore would split it)');
        if (track % VOICES === 0) throw new Error('mock: voice 1 has no element at tick ' + tick);
        const before = this.trackCrs(track).filter(c => c.tick < tick && c.tick >= m.tick);
        const from = before.length ? before[before.length - 1].tick + before[before.length - 1].actual : m.tick;
        if (tick > from) this.addRests(track, from, tick);
        const after = this.trackCrs(track).find(c => c.tick > tick && c.tick < m.tick + m.ticks);
        this.addRests(track, tick, after ? after.tick : m.tick + m.ticks);
    }

    // Score::setNoteRest for a duration that fits in its bar.
    setNoteRest(tick, track, pitch, ticks) {
        this.expandVoice(tick, track);
        const cr = this.crAt(track, tick);
        const m = this.measureAt(tick);
        let actual = ticks;
        if (cr.tuplet) {
            if (cr.ticks !== ticks) throw new Error('mock: only same-value writes inside tuplets are modelled');
            actual = cr.actual;
            this.removeCr(cr);
            this.insertCr({ id: this.id(), track, tick, ticks, actual, rest: pitch === null, tuplet: cr.tuplet,
                            notes: pitch === null ? [] : [{ id: this.id(), pitch, tieFor: null, tieBack: null }] });
            return;
        }
        if (tick + ticks > m.tick + m.ticks) {
            throw new Error('mock: a ' + ticks + '-tick note at tick ' + tick + ' crosses the barline at ' + (m.tick + m.ticks) +
                            ' (MuseScore would split and tie it by itself)');
        }
        const end = tick + ticks;
        const victims = this.trackCrs(track).filter(c => c.tick < end && c.tick + c.actual > tick);
        let tail = 0;
        for (const v of victims) {
            if (v.tick < tick) throw new Error('mock: overwrite starts inside a held element');
            if (v.tuplet) throw new Error('mock: overwriting a tuplet');
            tail = Math.max(tail, v.tick + v.actual);
            this.removeCr(v);
        }
        this.insertCr({ id: this.id(), track, tick, ticks, actual: ticks, rest: pitch === null, tuplet: null,
                        notes: pitch === null ? [] : [{ id: this.id(), pitch, tieFor: null, tieBack: null }] });
        if (tail > end) this.addRests(track, end, tail);
    }

    // InputState::nextInputPos
    nextInputPos(tick, track) {
        const mi = this.measureIndexAt(tick);
        for (const t of this.segmentTicks()) {
            if (t <= tick) continue;
            if (this.crAt(track, t) || this.measureIndexAt(t) !== mi) return t;
        }
        return null;
    }

    // Score::searchTieNote (same track preferred, else another voice of the staff)
    searchTieNote(noteId) {
        const { cr, note } = this.findNote(noteId);
        const next = this.segmentTicks().find(t => t >= cr.tick + cr.actual);
        if (next === undefined) return null;
        const a = this.measureAt(cr.tick), b = this.measureAt(next);
        if (a !== b && (b.repeatStart || a.repeatEnd)) return null;   // segmentsAreAdjacent
        const staff = Math.floor(cr.track / VOICES);
        let found = null;
        for (let t = staff * VOICES; t < staff * VOICES + VOICES; t++) {
            const c = this.crAt(t, next);
            if (!c || c.rest) continue;
            const n = c.notes.find(x => x.pitch === note.pitch);
            if (n && (!found || t === cr.track)) found = n;
        }
        return found ? found.id : null;
    }

    cmdToggleTie() {
        const sel = this.selection;
        if (sel.kind !== 'list' || sel.els.length !== 1 || sel.els[0].kind !== 'note') return;
        const noteId = sel.els[0].id;
        const { cr, note } = this.findNote(noteId);
        if (note.tieFor) { this.removeTie(note.tieFor); return; }                // toggles off
        const target = this.searchTieNote(noteId);
        if (target) { this.addTie(noteId, target); return; }
        // cmdAddTie: writes a new note of the same pitch after it, tied
        const at = this.nextInputPos(cr.tick, cr.track);
        if (at === null) return;
        this.setNoteRest(at, cr.track, note.pitch, this.inputDuration);
        this.addTie(noteId, this.crAt(cr.track, at).notes[0].id);
    }

    // What a track holds, for assertions: [{tick, ticks, actual, rest, pitches, tiedForward, tiedBack}]
    dump(track, from = 0, to = Infinity) {
        return this.trackCrs(track).filter(c => c.tick >= from && c.tick < to).map(c => ({
            tick: c.tick, ticks: c.ticks, actual: c.actual, rest: c.rest, tuplet: !!c.tuplet,
            pitches: c.notes.map(n => n.pitch).sort((a, b) => a - b),
            tiedForward: c.notes.filter(n => n.tieFor).map(n => n.pitch).sort((a, b) => a - b),
            tiedBack: c.notes.filter(n => n.tieBack).map(n => n.pitch).sort((a, b) => a - b),
            tieTargets: c.notes.filter(n => n.tieFor).map(n => {
                const end = this.findNote(this.state.ties[n.tieFor].end);
                return { pitch: n.pitch, tick: end.cr.tick, track: end.cr.track, pitch2: end.note.pitch };
            }),
        }));
    }

    // ---------------- API wrappers ----------------
    frac(ticks) { return { ticks, numerator: ticks / 120, denominator: 16 }; }

    wrapNote(noteId) {
        const eng = this;
        const found = eng.findNote(noteId);
        if (!found) return null;
        return {
            __id: noteId, type: Element.NOTE, name: 'Note',
            get pitch() { return eng.findNote(noteId).note.pitch; },
            get tpc() { return 14; },
            get track() { return eng.findNote(noteId).cr.track; },
            get fraction() { return eng.frac(eng.findNote(noteId).cr.tick); },
            get tieForward() { const n = eng.findNote(noteId); return n && n.note.tieFor ? eng.wrapTie(n.note.tieFor) : null; },
            get tieBack() { const n = eng.findNote(noteId); return n && n.note.tieBack ? eng.wrapTie(n.note.tieBack) : null; },
            get parent() { return eng.wrapCr(eng.findNote(noteId).cr); },
            is(other) { return !!other && other.__id === noteId; },
        };
    }

    wrapTie(tieId) {
        const eng = this;
        return {
            type: Element.TIE,
            get startNote() { const t = eng.state.ties[tieId]; return t ? eng.wrapNote(t.start) : null; },
            get endNote() { const t = eng.state.ties[tieId]; return t ? eng.wrapNote(t.end) : null; },
        };
    }

    wrapCr(cr) {
        if (!cr) return null;
        const eng = this;
        const id = cr.id;
        const live = () => { for (const t in eng.state.crs) { const c = eng.state.crs[t].find(x => x.id === id); if (c) return c; } return null; };
        return {
            __id: id,
            get type() { return live().rest ? Element.REST : Element.CHORD; },
            get name() { return live().rest ? 'Rest' : 'Chord'; },
            get track() { return live().track; },
            get fraction() { return eng.frac(live().tick); },
            get duration() { return eng.frac(live().ticks); },
            get actualDuration() { return eng.frac(live().actual); },
            get tuplet() { const c = live(); return c.tuplet ? { actualNotes: c.tuplet.actual, normalNotes: c.tuplet.normal } : null; },
            get notes() { return live().notes.map(n => eng.wrapNote(n.id)); },
            get lyrics() { return []; },
            get articulations() { return []; },
            get graceNotes() { return []; },
            add(el) {
                const c = live();
                if (el.__newNote && !c.rest) c.notes.push({ id: eng.id(), pitch: el.pitch, tieFor: null, tieBack: null });
            },
            is(other) { return !!other && other.__id === id; },
        };
    }

    wrapSegment(tick) {
        if (tick === null || tick === undefined || !this.hasSegment(tick)) return null;
        const eng = this;
        return {
            tick, type: Element.SEGMENT, annotations: [],
            elementAt(track) { return eng.wrapCr(eng.crAt(track, tick)); },
            get next() { const t = eng.segmentTicks().find(x => x > tick); return t === undefined ? null : eng.wrapSegment(t); },
            get nextInMeasure() {
                const t = eng.segmentTicks().find(x => x > tick);
                return t !== undefined && eng.measureIndexAt(t) === eng.measureIndexAt(tick) ? eng.wrapSegment(t) : null;
            },
        };
    }

    wrapMeasure(i) {
        const m = this.state.measures[i];
        if (!m) return null;
        const eng = this;
        return {
            type: Element.MEASURE,
            tick: eng.frac(m.tick), ticks: eng.frac(m.ticks),
            timesigActual: { numerator: m.num, denominator: m.den },
            timesigNominal: { numerator: m.num, denominator: m.den },
            get repeatStart() { return m.repeatStart; },
            get repeatEnd() { return m.repeatEnd; },
            repeatCount: 2, elements: [],
            get nextMeasure() { return eng.wrapMeasure(i + 1); },
            get prevMeasure() { return i > 0 ? eng.wrapMeasure(i - 1) : null; },
            get firstSegment() { return eng.wrapSegment(m.tick); },
        };
    }

    newCursor() {
        const eng = this;
        const st = { track: 0, seg: null, last: null, dur: null };
        const inTrack = t => t !== null && !!eng.crAt(st.track, t);
        return {
            get staffIdx() { return Math.floor(st.track / VOICES); },
            set staffIdx(v) { st.track = v * VOICES + st.track % VOICES; },
            get voice() { return st.track % VOICES; },
            set voice(v) { st.track = Math.floor(st.track / VOICES) * VOICES + v; },
            get track() { return st.track; },
            rewind(mode) {
                if (mode !== 0) return;
                st.seg = eng.segmentTicks().find(t => inTrack(t));
                if (st.seg === undefined) st.seg = null;
                st.last = st.seg;
            },
            rewindToFraction(f) { st.seg = eng.hasSegment(f.ticks) ? f.ticks : null; st.last = st.seg; },
            get segment() { return eng.wrapSegment(st.seg); },
            get element() { return st.seg === null ? null : eng.wrapCr(eng.crAt(st.track, st.seg)); },
            get tick() { return st.seg === null ? 0 : st.seg; },
            get tempo() { return 2; },
            time() { return 0; },
            next() {
                if (st.seg === null) return false;
                const t = eng.segmentTicks().find(x => x > st.seg && inTrack(x));
                st.seg = t === undefined ? null : t;
                return st.seg !== null;
            },
            prev() {
                if (st.seg === null) return false;
                const ts = eng.segmentTicks().filter(x => x < st.seg && inTrack(x));
                st.seg = ts.length ? ts[ts.length - 1] : null;
                return st.seg !== null;
            },
            setDuration(z, n) { st.dur = n ? tdurationTicks(WHOLE * z / n) : 480; },
            addNote(pitch, addToChord) {
                eng.requireOpen('Cursor.addNote');
                if (st.seg === null) return;
                const dur = st.dur || 480;
                if (addToChord) {
                    if (st.last === null) throw new Error('mock: addNote(p, true) with a null last segment (crashes MuseScore)');
                    const cr = eng.crAt(st.track, st.last);
                    if (!cr || cr.rest) return;
                    cr.notes.push({ id: eng.id(), pitch, tieFor: null, tieBack: null });
                    const at = cr.tick;
                    const next = eng.nextInputPos(at, st.track);
                    st.last = at;
                    st.seg = next === null ? at : next;
                    return;
                }
                const at = st.seg;
                eng.setNoteRest(at, st.track, pitch, dur);
                const next = eng.nextInputPos(at, st.track);
                st.last = at;
                if (next !== null) st.seg = next;
            },
            addRest() {
                eng.requireOpen('Cursor.addRest');
                if (st.seg === null) return;
                const at = st.seg;
                eng.setNoteRest(at, st.track, null, st.dur || 480);
                const next = eng.nextInputPos(at, st.track);
                st.last = at;
                if (next !== null) st.seg = next;
            },
            addTuplet(ratio, duration) {
                eng.requireOpen('Cursor.addTuplet');
                const at = st.seg;
                const total = duration.ticks;
                const base = total / ratio.denominator;
                const actual = total / ratio.numerator;
                const m = eng.measureAt(at);
                if (at + total > m.tick + m.ticks) return;
                eng.expandVoice(at, st.track);
                for (const v of eng.trackCrs(st.track).filter(c => c.tick < at + total && c.tick + c.actual > at)) eng.removeCr(v);
                const tup = { id: eng.id(), actual: ratio.numerator, normal: ratio.denominator };
                for (let i = 0; i < ratio.numerator; i++) {
                    eng.insertCr({ id: eng.id(), track: st.track, tick: at + i * actual, ticks: base, actual, rest: true, notes: [], tuplet: tup });
                }
            },
        };
    }

    requireOpen(what) {
        if (!this.open) throw new Error('mock: ' + what + ' outside startCmd/endCmd');
    }

    scoreApi() {
        const eng = this;
        const selection = {
            get isRange() { return eng.selection.kind === 'range'; },
            get startSegment() { return eng.selection.range ? eng.wrapSegment(eng.selection.range.start) || { tick: eng.selection.range.start } : null; },
            get endSegment() { return eng.selection.range && eng.selection.range.end < eng.endTick ? { tick: eng.selection.range.end } : null; },
            get startStaff() { return eng.selection.range ? eng.selection.range.s0 : 0; },
            get endStaff() { return eng.selection.range ? eng.selection.range.s1 : 0; },
            get elements() { return eng.selection.kind === 'list' ? eng.selection.els.map(e => eng.wrapNote(e.id)) : []; },
            select(el, add) {
                eng.counters.select++;
                if (!el || el.type !== Element.NOTE) return false;
                eng.selection = { kind: 'list', els: [{ kind: 'note', id: el.__id }], range: null };
                return true;
            },
            selectRange(st, et, s0, s1) {
                eng.counters.selectRange++;
                eng.selection = { kind: 'range', els: [], range: { start: st, end: et, s0, s1 } };
                return true;
            },
            clear() { eng.counters.selClear++; eng.selection = { kind: 'none', els: [], range: null }; return true; },
        };
        const staves = [];
        for (let s = 0; s < eng.state.nstaves; s++) {
            staves.push({ part: { longName: 'Staff ' + s, partName: 'Staff ' + s, shortName: '', instrumentId: 'piano', startTrack: 0, endTrack: eng.state.nstaves * VOICES, show: true },
                          key() { return 0; }, swing() { return { isOn: false }; } });
        }
        return {
            get nstaves() { return eng.state.nstaves; },
            get ntracks() { return eng.state.nstaves * VOICES; },
            staves,
            parts: [staves[0].part],
            title: 'Mock', duration: 0, spanners: [],
            metaTag() { return ''; },
            get nmeasures() { return eng.state.measures.length; },
            get firstMeasure() { eng.counters.firstMeasure++; return eng.wrapMeasure(0); },
            get lastMeasure() { return eng.wrapMeasure(eng.state.measures.length - 1); },
            get selection() { return selection; },
            tick2measure(f) { const i = eng.measureIndexAt(f.ticks); return i >= 0 ? eng.wrapMeasure(i) : null; },
            findSegmentAtTick(types, f) {
                if (eng.measureIndexAt(f.ticks) < 0) throw new Error('mock: findSegmentAtTick(' + f.ticks + ') past the end dereferences a null measure in MuseScore');
                return eng.wrapSegment(f.ticks);
            },
            firstSegment() { return eng.wrapSegment(eng.segmentTicks()[0]); },
            newCursor() { return eng.newCursor(); },
            appendMeasures(n) { eng.requireOpen('appendMeasures'); eng.appendMeasures(n); },
            startCmd() {
                eng.counters.startCmd++;
                if (eng.open) throw new Error('mock: nested startCmd (the plugin must track cmdDepth)');
                eng.open = eng.snapshot();
            },
            endCmd(rollback) {
                eng.counters.endCmd++;
                if (!eng.open) throw new Error('mock: endCmd without startCmd');
                if (rollback) eng.restore(eng.open);
                else if (eng.snapshot() !== eng.open) eng.undoStack.push(eng.open);
                eng.open = null;
            },
        };
    }

    cmd(code) {
        this.log.push(code);
        if (code === 'action://notation/cancel') {
            if (this.noteEntryMode) { this.noteEntryMode = false; return; }
            this.selection = { kind: 'none', els: [], range: null };
            return;
        }
        if (code === 'action://notation/undo') {
            if (this.open) return;                 // the plugin command locks the undo stack
            const s = this.undoStack.pop();
            if (s) this.restore(s);
            return;
        }
        if (code === 'tie') {
            this.counters.tieCmd++;
            this.requireOpen('cmd("tie")');
            if (this.noteEntryMode) {
                // NotationNoteInput::addTie: adds a tied note after the input position
                const el = this.selection.els[0];
                if (el) { const { cr } = this.findNote(el.id); this.setNoteRest(this.nextInputPos(cr.tick, cr.track), cr.track, 60, this.inputDuration); }
                return;
            }
            this.cmdToggleTie();
        }
    }

    // Loads the plugin (converted from QML) into a sandbox wired to this mock.
    loadPlugin(qmlPath) {
        const eng = this;
        qmlPath = qmlPath || path.join(__dirname, '..', '..', 'musescore-mcp-websocket.qml');
        let js = qmlToJs(fs.readFileSync(qmlPath, 'utf8'));
        const last = js.lastIndexOf('}');
        js = js.slice(0, last) + '\n    return { get: function(n) { return eval(n); }, set: function(n, v) { eval(n + " = v"); } };\n}\nMuseScorePlugin();\n';
        const score = this.scoreApi();
        const sandbox = {
            console: { log() {}, warn() {}, error() {} },
            get curScore() { return score; },
            Element, Segment: SegmentType, Cursor: { SCORE_START: 0, SELECTION_START: 1, SELECTION_END: 2 },
            Lyrics: { SINGLE: 0, BEGIN: 1, END: 2, MIDDLE: 3 }, DynamicType: {}, MarkerType: {}, KeyMode: { MAJOR: 1, MINOR: 0 },
            newElement(type) { return type === Element.NOTE ? { __newNote: true, pitch: 60 } : { type }; },
            removeElement() {},
            cmd(code) { eng.cmd(code); },
            fraction(n, d) { return { numerator: n, denominator: d, ticks: WHOLE * n / d }; },
            fractionFromTicks(t) { return eng.frac(t); },
            api: { websocketserver: { listen() {}, onMessage() {}, send(id, text) { eng.replies.push(JSON.parse(text)); } } },
        };
        this.plugin = vm.runInNewContext(js, sandbox, { filename: 'musescore-mcp-websocket.qml.js' });
        return this.plugin;
    }

    // Sends one request through processMessage, as the websocket would.
    call(action, params) {
        const msg = params === undefined ? { action } : { action, params };
        this.plugin.get('processMessage')(JSON.stringify(msg), 1);
        const reply = this.replies.pop();
        return reply.status === 'success' ? reply.result : { error: reply.message };
    }

    raw(obj) {
        this.plugin.get('processMessage')(JSON.stringify(obj), 1);
        const reply = this.replies.pop();
        return reply.status === 'success' ? reply.result : { error: reply.message };
    }
}

module.exports = { MockMuseScore, Element, WHOLE, tdurationTicks };
