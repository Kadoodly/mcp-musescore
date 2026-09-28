import QtQuick 2.9
import MuseScore 3.0

// MuseScore 4 lists plugins by file name, so this shows up as
// "musescore-mcp-websocket" in the Plugins menu.
MuseScore {
    id: root
    menuPath: "Plugins.MuseScore API Server"
    description: "Exposes MuseScore API via WebSocket"
    version: "3.0"

    property var clientConnections: []

    // The plugin's own write position. Every write action uses it (or the
    // explicit tick/measure/staff/voice params it was given) instead of
    // re-deriving the staff from the GUI selection.
    property var cursorState: ({ tick: 0, staff: 0, voice: 0, lastChord: null })

    // Signature of the GUI selection the plugin itself last made. If the
    // selection differs at the start of a command, the user clicked something
    // in MuseScore and the cursor moves there.
    property string lastSelectionSig: ""

    // Nesting depth of startCmd/endCmd. Only the outermost call opens a
    // MuseScore command, so nested helpers can't commit it early.
    property int cmdDepth: 0

    // Cursor positions before each undoable command, so undo can restore it.
    property var undoCursorStack: []

    // Score state and UI state are kept apart: edits change the score (and the
    // plugin cursor), and the visible MuseScore selection is only moved to the
    // cursor once, at the end of a request. uiDirty: the selection no longer
    // shows the cursor. uiDeferDepth > 0: a batch is running, so steps skip
    // all UI work (selection, cursor info).
    property bool uiDirty: false
    property int uiDeferDepth: 0

    // Ties requested inside the open command. They are made when the
    // outermost command ends (all notes of a batch exist by then).
    property var pendingTies: []

    // Reads what the last write of the open command left in the score. Read
    // again after the command is committed: MuseScore silently rolls a command
    // back if an engine error was raised inside it.
    property var commitProbe: null

    readonly property int ticksPerWhole: 1920

    // ========================================
    // WEBSOCKET & MESSAGE PROCESSING
    // ========================================

    function processMessage(message, clientId) {
        console.log("Received message: " + message);
        var reply;
        try {
            var command = JSON.parse(message);
            reply = { status: "success", result: processCommand(command) };
        } catch (e) {
            console.log("Error processing command: " + e.toString());
            reply = { status: "error", message: e.toString() };
        }
        // Whatever happened, the visible selection ends up on the cursor once.
        try {
            if (curScore && cmdDepth === 0 && uiDeferDepth === 0) syncUi();
        } catch (e2) {
            console.log("UI sync failed: " + e2.toString());
        }
        api.websocketserver.send(clientId, JSON.stringify(reply));
    }

    // Allowed params per action. Unknown actions and unknown params are errors,
    // so a caller never believes an unsupported option (e.g. a misspelled
    // "tie") took effect.
    property var actionParams: ({
        "ping": [],
        "getScore": [],
        "syncStateToSelection": [],
        "undo": ["steps"],
        "processSequence": ["sequence", "atomic"],
        "getCursorInfo": [],
        "setCursor": ["staff", "voice", "measure", "tick"],
        "goToMeasure": ["measure", "staff", "voice"],
        "goToBeginningOfScore": ["staff", "voice"],
        "goToFinalMeasure": ["staff", "voice"],
        "nextElement": ["numElements"],
        "prevElement": ["numElements"],
        "nextStaff": ["count"],
        "prevStaff": ["count"],
        "selectCurrentMeasure": ["allStaves", "staff", "voice", "measure", "tick"],
        "selectCustomRange": ["startTick", "endTick", "startStaff", "endStaff"],
        "addNote": ["pitch", "duration", "advanceCursorAfterAction", "addToChord", "tie", "staff", "voice", "measure", "tick"],
        "addRest": ["duration", "advanceCursorAfterAction", "staff", "voice", "measure", "tick"],
        "addTuplet": ["duration", "ratio", "advanceCursorAfterAction", "staff", "voice", "measure", "tick"],
        "writeVoice": ["events", "staff", "voice", "measure", "tick"],
        "addLyrics": ["lyrics", "verse", "staff", "voice", "measure", "tick"],
        "addDynamic": ["dynamic", "staff", "voice", "measure", "tick"],
        "addFermata": ["staff", "voice", "measure", "tick"],
        "setTempo": ["bpm", "text", "measure", "tick"],
        "appendMeasure": ["count"],
        "insertMeasure": ["measure", "count"],
        "deleteSelection": ["measure", "staff"],
        "addRepeat": ["startMeasure", "endMeasure", "times"],
        "removeRepeat": ["startMeasure", "endMeasure"],
        "addMarker": ["type", "measure"],
        "addJump": ["type", "measure"],
        "addRehearsalMark": ["text", "measure", "tick"],
        "setKeySignature": ["fifths", "measure", "mode", "staff"],
        "addGradualTempoChange": ["type", "measure", "tick", "endMeasure", "endTick", "targetBpm", "factor", "aTempo"],
        "removeMarking": ["kind", "tick", "measure", "staff"],
        "addSlur": ["startTick", "endTick", "startMeasure", "endMeasure", "staff"],
        "addHairpin": ["type", "startTick", "endTick", "startMeasure", "endMeasure", "staff"],
        "addArticulation": ["type", "startTick", "endTick", "startMeasure", "endMeasure", "staff"],
        "deleteMeasures": ["startMeasure", "endMeasure"],
        "copyMeasures": ["startMeasure", "endMeasure", "toMeasure", "insert", "staff"],
        "addInstrument": ["instrumentId"],
        "removeInstrument": ["part", "staff"],
        "setStaffMute": ["staff", "mute"],
        "setInstrumentSound": ["staff", "instrumentId"],
        "setTimeSignature": ["numerator", "denominator", "measure"]
    })

    // Throws unless the command is {action, params} with a known action and
    // only the params that action accepts.
    function checkCommand(command) {
        if (!isPlainObject(command)) throw new Error("A command must be an object {action, params}");
        checkKeys(command, ["action", "params"], "Command");
        if (typeof command.action !== "string" || !hasKey(actionParams, command.action)) {
            throw new Error("Unknown command: " + command.action);
        }
        if (isSet(command.params) && !isPlainObject(command.params)) {
            throw new Error(command.action + ": params must be an object");
        }
        checkKeys(command.params || {}, actionParams[command.action], command.action);
    }

    function processCommand(command) {
        checkCommand(command);
        console.log("Processing command: " + command.action);
        var params = command.params || {};

        // Only a top-level request picks up a selection the user made in
        // MuseScore; batch steps must not (the batch set it itself).
        if (command.action !== "ping" && curScore && uiDeferDepth === 0 && cmdDepth === 0) {
            adoptGuiSelection();
        }

        switch (command.action) {
            // Core operations
            case "getScore":                return getScore(params);
            case "syncStateToSelection":    return getCursorInfo(params);
            case "ping":                    return "pong";
            case "undo":                    return undo(params);
            case "processSequence":         return processSequence(params);

            // Navigation
            case "getCursorInfo":           return getCursorInfo(params);
            case "setCursor":               return setCursor(params);
            case "goToMeasure":             return goToMeasure(params);
            case "goToBeginningOfScore":    return goToBeginningOfScore(params);
            case "goToFinalMeasure":        return goToFinalMeasure(params);
            case "nextElement":             return nextElement(params);
            case "prevElement":             return prevElement(params);
            case "nextStaff":               return moveStaff(params, 1);
            case "prevStaff":               return moveStaff(params, -1);

            // Selection
            case "selectCurrentMeasure":    return selectCurrentMeasure(params);
            case "selectCustomRange":       return selectCustomRange(params);

            // Notes & Music
            case "addNote":                 return addNote(params);
            case "addRest":                 return addRest(params);
            case "addTuplet":               return addTuplet(params);
            case "writeVoice":              return writeVoice(params);
            case "addLyrics":               return addLyrics(params);

            // Markings
            case "addDynamic":              return addDynamic(params);
            case "addFermata":              return addFermata(params);
            case "setTempo":                return setTempo(params);

            // Measures
            case "appendMeasure":           return appendMeasure(params);
            case "insertMeasure":           return insertMeasure(params);
            case "deleteSelection":         return deleteSelection(params);

            // Structure & expression
            case "addRepeat":               return addRepeat(params);
            case "removeRepeat":            return removeRepeat(params);
            case "addMarker":               return addMarker(params);
            case "addJump":                 return addJump(params);
            case "addRehearsalMark":        return addRehearsalMark(params);
            case "setKeySignature":         return setKeySignature(params);
            case "addGradualTempoChange":   return addGradualTempoChange(params);
            case "removeMarking":           return removeMarking(params);
            case "addSlur":                 return addSlur(params);
            case "addHairpin":              return addHairpin(params);
            case "addArticulation":         return addArticulation(params);
            case "deleteMeasures":          return deleteMeasures(params);
            case "copyMeasures":            return copyMeasures(params);

            // Staff & Instruments
            case "addInstrument":           return addInstrument(params);
            case "removeInstrument":        return removeInstrument(params);
            case "setStaffMute":            return setStaffMute(params);
            case "setInstrumentSound":      return setInstrumentSound(params);
            case "setTimeSignature":        return setTimeSignature(params);

            default:
                throw new Error("Unknown command: " + command.action);
        }
    }

    // ========================================
    // UTILITY FUNCTIONS
    // ========================================

    function validateParams(params, required) {
        var missing = [];
        for (var i = 0; i < required.length; i++) {
            if (params[required[i]] === undefined || params[required[i]] === null) {
                missing.push(required[i]);
            }
        }
        return missing.length > 0 ? { error: "Missing required parameters: " + missing.join(", ") } : { valid: true };
    }

    function isSet(v) {
        return v !== undefined && v !== null;
    }

    function isPlainObject(v) {
        return v !== null && typeof v === "object" && !Array.isArray(v);
    }

    function isInt(v) {
        return typeof v === "number" && isFinite(v) && Math.floor(v) === v;
    }

    function hasKey(obj, key) {
        return Object.prototype.hasOwnProperty.call(obj, key);
    }

    // Throws if obj has a key that isn't in `allowed`.
    function checkKeys(obj, allowed, label) {
        var keys = Object.keys(obj);
        for (var i = 0; i < keys.length; i++) {
            if (allowed.indexOf(keys[i]) < 0) {
                throw new Error(label + ": unknown parameter '" + keys[i] + "'" +
                                (allowed.length ? " (allowed: " + allowed.join(", ") + ")" : " (it takes no parameters)"));
            }
        }
    }

    // Integer param in [min, max] (either bound may be null), or throws.
    function checkInt(value, label, min, max) {
        if (!isInt(value) || (min !== null && value < min) || (max !== null && value > max)) {
            var range = min !== null && max !== null ? " " + min + "-" + max : (min !== null ? " >= " + min : "");
            throw new Error(label + " must be an integer" + range + ", got " + JSON.stringify(value));
        }
        return value;
    }

    // Optional boolean param: its value, or `fallback` when not given.
    function boolParam(params, key, fallback) {
        if (!isSet(params[key])) return fallback;
        if (typeof params[key] !== "boolean") throw new Error(key + " must be true or false, got " + JSON.stringify(params[key]));
        return params[key];
    }

    function copyCursor(c) {
        var last = null;
        if (c.lastChord) {
            last = { tick: c.lastChord.tick, staff: c.lastChord.staff, voice: c.lastChord.voice, pieces: c.lastChord.pieces || 1 };
        }
        return { tick: c.tick, staff: c.staff, voice: c.voice, lastChord: last };
    }

    // Runs `operation` as one undoable MuseScore command. Nested calls join
    // the outer command instead of opening (and prematurely closing) their own.
    // Ties queued by the operation are made before the command closes; if one
    // can't be made, the whole command is rolled back.
    function executeWithUndo(operation) {
        if (!curScore) return { error: "No score open" };
        if (cmdDepth > 0) return operation();

        var before = copyCursor(cursorState);
        var result;
        var failed = false;
        pendingTies = [];
        commitProbe = null;
        cmdDepth++;
        curScore.startCmd();
        try {
            result = operation();
            failed = !!(result && result.error);
            if (!failed && pendingTies.length > 0) {
                var tieError = applyPendingTies();
                if (tieError) {
                    failed = true;
                    result = { error: tieError };
                }
            }
        } catch (e) {
            failed = true;
            result = { error: e.toString() };
        } finally {
            cmdDepth--;
            pendingTies = [];
        }
        var probe = failed ? null : commitProbe;
        var expected = probe ? probe() : null;
        commitProbe = null;
        curScore.endCmd(failed);
        if (probe && probe() !== expected) {
            failed = true;
            result = { error: "MuseScore rolled the edit back (an engine error occurred inside the command); nothing was written" };
        }

        if (failed) {
            cursorState = before;
        } else {
            undoCursorStack.push(before);
            if (undoCursorStack.length > 200) undoCursorStack.shift();
        }
        return result;
    }

    // Wraps a score-changing action: runs it in a command, then (outside the
    // command, and only for a top-level request) moves the GUI selection to the
    // cursor and attaches cursor info. Inside a batch the UI work is left to the
    // end of the batch.
    function mutate(operation) {
        var result = executeWithUndo(operation);
        if (result && result.error) return result;
        uiDirty = true;
        result = result || {};
        result.success = true;
        if (uiDeferDepth === 0) {
            syncUi();
            result.cursor = cursorInfo();
        }
        return result;
    }

    function getTpcName(tpc) {
        if (tpc === -1) return "Fbb";
        var tpcNames = [
            "Cbb", "Gbb", "Dbb", "Abb", "Ebb", "Bbb", "Fb",
            "Cb",  "Gb",  "Db",  "Ab",  "Eb",  "Bb",  "F",
            "C",   "G",   "D",   "A",   "E",   "B",   "F#",
            "C#",  "G#",  "D#",  "A#",  "E#",  "B#",  "F##",
            "C##", "G##", "D##", "A##", "E##", "B##", "F###"
        ];
        if (tpc >= 0 && tpc < tpcNames.length) {
            return tpcNames[tpc];
        }
        return "Unknown";
    }

    function keyName(fifths) {
        var major = ["Cb", "Gb", "Db", "Ab", "Eb", "Bb", "F", "C", "G", "D", "A", "E", "B", "F#", "C#"];
        var minor = ["Ab", "Eb", "Bb", "F", "C", "G", "D", "A", "E", "B", "F#", "C#", "G#", "D#", "A#"];
        var i = fifths + 7;
        if (i < 0 || i >= major.length) return "custom";
        return major[i] + " major / " + minor[i] + " minor";
    }

    function syllabicName(value) {
        return ["single", "begin", "end", "middle"][value] || "unknown";
    }

    function stripTags(text) {
        if (!text) return "";
        return String(text)
            .replace(/<sym>metNoteQuarterUp<\/sym>/g, "♩")
            .replace(/<sym>metNoteHalfUp<\/sym>/g, "𝅗𝅥")
            .replace(/<sym>metNote8thUp<\/sym>/g, "♪")
            .replace(/<sym>metAugmentationDot<\/sym>/g, ".")
            .replace(/<sym>dynamic([A-Za-z]+)<\/sym>/g, function(m, name) {
                var letters = { Piano: "p", Mezzo: "m", Forte: "f", Sforzando: "s", Z: "z", Rinforzando: "r", Niente: "n" };
                return letters[name] || "";
            })
            .replace(/<[^>]*>/g, "");
    }

    function durationTicks(d) {
        return Math.round(ticksPerWhole * d.numerator / d.denominator);
    }

    // A {numerator, denominator} object of positive integers (tuplet sizes and ratios).
    function checkDuration(d, label) {
        label = label || "Duration";
        if (!isPlainObject(d)) throw new Error(label + " must be specified as { numerator: int, denominator: int }");
        checkKeys(d, ["numerator", "denominator"], label);
        checkInt(d.numerator, label + " numerator", 1, null);
        checkInt(d.denominator, label + " denominator", 1, null);
    }

    // ========================================
    // SCORE GEOMETRY
    // ========================================

    function listMeasures() {
        var result = [];
        var m = curScore.firstMeasure;
        var no = 1;
        while (m) {
            var start = m.tick.ticks;
            var len = m.ticks.ticks;
            var ts = m.timesigActual;
            result.push({
                number: no,
                startTick: start,
                endTick: start + len,
                numerator: ts ? ts.numerator : 4,
                denominator: ts ? ts.denominator : 4,
                obj: m
            });
            m = m.nextMeasure;
            no++;
        }
        return result;
    }

    function scoreEndTick() {
        var last = curScore.lastMeasure;
        return last ? last.tick.ticks + last.ticks.ticks : 0;
    }

    function measureByNumber(n) {
        var measures = listMeasures();
        if (!(isInt(n) && n >= 1 && n <= measures.length)) {
            throw new Error("Invalid measure number " + n + " (score has " + measures.length + " measures, numbered from 1)");
        }
        return measures[n - 1];
    }

    // Measure containing tick; a tick at the very end of the score maps to the
    // last measure.
    function measureAtTick(tick) {
        var measures = listMeasures();
        for (var i = 0; i < measures.length; i++) {
            if (tick >= measures[i].startTick && tick < measures[i].endTick) return measures[i];
        }
        return measures.length ? measures[measures.length - 1] : null;
    }

    function staffObj(idx) {
        return curScore.staves[idx];
    }

    function partIndexOf(part) {
        var parts = curScore.parts;
        for (var i = 0; i < parts.length; i++) {
            if (parts[i].startTrack === part.startTrack) return i;
        }
        return -1;
    }

    function staffLabel(idx) {
        var st = staffObj(idx);
        if (!st || !st.part) return "staff" + idx;
        return st.part.longName || st.part.partName || ("staff" + idx);
    }

    // Grows the score so [tick, tick + length) exists, appending the missing
    // bars in as few calls as possible.
    function ensureRoom(tick, length) {
        var needed = tick + Math.max(length || 0, 1);
        var appended = 0;
        while (scoreEndTick() < needed) {
            var last = curScore.lastMeasure;
            var ts = last ? last.timesigNominal : null;
            var barTicks = ts ? Math.round(ticksPerWhole * ts.numerator / ts.denominator) : ticksPerWhole;
            var count = Math.max(1, Math.ceil((needed - scoreEndTick()) / Math.max(barTicks, 1)));
            if (appended + count > 1000) throw new Error("Writing there would append more than 1000 bars");
            curScore.appendMeasures(count);
            appended += count;
        }
    }

    // Start/end ticks of the bars covering [startTick, endTick), found from
    // the start bar on (no walk over the whole score). The range must exist.
    function barsCovering(startTick, endTick) {
        var bars = [];
        var m = curScore.tick2measure(fractionFromTicks(startTick));
        while (m && m.tick.ticks < endTick) {
            var start = m.tick.ticks;
            bars.push({ startTick: start, endTick: start + m.ticks.ticks });
            m = m.nextMeasure;
        }
        return bars;
    }

    // ========================================
    // CURSOR MANAGEMENT
    // ========================================

    function clampCursorToScore() {
        if (!curScore) return;
        if (cursorState.staff >= curScore.nstaves) cursorState.staff = Math.max(0, curScore.nstaves - 1);
        if (cursorState.voice < 0 || cursorState.voice > 3) cursorState.voice = 0;
        var end = scoreEndTick();
        if (cursorState.tick > end) cursorState.tick = end;
        if (cursorState.tick < 0) cursorState.tick = 0;
    }

    // Resolves the target position of an action from its params, falling back
    // to the plugin cursor. Accepts tick, measure (1-based), staff and voice.
    function resolveTarget(params) {
        clampCursorToScore();
        params = params || {};
        var t = { tick: cursorState.tick, staff: cursorState.staff, voice: cursorState.voice };

        if (isSet(params.staff)) {
            if (!(isInt(params.staff) && params.staff >= 0 && params.staff < curScore.nstaves)) {
                throw new Error("Invalid staff " + params.staff + " (score has " + curScore.nstaves + " staves, numbered from 0)");
            }
            t.staff = params.staff;
        }
        if (isSet(params.voice)) {
            if (!(isInt(params.voice) && params.voice >= 0 && params.voice <= 3)) {
                throw new Error("Invalid voice " + params.voice + " (voices are 0-3)");
            }
            t.voice = params.voice;
        }
        if (isSet(params.tick)) {
            if (!(isInt(params.tick) && params.tick >= 0 && params.tick <= scoreEndTick())) {
                throw new Error("Invalid tick " + params.tick + " (score ends at tick " + scoreEndTick() + ")");
            }
            t.tick = params.tick;
        } else if (isSet(params.measure)) {
            t.tick = measureByNumber(params.measure).startTick;
        }
        return t;
    }

    // A cursor with its own input state, positioned exactly on (tick, staff,
    // voice). cursor.segment is null when no chord/rest starts at that tick.
    function makeCursor(t) {
        var c = curScore.newCursor();
        c.staffIdx = t.staff;
        c.voice = t.voice;
        c.rewindToFraction(fractionFromTicks(t.tick));
        return c;
    }

    function requireSegment(c, t) {
        if (!c.segment) {
            throw new Error("No note or rest starts at tick " + t.tick + " on staff " + t.staff +
                            " (it falls inside a longer note/rest). Move to a note boundary first.");
        }
    }

    function cursorInfo() {
        if (!curScore) return null;
        clampCursorToScore();
        var t = cursorState;
        var end = scoreEndTick();
        var m = measureAtTick(t.tick);
        var beatTicks = m ? ticksPerWhole / m.denominator : 480;
        var info = {
            tick: t.tick,
            staff: t.staff,
            voice: t.voice,
            staffName: staffLabel(t.staff),
            measure: m ? m.number : 1,
            beat: m ? Math.round((1 + (t.tick - m.startTick) / beatTicks) * 1000) / 1000 : 1,
            timeSignature: m ? m.numerator + "/" + m.denominator : "4/4",
            atEndOfScore: t.tick >= end,
            element: null
        };
        if (t.tick >= end) {
            info.measure = m ? m.number + 1 : 1;
            info.beat = 1;
        } else {
            var c = makeCursor(t);
            if (c.segment) info.element = processElement(c.element);
        }
        return info;
    }

    function selectionSignature() {
        var sel = curScore.selection;
        if (sel.isRange) {
            var s = sel.startSegment, e = sel.endSegment;
            return "R" + (s ? s.tick : "?") + "-" + (e ? e.tick : "end") + ":" + sel.startStaff + "-" + sel.endStaff;
        }
        var els = sel.elements;
        if (els && els.length > 0) {
            var el = els[0];
            return "L" + els.length + ":" + el.track + "@" + elementTick(el);
        }
        return "";
    }

    function elementTick(el) {
        try {
            if (el.fraction) return el.fraction.ticks;
        } catch (e) {}
        var p = el;
        for (var i = 0; p && i < 4; i++) {
            if (p.type === Element.SEGMENT) return p.tick;
            p = p.parent;
        }
        return -1;
    }

    // If the user changed the selection in MuseScore since the plugin last
    // set it, move the cursor to it.
    function adoptGuiSelection() {
        var sig;
        try {
            sig = selectionSignature();
        } catch (e) {
            return;
        }
        if (sig === lastSelectionSig) return;
        lastSelectionSig = sig;
        if (sig === "") return;

        var sel = curScore.selection;
        if (sel.isRange) {
            if (sel.startSegment) {
                cursorState = { tick: sel.startSegment.tick, staff: sel.startStaff, voice: cursorState.voice, lastChord: null };
            }
        } else {
            var el = sel.elements[0];
            var tick = elementTick(el);
            if (tick >= 0 && el.track >= 0) {
                cursorState = { tick: tick, staff: Math.floor(el.track / 4), voice: el.track % 4, lastChord: null };
            }
        }
        console.log("Cursor moved to GUI selection: " + JSON.stringify(cursorState));
    }

    // Brings the visible selection in line with the cursor, if it moved.
    // Called once per request (or batch), never per step.
    function syncUi() {
        if (uiDirty && cmdDepth === 0) showCursor();
    }

    // Shows the cursor position in MuseScore by selecting the chord/rest there.
    // Must be called outside of a command.
    function showCursor() {
        uiDirty = false;
        try {
            clampCursorToScore();
            var t = cursorState;
            var sel = curScore.selection;
            sel.clear();
            if (t.tick < scoreEndTick()) {
                var c = makeCursor(t);
                var el = c.segment ? c.element : null;
                if (!el && c.segment) {
                    c.voice = 0;
                    el = c.element;
                }
                if (el && el.actualDuration) {
                    sel.selectRange(t.tick, t.tick + el.actualDuration.ticks, t.staff, t.staff + 1);
                }
            }
            lastSelectionSig = selectionSignature();
        } catch (e) {
            console.log("showCursor failed: " + e.toString());
        }
    }

    // Selects [startTick, endTick) on staves [startStaff, endStaff] (inclusive)
    // and remembers the selection as plugin-made. Must be called outside a command.
    function selectRangeInclusive(startTick, endTick, startStaff, endStaff) {
        var sel = curScore.selection;
        sel.clear();
        var ok = sel.selectRange(startTick, endTick, startStaff, endStaff + 1);
        // A plugin range selection only sets its bounds; MuseScore fills in the
        // selected notes (needed by slurs, hairpins, ...) when a command ends.
        // An empty command does that and leaves no undo step.
        if (ok && cmdDepth === 0) {
            curScore.startCmd();
            curScore.endCmd();
        }
        lastSelectionSig = selectionSignature();
        uiDirty = false;   // this selection is deliberate; don't replace it with the cursor
        return ok;
    }

    // ========================================
    // ELEMENT PROCESSING
    // ========================================

    function processElement(element) {
        if (!element) return null;
        if (element.name !== "Chord" && element.name !== "Rest") return null;

        var base = {
            name: element.name,
            durationTicks: element.actualDuration ? element.actualDuration.ticks : 0,
            isTie: false,
            isTuplet: element.tuplet ? true : false
        };

        if (element.lyrics && element.lyrics.length > 0) {
            base.lyrics = [];
            for (var l = 0; l < element.lyrics.length; l++) {
                var lyr = element.lyrics[l];
                if (lyr) {
                    base.lyrics.push({
                        text: lyr.text,
                        verse: lyr.verse,
                        syllabic: syllabicName(lyr.syllabic)
                    });
                }
            }
        }

        if (element.tuplet) {
            base.tuplet = { actual: element.tuplet.actualNotes, normal: element.tuplet.normalNotes };
        }

        if (element.name === "Chord") {
            var arts = element.articulations;
            if (arts && arts.length > 0) {
                base.articulations = [];
                for (var a = 0; a < arts.length; a++) base.articulations.push(safeSubtypeName(arts[a]));
            }
            if (element.graceNotes && element.graceNotes.length > 0) base.graceNotes = element.graceNotes.length;
            base.notes = [];
            base.isTiedBack = isTiedBack(element);
            var notes = element.notes;
            for (var k = 0; k < notes.length; k++) {
                var note = notes[k];
                if (note.tieForward) base.isTie = true;
                base.notes.push({
                    pitchMidi: note.pitch,
                    tpc: note.tpc,
                    pitchName: getTpcName(note.tpc),
                    tiedForward: note.tieForward ? true : false,
                    tiedBack: note.tieBack ? true : false
                });
            }
        }

        return base;
    }

    // A chord that only continues tied notes (no newly struck note). A chord
    // where some notes are tied over but another note starts is a new attack.
    function isTiedBack(chord) {
        var notes = chord.notes;
        if (notes.length === 0) return false;
        for (var i = 0; i < notes.length; i++) {
            if (!notes[i].tieBack) return false;
        }
        return true;
    }

    // Segment annotations worth reporting (dynamics, tempo, fermatas, text).
    function processAnnotation(a) {
        if (!a) return null;
        var staff = a.track >= 0 ? Math.floor(a.track / 4) : null;
        switch (a.type) {
            case Element.DYNAMIC:
                return { type: "dynamic", staff: staff, value: stripTags(a.text) };
            case Element.TEMPO_TEXT:
                return { type: "tempo", staff: staff, bpm: Math.round(a.tempo * 60 * 100) / 100, text: stripTags(a.text), visible: a.visible !== false };
            case Element.FERMATA:
                return { type: "fermata", staff: staff };
            case Element.STAFF_TEXT:
            case Element.SYSTEM_TEXT:
            case Element.EXPRESSION:
                return { type: "text", staff: staff, text: stripTags(a.text) };
            case Element.HARMONY:
                return { type: "chordSymbol", staff: staff, text: stripTags(a.text) };
            case Element.REHEARSAL_MARK:
                return { type: "rehearsalMark", staff: staff, text: stripTags(a.text) };
            case Element.BREATH:
                return { type: "breath", staff: staff };
            case Element.TRIPLET_FEEL:
                return { type: "tripletFeel", staff: staff, text: stripTags(a.text) };
            default:
                return null;
        }
    }

    function safeSubtypeName(el) {
        try {
            return el.subtypeName();
        } catch (e) {
            return "";
        }
    }

    function ticksOf(f) {
        return f && f.ticks !== undefined ? f.ticks : null;
    }

    // Slurs, hairpins, rit./accel., voltas, ottavas, pedal and trill lines.
    function listSpanners() {
        var kinds = {};
        kinds[Element.SLUR] = "slur";
        kinds[Element.HAIRPIN] = "hairpin";
        kinds[Element.GRADUAL_TEMPO_CHANGE] = "gradualTempoChange";
        kinds[Element.VOLTA] = "volta";
        kinds[Element.OTTAVA] = "ottava";
        kinds[Element.PEDAL] = "pedal";
        kinds[Element.TRILL] = "trill";
        kinds[Element.TEXTLINE] = "textLine";
        kinds[Element.LET_RING] = "letRing";

        var result = [];
        var spanners = curScore.spanners;
        for (var i = 0; i < spanners.length; i++) {
            var sp = spanners[i];
            var kind = kinds[sp.type];
            if (!kind) continue;
            var start = ticksOf(sp.spannerTick);
            var len = ticksOf(sp.spannerTicks);
            var item = {
                type: kind,
                name: safeSubtypeName(sp),
                staff: sp.track >= 0 ? Math.floor(sp.track / 4) : null,
                startTick: start,
                endTick: start !== null && len !== null ? start + len : null
            };
            if (kind === "volta") item.endings = String(sp.volta_ending || "");
            if (kind === "textLine" || kind === "gradualTempoChange") {
                try { if (sp.beginText) item.text = stripTags(sp.beginText); } catch (e) {}
            }
            result.push(item);
        }
        result.sort(function(a, b) { return a.startTick - b.startTick; });
        return result;
    }

    // Markers (Segno, Coda, Fine, ...) and jumps (D.C., D.S., ...) live on measures.
    function measureMarks(m) {
        var out = [];
        var els = m.elements;
        for (var i = 0; i < els.length; i++) {
            var el = els[i];
            if (el.type === Element.MARKER) out.push({ type: "marker", name: safeSubtypeName(el), text: stripTags(el.text) });
            else if (el.type === Element.JUMP) out.push({ type: "jump", name: safeSubtypeName(el), text: stripTags(el.text) });
        }
        return out;
    }

    function findAnnotation(segment, type, track) {
        var anns = segment.annotations;
        for (var i = 0; i < anns.length; i++) {
            var a = anns[i];
            if (a.type === type && (track === undefined || a.track === track)) return a;
        }
        return null;
    }

    // ========================================
    // CORE OPERATIONS
    // ========================================

    // Undo must run outside startCmd/endCmd: MuseScore locks the undo stack
    // while a plugin command is open, which silently turns undo into a no-op.
    // MuseScore 4 registers it as "action://notation/undo"; plain "undo" is
    // an unknown action and is ignored.
    function undo(params) {
        if (!curScore) return { error: "No score open" };
        var steps = isSet(params.steps) ? checkInt(params.steps, "steps", 1, 1000) : 1;
        for (var i = 0; i < steps; i++) {
            cmd("action://notation/undo");
            if (undoCursorStack.length > 0) cursorState = undoCursorStack.pop();
        }
        return navResult("Undid " + steps + " step(s)");
    }

    property var sequenceCommands: [
        "getScore", "addNote", "addRest", "addTuplet", "writeVoice", "addLyrics", "appendMeasure", "insertMeasure",
        "deleteSelection", "getCursorInfo", "setCursor", "goToMeasure", "goToBeginningOfScore",
        "goToFinalMeasure", "nextElement", "prevElement", "nextStaff", "prevStaff",
        "selectCurrentMeasure", "selectCustomRange", "setTimeSignature", "setTempo",
        "addDynamic", "addFermata", "addInstrument", "removeInstrument", "setStaffMute",
        "setInstrumentSound", "undo",
        "addRepeat", "removeRepeat", "addMarker", "addJump", "addRehearsalMark",
        "setKeySignature", "addGradualTempoChange", "removeMarking", "addSlur", "addHairpin",
        "addArticulation", "deleteMeasures", "copyMeasures"
    ]

    // Actions that change the MuseScore selection before running a command
    // can't be grouped into one undo step (the selection only updates
    // between commands).
    property var nonAtomicCommands: [
        "undo", "deleteSelection", "insertMeasure", "selectCurrentMeasure", "selectCustomRange",
        "addSlur", "addHairpin", "addArticulation", "deleteMeasures", "copyMeasures", "processSequence"
    ]

    // Checks every step before anything runs: known sequence action, known params.
    function checkSequence(sequence, atomic) {
        if (!Array.isArray(sequence) || sequence.length === 0) throw new Error("sequence must be a non-empty list of steps");
        for (var i = 0; i < sequence.length; i++) {
            var step = sequence[i];
            try {
                checkCommand(step);
            } catch (e) {
                throw new Error("Step " + i + ": " + e.message);
            }
            if (sequenceCommands.indexOf(step.action) < 0) throw new Error("Step " + i + ": invalid command " + step.action);
            if (atomic && nonAtomicCommands.indexOf(step.action) >= 0) {
                throw new Error("Step " + i + ": " + step.action + " can't be part of an atomic sequence (it changes the selection); run it separately");
            }
        }
    }

    function stepResult(action, r) {
        var out = { action: action, message: r && r.message ? r.message : "ok" };
        if (r && r.warnings && r.warnings.length) out.warnings = r.warnings;
        return out;
    }

    // Runs each step as its own command (so each can be undone), stopping at
    // the first step that fails. The visible selection is updated once, at the end.
    function processSequence(params) {
        if (!curScore) return { error: "No score open" };
        var atomic = boolParam(params, "atomic", false);
        checkSequence(params.sequence, atomic);
        if (atomic) return processAtomicSequence(params.sequence);

        var results = [];
        var failure = null;
        uiDeferDepth++;
        try {
            for (var i = 0; i < params.sequence.length && !failure; i++) {
                var command = params.sequence[i];
                var r;
                try {
                    r = processCommand(command);
                } catch (e) {
                    r = { error: e.toString() };
                }
                if (r && r.error) {
                    failure = { error: "Step " + i + " (" + command.action + ") failed: " + r.error, completedSteps: i, results: results };
                } else {
                    results.push(stepResult(command.action, r));
                }
            }
        } finally {
            uiDeferDepth--;
        }
        syncUi();
        var out = failure || { success: true, message: "Sequence processed (" + results.length + " steps)", results: results };
        out.cursor = cursorInfo();
        return out;
    }

    // All steps as ONE undo step; if any step fails, nothing is kept. Steps
    // do no UI work; ties are made and the view is updated once, at the end.
    function processAtomicSequence(sequence) {
        var results = [];
        var failure = null;
        var r;
        uiDeferDepth++;
        try {
            r = executeWithUndo(function() {
                for (var k = 0; k < sequence.length; k++) {
                    var step;
                    try {
                        step = processCommand(sequence[k]);
                    } catch (e) {
                        step = { error: e.toString() };
                    }
                    if (step && step.error) {
                        failure = "Step " + k + " (" + sequence[k].action + ") failed: " + step.error;
                        throw new Error(failure);
                    }
                    results.push(stepResult(sequence[k].action, step));
                }
                return { message: "ok" };
            });
        } finally {
            uiDeferDepth--;
        }
        uiDirty = true;
        syncUi();
        if (r && r.error) {
            var reason = failure || r.error;
            if (reason.indexOf("Error: ") === 0) reason = reason.substring(7);
            return { error: reason + ". Nothing from this sequence was kept.", cursor: cursorInfo() };
        }
        return { success: true, message: "Sequence processed as one undo step (" + results.length + " steps)", results: results, cursor: cursorInfo() };
    }

    // ========================================
    // NAVIGATION FUNCTIONS
    // ========================================

    function navResult(message) {
        uiDirty = true;
        if (uiDeferDepth > 0) return { success: true, message: message };
        syncUi();
        return { success: true, message: message, cursor: cursorInfo() };
    }

    function getCursorInfo(params) {
        if (!curScore) return { error: "No score open" };
        return { success: true, cursor: cursorInfo() };
    }

    function moveCursorTo(t) {
        var keepChord = cursorState.lastChord;
        cursorState = { tick: t.tick, staff: t.staff, voice: t.voice, lastChord: null };
        if (keepChord && keepChord.tick === t.tick && keepChord.staff === t.staff && keepChord.voice === t.voice) {
            cursorState.lastChord = keepChord;
        }
    }

    function setCursor(params) {
        if (!curScore) return { error: "No score open" };
        moveCursorTo(resolveTarget(params));
        return navResult("Cursor set");
    }

    function goToMeasure(params) {
        var validation = validateParams(params, ["measure"]);
        if (!validation.valid) return validation;
        if (!curScore) return { error: "No score open" };
        var t = resolveTarget(params);
        moveCursorTo(t);
        return navResult("Moved to measure " + params.measure);
    }

    function goToBeginningOfScore(params) {
        if (!curScore) return { error: "No score open" };
        var t = resolveTarget(params);
        t.tick = 0;
        moveCursorTo(t);
        return navResult("Moved to beginning of score");
    }

    function goToFinalMeasure(params) {
        if (!curScore) return { error: "No score open" };
        var t = resolveTarget(params);
        var measures = listMeasures();
        t.tick = measures[measures.length - 1].startTick;
        moveCursorTo(t);
        return navResult("Moved to measure " + measures.length);
    }

    // Start ticks of the chords/rests in the cursor's voice (falls back to
    // voice 0 when the voice is empty).
    function trackTicks(staff, voice) {
        var ticks = [];
        var c = curScore.newCursor();
        c.staffIdx = staff;
        c.voice = voice;
        c.rewind(Cursor.SCORE_START);
        while (c.segment) {
            ticks.push(c.tick);
            c.next();
        }
        if (ticks.length === 0 && voice !== 0) return trackTicks(staff, 0);
        return ticks;
    }

    function nextElement(params) {
        if (!curScore) return { error: "No score open" };
        var n = params && params.numElements ? params.numElements : 1;
        var t = resolveTarget({});
        var end = scoreEndTick();
        var ticks = trackTicks(t.staff, t.voice).filter(function(x) { return x > t.tick; });
        ticks.push(end);
        var target = ticks[Math.min(n, ticks.length) - 1];
        if (t.tick >= end) return { success: false, message: "End of score reached", cursor: cursorInfo() };
        t.tick = target;
        moveCursorTo(t);
        return navResult(target >= end ? "Moved to end of score" : "Moved forward " + n + " element(s)");
    }

    function prevElement(params) {
        if (!curScore) return { error: "No score open" };
        var n = params && params.numElements ? params.numElements : 1;
        var t = resolveTarget({});
        var ticks = trackTicks(t.staff, t.voice).filter(function(x) { return x < t.tick; });
        if (ticks.length === 0) return { success: false, message: "Beginning of score reached", cursor: cursorInfo() };
        t.tick = ticks[Math.max(0, ticks.length - n)];
        moveCursorTo(t);
        return navResult("Moved back " + n + " element(s)");
    }

    function moveStaff(params, direction) {
        if (!curScore) return { error: "No score open" };
        var count = params && params.count ? params.count : 1;
        var t = resolveTarget({});
        var target = t.staff + direction * count;
        if (target < 0) return { success: false, message: "Already at first staff", cursor: cursorInfo() };
        if (target >= curScore.nstaves) return { success: false, message: "Already at last staff", cursor: cursorInfo() };
        t.staff = target;
        moveCursorTo(t);
        return navResult("Moved to staff " + target + " (" + staffLabel(target) + ")");
    }

    // ========================================
    // SELECTION FUNCTIONS
    // ========================================

    function collectRange(startTick, endTick, startStaff, endStaff) {
        var elementsMap = {};
        for (var st = startStaff; st <= endStaff; st++) {
            elementsMap["staff" + st] = [];
        }
        var seg = curScore.firstSegment(Segment.ChordRest);
        while (seg && seg.tick < endTick) {
            if (seg.tick >= startTick) {
                for (var s = startStaff; s <= endStaff; s++) {
                    for (var v = 0; v < 4; v++) {
                        var processed = processElement(seg.elementAt(s * 4 + v));
                        if (processed) {
                            processed.voice = v;
                            processed.startTick = seg.tick;
                            elementsMap["staff" + s].push(processed);
                        }
                    }
                }
            }
            seg = seg.next;
        }
        return elementsMap;
    }

    function selectionResult(message, startTick, endTick, startStaff, endStaff) {
        // In a batch the step result is discarded: skip reading the range.
        if (uiDeferDepth > 0) return { success: true, message: message };
        return {
            success: true,
            message: message,
            currentSelection: {
                startStaff: startStaff,
                endStaff: endStaff,
                startTick: startTick,
                endTick: endTick,
                totalDuration: endTick - startTick,
                elements: collectRange(startTick, endTick, startStaff, endStaff)
            },
            cursor: cursorInfo()
        };
    }

    function selectCurrentMeasure(params) {
        if (!curScore) return { error: "No score open" };
        var t = resolveTarget(params);
        var m = measureAtTick(t.tick);
        if (!m) return { error: "Score has no measures" };
        var startStaff = params.allStaves ? 0 : t.staff;
        var endStaff = params.allStaves ? curScore.nstaves - 1 : t.staff;
        moveCursorTo({ tick: m.startTick, staff: t.staff, voice: t.voice });
        selectRangeInclusive(m.startTick, m.endTick, startStaff, endStaff);
        return selectionResult("Selected measure " + m.number, m.startTick, m.endTick, startStaff, endStaff);
    }

    // endStaff is inclusive: selectCustomRange(0, 1920, 2, 2) selects staff 2 only.
    function selectCustomRange(params) {
        var validation = validateParams(params, ["startTick", "endTick", "startStaff", "endStaff"]);
        if (!validation.valid) return validation;
        if (!curScore) return { error: "No score open" };

        var startStaff = params.startStaff, endStaff = params.endStaff;
        if (!(startStaff >= 0 && endStaff >= startStaff && endStaff < curScore.nstaves)) {
            return { error: "Invalid staff range " + startStaff + "-" + endStaff + " (staves are 0-" + (curScore.nstaves - 1) + ", endStaff is inclusive)" };
        }
        if (!(params.endTick > params.startTick)) return { error: "endTick must be greater than startTick" };

        moveCursorTo({ tick: params.startTick, staff: startStaff, voice: cursorState.voice });
        if (!selectRangeInclusive(params.startTick, params.endTick, startStaff, endStaff)) {
            return { error: "MuseScore rejected the selection range" };
        }
        return selectionResult("Custom range selected", params.startTick, params.endTick, startStaff, endStaff);
    }

    // ========================================
    // DURATIONS
    // Cursor.setDuration() takes one plain or dotted value and silently turns
    // anything else into a different value (5/8 becomes a half note), so a
    // longer or odd duration is split into such values and tied: at every
    // barline first, then greedily (5/8 = 1/2 + 1/8). Durations that need a
    // tuplet (1/12) are an error. src/utils/durations.py has the same rules.
    // ========================================

    // Plain, dotted and double-dotted values from a double-dotted breve down
    // to a 128th, in ticks, longest first.
    property var noteValueTicks: [6720, 5760, 3840, 3360, 2880, 1920, 1680, 1440, 960, 840, 720, 480,
                                  420, 360, 240, 210, 180, 120, 105, 90, 60, 45, 30, 15]

    function gcd(a, b) {
        while (b) {
            var r = a % b;
            a = b;
            b = r;
        }
        return a;
    }

    function isPowerOfTwo(n) {
        while (n > 1 && n % 2 === 0) n /= 2;
        return n === 1;
    }

    // Ticks as reduced "n/d" text (480 -> "1/4").
    function ticksText(ticks) {
        var g = gcd(ticks, ticksPerWhole);
        return (ticks / g) + "/" + (ticksPerWhole / g);
    }

    // Ticks of a duration given as "n/d" or {numerator, denominator} that can
    // be written as plain/dotted notes tied together; throws otherwise.
    function parseDuration(value, label) {
        var num, den;
        if (typeof value === "string") {
            var m = /^\s*(\d+)\s*\/\s*(\d+)\s*$/.exec(value);
            if (!m) throw new Error(label + " must look like \"1/4\" (numerator/denominator), got " + JSON.stringify(value));
            num = parseInt(m[1], 10);
            den = parseInt(m[2], 10);
        } else if (isPlainObject(value)) {
            checkKeys(value, ["numerator", "denominator"], label);
            if (!isInt(value.numerator) || !isInt(value.denominator)) {
                throw new Error(label + " numerator and denominator must be integers, got " + JSON.stringify(value));
            }
            num = value.numerator;
            den = value.denominator;
        } else {
            throw new Error(label + " must be \"n/d\" or {numerator, denominator}, got " + JSON.stringify(value));
        }
        if (den === 0) throw new Error(label + " " + num + "/" + den + " has a zero denominator");
        if (num <= 0 || den < 0) throw new Error(label + " must be greater than zero, got " + num + "/" + den);
        var g = gcd(num, den);
        num /= g;
        den /= g;
        if (!isPowerOfTwo(den)) {
            throw new Error(label + " " + num + "/" + den + " can't be written with plain, dotted or double-dotted notes: " +
                            "it needs a tuplet (e.g. 1/12 is an eighth-note triplet). Use add_tuplet and fill it with add_note.");
        }
        if (den > 128) throw new Error(label + " " + num + "/" + den + " is not a multiple of 1/128, the shortest supported value");
        return num * (ticksPerWhole / den);
    }

    function largestNoteValue(ticks) {
        for (var i = 0; i < noteValueTicks.length; i++) {
            if (noteValueTicks[i] <= ticks) return noteValueTicks[i];
        }
        return 0;
    }

    // Greedy split of a length within one bar into note values.
    function splitValue(ticks) {
        var pieces = [];
        var rest = ticks;
        while (rest > 0) {
            var value = largestNoteValue(rest);
            if (!value) throw new Error("Internal: " + ticks + " ticks can't be split into note values");
            pieces.push(value);
            rest -= value;
        }
        return pieces;
    }

    // [{tick, ticks}] of the notes that write [start, start + length): split at
    // every barline, then greedily. bars: [{startTick, endTick}] covering the range.
    function planPieces(start, length, bars) {
        var end = start + length;
        var pieces = [];
        var pos = start;
        for (var b = 0; b < bars.length && pos < end; b++) {
            if (bars[b].endTick <= pos) continue;
            if (bars[b].startTick > pos) throw new Error("Internal: no bar at tick " + pos);
            var values = splitValue(Math.min(bars[b].endTick, end) - pos);
            for (var v = 0; v < values.length; v++) {
                pieces.push({ tick: pos, ticks: values[v] });
                pos += values[v];
            }
        }
        if (pos !== end) throw new Error("Internal: the bars end at tick " + pos + ", before tick " + end);
        return pieces;
    }

    function checkPitchList(list, label) {
        if (!Array.isArray(list) || list.length === 0) throw new Error(label + " must be a non-empty list of MIDI pitches");
        for (var i = 0; i < list.length; i++) {
            checkInt(list[i], label + " entry", 0, 127);
            if (list.indexOf(list[i]) !== i) throw new Error(label + " lists pitch " + list[i] + " twice");
        }
        return list.slice();
    }

    // Checks writeVoice events before anything is written. Returns
    // [{rest, pitches, ticks, tie}], tie being the list of tied pitches.
    function parseVoiceEvents(events) {
        if (!Array.isArray(events) || events.length === 0) throw new Error("events must be a non-empty list of notes, chords and rests");
        var out = [];
        for (var i = 0; i < events.length; i++) {
            var ev = events[i];
            var where = "Event " + i;
            if (!isPlainObject(ev)) throw new Error(where + " must be an object");
            checkKeys(ev, ["pitches", "rest", "duration", "tie"], where);
            if (!isSet(ev.duration)) throw new Error(where + ": missing duration");
            var ticks = parseDuration(ev.duration, where + " duration");
            if (isSet(ev.rest) && typeof ev.rest !== "boolean") throw new Error(where + ": rest must be true or false");
            if (ev.rest === true) {
                if (isSet(ev.pitches)) throw new Error(where + ": a rest can't have pitches");
                if (isSet(ev.tie) && ev.tie !== false) throw new Error(where + ": a rest can't be tied");
                out.push({ rest: true, pitches: [], ticks: ticks, tie: [] });
                continue;
            }
            if (!isSet(ev.pitches)) throw new Error(where + ": give \"pitches\" for a note/chord, or \"rest\": true for a rest");
            var pitches = checkPitchList(ev.pitches, where + " pitches");
            var tie = [];
            if (ev.tie === true) {
                tie = pitches.slice();
            } else if (isSet(ev.tie) && ev.tie !== false) {
                tie = checkPitchList(ev.tie, where + " tie");
                for (var k = 0; k < tie.length; k++) {
                    if (pitches.indexOf(tie[k]) < 0) throw new Error(where + ": tie lists pitch " + tie[k] + ", which is not in its pitches");
                }
            }
            out.push({ rest: false, pitches: pitches, ticks: ticks, tie: tie });
        }
        // A tie continues into the next event, which must hold the pitch. A tie
        // on the last event goes to the note written after the passage.
        for (var j = 0; j + 1 < out.length; j++) {
            if (!out[j].tie.length) continue;
            if (out[j + 1].rest) throw new Error("Event " + j + " is tied, but event " + (j + 1) + " is a rest");
            for (var p = 0; p < out[j].tie.length; p++) {
                if (out[j + 1].pitches.indexOf(out[j].tie[p]) < 0) {
                    throw new Error("Event " + j + " ties pitch " + out[j].tie[p] + ", which event " + (j + 1) + " doesn't contain");
                }
            }
        }
        return out;
    }

    // ========================================
    // WRITING NOTES AND RESTS
    // One engine for writeVoice, addNote and addRest. Inside a command: check
    // the target, plan the pieces, write them all through one cursor, read them
    // back, and queue the ties (made when the command ends). No UI work here.
    // ========================================

    // The chord/rest starting at `tick` in `track`, or null.
    function elementAt(track, tick) {
        if (!(tick >= 0 && tick < scoreEndTick())) return null;
        var seg = curScore.findSegmentAtTick(Segment.ChordRest, fractionFromTicks(tick));
        return seg ? seg.elementAt(track) : null;
    }

    function chordPitches(chord) {
        var out = [];
        var notes = chord.notes;
        for (var i = 0; i < notes.length; i++) out.push(notes[i].pitch);
        return out.sort(function(a, b) { return a - b; });
    }

    function hasTie(chord, back) {
        var notes = chord.notes;
        for (var i = 0; i < notes.length; i++) {
            if (back ? notes[i].tieBack : notes[i].tieForward) return true;
        }
        return false;
    }

    // Checks that [t.tick, endTick) of the target voice can be overwritten:
    // it starts on a note/rest boundary (or where the voice is empty) and holds
    // no tuplet. Returns warnings about what the overwrite changes nearby.
    function checkWriteTarget(t, endTick) {
        var where = "staff " + t.staff + " voice " + t.voice;
        var warnings = [];
        var c = makeCursor(t);
        requireSegment(c, t);
        if (!c.element) {
            // Nothing of this voice starts here: fine if the voice is empty
            // here (voices 2-4), not if one of its notes/rests still sounds.
            var p = makeCursor(t);
            if (p.prev() && p.element && p.tick + p.element.actualDuration.ticks > t.tick) {
                throw new Error("Tick " + t.tick + " is inside a note/rest of " + where + " that starts at tick " + p.tick +
                                ". Start at a note/rest boundary (splitting held notes isn't supported yet).");
            }
        }
        var w = makeCursor(t);
        if (!w.element) w.next();
        var firstChecked = false;
        while (w.segment && w.tick < endTick) {
            var el = w.element;
            if (el.tuplet) {
                throw new Error("There is a tuplet at tick " + w.tick + " in " + where +
                                "; writing over tuplets isn't supported yet (fill tuplets with add_note)");
            }
            if (!firstChecked && w.tick === t.tick && el.type === Element.CHORD && hasTie(el, true)) {
                warnings.push("The tie into the overwritten note at tick " + t.tick + " was removed");
            }
            firstChecked = true;
            var end = w.tick + el.actualDuration.ticks;
            if (end > endTick && el.type === Element.CHORD) {
                warnings.push("The note/chord at tick " + w.tick + " lasted until tick " + end + "; after tick " + endTick + " it is now a rest");
            } else if (end === endTick && el.type === Element.CHORD && hasTie(el, false)) {
                warnings.push("The tie from the overwritten note at tick " + w.tick + " to tick " + endTick + " was removed");
            }
            w.next();
        }
        return warnings;
    }

    // Throws unless `el` (read back at `tick`) is exactly the requested piece.
    function checkWritten(el, ev, ticks, tick, t, inTuplet) {
        var want = (ev.rest ? "rest" : "chord [" + ev.pitches.join(", ") + "]") + " of " + ticksText(ticks);
        var got;
        if (!el) {
            got = "nothing";
        } else {
            got = (el.type === Element.CHORD ? "chord [" + chordPitches(el).join(", ") + "]" : el.type === Element.REST ? "rest" : el.name) +
                  " of " + ticksText(el.duration.ticks) + (el.tuplet ? " in a tuplet" : "");
            var ok = el.type === (ev.rest ? Element.REST : Element.CHORD) && el.duration.ticks === ticks &&
                     (inTuplet ? !!el.tuplet : !el.tuplet && el.actualDuration.ticks === ticks);
            if (ok && !ev.rest) ok = chordPitches(el).join(",") === ev.pitches.slice().sort(function(a, b) { return a - b; }).join(",");
            if (ok) return;
        }
        throw new Error("MuseScore wrote something else at tick " + tick + " (staff " + t.staff + " voice " + t.voice + "): expected a " +
                        want + ", found " + got + ". Nothing was written.");
    }

    // Writes one note/chord/rest of a single value at the cursor.
    function writePiece(c, ev, ticks) {
        var g = gcd(ticks, ticksPerWhole);
        c.setDuration(ticks / g, ticksPerWhole / g);
        if (ev.rest) {
            c.addRest();
        } else {
            // addNote(p, true) adds to the chord the previous addNote wrote on
            // this cursor (its input state's last segment).
            c.addNote(ev.pitches[0], false);
            for (var i = 1; i < ev.pitches.length; i++) c.addNote(ev.pitches[i], true);
        }
    }

    function queueTie(t, tick, pitch, toTick) {
        pendingTies.push({ staff: t.staff, voice: t.voice, track: t.staff * 4 + t.voice, tick: tick, pitch: pitch, toTick: toTick });
    }

    // Signature of what is at `tick` in `track`, to notice an engine rollback.
    function contentSignature(track, tick) {
        var el = elementAt(track, tick);
        if (!el) return "none";
        return el.name + ":" + el.duration.ticks + ":" + (el.type === Element.CHORD ? chordPitches(el).join(".") : "");
    }

    // Writes parsed events one after another from t (staff t.staff, voice
    // t.voice). allowTuplet: a single event may fill a note of an existing
    // tuplet (addNote/addRest). Returns what was written.
    function writeEvents(t, events, allowTuplet) {
        var track = t.staff * 4 + t.voice;
        var total = 0;
        for (var i = 0; i < events.length; i++) total += events[i].ticks;
        var first = t.tick < scoreEndTick() ? makeCursor(t) : null;
        if (first && first.segment && first.element && first.element.tuplet) {
            if (!allowTuplet || events.length !== 1) {
                throw new Error("Tick " + t.tick + " is inside a tuplet; write_voice can't write into tuplets yet (fill them with add_note)");
            }
            return writeInTuplet(t, events[0]);
        }

        ensureRoom(t.tick, total);
        var endTick = t.tick + total;
        var warnings = checkWriteTarget(t, endTick);
        var bars = barsCovering(t.tick, endTick);

        var splits = [];
        var pos = t.tick;
        for (i = 0; i < events.length; i++) {
            events[i].pieces = planPieces(pos, events[i].ticks, bars);
            if (events[i].pieces.length > 1) {
                splits.push({ event: i, duration: ticksText(events[i].ticks),
                              writtenAs: events[i].pieces.map(function(piece) { return ticksText(piece.ticks); }) });
            }
            pos += events[i].ticks;
        }

        // Write through one cursor: after each piece MuseScore moves it to the
        // next position, which must be where the next piece starts.
        var c = makeCursor(t);
        var written = 0;
        for (i = 0; i < events.length; i++) {
            for (var k = 0; k < events[i].pieces.length; k++) {
                var piece = events[i].pieces[k];
                if (!c.segment || c.tick !== piece.tick) {
                    throw new Error("MuseScore moved the write position to tick " + (c.segment ? c.tick : "none") + " instead of " +
                                    piece.tick + " (staff " + t.staff + " voice " + t.voice + "). Nothing was written.");
                }
                writePiece(c, events[i], piece.ticks);
                written++;
            }
        }

        // Read everything back once: MuseScore must not have changed a duration.
        for (i = 0; i < events.length; i++) {
            for (k = 0; k < events[i].pieces.length; k++) {
                piece = events[i].pieces[k];
                checkWritten(elementAt(track, piece.tick), events[i], piece.ticks, piece.tick, t, false);
            }
        }

        // Ties between the pieces of a split note, and where an event asks for one.
        var ties = 0;
        for (i = 0; i < events.length; i++) {
            var ev = events[i];
            for (k = 0; k + 1 < ev.pieces.length && !ev.rest; k++) {
                for (var p = 0; p < ev.pitches.length; p++) {
                    queueTie(t, ev.pieces[k].tick, ev.pitches[p], ev.pieces[k + 1].tick);
                    ties++;
                }
            }
            var last = ev.pieces[ev.pieces.length - 1];
            for (p = 0; p < ev.tie.length; p++) {
                queueTie(t, last.tick, ev.tie[p], last.tick + last.ticks);
                ties++;
            }
        }

        var lastEvent = events[events.length - 1];
        var lastPiece = lastEvent.pieces[lastEvent.pieces.length - 1];
        commitProbe = function() { return contentSignature(track, lastPiece.tick); };
        return {
            startTick: t.tick, endTick: endTick, written: written, ties: ties, splits: splits, warnings: warnings,
            lastChord: lastEvent.rest ? null : { tick: lastEvent.pieces[0].tick, staff: t.staff, voice: t.voice, pieces: lastEvent.pieces.length }
        };
    }

    // One note/rest inside an existing tuplet (made by addTuplet): a single
    // note value, scaled by the tuplet (1/8 in an eighth-note triplet).
    function writeInTuplet(t, ev) {
        if (noteValueTicks.indexOf(ev.ticks) < 0) {
            throw new Error("Inside a tuplet the duration must be one plain or dotted note value (e.g. 1/8 in an eighth-note triplet), got " +
                            ticksText(ev.ticks));
        }
        var track = t.staff * 4 + t.voice;
        writePiece(makeCursor(t), ev, ev.ticks);
        var el = elementAt(track, t.tick);
        checkWritten(el, ev, ev.ticks, t.tick, t, true);
        var endTick = t.tick + el.actualDuration.ticks;
        for (var p = 0; p < ev.tie.length; p++) queueTie(t, t.tick, ev.tie[p], endTick);
        commitProbe = function() { return contentSignature(track, t.tick); };
        return { startTick: t.tick, endTick: endTick, written: 1, ties: ev.tie.length, splits: [], warnings: [],
                 lastChord: ev.rest ? null : { tick: t.tick, staff: t.staff, voice: t.voice, pieces: 1 } };
    }

    // The result of a write: splits and warnings are always reported.
    function writeResult(message, info) {
        var r = { message: message };
        if (info.splits.length) {
            r.split = info.splits;
            r.message += "; written as tied notes: " + info.splits.map(function(s) {
                return s.duration + " = " + s.writtenAs.join(" + ");
            }).join(", ");
        }
        if (info.warnings.length) r.warnings = info.warnings;
        return r;
    }

    // ========================================
    // NOTE & MUSIC OPERATIONS
    // ========================================

    // Writes a whole passage in one command (one undo step): notes, chords,
    // rests and ties, one after another from the start position.
    function writeVoice(params) {
        if (!curScore) return { error: "No score open" };
        var events = parseVoiceEvents(params.events);

        return mutate(function() {
            var t = resolveTarget(params);
            var info = writeEvents(t, events, false);
            cursorState = { tick: info.endTick, staff: t.staff, voice: t.voice, lastChord: info.lastChord };
            var bars = listMeasures();
            var firstBar = null, lastBar = null;
            for (var i = 0; i < bars.length; i++) {
                if (firstBar === null && bars[i].endTick > t.tick) firstBar = bars[i].number;
                if (bars[i].startTick < info.endTick) lastBar = bars[i].number;
            }
            var r = writeResult("Wrote " + events.length + " event(s) on staff " + t.staff + " voice " + t.voice +
                                ", bars " + firstBar + "-" + lastBar + " (ticks " + t.tick + "-" + info.endTick + "): " +
                                info.written + " notes/rests, " + info.ties + " tie(s), one undo step", info);
            r.startTick = t.tick;
            r.endTick = info.endTick;
            r.startMeasure = firstBar;
            r.endMeasure = lastBar;
            r.events = events.length;
            r.written = info.written;
            r.ties = info.ties;
            return r;
        });
    }

    function addNote(params) {
        if (!curScore) return { error: "No score open" };
        var validation = validateParams(params, ["pitch"]);
        if (!validation.valid) return validation;
        if (!(isInt(params.pitch) && params.pitch >= 0 && params.pitch <= 127)) return { error: "Pitch must be a MIDI value 0-127" };
        var advance = boolParam(params, "advanceCursorAfterAction", true);
        var tie = boolParam(params, "tie", false);
        if (boolParam(params, "addToChord", false)) return addPitchToChord(params, tie);
        if (!isSet(params.duration)) return { error: "Missing required parameters: duration" };
        var ev = { rest: false, pitches: [params.pitch], ticks: parseDuration(params.duration, "duration"), tie: tie ? [params.pitch] : [] };

        return mutate(function() {
            var t = resolveTarget(params);
            var info = writeEvents(t, [ev], true);
            cursorState = { tick: advance ? info.endTick : t.tick, staff: t.staff, voice: t.voice, lastChord: info.lastChord };
            return writeResult("Note " + params.pitch + " added at tick " + t.tick + " on staff " + t.staff + " voice " + t.voice +
                               (tie ? ", tied to the next note" : ""), info);
        });
    }

    // Adds a pitch to the chord just written (or the chord at tick/measure).
    function addPitchToChord(params, tie) {
        var want = isSet(params.duration) ? parseDuration(params.duration, "duration") : null;
        return mutate(function() {
            var t = resolveTarget(params);
            var chordTick = t.tick;
            var last = cursorState.lastChord;
            if (!isSet(params.tick) && !isSet(params.measure) && last && last.staff === t.staff && last.voice === t.voice) {
                if (last.pieces > 1) {
                    throw new Error("The last note was written as " + last.pieces + " tied notes (its duration had to be split), " +
                                    "so add_to_chord can't extend it; write the chord with write_voice instead");
                }
                chordTick = last.tick;
            }
            var c = makeCursor({ tick: chordTick, staff: t.staff, voice: t.voice });
            var chord = c.segment ? c.element : null;
            if (!chord || chord.type !== Element.CHORD) {
                throw new Error("No chord at tick " + chordTick + " on staff " + t.staff + " voice " + t.voice + " to add the pitch to");
            }
            if (want !== null && want !== chord.duration.ticks) {
                throw new Error("The chord at tick " + chordTick + " is " + ticksText(chord.duration.ticks) + " long, not " + ticksText(want) +
                                "; omit duration with add_to_chord (the pitch takes the chord's duration)");
            }
            var note = newElement(Element.NOTE);
            note.pitch = params.pitch;
            chord.add(note);
            if (tie) queueTie(t, chordTick, params.pitch, chordTick + chord.actualDuration.ticks);
            // The write position doesn't move; only staff/voice may change.
            cursorState = { tick: isSet(params.tick) || isSet(params.measure) ? t.tick : cursorState.tick,
                            staff: t.staff, voice: t.voice,
                            lastChord: { tick: chordTick, staff: t.staff, voice: t.voice, pieces: 1 } };
            return { message: "Added pitch " + params.pitch + " to chord at tick " + chordTick + (tie ? ", tied to the next note" : "") };
        });
    }

    function addRest(params) {
        if (!curScore) return { error: "No score open" };
        var validation = validateParams(params, ["duration"]);
        if (!validation.valid) return validation;
        var advance = boolParam(params, "advanceCursorAfterAction", true);
        var ev = { rest: true, pitches: [], ticks: parseDuration(params.duration, "duration"), tie: [] };

        return mutate(function() {
            var t = resolveTarget(params);
            var info = writeEvents(t, [ev], true);
            cursorState = { tick: advance ? info.endTick : t.tick, staff: t.staff, voice: t.voice, lastChord: null };
            return writeResult("Rest added at tick " + t.tick + " on staff " + t.staff + " voice " + t.voice, info);
        });
    }

    // ========================================
    // TIES
    // Made with MuseScore's own "tie" action on the selected note
    // (Score::cmdToggleTie). Inside our command its startCmd/endCmd are
    // no-ops (the plugin's startCmd locks the undo stack), so the ties join the
    // command. But the action toggles (removes an existing tie), and when no
    // following note of the same pitch exists it WRITES one (cmdAddTie). So each
    // tie is checked first (not tied yet, the next chord of the same voice has
    // the pitch), and afterwards it must end on exactly that note, or the whole
    // command is rolled back.
    // ========================================

    function noteAt(track, tick, pitch) {
        var el = elementAt(track, tick);
        if (!el || el.type !== Element.CHORD) return null;
        var notes = el.notes;
        for (var i = 0; i < notes.length; i++) {
            if (notes[i].pitch === pitch) return notes[i];
        }
        return null;
    }

    function tieEndsOn(note, target) {
        var tie = note.tieForward;
        var end = tie ? tie.endNote : null;
        return !!(end && end.is(target));
    }

    function tieLabel(tie) {
        return "tie on pitch " + tie.pitch + " at tick " + tie.tick + " (staff " + tie.staff + " voice " + tie.voice + ")";
    }

    function missingTieTarget(tie) {
        if (tie.toTick >= scoreEndTick()) return "no note follows it (end of score)";
        var el = elementAt(tie.track, tie.toTick);
        if (!el) return "nothing starts right after it (tick " + tie.toTick + ") in that voice";
        if (el.type !== Element.CHORD) return "a rest follows it (tick " + tie.toTick + ")";
        return "the next chord (tick " + tie.toTick + ") has pitches [" + chordPitches(el).join(", ") + "], not " + tie.pitch;
    }

    // True if a repeat barline is at `tick`: MuseScore won't tie across it.
    function repeatBarlineAt(tick) {
        var m = tick < scoreEndTick() ? curScore.tick2measure(fractionFromTicks(tick)) : null;
        if (!m || m.tick.ticks !== tick) return false;
        var prev = m.prevMeasure;
        return !!(m.repeatStart || (prev && prev.repeatEnd));
    }

    // Makes the ties queued in this command. Returns an error message or null.
    function applyPendingTies() {
        var jobs = [];
        var seen = {};
        for (var i = 0; i < pendingTies.length; i++) {
            var tie = pendingTies[i];
            var key = tie.track + ":" + tie.tick + ":" + tie.pitch;
            if (seen[key]) continue;
            seen[key] = true;
            var start = noteAt(tie.track, tie.tick, tie.pitch);
            if (!start) return "Can't make the " + tieLabel(tie) + ": that note is no longer there (overwritten by a later step?)";
            var target = noteAt(tie.track, tie.toTick, tie.pitch);
            if (!target) return "Can't make the " + tieLabel(tie) + ": " + missingTieTarget(tie) + ". Nothing was written.";
            if (start.tieForward) {
                if (tieEndsOn(start, target)) continue;
                return "Can't make the " + tieLabel(tie) + ": the note is already tied to another note";
            }
            if (repeatBarlineAt(tie.toTick)) return "Can't make the " + tieLabel(tie) + ": ties can't cross the repeat barline at tick " + tie.toTick;
            jobs.push({ tie: tie, note: start, target: target });
        }
        if (jobs.length === 0) return null;

        // In note-input mode the "tie" action adds a new tied note instead of
        // tying the selected one: leave it first ("escape"; only changes state).
        cmd("action://notation/cancel");
        uiDirty = true;
        var sel = curScore.selection;
        for (var j = 0; j < jobs.length; j++) {
            if (!sel.select(jobs[j].note, false)) return "Can't make the " + tieLabel(jobs[j].tie) + ": MuseScore refused to select the note";
            cmd("tie");
            if (!tieEndsOn(jobs[j].note, jobs[j].target)) {
                return "MuseScore did not make the " + tieLabel(jobs[j].tie) + " to the note at tick " + jobs[j].tie.toTick + ". Nothing was written.";
            }
        }
        sel.clear();
        return null;
    }

    // Creates a tuplet filled with rests. By default the cursor stays at its
    // start so the following addNote calls fill it.
    function addTuplet(params) {
        var validation = validateParams(params, ["ratio", "duration"]);
        if (!validation.valid) return validation;
        if (!curScore) return { error: "No score open" };
        checkDuration(params.duration);
        checkDuration(params.ratio, "Ratio");
        var advance = params.advanceCursorAfterAction === true;

        return mutate(function() {
            var t = resolveTarget(params);
            var total = durationTicks(params.duration);
            ensureRoom(t.tick, total);
            var m = measureAtTick(t.tick);
            if (m && t.tick + total > m.endTick) {
                throw new Error("Tuplet would cross the barline of measure " + m.number);
            }
            var c = makeCursor(t);
            requireSegment(c, t);
            c.addTuplet(fraction(params.ratio.numerator, params.ratio.denominator),
                        fraction(params.duration.numerator, params.duration.denominator));
            cursorState = { tick: advance ? t.tick + total : t.tick, staff: t.staff, voice: t.voice, lastChord: null };
            return { message: "Tuplet " + params.ratio.numerator + ":" + params.ratio.denominator + " added at tick " + t.tick };
        });
    }

    // Syllables ending in "-" continue the word on the next note ("Hel-", "lo").
    // "_" leaves a note without a syllable (melisma). Chords that only
    // continue tied notes are skipped. An existing lyric in the same verse is replaced.
    function addLyrics(params) {
        if (!params.lyrics || !Array.isArray(params.lyrics) || params.lyrics.length === 0) {
            return { error: "Lyrics must be specified as an array of strings" };
        }
        if (!curScore) return { error: "No score open" };
        var verse = params.verse || 0;

        return mutate(function() {
            var t = resolveTarget(params);
            var c = makeCursor(t);
            requireSegment(c, t);

            var idx = 0, added = 0, skippedRests = 0, replaced = 0;
            var inWord = false;
            while (c.segment && idx < params.lyrics.length) {
                var el = c.element;
                if (el && el.type === Element.CHORD) {
                    if (!isTiedBack(el)) {
                        var raw = String(params.lyrics[idx++]);
                        if (raw !== "_") {
                            var cont = raw.length > 1 && raw.charAt(raw.length - 1) === "-";
                            var text = cont ? raw.substring(0, raw.length - 1) : raw;
                            var syl = inWord ? (cont ? Lyrics.MIDDLE : Lyrics.END) : (cont ? Lyrics.BEGIN : Lyrics.SINGLE);
                            inWord = cont;

                            var existing = el.lyrics;
                            for (var k = existing.length - 1; k >= 0; k--) {
                                if (existing[k].verse === verse) {
                                    removeElement(existing[k]);
                                    replaced++;
                                }
                            }
                            var lyr = newElement(Element.LYRICS);
                            lyr.text = text;
                            lyr.syllabic = syl;
                            lyr.verse = verse;
                            c.add(lyr);
                            added++;
                        }
                    }
                } else if (el && el.type === Element.REST) {
                    skippedRests++;
                }
                c.next();
            }

            var nextTick = c.segment ? c.tick : scoreEndTick();
            cursorState = { tick: nextTick, staff: t.staff, voice: t.voice, lastChord: null };

            var message = "Added " + added + " lyrics";
            if (replaced > 0) message += " (replaced " + replaced + " existing)";
            if (skippedRests > 0) message += ", skipped " + skippedRests + " rests";
            if (idx < params.lyrics.length) message += ", " + (params.lyrics.length - idx) + " lyrics left over (ran out of notes)";
            return { message: message, addedCount: added, remainingLyrics: params.lyrics.slice(idx) };
        });
    }

    // ========================================
    // MARKINGS
    // ========================================

    function dynamicTypeValue(name) {
        var key = String(name).toUpperCase();
        if (!/^[PMFSZRN]+$/.test(key)) return undefined;
        return DynamicType[key];
    }

    function dynamicSymbols(name) {
        var syms = { p: "dynamicPiano", m: "dynamicMezzo", f: "dynamicForte", s: "dynamicSforzando",
                     z: "dynamicZ", r: "dynamicRinforzando", n: "dynamicNiente" };
        var out = "";
        for (var i = 0; i < name.length; i++) {
            out += "<sym>" + syms[name.charAt(i)] + "</sym>";
        }
        return out;
    }

    function addDynamic(params) {
        var validation = validateParams(params, ["dynamic"]);
        if (!validation.valid) return validation;
        if (!curScore) return { error: "No score open" };
        var name = String(params.dynamic).toLowerCase();
        var type = dynamicTypeValue(name);
        if (type === undefined || type === DynamicType.OTHER) {
            return { error: "Unknown dynamic '" + params.dynamic + "'. Use e.g. ppp, pp, p, mp, mf, f, ff, fff, fp, sf, sfz, sffz, fz, rf, rfz" };
        }

        return mutate(function() {
            var t = resolveTarget(params);
            var c = makeCursor(t);
            requireSegment(c, t);
            var old = findAnnotation(c.segment, Element.DYNAMIC, t.staff * 4 + t.voice);
            if (old) removeElement(old);
            var dyn = newElement(Element.DYNAMIC);
            dyn.text = dynamicSymbols(name);
            dyn.dynamicType = type;
            c.add(dyn);
            moveCursorTo(t);
            return { message: "Dynamic " + name + " added at tick " + t.tick + " on staff " + t.staff };
        });
    }

    function addFermata(params) {
        if (!curScore) return { error: "No score open" };
        return mutate(function() {
            var t = resolveTarget(params);
            var c = makeCursor(t);
            requireSegment(c, t);
            if (findAnnotation(c.segment, Element.FERMATA, t.staff * 4 + t.voice)) {
                return { message: "Fermata already present at tick " + t.tick };
            }
            c.add(newElement(Element.FERMATA));
            moveCursorTo(t);
            return { message: "Fermata added at tick " + t.tick + " on staff " + t.staff };
        });
    }

    // Tempo marks are system text: placed on the top staff at the target tick.
    function setTempo(params) {
        var validation = validateParams(params, ["bpm"]);
        if (!validation.valid) return validation;
        if (!curScore) return { error: "No score open" };
        if (!(params.bpm > 0 && params.bpm < 1000)) return { error: "bpm must be between 0 and 1000" };

        return mutate(function() {
            var t = resolveTarget(params);
            var c = makeCursor({ tick: t.tick, staff: 0, voice: 0 });
            requireSegment(c, { tick: t.tick, staff: 0 });
            var old = findAnnotation(c.segment, Element.TEMPO_TEXT);
            if (old) removeElement(old);
            var tempo = newElement(Element.TEMPO_TEXT);
            tempo.text = (params.text ? params.text + " " : "") + "<sym>metNoteQuarterUp</sym> = " + params.bpm;
            tempo.tempo = params.bpm / 60.0;
            c.add(tempo);
            return { message: "Tempo set to " + params.bpm + " BPM at tick " + t.tick };
        });
    }

    // ========================================
    // STRUCTURE: REPEATS, JUMPS, SECTIONS, KEYS
    // ========================================

    // Tick range of bars startMeasure..endMeasure (1-based, inclusive).
    function barRange(params, startKey, endKey) {
        startKey = startKey || "startMeasure";
        endKey = endKey || "endMeasure";
        if (!isSet(params[startKey])) throw new Error("Missing " + startKey);
        var first = measureByNumber(params[startKey]);
        var last = isSet(params[endKey]) ? measureByNumber(params[endKey]) : first;
        if (last.number < first.number) throw new Error(endKey + " is before " + startKey);
        return { first: first, last: last, startTick: first.startTick, endTick: last.endTick, bars: last.number - first.number + 1 };
    }

    function staffRange(params) {
        if (isSet(params.staff)) {
            if (!(params.staff >= 0 && params.staff < curScore.nstaves)) throw new Error("Invalid staff " + params.staff);
            return { first: params.staff, last: params.staff };
        }
        return { first: 0, last: curScore.nstaves - 1 };
    }

    // Start/end repeat barlines around bars startMeasure..endMeasure.
    function addRepeat(params) {
        if (!curScore) return { error: "No score open" };
        var r = barRange(params);
        var times = isSet(params.times) ? params.times : 2;
        return mutate(function() {
            r.first.obj.repeatStart = true;
            r.last.obj.repeatEnd = true;
            if (times !== 2) r.last.obj.repeatCount = times;
            return { message: "Repeat around bars " + r.first.number + "-" + r.last.number + " (played " + times + " times)" };
        });
    }

    function removeRepeat(params) {
        if (!curScore) return { error: "No score open" };
        var r = barRange(params);
        return mutate(function() {
            r.first.obj.repeatStart = false;
            r.last.obj.repeatEnd = false;
            return { message: "Removed repeat barlines at bars " + r.first.number + " and " + r.last.number };
        });
    }

    property var markerTypes: ({
        "segno": "SEGNO", "varsegno": "VARSEGNO", "coda": "CODA", "varcoda": "VARCODA",
        "fine": "FINE", "tocoda": "TOCODA", "tocodasym": "TOCODASYM"
    })

    // Segno, Coda, Fine, To Coda at the start (or end, for Fine/To Coda) of a bar.
    function addMarker(params) {
        var validation = validateParams(params, ["type", "measure"]);
        if (!validation.valid) return validation;
        if (!curScore) return { error: "No score open" };
        var key = String(params.type).toLowerCase().replace(/[\s_.-]/g, "");
        if (!markerTypes[key]) return { error: "Unknown marker '" + params.type + "'. Use segno, coda, fine, to coda, varsegno, varcoda" };
        var m = measureByNumber(params.measure);
        return mutate(function() {
            var mk = newElement(Element.MARKER);
            mk.markerType = MarkerType[markerTypes[key]];
            var c = makeCursor({ tick: m.startTick, staff: 0, voice: 0 });
            requireSegment(c, { tick: m.startTick, staff: 0 });
            c.add(mk);
            return { message: "Marker " + params.type + " added at bar " + m.number };
        });
    }

    property var jumpTypes: ({
        "dc":            ["D.C.", "start", "end", ""],
        "dcalfine":      ["D.C. al Fine", "start", "fine", ""],
        "dcalcoda":      ["D.C. al Coda", "start", "coda", "codab"],
        "ds":            ["D.S.", "segno", "end", ""],
        "dsalfine":      ["D.S. al Fine", "segno", "fine", ""],
        "dsalcoda":      ["D.S. al Coda", "segno", "coda", "codab"]
    })

    // D.C./D.S. instruction at the end of a bar.
    function addJump(params) {
        var validation = validateParams(params, ["type", "measure"]);
        if (!validation.valid) return validation;
        if (!curScore) return { error: "No score open" };
        var key = String(params.type).toLowerCase().replace(/[\s_.-]/g, "");
        var spec = jumpTypes[key];
        if (!spec) return { error: "Unknown jump '" + params.type + "'. Use D.C., D.C. al Fine, D.C. al Coda, D.S., D.S. al Fine, D.S. al Coda" };
        var m = measureByNumber(params.measure);
        return mutate(function() {
            var j = newElement(Element.JUMP);
            j.text = spec[0];
            j.jumpTo = spec[1];
            j.playUntil = spec[2];
            j.continueAt = spec[3];
            var c = makeCursor({ tick: m.startTick, staff: 0, voice: 0 });
            requireSegment(c, { tick: m.startTick, staff: 0 });
            c.add(j);
            return { message: spec[0] + " added at bar " + m.number };
        });
    }

    // Section label (rehearsal mark) such as "Verse 1", "Chorus", "A".
    function addRehearsalMark(params) {
        var validation = validateParams(params, ["text"]);
        if (!validation.valid) return validation;
        if (!curScore) return { error: "No score open" };
        return mutate(function() {
            var t = resolveTarget(params);
            var c = makeCursor({ tick: t.tick, staff: 0, voice: 0 });
            requireSegment(c, { tick: t.tick, staff: 0 });
            var old = findAnnotation(c.segment, Element.REHEARSAL_MARK);
            if (old) removeElement(old);
            var mark = newElement(Element.REHEARSAL_MARK);
            mark.text = params.text;
            c.add(mark);
            return { message: "Section label '" + params.text + "' at tick " + t.tick };
        });
    }

    // Key signature from a bar onwards, on every staff (or one staff).
    function setKeySignature(params) {
        var validation = validateParams(params, ["fifths"]);
        if (!validation.valid) return validation;
        if (!curScore) return { error: "No score open" };
        if (!(params.fifths >= -7 && params.fifths <= 7)) return { error: "fifths must be -7..7 (negative = flats)" };
        var staves = staffRange(params);
        return mutate(function() {
            var t = resolveTarget(params);
            var m = measureAtTick(Math.min(t.tick, scoreEndTick() - 1));
            for (var s = staves.first; s <= staves.last; s++) {
                // Setting the mode (or transposing) on a key signature that is
                // not yet on a staff crashes MuseScore: add it first.
                var ks = newElement(Element.KEYSIG);
                ks.concertKey = params.fifths;
                var c = makeCursor({ tick: m.startTick, staff: s, voice: 0 });
                requireSegment(c, { tick: m.startTick, staff: s });
                c.add(ks);
                ks.concertKey = params.fifths;   // now with the staff: transposing instruments get their written key
                if (params.mode === "minor") ks.keysig_mode = KeyMode.MINOR;
                else if (params.mode === "major") ks.keysig_mode = KeyMode.MAJOR;
            }
            var got = staffObj(staves.first).key(fractionFromTicks(m.startTick));
            if (got !== params.fifths) throw new Error("MuseScore did not apply the key signature (staff reads " + got + ")");
            return { message: "Key signature " + keyName(params.fifths) + " from bar " + m.number };
        });
    }

    property var tempoChangeNames: ({
        "rit": ["rit.", 0.75], "ritardando": ["rit.", 0.75], "rall": ["rall.", 0.75], "rallentando": ["rall.", 0.75],
        "allarg": ["allarg.", 0.8], "allargando": ["allarg.", 0.8], "morendo": ["morendo", 0.7],
        "smorz": ["smorz.", 0.7], "smorzando": ["smorz.", 0.7], "calando": ["calando", 0.75],
        "accel": ["accel.", 1.25], "accelerando": ["accel.", 1.25], "string": ["string.", 1.2], "stringendo": ["string.", 1.2]
    })

    // rit./accel.: a visible marking at the start, then hidden tempo marks on
    // the notes played inside the range, reaching the target tempo on the last (MuseScore's plugin API can't add
    // tempo-change lines). Optionally "a tempo" afterwards.
    function addGradualTempoChange(params) {
        var validation = validateParams(params, ["type"]);
        if (!validation.valid) return validation;
        if (!curScore) return { error: "No score open" };
        var spec = tempoChangeNames[String(params.type).toLowerCase().replace(/\./g, "")];
        if (!spec) return { error: "Unknown tempo change '" + params.type + "'. Use rit., rall., accel., allarg., string., smorz., morendo" };

        return mutate(function() {
            var t = resolveTarget(params);
            var endTick;
            if (isSet(params.endTick)) endTick = params.endTick;
            else if (isSet(params.endMeasure)) endTick = measureByNumber(params.endMeasure).endTick;
            else endTick = measureAtTick(t.tick).endTick;
            if (endTick <= t.tick) throw new Error("The end must be after the start");

            var c0 = makeCursor({ tick: t.tick, staff: 0, voice: 0 });
            requireSegment(c0, { tick: t.tick, staff: 0 });
            var startBpm = Math.round(c0.tempo * 60 * 100) / 100;
            var targetBpm = isSet(params.targetBpm) ? params.targetBpm
                          : Math.round(startBpm * (isSet(params.factor) ? params.factor : spec[1]) * 10) / 10;

            // Step where notes are actually struck inside the range (tempo only
            // matters there), at most one step per denominator unit (8th in 6/8)
            var minGap = ticksPerWhole / measureAtTick(t.tick).denominator;
            var beats = [];
            var cs = curScore.firstSegment(Segment.ChordRest);
            while (cs && cs.tick < endTick) {
                if (cs.tick > t.tick && (beats.length === 0 ? cs.tick - t.tick : cs.tick - beats[beats.length - 1]) >= minGap) {
                    beats.push(cs.tick);
                }
                cs = cs.next;
            }
            if (endTick < scoreEndTick() && beats.indexOf(endTick) < 0) beats.push(endTick);

            // Existing tempo marks inside the change would fight it: remove them
            var removed = 0;
            var seg = curScore.firstSegment();
            while (seg && seg.tick <= endTick) {
                if (seg.tick > t.tick) {
                    var anns = seg.annotations;
                    for (var k = anns.length - 1; k >= 0; k--) {
                        if (anns[k].type === Element.TEMPO_TEXT) { removeElement(anns[k]); removed++; }
                    }
                }
                seg = seg.next;
            }

            function place(tick, bpm, text, visible) {
                var c = makeCursor({ tick: tick, staff: 0, voice: 0 });
                if (!c.segment) return false;
                var old = findAnnotation(c.segment, Element.TEMPO_TEXT);
                if (old) removeElement(old);
                var tt = newElement(Element.TEMPO_TEXT);
                tt.text = text;
                tt.tempo = bpm / 60.0;
                c.add(tt);
                if (!visible) tt.visible = false;
                return true;
            }

            place(t.tick, startBpm, spec[0], true);
            // The last step reaches the target tempo
            var span = beats.length ? beats[beats.length - 1] - t.tick : 1;
            var steps = 0;
            for (var j = 0; j < beats.length; j++) {
                var frac = (beats[j] - t.tick) / span;
                var bpm = Math.round((startBpm + (targetBpm - startBpm) * frac) * 10) / 10;
                var isLast = j === beats.length - 1;
                if (isLast && params.aTempo && beats[j] === endTick) {
                    if (place(beats[j], startBpm, "a tempo", true)) steps++;
                } else if (place(beats[j], bpm, "", false)) {
                    steps++;
                }
            }
            return { message: spec[0] + " from tick " + t.tick + " to " + endTick + ": \u2669 = " + startBpm + " -> " + targetBpm +
                               " over " + steps + " hidden tempo steps" + (params.aTempo ? ", then a tempo" : "") +
                               (removed ? " (replaced " + removed + " tempo mark(s) inside the range)" : "") };
        });
    }

    // Removes a marking at a position: tempo, dynamic, fermata, text,
    // rehearsalMark, chordSymbol, breath (on a note/rest), or a line
    // (slur, hairpin, volta, gradualTempoChange) starting there.
    function removeMarking(params) {
        var validation = validateParams(params, ["kind"]);
        if (!validation.valid) return validation;
        if (!curScore) return { error: "No score open" };
        var annotationTypes = {
            tempo: Element.TEMPO_TEXT, dynamic: Element.DYNAMIC, fermata: Element.FERMATA,
            rehearsalmark: Element.REHEARSAL_MARK, chordsymbol: Element.HARMONY, breath: Element.BREATH,
            text: Element.STAFF_TEXT
        };
        var lineTypes = {
            slur: Element.SLUR, hairpin: Element.HAIRPIN, volta: Element.VOLTA,
            gradualtempochange: Element.GRADUAL_TEMPO_CHANGE
        };
        var kind = String(params.kind).toLowerCase().replace(/[\s_.-]/g, "");
        if (!annotationTypes[kind] && !lineTypes[kind]) return { error: "Unknown kind '" + params.kind + "'" };

        return mutate(function() {
            var t = resolveTarget(params);
            var removed = 0;
            if (lineTypes[kind]) {
                var sps = curScore.spanners;
                var victims = [];
                for (var i = 0; i < sps.length; i++) {
                    var sp = sps[i];
                    if (sp.type === lineTypes[kind] && ticksOf(sp.spannerTick) === t.tick &&
                        (!isSet(params.staff) || Math.floor(sp.track / 4) === t.staff)) victims.push(sp);
                }
                for (var v = 0; v < victims.length; v++) { removeElement(victims[v]); removed++; }
            } else {
                // Search the exact segment (any segment type) at the tick
                var seg = curScore.firstSegment();
                while (seg && seg.tick < t.tick) seg = seg.next;
                while (seg && seg.tick === t.tick) {
                    var anns = seg.annotations;
                    for (var k = anns.length - 1; k >= 0; k--) {
                        var a = anns[k];
                        var match = a.type === annotationTypes[kind] ||
                            (kind === "text" && (a.type === Element.SYSTEM_TEXT || a.type === Element.EXPRESSION));
                        if (match && (!isSet(params.staff) || Math.floor(a.track / 4) === t.staff)) {
                            removeElement(a);
                            removed++;
                        }
                    }
                    seg = seg.next;
                }
            }
            if (removed === 0) throw new Error("No " + params.kind + " found at tick " + t.tick);
            return { message: "Removed " + removed + " " + params.kind + " at tick " + t.tick };
        });
    }

    // ========================================
    // SELECTION-BASED EDITS (slurs, hairpins, articulations, copy, delete)
    // These select a range first (outside the command), then run a
    // MuseScore action on it.
    // ========================================

    // Tick range from startTick/endTick, or measure/endMeasure, or the cursor's note.
    function editRange(params) {
        if (isSet(params.startTick)) {
            var end = isSet(params.endTick) ? params.endTick : null;
            if (end === null) {
                var c0 = makeCursor({ tick: params.startTick, staff: isSet(params.staff) ? params.staff : cursorState.staff, voice: 0 });
                end = params.startTick + (c0.element ? c0.element.actualDuration.ticks : 1);
            }
            return { startTick: params.startTick, endTick: end };
        }
        if (isSet(params.measure) || isSet(params.startMeasure)) {
            var r = barRange({ startMeasure: isSet(params.startMeasure) ? params.startMeasure : params.measure,
                               endMeasure: params.endMeasure });
            return { startTick: r.startTick, endTick: r.endTick };
        }
        throw new Error("Specify startTick/endTick or startMeasure/endMeasure");
    }

    // Runs a MuseScore action on the current selection OUTSIDE a plugin
    // command: MuseScore only enables selection-based actions (insert measure,
    // delete, ...) there, and records the action as its own undo step.
    // `verify` returns true if the action took effect.
    function runSelectionCommand(code, verify) {
        var before = copyCursor(cursorState);
        cmd(code);
        var ok = verify ? verify() : true;
        if (ok) {
            undoCursorStack.push(before);
            if (undoCursorStack.length > 200) undoCursorStack.shift();
        }
        return ok;
    }

    function finishSelectionEdit(result) {
        cmd("action://notation/cancel");   // leave edit mode (e.g. on a new slur)
        uiDirty = true;
        result.success = true;
        if (uiDeferDepth === 0) {
            syncUi();
            result.cursor = cursorInfo();
        }
        return result;
    }

    function countArticulations(startTick, endTick, staff) {
        var n = 0;
        var seg = curScore.firstSegment(Segment.ChordRest);
        while (seg && seg.tick < endTick) {
            if (seg.tick >= startTick) {
                for (var v = 0; v < 4; v++) {
                    var el = seg.elementAt(staff * 4 + v);
                    if (el && el.type === Element.CHORD) n += el.articulations.length;
                }
            }
            seg = seg.next;
        }
        return n;
    }

    function selectionAction(params, actionCode, label, kind) {
        if (!curScore) return { error: "No score open" };
        var range = editRange(params);
        var staff = isSet(params.staff) ? params.staff : cursorState.staff;
        if (!(staff >= 0 && staff < curScore.nstaves)) return { error: "Invalid staff " + staff };
        if (!selectRangeInclusive(range.startTick, range.endTick, staff, staff)) {
            return { error: "Could not select ticks " + range.startTick + "-" + range.endTick + " on staff " + staff };
        }
        var spanners = curScore.spanners.length;
        var arts = countArticulations(range.startTick, range.endTick, staff);
        var ok = runSelectionCommand(actionCode, function() {
            if (kind === "line") return curScore.spanners.length > spanners;
            if (kind === "articulation") return countArticulations(range.startTick, range.endTick, staff) > arts;
            return true;
        });
        if (!ok) {
            showCursor();
            return { error: label + " was not applied (are there notes on staff " + staff + " in that range?)" };
        }
        return finishSelectionEdit({ message: label + " from tick " + range.startTick + " to " + range.endTick + " on staff " + staff });
    }

    function addSlur(params) {
        return selectionAction(params, "add-slur", "Slur", "line");
    }

    function addHairpin(params) {
        var kind = String(params.type || "crescendo").toLowerCase();
        var code = kind.indexOf("dim") === 0 || kind.indexOf("decresc") === 0 ? "add-hairpin-reverse" : "add-hairpin";
        return selectionAction(params, code, code === "add-hairpin" ? "Crescendo hairpin" : "Diminuendo hairpin", "line");
    }

    property var articulationActions: ({
        "staccato": "add-staccato", "tenuto": "add-tenuto", "marcato": "add-marcato",
        "accent": "add-sforzato", "sforzato": "add-sforzato"
    })

    function addArticulation(params) {
        var code = articulationActions[String(params.type || "").toLowerCase()];
        if (!code) return { error: "Unknown articulation '" + params.type + "'. Use staccato, tenuto, accent, marcato" };
        return selectionAction(params, code, params.type + " added", "articulation");
    }

    // Inserts `count` empty bars before bar `before` (each is one MuseScore undo step).
    function insertBarsBefore(before, count) {
        for (var i = 0; i < count; i++) {
            var m = measureByNumber(before);
            var n = listMeasures().length;
            selectRangeInclusive(m.startTick, m.endTick, 0, curScore.nstaves - 1);
            if (!runSelectionCommand("insert-measure", function() { return listMeasures().length === n + 1; })) return i;
        }
        return count;
    }

    // Removes whole bars (not just their contents).
    function deleteMeasures(params) {
        if (!curScore) return { error: "No score open" };
        var r = barRange(params);
        var count = listMeasures().length;
        if (r.bars >= count) return { error: "Cannot delete every bar of the score" };
        if (!selectRangeInclusive(r.startTick, r.endTick, 0, curScore.nstaves - 1)) return { error: "Could not select the bars" };
        if (!runSelectionCommand("time-delete", function() { return listMeasures().length === count - r.bars; })) {
            showCursor();
            return { error: "MuseScore did not delete the bars" };
        }
        if (cursorState.tick >= r.endTick) cursorState.tick -= (r.endTick - r.startTick);
        else if (cursorState.tick >= r.startTick) cursorState.tick = Math.min(r.startTick, scoreEndTick());
        cursorState.lastChord = null;
        return finishSelectionEdit({ message: "Deleted bars " + r.first.number + "-" + r.last.number });
    }

    function rangeSignature(startTick, endTick, s0, s1) {
        var map = collectRange(startTick, endTick, s0, s1);
        var out = [];
        for (var st = s0; st <= s1; st++) {
            var els = map["staff" + st] || [];
            for (var i = 0; i < els.length; i++) {
                var e = els[i];
                var pitches = (e.notes || []).map(function(n) { return n.pitchMidi; }).join(".");
                out.push(st + ":" + e.voice + ":" + (e.startTick - startTick) + ":" + e.name + ":" + e.durationTicks + ":" + pitches);
            }
        }
        return out.join("|");
    }

    // Copies bars startMeasure..endMeasure to toMeasure. With insert (default)
    // the copy goes into new empty bars inserted before toMeasure (or appended
    // at the end); otherwise it overwrites the bars there.
    function copyMeasures(params) {
        var validation = validateParams(params, ["startMeasure", "toMeasure"]);
        if (!validation.valid) return validation;
        if (!curScore) return { error: "No score open" };
        var src = barRange(params);
        var staves = staffRange(params);
        var insert = params.insert !== false;
        var total = listMeasures().length;
        if (!(params.toMeasure >= 1 && params.toMeasure <= total + 1)) return { error: "toMeasure must be 1-" + (total + 1) };
        if (!insert && params.toMeasure + src.bars - 1 > total) return { error: "Not enough bars after bar " + params.toMeasure + " to paste over; use insert" };
        var srcSig = rangeSignature(src.startTick, src.endTick, staves.first, staves.last);

        // 1. Copy the source to MuseScore's clipboard
        if (!selectRangeInclusive(src.startTick, src.endTick, staves.first, staves.last)) return { error: "Could not select the source bars" };
        cmd("action://notation/copy");

        // 2. Make room
        var steps = 0;
        if (insert) {
            if (params.toMeasure > total) {
                var ap = appendMeasure({ count: src.bars });
                if (ap.error) return ap;
                steps = 1;
            } else {
                steps = insertBarsBefore(params.toMeasure, src.bars);
                if (steps !== src.bars) {
                    showCursor();
                    return { error: "MuseScore inserted only " + steps + " of " + src.bars + " bars", undoSteps: steps };
                }
            }
        }

        // 3. Paste at the target
        var target = measureByNumber(params.toMeasure);
        var targetEnd = measureByNumber(params.toMeasure + src.bars - 1);
        if (!selectRangeInclusive(target.startTick, targetEnd.endTick, staves.first, staves.last)) return { error: "Could not select the target bars" };
        var pasted = runSelectionCommand("action://notation/paste", function() {
            return rangeSignature(target.startTick, targetEnd.endTick, staves.first, staves.last) === srcSig;
        });
        if (!pasted) {
            showCursor();
            return { error: "The pasted bars don't match the source (MuseScore may have refused the paste)", undoSteps: steps };
        }
        cursorState = { tick: target.startTick, staff: cursorState.staff, voice: cursorState.voice, lastChord: null };
        return finishSelectionEdit({
            message: "Copied bars " + src.first.number + "-" + src.last.number + " to bars " + target.number + "-" + targetEnd.number +
                     (insert ? " (inserted)" : " (overwritten)"),
            undoSteps: steps + 1
        });
    }

    // ========================================
    // MEASURE OPERATIONS
    // ========================================

    function appendMeasure(params) {
        if (!curScore) return { error: "No score open" };
        var count = params && params.count ? params.count : 1;
        return mutate(function() {
            curScore.appendMeasures(count);
            return { message: count + " measure(s) appended; score has " + curScore.nmeasures + " measures" };
        });
    }

    // Inserts `count` empty measures before the given measure (default: the cursor's).
    function insertMeasure(params) {
        if (!curScore) return { error: "No score open" };
        var count = isSet(params.count) ? params.count : 1;
        var t = resolveTarget(params);
        var m = measureAtTick(t.tick);
        if (!m || t.tick >= scoreEndTick()) return appendMeasure({ count: count });
        var done = insertBarsBefore(m.number, count);
        cursorState = { tick: m.startTick, staff: t.staff, voice: t.voice, lastChord: null };
        if (done !== count) {
            showCursor();
            return { error: "MuseScore inserted only " + done + " of " + count + " measures" };
        }
        return finishSelectionEdit({ message: count + " measure(s) inserted before measure " + m.number + " (" + count + " undo step(s))" });
    }

    // Deletes the current selection; with `measure`, clears that measure
    // (on `staff` only, or on all staves) instead.
    function deleteSelection(params) {
        if (!curScore) return { error: "No score open" };
        var message = "Selection deleted";
        if (isSet(params.measure)) {
            var m = measureByNumber(params.measure);
            var s0 = isSet(params.staff) ? params.staff : 0;
            var s1 = isSet(params.staff) ? params.staff : curScore.nstaves - 1;
            if (!selectRangeInclusive(m.startTick, m.endTick, s0, s1)) return { error: "Could not select measure " + params.measure };
            message = "Cleared measure " + params.measure;
        } else if (uiDirty) {
            // The cursor moved without the selection (inside a batch): the
            // note/rest at the cursor is what gets deleted.
            showCursor();
        }
        runSelectionCommand("action://notation/delete");
        return finishSelectionEdit({ message: message });
    }

    // ========================================
    // STAFF & INSTRUMENT OPERATIONS
    // ========================================

    function partSummary(part, index) {
        var firstStaff = Math.floor(part.startTrack / 4);
        var staves = [];
        for (var s = firstStaff; s < Math.floor(part.endTrack / 4); s++) staves.push(s);
        return { index: index, name: part.longName || part.partName, instrumentId: part.instrumentId, staves: staves };
    }

    function addInstrument(params) {
        var validation = validateParams(params, ["instrumentId"]);
        if (!validation.valid) return validation;
        if (!curScore) return { error: "No score open" };

        return mutate(function() {
            curScore.appendPart(params.instrumentId);
            var parts = curScore.parts;
            var added = partSummary(parts[parts.length - 1], parts.length - 1);
            var result = { message: "Instrument " + added.name + " added on staff " + added.staves.join(", "), part: added };
            if (added.instrumentId !== params.instrumentId) {
                result.warning = "Instrument id '" + params.instrumentId + "' was not found; MuseScore used '" + added.instrumentId + "' instead";
            }
            return result;
        });
    }

    // Removes a whole instrument (part), given its part index or any staff index.
    function removeInstrument(params) {
        if (!curScore) return { error: "No score open" };
        if (!isSet(params.part) && !isSet(params.staff)) return { error: "Specify part (part index) or staff (staff index)" };

        var parts = curScore.parts;
        var part;
        if (isSet(params.part)) {
            if (!(params.part >= 0 && params.part < parts.length)) return { error: "Invalid part " + params.part + " (score has " + parts.length + " parts)" };
            part = parts[params.part];
        } else {
            if (!(params.staff >= 0 && params.staff < curScore.nstaves)) return { error: "Invalid staff " + params.staff };
            part = staffObj(params.staff).part;
        }
        if (parts.length <= 1) return { error: "Cannot remove the only instrument in the score" };

        // curScore.parts is a live list, so keep the count as a plain number
        var partCount = parts.length;
        var removed = partSummary(part, partIndexOf(part));
        return mutate(function() {
            curScore.removeParts([part]);
            if (curScore.parts.length !== partCount - 1) throw new Error("MuseScore did not remove the part");
            // Keep the cursor on the same staff if it survived; otherwise use
            // the staff that took the removed part's place.
            var first = removed.staves[0], count = removed.staves.length;
            if (cursorState.staff >= first + count) {
                cursorState.staff -= count;
            } else if (cursorState.staff >= first) {
                cursorState.staff = Math.min(first, curScore.nstaves - 1);
            }
            cursorState.lastChord = null;
            return { message: "Removed instrument " + removed.name + " (was staff " + removed.staves.join(", ") + ")", removed: removed };
        });
    }

    function setStaffMute(params) {
        var validation = validateParams(params, ["staff"]);
        if (!validation.valid) return validation;

        return executeWithUndo(function() {
            var staff = curScore.staves && curScore.staves[params.staff] ||
                       (typeof curScore.staff === "function" ? curScore.staff(params.staff) : null);

            if (staff) {
                staff.invisible = Boolean(params.mute);
                return { success: true, message: "Staff " + (params.mute ? "muted" : "unmuted") };
            } else {
                return { error: "Staff not found" };
            }
        });
    }

    function setInstrumentSound(params) {
        var validation = validateParams(params, ["staff", "instrumentId"]);
        if (!validation.valid) return validation;
        if (!curScore) return { error: "No score open" };
        if (!(params.staff >= 0 && params.staff < curScore.nstaves)) return { error: "Invalid staff " + params.staff };

        return mutate(function() {
            var part = staffObj(params.staff).part;
            curScore.replaceInstrument(part, params.instrumentId);
            if (part.instrumentId !== params.instrumentId) {
                throw new Error("Instrument id '" + params.instrumentId + "' not found");
            }
            return { message: "Staff " + params.staff + " changed to " + part.longName };
        });
    }

    function setTimeSignature(params) {
        var validation = validateParams(params, ["numerator", "denominator"]);
        if (!validation.valid) return validation;
        if (!curScore) return { error: "No score open" };

        return mutate(function() {
            var t = resolveTarget(params);
            var m = measureAtTick(Math.min(t.tick, scoreEndTick() - 1));
            var c = makeCursor({ tick: m.startTick, staff: 0, voice: 0 });
            requireSegment(c, { tick: m.startTick, staff: 0 });
            var ts = newElement(Element.TIMESIG);
            ts.timesig = fraction(params.numerator, params.denominator);
            c.add(ts);
            return { message: "Time signature set to " + params.numerator + "/" + params.denominator + " from measure " + m.number };
        });
    }

    // ========================================
    // SCORE ANALYSIS
    // ========================================

    function getScore(params) {
        if (!curScore) return { error: "No score open" };
        return { success: true, analysis: getScoreSummary(), cursor: cursorInfo() };
    }

    function getScoreSummary() {
        var measures = listMeasures();
        var nstaves = curScore.nstaves;
        var score = {
            title: curScore.metaTag("workTitle") || curScore.title || "",
            composer: curScore.metaTag("composer") || "",
            numMeasures: measures.length,
            numStaves: nstaves,
            keySignature: null,
            timeSignatures: [],
            tempos: [],
            parts: [],
            staves: [],
            measures: []
        };

        var parts = curScore.parts;
        for (var p = 0; p < parts.length; p++) {
            score.parts.push(partSummary(parts[p], p));
        }

        var tick0 = fractionFromTicks(0);
        for (var i = 0; i < nstaves; i++) {
            var st = staffObj(i);
            var part = st ? st.part : null;
            var fifths = st ? st.key(tick0) : 0;
            score.staves.push({
                name: "staff" + i,
                index: i,
                instrument: part ? (part.longName || part.partName) : "",
                shortName: part ? part.shortName : "",
                instrumentId: part ? part.instrumentId : "",
                part: part ? partIndexOf(part) : -1,
                visible: part ? part.show : true,
                keySignature: { fifths: fifths, name: keyName(fifths) }
            });
        }
        if (score.staves.length) score.keySignature = score.staves[0].keySignature;

        // Initial tempo from the tempo map, in case there is no tempo marking.
        var tc = curScore.newCursor();
        tc.rewind(Cursor.SCORE_START);
        score.initialTempoBpm = Math.round(tc.tempo * 60 * 100) / 100;
        score.durationSeconds = curScore.duration;
        score.spanners = listSpanners();
        score.swing = [];
        for (var sw = 0; sw < nstaves; sw++) {
            try {
                var swing = staffObj(sw).swing(tick0);
                if (swing && swing.isOn) score.swing.push({ staff: sw, unit: swing.swingUnit, ratio: swing.swingRatio });
            } catch (e) {}
        }

        var prevTs = "";
        for (var mi = 0; mi < measures.length; mi++) {
            var md = measures[mi];
            var tsText = md.numerator + "/" + md.denominator;
            if (tsText !== prevTs) {
                score.timeSignatures.push({ measure: md.number, tick: md.startTick, numerator: md.numerator, denominator: md.denominator });
                prevTs = tsText;
            }

            var nominal = md.obj.timesigNominal;
            var measure = {
                measure: md.number,
                startTick: md.startTick,
                endTick: md.endTick,
                timeSignature: tsText,
                nominalTimeSignature: nominal ? nominal.numerator + "/" + nominal.denominator : tsText,
                keyFifths: staffObj(0).key(fractionFromTicks(md.startTick)),
                numElements: 0,
                elements: {},
                markings: []
            };
            if (md.obj.repeatStart) measure.repeatStart = true;
            if (md.obj.repeatEnd) {
                measure.repeatEnd = true;
                measure.repeatCount = md.obj.repeatCount;
            }
            var marks = measureMarks(md.obj);
            if (marks.length) measure.marks = marks;

            // Effective tempo (quarter-note BPM, includes rit./accel.) and
            // playback time at the start of the bar.
            tc.rewindToFraction(fractionFromTicks(md.startTick));
            if (tc.segment) {
                measure.tempoBpm = Math.round(tc.tempo * 60 * 100) / 100;
                measure.timeSeconds = Math.round(tc.time(false)) / 1000;
            }
            for (var j = 0; j < nstaves; j++) {
                measure.elements["staff" + j] = [];
            }

            var seg = md.obj.firstSegment;
            while (seg) {
                var anns = seg.annotations;
                for (var a = 0; a < anns.length; a++) {
                    var ann = processAnnotation(anns[a]);
                    if (ann) {
                        ann.tick = seg.tick;
                        measure.markings.push(ann);
                        if (ann.type === "tempo") {
                            score.tempos.push({ measure: md.number, tick: seg.tick, bpm: ann.bpm, text: ann.text });
                        }
                    }
                }
                for (var k = 0; k < nstaves; k++) {
                    for (var v = 0; v < 4; v++) {
                        var processed = processElement(seg.elementAt(k * 4 + v));
                        if (processed) {
                            processed.startTick = seg.tick;
                            processed.voice = v;
                            measure.elements["staff" + k].push(processed);
                            measure.numElements++;
                        }
                    }
                }
                seg = seg.nextInMeasure;
            }
            score.measures.push(measure);
        }
        return score;
    }

    // ========================================
    // INITIALIZATION
    // ========================================

    onRun: {
        console.log("Starting MuseScore API Server on port 8765");

        api.websocketserver.listen(8765, function(clientId) {
            console.log("Client connected with ID: " + clientId);
            clientConnections.push(clientId);

            api.websocketserver.onMessage(clientId, function(message) {
                processMessage(message, clientId);
            });
        });

        if (curScore) {
            cursorState = { tick: 0, staff: 0, voice: 0, lastChord: null };
            showCursor();
        }
    }
}
