// A small model of the MuseScore 4.7 plugin API, enough to run the plugin's
// note writing, tie, batch, range, text and versioning code offline with node.
//
// It follows the engine behaviour the plugin relies on, as read in the
// MuseScore v4.7.5 source (Cursor with its own input state, Score::setNoteRest /
// makeGap / expandVoice, InputState::moveToNextInputPos, the plugin command
// locking the undo stack, Score::cmdToggleTie falling back to cmdAddTie,
// TDuration(Fraction) truncating, Score::changeCRlen behind
// ChordRest.duration =, Score::deleteItem turning a chord into a rest and a
// rest of voices 2-4 into a gap, Cursor::add putting each element type in its
// place, system locks working on the selection). Where the plugin must never
// go it is stricter than MuseScore and throws (a duration crossing a barline,
// a write starting inside a held note, findSegmentAtTick past the end of the
// score, lengthening with ChordRest.duration, ...).
//
// This is NOT MuseScore. It tests the plugin's own logic; only the live tests
// (tests/live/) show what MuseScore really does.

'use strict';

const vm = require('vm');
const fs = require('fs');
const path = require('path');
const { qmlToJs } = require('../../syntax_check.js');

const WHOLE = 1920;
const VOICES = 4;

const Element = {
    INVALID: 0, NOTE: 20, REST: 21, CHORD: 93, TIE: 30, ARTICULATION: 31, ORNAMENT: 32, BREATH: 42, DYNAMIC: 43, TEXT: 44,
    STAFF_TEXT: 45, SYSTEM_TEXT: 46, EXPRESSION: 47, TRIPLET_FEEL: 48, REHEARSAL_MARK: 49, FERMATA: 51,
    HARMONY: 53, TEMPO_TEXT: 60, MARKER: 70, JUMP: 71, LYRICS: 72, KEYSIG: 73, TIMESIG: 74, CLEF: 75,
    LAYOUT_BREAK: 76, SEGMENT: 90, MEASURE: 91, VBOX: 92,
    SLUR: 100, HAIRPIN: 101, GRADUAL_TEMPO_CHANGE: 102, VOLTA: 103, OTTAVA: 104, PEDAL: 105, TRILL: 106,
    TEXTLINE: 107, LET_RING: 108,
};
const SegmentType = { ChordRest: 128, All: 0xffffff };
// Tid as the plugin API has it: the lyricist style is POET
const Tid = { DEFAULT: 0, TITLE: 1, SUBTITLE: 2, COMPOSER: 3, POET: 4, TRANSLATOR: 5 };
// curScore.addText takes TextStyleType names
const TextStyleType = { TITLE: Tid.TITLE, SUBTITLE: Tid.SUBTITLE, COMPOSER: Tid.COMPOSER, LYRICIST: Tid.POET };
const ClefType = {
    INVALID: -1, G: 0, G15_MB: 1, G8_VB: 2, G8_VA: 3, G15_MA: 4, G8_VB_O: 5, G8_VB_P: 6, G_1: 7, C1: 8, C2: 9, C3: 10,
    C4: 11, C5: 12, F: 20, F15_MB: 21, F8_VB: 22, F_8VA: 23, F_15MA: 24, F_B: 25, F_C: 26, PERC: 29, PERC2: 30, TAB: 31,
};
const LayoutBreak = { PAGE: 0, LINE: 1, SECTION: 2, NOBREAK: 3 };
// A few of MuseScore's instrument templates (id -> name, short name, clefs of its staves)
const MOCK_INSTRUMENTS = {};
[['piano', 'Piano', 'Pno.', ['G', 'F']], ['flute', 'Flute', 'Fl.', ['G']], ['violin', 'Violin', 'Vln.', ['G']],
 ['cello', 'Violoncello', 'Vc.', ['F']], ['voice', 'Voice', 'Vo.', ['G']]]
    .forEach(([id, name, short, clefs]) => { MOCK_INSTRUMENTS[id] = { id, name, short, clefs }; });
const DynamicType = {};
['OTHER', 'PPPPPP', 'PPPPP', 'PPPP', 'PPP', 'PP', 'P', 'MP', 'MF', 'F', 'FF', 'FFF', 'FFFF', 'FFFFF', 'FFFFFF', 'FP', 'PF',
 'SF', 'SFZ', 'SFF', 'SFFZ', 'SFFF', 'SFFFZ', 'SFP', 'SFPP', 'RFZ', 'RF', 'FZ', 'M', 'R', 'S', 'Z', 'N']
    .forEach((k, i) => { DynamicType[k] = i; });
// SymId is MuseScore's name -> number map (a QQmlPropertyMap of the enum). The mock
// knows the names checked in the 4.7.5 source (api/v1/apitypes.h); others are
// undefined, as in MuseScore. symName() turns a number back into its name.
const SYM_NAMES = [
    'noSym', 'articStaccatoAbove', 'articStaccatoBelow', 'articStaccatissimoAbove', 'articStaccatissimoBelow',
    'articTenutoAbove', 'articTenutoBelow', 'articAccentAbove', 'articAccentBelow', 'articMarcatoAbove', 'articMarcatoBelow',
    'articTenutoStaccatoAbove', 'articTenutoStaccatoBelow', 'articAccentStaccatoAbove', 'articAccentStaccatoBelow',
    'articMarcatoStaccatoAbove', 'articMarcatoStaccatoBelow', 'articStressAbove', 'articStressBelow',
    'articUnstressAbove', 'articUnstressBelow', 'articMarcatoTenutoAbove', 'articMarcatoTenutoBelow',
    'stringsUpBow', 'stringsDownBow', 'stringsHarmonic', 'pluckedSnapPizzicatoAbove', 'pluckedSnapPizzicatoBelow',
    'brassMuteOpen', 'brassMuteClosed', 'ornamentTrill', 'ornamentMordent', 'ornamentShortTrill', 'ornamentTurn',
    'ornamentTurnInverted',
];
const SymId = {};
SYM_NAMES.forEach((n, i) => { SymId[n] = 3000 + i; });
function symName(v) { const i = v - 3000; return i >= 0 && i < SYM_NAMES.length ? SYM_NAMES[i] : undefined; }
// Articulation::subtypeUserName(): the (translated) display name, e.g. "Staccato below", "Up bow"
function symUserName(v) {
    const words = (symName(v) || 'unknown').replace(/^(artic|ornament|strings|plucked|brassMute)/, '')
        .replace(/([a-z])([A-Z])/g, '$1 $2').toLowerCase();
    return words.charAt(0).toUpperCase() + words.slice(1);
}

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

// How MuseScore spells a pitch entered without a name (key of C)
const DEFAULT_TPC = [14, 21, 16, 11, 18, 13, 20, 15, 22, 17, 12, 19];

class MockMuseScore {
    constructor(opts = {}) {
        const nstaves = opts.nstaves || 2;
        const bars = opts.bars || 4;
        const ts = opts.timesig || [4, 4];
        this.state = {
            nstaves, measures: [], crs: {}, ties: {}, anns: [], keys: {}, clefs: {}, nextId: 2, locks: null,
            parts: [{ id: 1, instrumentId: 'piano', longName: 'Piano', partName: 'Piano', shortName: 'Pno.', nstaves, show: true }],
            frame: opts.noTitle ? null : { elements: [{ id: 0, type: Element.TEXT, subStyle: Tid.TITLE, text: 'Mock' }] },
        };
        for (let t = 0; t < nstaves * VOICES; t++) this.state.crs[t] = [];
        for (let s = 0; s < nstaves; s++) {
            this.state.keys[s] = [{ tick: 0, fifths: opts.fifths || 0 }];
            this.state.clefs[s] = [{ tick: 0, type: s === 1 ? ClefType.F : ClefType.G }];
        }
        this.appendMeasures(bars, ts);
        this.meta = { workTitle: opts.noTitle ? '' : 'Mock' };   // setMetaTag is not undoable in MuseScore
        this.undoStack = [];
        this.redoStack = [];
        this.open = null;          // snapshot of the open command
        this.noteEntryMode = false;
        this.inputDuration = 480;  // the score's own input state (used by cmdAddTie)
        this.selection = { kind: 'none', els: [], range: null };
        this.clipboard = null;
        this.exports = [];
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
    crById(id) { for (const t in this.state.crs) { const c = this.state.crs[t].find(x => x.id === id); if (c) return c; } return null; }
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
    staffOf(track) { return Math.floor(track / VOICES); }

    newNote(pitch) {
        const tpc = DEFAULT_TPC[pitch % 12];
        return { id: this.id(), pitch, tpc1: tpc, tpc2: tpc, tieFor: null, tieBack: null };
    }
    newCr(track, tick, ticks, actual, pitch, tuplet) {
        return { id: this.id(), track, tick, ticks, actual, rest: pitch === null, gap: false, tuplet: tuplet || null,
                 notes: pitch === null ? [] : [this.newNote(pitch)], lyrics: [], arts: [] };
    }

    appendMeasures(n, ts) {
        const ms = this.state.measures;
        const last = ms[ms.length - 1];
        const [num, den] = ts || (last ? [last.num, last.den] : [4, 4]);
        for (let i = 0; i < n; i++) {
            const tick = this.endTick;
            const ticks = WHOLE * num / den;
            ms.push({ id: this.id(), tick, ticks, num, den, repeatStart: false, repeatEnd: false, repeatCount: 2, elements: [] });
            for (let s = 0; s < this.state.nstaves; s++) this.insertCr(this.newCr(s * VOICES, tick, ticks, ticks, null));
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
            this.insertCr(this.newCr(track, pos, v, v, null));
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
        if (cr.tuplet) {
            if (cr.ticks !== ticks) throw new Error('mock: only same-value writes inside tuplets are modelled');
            this.removeCr(cr);
            this.insertCr(this.newCr(track, tick, ticks, cr.actual, pitch, cr.tuplet));
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
        this.insertCr(this.newCr(track, tick, ticks, ticks, pitch));
        if (tail > end) this.addRests(track, end, tail);
    }

    // Score::changeCRlen, behind ChordRest.duration = (shortening only).
    changeCRlen(id, ticks) {
        this.requireOpen('ChordRest.duration =');
        const cr = this.crById(id);
        if (!(ticks > 0 && ticks < cr.ticks)) throw new Error('mock: ChordRest.duration = ' + ticks + ' on a ' + cr.ticks + '-tick element: only shortening is modelled');
        if (cr.tuplet) throw new Error('mock: shortening a note of a tuplet is not modelled');
        if (NOTE_VALUES.indexOf(ticks) < 0) {
            throw new Error('mock: ChordRest.duration = ' + ticks + ' ticks is not one note value (changeCRlen would put rests into the chord\'s own length)');
        }
        for (const n of cr.notes) if (n.tieFor) this.removeTie(n.tieFor);
        const oldEnd = cr.tick + cr.actual;
        cr.ticks = ticks;
        cr.actual = ticks;
        this.addRests(cr.track, cr.tick + ticks, oldEnd);    // setRest(): visible rests, even after a gap
    }

    // Score::deleteItem
    deleteCr(cr) {
        if (!cr.rest) {
            this.removeCr(cr);
            const rest = this.newCr(cr.track, cr.tick, cr.ticks, cr.actual, null, cr.tuplet);
            this.insertCr(rest);
            return;
        }
        if (cr.track % VOICES === 0 || cr.tuplet) return;     // voice 1 rests stay
        cr.gap = true;
        const m = this.measureAt(cr.tick);
        const inBar = this.trackCrs(cr.track).filter(c => c.tick >= m.tick && c.tick < m.tick + m.ticks);
        if (inBar.every(c => c.rest && c.gap)) inBar.forEach(c => this.removeCr(c));
    }

    removeElement(el) {
        this.requireOpen('removeElement');
        if (!el) return;
        const id = el.__id !== undefined ? el.__id : el.id;
        const cr = this.crById(id);
        if (cr) return this.deleteCr(cr);
        const found = this.findNote(id);
        if (found) {
            if (found.cr.notes.length > 1) {
                if (found.note.tieFor) this.removeTie(found.note.tieFor);
                if (found.note.tieBack) this.removeTie(found.note.tieBack);
                found.cr.notes.splice(found.cr.notes.indexOf(found.note), 1);
            } else {
                this.deleteCr(found.cr);
            }
            return;
        }
        const lists = [this.state.anns];
        for (const t in this.state.crs) for (const c of this.state.crs[t]) lists.push(c.lyrics, c.arts);
        for (const m of this.state.measures) lists.push(m.elements);
        if (this.state.frame) lists.push(this.state.frame.elements);
        for (const list of lists) {
            const i = list.findIndex(x => x.id === id);
            if (i >= 0) { list.splice(i, 1); return; }
        }
        throw new Error('mock: removeElement of an element that is not in the score (' + JSON.stringify(el) + ')');
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
        const staff = this.staffOf(cr.track);
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

    // Cursor::add: each element type goes where MuseScore puts it.
    cursorAdd(tick, track, el) {
        this.requireOpen('Cursor.add');
        if (el.id !== undefined) throw new Error('mock: Cursor.add of an element that is already in the score');
        const staff = this.staffOf(track);
        const cr = this.crAt(track, tick);
        switch (el.type) {
            case Element.ARTICULATION:
            case Element.ORNAMENT:
                if (!symName(el.symbol) || el.symbol === SymId.noSym) throw new Error('mock: articulation without a valid symbol');
                if (cr && !cr.rest) {
                    el.id = this.id();
                    // Layout puts it on the side away from the stem: below a low note (stem up)
                    const name = symName(el.symbol);
                    const low = Math.max(...cr.notes.map(n => n.pitch)) < 71 && track % VOICES === 0;
                    if (low && /Above$/.test(name) && SymId[name.replace(/Above$/, 'Below')] !== undefined) {
                        el.symbol = SymId[name.replace(/Above$/, 'Below')];
                    }
                    cr.arts.push(el);
                }
                return;
            case Element.LYRICS:
                if (cr) { el.id = this.id(); el.verse = el.verse || 0; cr.lyrics.push(el); }
                return;
            case Element.CLEF: {
                el.id = this.id();
                const type = el.concertClefType !== undefined ? el.concertClefType : ClefType.G;
                const list = this.state.clefs[staff].filter(c => c.tick !== tick);
                list.push({ tick, type });
                this.state.clefs[staff] = list.sort((a, b) => a.tick - b.tick);
                return;
            }
            case Element.KEYSIG: {
                el.id = this.id();
                const at = this.measureAt(tick).tick;
                const list = this.state.keys[staff].filter(k => k.tick !== at);
                list.push({ tick: at, fifths: el.concertKey || 0 });
                this.state.keys[staff] = list.sort((a, b) => a.tick - b.tick);
                return;
            }
            case Element.TIMESIG:
                this.setTimeSig(this.measureIndexAt(tick), el.timesig);
                return;
            case Element.LAYOUT_BREAK:
            case Element.MARKER:
            case Element.JUMP:
                el.id = this.id();
                this.measureAt(tick).elements.push(el);
                return;
            default:
                el.id = this.id();
                el.tick = tick;
                el.track = track;
                this.state.anns.push(el);
        }
    }

    // Score::cmdAddTimeSig, only for empty bars at the end of the score (what the live test does):
    // they are re-barred, keeping their total length (80 bars of 7/4 become 140 bars of 4/4)
    setTimeSig(mi, f) {
        const ms = this.state.measures;
        for (let i = mi; i < ms.length; i++) {
            const m = ms[i];
            for (const t in this.state.crs) {
                const crs = this.trackCrs(+t).filter(c => c.tick >= m.tick && c.tick < m.tick + m.ticks);
                if (crs.some(c => !c.rest || c.tick !== m.tick || c.actual !== m.ticks)) {
                    throw new Error('mock: time signature changes are only modelled for empty bars at the end');
                }
            }
            if (this.state.anns.some(a => a.tick >= m.tick) ) throw new Error('mock: time signature change over markings');
        }
        let pos = ms[mi].tick;
        const total = this.endTick - pos;
        const len = WHOLE * f.numerator / f.denominator;
        for (const t in this.state.crs) {
            for (const c of this.trackCrs(+t).filter(x => x.tick >= pos)) this.removeCr(c);
        }
        ms.splice(mi);
        for (let i = 0; i < Math.ceil(total / len); i++) {
            ms.push({ id: this.id(), tick: pos, ticks: len, num: f.numerator, den: f.denominator,
                      repeatStart: false, repeatEnd: false, repeatCount: 2, elements: [] });
            for (let s = 0; s < this.state.nstaves; s++) this.insertCr(this.newCr(s * VOICES, pos, len, len, null));
            pos += len;
        }
    }

    annotationsAt(tick) { return this.state.anns.filter(a => a.tick === tick); }

    // Quarter notes per second at `tick`, from the tempo marks (default 120 BPM)
    tempoAt(tick) {
        let tempo = 2, at = -1;
        for (const a of this.state.anns) {
            if (a.type === Element.TEMPO_TEXT && a.tick <= tick && a.tick >= at) { tempo = a.tempo; at = a.tick; }
        }
        return tempo;
    }


    // What a track holds, for assertions: [{tick, ticks, actual, rest, pitches, tiedForward, tiedBack}]
    dump(track, from = 0, to = Infinity) {
        return this.trackCrs(track).filter(c => c.tick >= from && c.tick < to).map(c => {
            const d = {
                tick: c.tick, ticks: c.ticks, actual: c.actual, rest: c.rest, tuplet: !!c.tuplet,
                pitches: c.notes.map(n => n.pitch).sort((a, b) => a - b),
                tiedForward: c.notes.filter(n => n.tieFor).map(n => n.pitch).sort((a, b) => a - b),
                tiedBack: c.notes.filter(n => n.tieBack).map(n => n.pitch).sort((a, b) => a - b),
                tieTargets: c.notes.filter(n => n.tieFor).map(n => {
                    const end = this.findNote(this.state.ties[n.tieFor].end);
                    return { pitch: n.pitch, tick: end.cr.tick, track: end.cr.track, pitch2: end.note.pitch };
                }),
            };
            if (c.gap) d.gap = true;
            return d;
        });
    }

    // Compact view of a track: "C4:480 r:480 ..." (pitch numbers, ~ for a tie)
    brief(track, from = 0, to = Infinity) {
        return this.trackCrs(track).filter(c => c.tick >= from && c.tick < to).map(c => {
            if (c.rest) return (c.gap ? 'g' : 'r') + ':' + c.actual;
            return c.notes.map(n => n.pitch + (n.tieFor ? '~' : '')).sort().join('.') + ':' + c.actual + (c.tuplet ? 't' : '');
        }).join(' ');
    }

    // ---------------- API wrappers ----------------
    frac(ticks) { return { ticks, numerator: ticks / 120, denominator: 16 }; }

    wrapNote(noteId) {
        const eng = this;
        const found = eng.findNote(noteId);
        if (!found) return null;
        const live = () => eng.findNote(noteId).note;
        return {
            __id: noteId, type: Element.NOTE, name: 'Note',
            get pitch() { return live().pitch; },
            set pitch(v) { eng.requireOpen('Note.pitch ='); live().pitch = v; },
            get tpc() { return live().tpc1; },
            get tpc1() { return live().tpc1; },
            set tpc1(v) { eng.requireOpen('Note.tpc1 ='); live().tpc1 = v; },
            get tpc2() { return live().tpc2; },
            set tpc2(v) { eng.requireOpen('Note.tpc2 ='); live().tpc2 = v; },
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
        const live = () => eng.crById(id);
        return {
            __id: id,
            get type() { return live().rest ? Element.REST : Element.CHORD; },
            get name() { return live().rest ? 'Rest' : 'Chord'; },
            get gap() { const c = live(); return c.rest ? !!c.gap : undefined; },
            get track() { return live().track; },
            get fraction() { return eng.frac(live().tick); },
            get duration() { return eng.frac(live().ticks); },
            set duration(f) { eng.changeCRlen(id, f.ticks); },
            get actualDuration() { return eng.frac(live().actual); },
            get tuplet() {
                const c = live();
                if (!c.tuplet) return null;
                const members = eng.trackCrs(c.track).filter(x => x.tuplet && x.tuplet.id === c.tuplet.id);
                return {
                    actualNotes: c.tuplet.actual, normalNotes: c.tuplet.normal,
                    fraction: eng.frac(Math.min(...members.map(x => x.tick))),
                    actualDuration: eng.frac(members.reduce((n, x) => n + x.actual, 0)),
                };
            },
            get notes() { return live().notes.map(n => eng.wrapNote(n.id)); },
            get lyrics() { return live().lyrics.map(l => Object.assign({}, l)); },
            get articulations() {
                return live().arts.map(a => Object.assign({ subtypeName() { return symUserName(a.symbol); } }, a));
            },
            get graceNotes() { return []; },
            add(el) {
                eng.requireOpen('Chord.add');
                const c = live();
                if (el.__newNote && !c.rest) c.notes.push(eng.newNote(el.pitch));
            },
            is(other) { return !!other && other.__id === id; },
        };
    }

    wrapSegment(tick) {
        if (tick === null || tick === undefined || !this.hasSegment(tick)) return null;
        const eng = this;
        return {
            tick, type: Element.SEGMENT,
            get annotations() { return eng.annotationsAt(tick); },
            elementAt(track) { return eng.wrapCr(eng.crAt(track, tick)); },
            get next() { const t = eng.segmentTicks().find(x => x > tick); return t === undefined ? null : eng.wrapSegment(t); },
            get nextInMeasure() {
                const t = eng.segmentTicks().find(x => x > tick);
                return t !== undefined && eng.measureIndexAt(t) === eng.measureIndexAt(tick) ? eng.wrapSegment(t) : null;
            },
        };
    }

    wrapFrame() {
        const eng = this;
        if (!eng.state.frame) return null;
        return { type: Element.VBOX, get elements() { return eng.state.frame.elements; }, prev: null };
    }

    wrapMeasure(i) {
        const m = this.state.measures[i];
        if (!m) return null;
        const eng = this;
        const live = () => eng.state.measures.find(x => x.id === m.id);
        return {
            type: Element.MEASURE,
            tick: eng.frac(m.tick), ticks: eng.frac(m.ticks),
            timesigActual: { numerator: m.num, denominator: m.den },
            timesigNominal: { numerator: m.num, denominator: m.den },
            get repeatStart() { return live().repeatStart; },
            set repeatStart(v) { eng.requireOpen('Measure.repeatStart ='); live().repeatStart = v; },
            get repeatEnd() { return live().repeatEnd; },
            set repeatEnd(v) { eng.requireOpen('Measure.repeatEnd ='); live().repeatEnd = v; },
            get repeatCount() { return live().repeatCount; },
            set repeatCount(v) { eng.requireOpen('Measure.repeatCount ='); live().repeatCount = v; },
            get elements() { return live().elements; },
            // MuseScore sets this flag only when it checks a score it loads: stale after edits
            corrupted(staff) { return false; },
            get nextMeasure() { return eng.wrapMeasure(i + 1); },
            get prevMeasure() { return i > 0 ? eng.wrapMeasure(i - 1) : null; },
            get prev() { return i > 0 ? eng.wrapMeasure(i - 1) : eng.wrapFrame(); },
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
            get tempo() { return eng.tempoAt(st.seg === null ? 0 : st.seg); },
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
                    cr.notes.push(eng.newNote(pitch));
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
            // Cursor::addTuplet: changeCRlen(cr, duration), then the tuplet
            // replaces that chord/rest.
            addTuplet(ratio, duration) {
                eng.requireOpen('Cursor.addTuplet');
                if (st.seg === null) return;
                const at = st.seg;
                const total = duration.ticks;
                const base = total / ratio.denominator;
                const actual = total / ratio.numerator;
                const m = eng.measureAt(at);
                if (at + total > m.tick + m.ticks) return;
                eng.expandVoice(at, st.track);
                let tail = 0;
                for (const v of eng.trackCrs(st.track).filter(c => c.tick >= at && c.tick < at + total)) {
                    if (v.tuplet) throw new Error('mock: a tuplet over a tuplet is not modelled');
                    tail = Math.max(tail, v.tick + v.actual);
                    eng.removeCr(v);
                }
                if (tail > at + total) eng.addRests(st.track, at + total, tail);
                const tup = { id: eng.id(), actual: ratio.numerator, normal: ratio.denominator };
                for (let i = 0; i < ratio.numerator; i++) {
                    eng.insertCr(eng.newCr(st.track, at + i * actual, base, actual, null, tup));
                }
            },
            add(el) {
                if (st.seg === null || !el) return;
                eng.cursorAdd(st.seg, st.track, el);
            },
        };
    }

    requireOpen(what) {
        if (!this.open) throw new Error('mock: ' + what + ' outside startCmd/endCmd');
    }

    // ---------------- parts (instruments) ----------------
    // A part's staves are consecutive, in part order (MuseScore relies on that).
    partStart(partId) {
        let s = 0;
        for (const p of this.state.parts) { if (p.id === partId) return s; s += p.nstaves; }
        return -1;
    }
    partOfStaff(staff) {
        let s = 0;
        for (const p of this.state.parts) { if (staff < s + p.nstaves) return p; s += p.nstaves; }
        return null;
    }
    partById(id) { return this.state.parts.find(p => p.id === id) || null; }

    // Moves staff data to new indices (map: old staff -> new staff; unmapped staves are dropped).
    remapStaves(map, count) {
        const st = this.state;
        const crs = {}, keys = {}, clefs = {};
        for (let t = 0; t < count * VOICES; t++) crs[t] = [];
        for (const t in st.crs) {
            const ns = map[this.staffOf(+t)];
            if (ns === undefined) continue;
            const nt = ns * VOICES + (+t % VOICES);
            crs[nt] = st.crs[t];
            for (const c of crs[nt]) c.track = nt;
        }
        for (const s in st.keys) if (map[s] !== undefined) keys[map[s]] = st.keys[s];
        for (const s in st.clefs) if (map[s] !== undefined) clefs[map[s]] = st.clefs[s];
        st.anns = st.anns.filter(a => a.track < 0 || map[this.staffOf(a.track)] !== undefined);
        for (const a of st.anns) if (a.track >= 0) a.track = map[this.staffOf(a.track)] * VOICES + a.track % VOICES;
        st.crs = crs;
        st.keys = keys;
        st.clefs = clefs;
        st.nstaves = count;
        for (const id in st.ties) {
            if (!this.findNote(st.ties[id].start) || !this.findNote(st.ties[id].end)) delete st.ties[id];
        }
    }

    // Score::appendPart(template): new staves at the bottom, the part last.
    appendPart(instrumentId) {
        const t = MOCK_INSTRUMENTS[instrumentId] || MOCK_INSTRUMENTS.piano;   // MuseScore falls back to a default
        const st = this.state;
        const s0 = st.nstaves;
        for (let i = 0; i < t.clefs.length; i++) {
            const s = s0 + i;
            for (let v = 0; v < VOICES; v++) st.crs[s * VOICES + v] = [];
            for (const m of st.measures) this.insertCr(this.newCr(s * VOICES, m.tick, m.ticks, m.ticks, null));
            st.keys[s] = st.keys[0].map(k => Object.assign({}, k));
            st.clefs[s] = [{ tick: 0, type: ClefType[t.clefs[i]] }];
        }
        st.nstaves += t.clefs.length;
        st.parts.push({ id: this.id(), instrumentId: t.id, longName: t.name, partName: t.name, shortName: t.short,
                        nstaves: t.clefs.length, show: true });
    }

    // EditPart::moveParts: the new part order, then the staves sorted to match (SortStaves).
    moveParts(ids, destId, after) {
        const st = this.state;
        const moving = ids.map(id => this.partById(id));
        const order = st.parts.filter(p => ids.indexOf(p.id) < 0);
        let at = order.findIndex(p => p.id === destId);
        if (at < 0 || moving.some(p => !p)) return;
        if (after) at++;
        order.splice(at, 0, ...moving);
        const map = {};
        let n = 0;
        for (const p of order) { const s0 = this.partStart(p.id); for (let i = 0; i < p.nstaves; i++) map[s0 + i] = n++; }
        this.remapStaves(map, n);
        st.parts = order;
    }

    removeParts(ids) {
        const st = this.state;
        const keep = st.parts.filter(p => ids.indexOf(p.id) < 0);
        const map = {};
        let n = 0;
        for (const p of keep) { const s0 = this.partStart(p.id); for (let i = 0; i < p.nstaves; i++) map[s0 + i] = n++; }
        this.remapStaves(map, n);
        st.parts = keep;
    }

    // A live Part wrapper (MuseScore makes a new wrapper on every access; is() compares the part)
    wrapPart(id) {
        const eng = this;
        const live = () => { const p = eng.partById(id); if (!p) throw new Error('mock: part ' + id + ' is not in the score'); return p; };
        return {
            __partId: id,
            is(other) { return !!other && other.__partId === id; },
            get startTrack() { return eng.partStart(id) * VOICES; },
            get endTrack() { return (eng.partStart(id) + live().nstaves) * VOICES; },
            get instrumentId() { return live().instrumentId; },
            get longName() { return live().longName; },
            get partName() { return live().partName; },
            get shortName() { return live().shortName; },
            get show() { return live().show; },
        };
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
            // Selection::selectRange finds both ends with Score::tick2leftSegmentMM: the
            // segment at the tick, else the one before it in that bar. At the very end of
            // the score that is the last segment (the last note is left out); past the end
            // there is none, and the range goes to the end of the score.
            selectRange(st, et, s0, s1) {
                eng.counters.selectRange++;
                const left = t => {
                    if (t > eng.endTick) return null;
                    const ms = eng.state.measures;
                    const mi = eng.measureIndexAt(Math.max(t, 0));
                    const m = mi >= 0 ? ms[mi] : ms[ms.length - 1];
                    const ticks = eng.segmentTicks().filter(x => x >= m.tick && x < m.tick + m.ticks && x <= t);
                    return ticks.length ? ticks[ticks.length - 1] : null;
                };
                const a = left(st), b = left(et);
                if (a === null || (b !== null && !(b > a))) return false;
                eng.selection = { kind: 'range', els: [], range: { start: a, end: b === null ? eng.endTick : b, s0, s1 } };
                return true;
            },
            clear() {
                eng.counters.selClear++;
                if (process.env.TRACE) console.log('CLEAR', new Error().stack.split('\n').slice(2, 6).join(' | '));
                eng.selection = { kind: 'none', els: [], range: null };
                return true;
            },
        };
        const staffApi = s => ({
            get part() { const p = eng.partOfStaff(s); return p ? eng.wrapPart(p.id) : null; },
            key(f) { let k = 0; for (const e of eng.state.keys[s]) if (e.tick <= f.ticks) k = e.fifths; return k; },
            swing() { return { isOn: false }; },
            clefType(f) { let c = 0; for (const e of eng.state.clefs[s]) if (e.tick <= f.ticks) c = e.type; return c; },
            transpose() { return { chromatic: 0, diatonic: 0 }; },
        });
        const partId = (part, what) => {
            if (!part || part.__partId === undefined || !eng.partById(part.__partId)) throw new Error('mock: ' + what + ' with a part that is not in the score');
            return part.__partId;
        };
        const api = {
            is(other) { return other === api; },
            get nstaves() { return eng.state.nstaves; },
            get ntracks() { return eng.state.nstaves * VOICES; },
            get staves() { const out = []; for (let s = 0; s < eng.state.nstaves; s++) out.push(staffApi(s)); return out; },
            get parts() { return eng.state.parts.map(p => eng.wrapPart(p.id)); },
            appendPart(id) { eng.requireOpen('appendPart'); eng.appendPart(id); },
            insertPart() {
                throw new Error('mock: Score.insertPart in 4.7.5 adds the staves before the part (EditPart::insertPart), ' +
                                'so the staff order no longer matches the part order');
            },
            moveParts(parts, dest, mode) {
                eng.requireOpen('moveParts');
                eng.moveParts(parts.map(p => partId(p, 'moveParts')), partId(dest, 'moveParts'), mode === 1);
            },
            removeParts(parts) { eng.requireOpen('removeParts'); eng.removeParts(parts.map(p => partId(p, 'removeParts'))); },
            replaceInstrument(part, id) {
                eng.requireOpen('replaceInstrument');
                const p = eng.partById(partId(part, 'replaceInstrument'));
                const t = MOCK_INSTRUMENTS[id];
                if (!t) return;                                      // MuseScore logs and does nothing
                Object.assign(p, { instrumentId: t.id, longName: t.name, partName: t.name, shortName: t.short });
            },
            title: 'Mock', duration: 0,
            get spanners() { return []; },
            metaTag(k) { return eng.meta[k] || ''; },
            setMetaTag(k, v) { eng.meta[k] = v; },
            addText(style, text) {
                eng.requireOpen('addText');
                if (!(style in TextStyleType)) throw new Error('mock: addText with an unknown text style ' + style);
                if (!eng.state.frame) eng.state.frame = { elements: [] };
                eng.state.frame.elements.push({ id: eng.id(), type: Element.TEXT, subStyle: TextStyleType[style], text });
            },
            setInstrumentName(part, tick, name) {
                eng.requireOpen('setInstrumentName');
                eng.partById(partId(part, 'setInstrumentName')).longName = name;
            },
            setInstrumentAbbreviature(part, tick, name) {
                eng.requireOpen('setInstrumentAbbreviature');
                eng.partById(partId(part, 'setInstrumentAbbreviature')).shortName = name;
            },
            addRemoveSystemLocks(interval, lock) {
                eng.requireOpen('addRemoveSystemLocks');
                if (eng.selection.kind !== 'range') return;          // works on the selected bars
                const r = eng.selection.range;
                eng.state.locks = lock ? 'as laid out' : (interval ? { interval, start: r.start, end: r.end } : null);
            },
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
                else if (eng.snapshot() !== eng.open) { eng.undoStack.push(eng.open); eng.redoStack = []; }
                eng.open = null;
            },
        };
        return api;
    }

    // A MuseScore action that is its own undo step (run outside plugin commands).
    ownCommand(name, fn) {
        if (this.open) throw new Error('mock: ' + name + ' dispatched inside a plugin command');
        const before = this.snapshot();
        fn();
        if (this.snapshot() !== before) { this.undoStack.push(before); this.redoStack = []; }
    }

    rangeSel(name) {
        if (this.selection.kind !== 'range') throw new Error('mock: ' + name + ' needs a range selection');
        return this.selection.range;
    }

    // Shifts everything at or after `tick` by `delta` ticks.
    shiftFrom(tick, delta) {
        for (const m of this.state.measures) if (m.tick >= tick) m.tick += delta;
        for (const t in this.state.crs) for (const c of this.state.crs[t]) if (c.tick >= tick) c.tick += delta;
        for (const a of this.state.anns) if (a.tick >= tick) a.tick += delta;
        for (const s in this.state.keys) for (const k of this.state.keys[s]) if (k.tick >= tick && k.tick > 0) k.tick += delta;
        for (const s in this.state.clefs) for (const c of this.state.clefs[s]) if (c.tick >= tick && c.tick > 0) c.tick += delta;
    }

    insertMeasureBefore(tick) {
        const i = this.measureIndexAt(tick);
        const m = this.state.measures[i];
        const at = m.tick;
        this.shiftFrom(at, m.ticks);
        this.state.measures.splice(i, 0, { id: this.id(), tick: at, ticks: m.ticks, num: m.num, den: m.den,
                                           repeatStart: false, repeatEnd: false, repeatCount: 2, elements: [] });
        for (let s = 0; s < this.state.nstaves; s++) this.insertCr(this.newCr(s * VOICES, at, m.ticks, m.ticks, null));
    }

    deleteBars(start, end) {
        for (const t in this.state.crs) {
            for (const c of this.state.crs[t].filter(x => x.tick >= start && x.tick < end)) this.removeCr(c);
        }
        this.state.anns = this.state.anns.filter(a => a.tick < start || a.tick >= end);
        this.state.measures = this.state.measures.filter(m => m.tick < start || m.tick >= end);
        this.shiftFrom(end, start - end);
    }

    copyRange(r) {
        const clip = { len: r.end - r.start, staves: r.s1 - r.s0, tracks: {}, ties: [], anns: [] };
        const noteIds = new Set();
        for (let s = r.s0; s < r.s1; s++) {
            for (let v = 0; v < VOICES; v++) {
                const crs = this.trackCrs(s * VOICES + v).filter(c => c.tick >= r.start && c.tick < r.end);
                if (!crs.length) continue;
                clip.tracks[(s - r.s0) * VOICES + v] = crs.map(c => {
                    c.notes.forEach(n => noteIds.add(n.id));
                    return JSON.parse(JSON.stringify(Object.assign({}, c, { tick: c.tick - r.start })));
                });
            }
        }
        for (const id in this.state.ties) {
            const tie = this.state.ties[id];
            if (noteIds.has(tie.start) && noteIds.has(tie.end)) clip.ties.push([tie.start, tie.end]);
        }
        for (const a of this.state.anns) {
            const s = this.staffOf(a.track);
            if (a.tick >= r.start && a.tick < r.end && s >= r.s0 && s < r.s1) {
                clip.anns.push(Object.assign({}, a, { tick: a.tick - r.start, track: a.track - r.s0 * VOICES }));
            }
        }
        return clip;
    }

    paste(r, clip) {
        const start = r.start, end = r.start + clip.len;
        if (r.s0 + clip.staves > this.state.nstaves) throw new Error('mock: paste past the last staff');
        const newIds = {};
        for (const rel in clip.tracks) {
            const track = r.s0 * VOICES + (+rel);
            for (const c of this.trackCrs(track)) {
                if (c.tick < start && c.tick + c.actual > start) throw new Error('mock: paste target starts inside a held note');
                if (c.tick >= start && c.tick < end && c.tick + c.actual > end) throw new Error('mock: paste target ends inside a held note');
            }
            for (const c of this.trackCrs(track).filter(x => x.tick >= start && x.tick < end)) this.removeCr(c);
            for (const src of clip.tracks[rel]) {
                const c = JSON.parse(JSON.stringify(src));
                c.id = this.id();
                c.track = track;
                c.tick += start;
                c.notes.forEach(n => { const old = n.id; n.id = this.id(); newIds[old] = n.id; n.tieFor = null; n.tieBack = null; });
                c.lyrics.forEach(l => { l.id = this.id(); });
                c.arts.forEach(a => { a.id = this.id(); });
                this.insertCr(c);
            }
        }
        for (const [a, b] of clip.ties) this.addTie(newIds[a], newIds[b]);
        for (const a of clip.anns) {
            this.state.anns.push(Object.assign({}, a, { id: this.id(), tick: a.tick + start, track: a.track + r.s0 * VOICES }));
        }
    }

    // Deleting a range selection: chords become rests; voices 2-4 go.
    deleteRange(r) {
        for (let s = r.s0; s < r.s1; s++) {
            for (let v = 0; v < VOICES; v++) {
                for (const c of this.trackCrs(s * VOICES + v).filter(x => x.tick >= r.start && x.tick < r.end)) {
                    if (v === 0) { if (!c.rest) this.deleteCr(c); } else this.removeCr(c);
                }
            }
        }
        this.state.anns = this.state.anns.filter(a => !(a.tick >= r.start && a.tick < r.end &&
                                                         this.staffOf(a.track) >= r.s0 && this.staffOf(a.track) < r.s1));
    }

    cmd(code) {
        this.log.push(code);
        switch (code) {
            case 'action://notation/cancel':
                if (this.noteEntryMode) { this.noteEntryMode = false; return; }
                this.selection = { kind: 'none', els: [], range: null };
                return;
            case 'action://notation/undo': {
                if (this.open) return;                 // the plugin command locks the undo stack
                const s = this.undoStack.pop();
                if (s) { this.redoStack.push(this.snapshot()); this.restore(s); }
                return;
            }
            case 'action://notation/redo': {
                if (this.open) return;
                const s = this.redoStack.pop();
                if (s) { this.undoStack.push(this.snapshot()); this.restore(s); }
                return;
            }
            case 'action://notation/copy':
                this.clipboard = this.copyRange(this.rangeSel('copy'));
                return;
            case 'action://notation/paste': {
                const r = this.rangeSel('paste');
                if (!this.clipboard) return;
                this.ownCommand('paste', () => this.paste(r, this.clipboard));
                return;
            }
            case 'insert-measure': {
                const r = this.rangeSel('insert-measure');
                this.ownCommand('insert-measure', () => this.insertMeasureBefore(r.start));
                return;
            }
            case 'time-delete': {
                const r = this.rangeSel('time-delete');
                this.ownCommand('time-delete', () => this.deleteBars(r.start, r.end));
                return;
            }
            case 'action://notation/delete': {
                const r = this.rangeSel('delete');
                this.ownCommand('delete', () => this.deleteRange(r));
                return;
            }
            case 'tie':
                this.counters.tieCmd++;
                this.requireOpen('cmd("tie")');
                if (this.noteEntryMode) {
                    // NotationNoteInput::addTie: adds a tied note after the input position
                    const el = this.selection.els[0];
                    if (el) { const { cr } = this.findNote(el.id); this.setNoteRest(this.nextInputPos(cr.tick, cr.track), cr.track, 60, this.inputDuration); }
                    return;
                }
                this.cmdToggleTie();
                return;
            default:
                return;                                // file-save etc.: only logged
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
            Lyrics: { SINGLE: 0, BEGIN: 1, END: 2, MIDDLE: 3 }, DynamicType, MarkerType: {}, KeyMode: { MAJOR: 1, MINOR: 0 },
            Tid, ClefType, LayoutBreak, SymId,
            newElement(type) {
                if (type === Element.NOTE) return { __newNote: true, type, pitch: 60 };
                if (type === Element.HARMONY) {
                    // Harmony::setProperty(TEXT) calls explicitParent()->isFretDiagram(): with no
                    // parent yet (not added to the score) MuseScore 4.7.5 crashes (seen live)
                    const el = { type };
                    let text = '';
                    Object.defineProperty(el, 'text', {
                        enumerable: true,
                        get() { return text; },
                        set(v) {
                            if (el.id === undefined) throw new Error('mock: setting the text of a chord symbol that is not in the score crashes MuseScore 4.7.5');
                            text = v;
                        },
                    });
                    return el;
                }
                return { type };
            },
            removeElement(el) { eng.removeElement(el); },
            readScore(p) { eng.log.push('readScore ' + p); return null; },
            writeScore(s, p, ext) {
                if (s !== score) return false;
                if (/missing-folder/.test(p)) return false;
                eng.exports.push({ path: p, ext });
                // Write a placeholder when the folder exists (dry runs of the live tests check the file)
                const out = p.endsWith(ext) ? p : p + '.' + ext;
                try { if (fs.statSync(path.dirname(out)).isDirectory()) fs.writeFileSync(out, 'mock ' + ext + ' export\n'); } catch (e) {}
                return true;
            },
            cmd(code) { eng.cmd(code); },
            fraction(n, d) { return { numerator: n, denominator: d, ticks: WHOLE * n / d }; },
            fractionFromTicks(t) { return eng.frac(t); },
            api: { websocketserver: { listen() {}, onMessage() {}, send(id, text) { eng.replies.push(JSON.parse(text)); } } },
        };
        this.plugin = vm.runInNewContext(js, sandbox, { filename: 'musescore-mcp-websocket.qml.js' });
        this.plugin.get('onRun')();            // MuseScore runs the plugin
        for (const k in this.counters) this.counters[k] = 0;
        return this.plugin;
    }

    // Sends one request through processMessage, as the websocket would.
    call(action, params) {
        const msg = params === undefined ? { action } : { action, params };
        return this.raw(msg);
    }

    raw(obj) {
        this.plugin.get('processMessage')(JSON.stringify(obj), 1);
        const reply = this.replies.pop();
        this.lastVersion = reply.version;
        return reply.status === 'success' ? reply.result : { error: reply.message };
    }

    // Edits made "by the user in MuseScore": outside any plugin command, as an undo step.
    userEdit(fn) {
        this.ownCommand('user edit', () => fn(this));
    }
}

module.exports = { MockMuseScore, Element, WHOLE, tdurationTicks, Tid, ClefType, LayoutBreak, DynamicType, SymId, symName };
