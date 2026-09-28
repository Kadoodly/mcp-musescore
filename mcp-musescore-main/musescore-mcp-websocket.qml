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

    // The current MuseScore selection was made by the user (not by the plugin).
    property bool selectionFromUser: false

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

    // ---- Score versions ----
    // Every change to the score, by this plugin or by the user in MuseScore,
    // raises scoreVersion. Changes are found by comparing a digest of each
    // bar with the one stored after the last read or edit (MuseScore 4 doesn't
    // tell plugins about edits), and the log records which bars changed.
    property int scoreVersion: 0
    property var versionLog: []          // [{version, source, action, bars: [[first, last], ...] or null = unknown}]
    property var barDigests: null        // digest per bar (index = bar number - 1); null = no baseline yet
    property var touchedTicks: null      // [[startTick, endTick]] edited by the running request; [] = unknown
    property var lastScore: null
    readonly property int maxLogEntries: 500

    readonly property int ticksPerWhole: 1920

    // ========================================
    // WEBSOCKET & MESSAGE PROCESSING
    // ========================================

    function processMessage(message, clientId) {
        console.log("Received message: " + message);
        var reply;
        try {
            var command = JSON.parse(message);
            reply = { status: "success", result: handleRequest(command) };
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
        reply.version = scoreVersion;
        api.websocketserver.send(clientId, JSON.stringify(reply));
    }

    // Actions that never change the score. Everything else is an edit: it may
    // carry expectedVersion, and afterwards the version is raised.
    property var readOnlyActions: [
        "ping", "getScore", "syncStateToSelection", "getCursorInfo", "setCursor", "goToMeasure",
        "goToBeginningOfScore", "goToFinalMeasure", "nextElement", "prevElement", "nextStaff", "prevStaff",
        "selectCurrentMeasure", "selectCustomRange", "getVersion", "getChangesSince", "getSelection",
        "checkScore", "exportScore", "saveScore", "openScore"
    ]

    function isEdit(command) {
        if (command.action === "processSequence") {
            var steps = (command.params && command.params.sequence) || [];
            for (var i = 0; i < steps.length; i++) {
                if (steps[i] && readOnlyActions.indexOf(steps[i].action) < 0) return true;
            }
            return false;
        }
        return readOnlyActions.indexOf(command.action) < 0;
    }

    // One request from the MCP server: version check, the action, version update.
    function handleRequest(command) {
        checkCommand(command, true);
        if (!curScore) return processCommand(command);
        noteScoreSwitch();

        var edit = isEdit(command);
        var params = command.params || {};
        if (isSet(params.expectedVersion)) {
            checkInt(params.expectedVersion, "expectedVersion", 0, null);
            var expected = params.expectedVersion;
            delete params.expectedVersion;
            detectUserChanges();
            if (expected !== scoreVersion) {
                throw new Error("The score changed since version " + expected + " (it is now version " + scoreVersion +
                                "). Nothing was done. Get the changes (get_changes_since) or reload, then redo the edit.");
            }
        }
        if (!edit) return processCommand(command);

        touchedTicks = [];
        var result, failure = null;
        try {
            result = processCommand(command);
        } catch (e) {
            failure = e;
        }
        try {
            recordChange(command.action, !failure && !(result && result.error));
        } finally {
            touchedTicks = null;
        }
        if (failure) throw failure;
        return result;
    }

    // Allowed params per action. Unknown actions and unknown params are errors,
    // so a caller never believes an unsupported option (e.g. a misspelled
    // "tie") took effect.
    property var actionParams: ({
        "ping": [],
        "getScore": ["startMeasure", "endMeasure"],
        "getVersion": [],
        "getChangesSince": ["version"],
        "syncStateToSelection": [],
        "undo": ["steps"],
        "processSequence": ["sequence", "atomic"],
        "getCursorInfo": [],
        "setCursor": ["staff", "voice", "measure", "tick", "offset"],
        "goToMeasure": ["measure", "staff", "voice", "offset"],
        "goToBeginningOfScore": ["staff", "voice"],
        "goToFinalMeasure": ["staff", "voice"],
        "nextElement": ["numElements"],
        "prevElement": ["numElements"],
        "nextStaff": ["count"],
        "prevStaff": ["count"],
        "selectCurrentMeasure": ["allStaves", "staff", "voice", "measure", "tick"],
        "selectCustomRange": ["startTick", "endTick", "startStaff", "endStaff"],
        "addNote": ["pitch", "duration", "advanceCursorAfterAction", "addToChord", "tie", "staff", "voice", "measure", "tick", "offset"],
        "addRest": ["duration", "advanceCursorAfterAction", "staff", "voice", "measure", "tick", "offset"],
        "addTuplet": ["duration", "ratio", "advanceCursorAfterAction", "staff", "voice", "measure", "tick", "offset"],
        "writeVoice": ["events", "staff", "voice", "measure", "tick", "offset"],
        "addLyrics": ["lyrics", "verse", "staff", "voice", "measure", "tick", "offset"],
        "addDynamic": ["dynamic", "staff", "voice", "measure", "tick", "offset"],
        "addFermata": ["staff", "voice", "measure", "tick", "offset"],
        "setTempo": ["bpm", "beatUnit", "text", "measure", "tick", "offset"],
        "appendMeasure": ["count"],
        "insertMeasure": ["measure", "count"],
        "deleteSelection": ["measure", "staff"],
        "addRepeat": ["startMeasure", "endMeasure", "times"],
        "removeRepeat": ["startMeasure", "endMeasure"],
        "addMarker": ["type", "measure"],
        "addJump": ["type", "measure"],
        "addRehearsalMark": ["text", "measure", "tick", "offset"],
        "setKeySignature": ["fifths", "measure", "mode", "staff"],
        "addGradualTempoChange": ["type", "measure", "tick", "endMeasure", "endTick", "targetBpm", "factor", "aTempo", "offset"],
        "removeMarking": ["kind", "tick", "measure", "staff", "offset"],
        "addSlur": ["startTick", "endTick", "startMeasure", "endMeasure", "staff"],
        "addHairpin": ["type", "startTick", "endTick", "startMeasure", "endMeasure", "staff"],
        "addArticulation": ["type", "startTick", "endTick", "startMeasure", "endMeasure", "staff"],
        "deleteMeasures": ["startMeasure", "endMeasure"],
        "copyMeasures": ["startMeasure", "endMeasure", "toMeasure", "insert", "staff", "toStaff", "transpose"],
        "addInstrument": ["instrumentId", "position"],
        "setInstrumentName": ["staff", "part", "name", "shortName"],
        "removeInstrument": ["part", "staff"],
        "setStaffMute": ["staff", "mute"],
        "setInstrumentSound": ["staff", "instrumentId"],
        "setTimeSignature": ["numerator", "denominator", "measure"],
        "transpose": ["semitones", "startMeasure", "endMeasure", "startTick", "endTick", "staves", "voices", "chordSymbols", "keySignatures"],
        "clearRange": ["startMeasure", "endMeasure", "startTick", "endTick", "staves", "voices", "markings"],
        "replaceSection": ["startMeasure", "endMeasure", "parts", "clearOtherVoices"],
        "addText": ["text", "kind", "staff", "voice", "measure", "tick", "offset"],
        "addChordSymbol": ["text", "staff", "measure", "tick", "offset"],
        "addPedalMarks": ["startMeasure", "endMeasure", "startTick", "endTick", "staff"],
        "addClef": ["type", "staff", "measure", "tick", "offset"],
        "addLayoutBreak": ["type", "measure"],
        "setMeasuresPerSystem": ["count"],
        "setScoreInfo": ["title", "subtitle", "composer", "lyricist"],
        "exportScore": ["path", "format"],
        "saveScore": [],
        "redo": ["steps"],
        "getSelection": [],
        "checkScore": [],
        "openScore": ["path"]
    })

    // Throws unless the command is {action, params} with a known action and
    // only the params that action accepts. A top-level edit may also carry
    // expectedVersion (inside a sequence, only the sequence itself may).
    function checkCommand(command, topLevel) {
        if (!isPlainObject(command)) throw new Error("A command must be an object {action, params}");
        checkKeys(command, ["action", "params"], "Command");
        if (typeof command.action !== "string" || !hasKey(actionParams, command.action)) {
            throw new Error("Unknown command: " + command.action);
        }
        if (isSet(command.params) && !isPlainObject(command.params)) {
            throw new Error(command.action + ": params must be an object");
        }
        var allowed = actionParams[command.action];
        if (topLevel && isEdit(command)) allowed = allowed.concat(["expectedVersion"]);
        checkKeys(command.params || {}, allowed, command.action);
    }

    function processCommand(command) {
        checkCommand(command, false);
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
            case "getVersion":              return getVersion(params);
            case "getChangesSince":         return getChangesSince(params);
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
            case "setInstrumentName":       return setInstrumentName(params);
            case "setTimeSignature":        return setTimeSignature(params);

            // Range operations and score tools
            case "transpose":               return transposeRange(params);
            case "clearRange":              return clearRange(params);
            case "replaceSection":          return replaceSection(params);
            case "addText":                 return addText(params);
            case "addChordSymbol":          return addChordSymbol(params);
            case "addPedalMarks":           return addPedalMarks(params);
            case "addClef":                 return addClef(params);
            case "addLayoutBreak":          return addLayoutBreak(params);
            case "setMeasuresPerSystem":    return setMeasuresPerSystem(params);
            case "setScoreInfo":            return setScoreInfo(params);
            case "exportScore":             return exportScore(params);
            case "saveScore":               return saveScore(params);
            case "redo":                    return redo(params);
            case "getSelection":            return getSelection(params);
            case "checkScore":              return checkScore(params);
            case "openScore":               return openScore(params);

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
            redoCursorStack = [];
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

    // Position inside a bar: "0" or a fraction of a whole note ("3/8" = three
    // eighths after the barline).
    function parseOffset(value) {
        if (value === 0 || value === "0") return 0;
        return parseDuration(value, "offset");
    }

    // Resolves the target position of an action from its params, falling back
    // to the plugin cursor. Accepts tick, measure (1-based) with an optional
    // offset into the bar, staff and voice.
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
            if (isSet(params.offset)) throw new Error("Give offset with measure, not with tick");
            t.tick = params.tick;
        } else if (isSet(params.measure)) {
            var m = measureByNumber(params.measure);
            t.tick = m.startTick;
            if (isSet(params.offset)) {
                var off = parseOffset(params.offset);
                if (off >= m.endTick - m.startTick) {
                    throw new Error("offset " + ticksText(off) + " is past the end of bar " + m.number + " (" + m.numerator + "/" + m.denominator + ")");
                }
                t.tick += off;
            }
        } else if (isSet(params.offset)) {
            throw new Error("offset needs measure (it is the position inside that bar)");
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
        selectionFromUser = sig !== "";
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
        selectionFromUser = false;
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
        selectionFromUser = false;
        return ok;
    }

    // ========================================
    // ELEMENT PROCESSING
    // ========================================

    function processElement(element) {
        if (!element) return null;
        if (element.name !== "Chord" && element.name !== "Rest") return null;
        // A deleted rest of voices 2-4 stays as an invisible "gap": not music
        if (element.name === "Rest" && element.gap === true) return null;

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
            var tup = element.tuplet;
            base.tuplet = { actual: tup.actualNotes, normal: tup.normalNotes };
            // Which tuplet: its start and length (two triplets in a row are two groups)
            try {
                base.tuplet.startTick = tup.fraction.ticks;
                base.tuplet.ticks = tup.actualDuration.ticks;
            } catch (e) {}
            base.nominalTicks = element.duration.ticks;
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
                // tpc1 is the concert spelling, which matches pitch (note.tpc
                // follows the Concert Pitch button)
                var tpc = isSet(note.tpc1) ? note.tpc1 : note.tpc;
                base.notes.push({
                    pitchMidi: note.pitch,
                    tpc: tpc,
                    pitchName: getTpcName(tpc),
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
            redoCursorStack.push(copyCursor(cursorState));
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
        "setInstrumentSound", "setInstrumentName", "undo",
        "addRepeat", "removeRepeat", "addMarker", "addJump", "addRehearsalMark",
        "setKeySignature", "addGradualTempoChange", "removeMarking", "addSlur", "addHairpin",
        "addArticulation", "deleteMeasures", "copyMeasures",
        "transpose", "clearRange", "replaceSection", "addText", "addChordSymbol", "addPedalMarks", "addClef",
        "addLayoutBreak", "setMeasuresPerSystem", "setScoreInfo", "redo"
    ]

    // Actions that change the MuseScore selection before running a command
    // can't be grouped into one undo step (the selection only updates
    // between commands).
    property var nonAtomicCommands: [
        "undo", "deleteSelection", "insertMeasure", "selectCurrentMeasure", "selectCustomRange",
        "addSlur", "addHairpin", "addArticulation", "deleteMeasures", "copyMeasures", "processSequence", "redo",
        "setMeasuresPerSystem"
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

    // Note names: letter, accidentals (# ## b bb), octave; C4 is middle C (60).
    // Returns {pitch, tpc}: the tpc keeps the spelling (F#4 vs Gb4).
    function parseNoteName(name, label) {
        var m = /^([A-Ga-g])(##|#|bb|b)?(-1|[0-9])$/.exec(String(name).trim());
        if (!m) throw new Error(label + ": " + JSON.stringify(name) + " is not a MIDI pitch or a note name like C4, F#3, Bb5");
        var letter = m[1].toUpperCase();
        var alter = { "": 0, "#": 1, "##": 2, "b": -1, "bb": -2 }[m[2] || ""];
        var steps = { C: 0, D: 2, E: 4, F: 5, G: 7, A: 9, B: 11 };
        var baseTpc = { F: 13, C: 14, G: 15, D: 16, A: 17, E: 18, B: 19 };
        var pitch = (parseInt(m[3], 10) + 1) * 12 + steps[letter] + alter;
        if (pitch < 0 || pitch > 127) throw new Error(label + ": " + name + " is outside MIDI 0-127");
        return { pitch: pitch, tpc: baseTpc[letter] + 7 * alter };
    }

    // Pitches given as MIDI numbers or note names. Returns the MIDI pitches;
    // names are recorded in `spell` (pitch -> tpc).
    function checkPitchList(list, label, spell) {
        if (!Array.isArray(list) || list.length === 0) throw new Error(label + " must be a non-empty list of MIDI pitches or note names");
        var out = [];
        for (var i = 0; i < list.length; i++) {
            var p;
            if (typeof list[i] === "string") {
                var named = parseNoteName(list[i], label);
                p = named.pitch;
                if (spell) spell[p] = named.tpc;
            } else {
                p = checkInt(list[i], label + " entry", 0, 127);
            }
            if (out.indexOf(p) >= 0) throw new Error(label + " lists pitch " + p + " twice");
            out.push(p);
        }
        return out;
    }

    property var articulationSymbols: ({
        "staccato": "articStaccatoAbove", "staccatissimo": "articStaccatissimoAbove", "tenuto": "articTenutoAbove",
        "accent": "articAccentAbove", "marcato": "articMarcatoAbove", "portato": "articTenutoStaccatoAbove",
        "accent-staccato": "articAccentStaccatoAbove", "marcato-staccato": "articMarcatoStaccatoAbove",
        "stress": "articStressAbove", "unstress": "articUnstressAbove",
        "up-bow": "stringsUpBow", "down-bow": "stringsDownBow", "harmonic": "stringsHarmonic",
        "snap-pizzicato": "pluckedSnapPizzicatoAbove", "open": "brassMuteOpen", "stopped": "brassMuteClosed"
    })

    // Ornaments are their own element type (added to the chord like articulations)
    property var ornamentSymbols: ({
        "trill": "ornamentTrill", "mordent": "ornamentMordent", "short-trill": "ornamentShortTrill",
        "turn": "ornamentTurn", "inverted-turn": "ornamentTurnInverted"
    })

    // The markings an event may carry, checked.
    function parseMarks(ev, where, isRest) {
        var marks = {};
        if (isSet(ev.dynamic)) {
            var name = String(ev.dynamic).toLowerCase();
            var type = dynamicTypeValue(name);
            if (type === undefined || type === DynamicType.OTHER) throw new Error(where + ": unknown dynamic '" + ev.dynamic + "'");
            marks.dynamic = name;
        }
        if (isSet(ev.articulations)) {
            if (!Array.isArray(ev.articulations)) throw new Error(where + ": articulations must be a list");
            for (var i = 0; i < ev.articulations.length; i++) {
                var a = ev.articulations[i];
                if (a !== "fermata" && !hasKey(articulationSymbols, a) && !hasKey(ornamentSymbols, a)) {
                    throw new Error(where + ": unknown articulation '" + a + "' (known: fermata, " + Object.keys(articulationSymbols).join(", ") +
                                    ", " + Object.keys(ornamentSymbols).join(", ") + ")");
                }
                if (isRest && a !== "fermata") throw new Error(where + ": a rest can only have a fermata, not " + a);
            }
            marks.articulations = ev.articulations.slice();
        }
        if (isSet(ev.lyric)) {
            if (isRest) throw new Error(where + ": a rest can't have a lyric");
            if (typeof ev.lyric !== "string" || !ev.lyric.length) throw new Error(where + ": lyric must be a non-empty string");
            marks.lyric = ev.lyric;
        }
        ["text", "chord"].forEach(function(key) {
            if (isSet(ev[key])) {
                if (typeof ev[key] !== "string" || !ev[key].length) throw new Error(where + ": " + key + " must be a non-empty string");
                marks[key] = ev[key];
            }
        });
        return marks;
    }

    // "3:2" -> {actual: 3, normal: 2}
    function parseTupletRatio(value, where) {
        var m = /^\s*(\d+)\s*:\s*(\d+)\s*$/.exec(String(value));
        if (!m) throw new Error(where + ": tuplet must look like \"3:2\" (3 notes in the time of 2)");
        var a = parseInt(m[1], 10), n = parseInt(m[2], 10);
        if (a < 2 || n < 1 || a === n) throw new Error(where + ": tuplet " + value + " is not a tuplet ratio");
        return { actual: a, normal: n };
    }

    // Checks writeVoice events before anything is written. Returns events
    // {kind: "note"|"rest"|"tuplet", ...}: notes/rests with pitches, spell,
    // ticks, tie (tied pitches) and marks; tuplets with their inner events.
    function parseVoiceEvents(events, inTuplet) {
        if (!Array.isArray(events) || events.length === 0) throw new Error("events must be a non-empty list of notes, chords and rests");
        var out = [];
        for (var i = 0; i < events.length; i++) {
            var ev = events[i];
            var where = (inTuplet ? "Tuplet event " : "Event ") + i;
            if (!isPlainObject(ev)) throw new Error(where + " must be an object");
            if (isSet(ev.tuplet) && !inTuplet) {
                checkKeys(ev, ["tuplet", "events"], where);
                var ratio = parseTupletRatio(ev.tuplet, where);
                var inner = parseVoiceEvents(ev.events, true);
                var nominal = 0, actual = 0;
                for (var k = 0; k < inner.length; k++) {
                    if (noteValueTicks.indexOf(inner[k].ticks) < 0) {
                        throw new Error(where + ": inside a tuplet each duration must be one plain or dotted value, got " + ticksText(inner[k].ticks));
                    }
                    var act = inner[k].ticks * ratio.normal / ratio.actual;
                    if (!isInt(act)) throw new Error(where + ": " + ticksText(inner[k].ticks) + " in a " + ev.tuplet + " tuplet doesn't fall on whole ticks");
                    inner[k].actual = act;
                    nominal += inner[k].ticks;
                    actual += act;
                }
                // n:m holds n parts of one note value (the base), and lasts m of them
                var base = nominal / ratio.actual;
                if (noteValueTicks.indexOf(base) < 0 || noteValueTicks.indexOf(base * ratio.normal) < 0) {
                    throw new Error(where + ": the notes of a " + ev.tuplet + " tuplet must add up to " + ratio.actual +
                                    " times one note value (e.g. three 1/8 = 3/8 in 3:2, five 1/16 = 5/16 in 5:4), got " + ticksText(nominal));
                }
                for (k = 0; k < inner.length; k++) inner[k].label = "event " + i + " (note " + k + " of the tuplet)";
                out.push({ kind: "tuplet", ratio: ratio, inner: inner, ticks: actual, nominal: nominal });
                continue;
            }
            checkKeys(ev, ["pitches", "rest", "duration", "tie", "dynamic", "articulations", "lyric", "text", "chord"], where);
            if (!isSet(ev.duration)) throw new Error(where + ": missing duration");
            var ticks = parseDuration(ev.duration, where + " duration");
            if (isSet(ev.rest) && typeof ev.rest !== "boolean") throw new Error(where + ": rest must be true or false");
            if (ev.rest === true) {
                if (isSet(ev.pitches)) throw new Error(where + ": a rest can't have pitches");
                if (isSet(ev.tie) && ev.tie !== false) throw new Error(where + ": a rest can't be tied");
                out.push({ kind: "rest", rest: true, pitches: [], spell: {}, ticks: ticks, tie: [], marks: parseMarks(ev, where, true), label: "event " + i });
                continue;
            }
            if (!isSet(ev.pitches)) throw new Error(where + ": give \"pitches\" for a note/chord, or \"rest\": true for a rest");
            var spell = {};
            var pitches = checkPitchList(ev.pitches, where + " pitches", spell);
            var tie = [];
            if (ev.tie === true) {
                tie = pitches.slice();
            } else if (isSet(ev.tie) && ev.tie !== false) {
                tie = checkPitchList(ev.tie, where + " tie", null);
                for (var t = 0; t < tie.length; t++) {
                    if (pitches.indexOf(tie[t]) < 0) throw new Error(where + ": tie lists pitch " + tie[t] + ", which is not in its pitches");
                }
            }
            out.push({ kind: "note", rest: false, pitches: pitches, spell: spell, ticks: ticks, tie: tie, marks: parseMarks(ev, where, false), label: "event " + i });
        }
        if (!inTuplet) {
            // A tie continues into the next note/rest (inside or after a tuplet),
            // which must hold the pitch. A tie on the very last one goes to the
            // note written after the passage.
            var flat = flattenEvents(out);
            for (var j = 0; j + 1 < flat.length; j++) {
                if (!flat[j].tie.length) continue;
                var a = flat[j].label.charAt(0).toUpperCase() + flat[j].label.substring(1), b = flat[j + 1].label;
                if (flat[j + 1].rest) throw new Error(a + " is tied, but " + b + " is a rest");
                for (var p = 0; p < flat[j].tie.length; p++) {
                    if (flat[j + 1].pitches.indexOf(flat[j].tie[p]) < 0) {
                        throw new Error(a + " ties pitch " + flat[j].tie[p] + ", which " + b + " doesn't contain");
                    }
                }
            }
        }
        return out;
    }

    // Notes and rests in time order, with the notes of tuplets in place.
    function flattenEvents(events) {
        var flat = [];
        for (var i = 0; i < events.length; i++) {
            if (events[i].kind === "tuplet") flat = flat.concat(events[i].inner);
            else flat.push(events[i]);
        }
        return flat;
    }

    // ========================================
    // WRITING NOTES AND RESTS
    // One engine for writeVoice, addNote, addRest and replaceSection. Inside
    // a command: prepare the start, check the target, plan the pieces, write
    // them all through one cursor, read them back, then spelling, markings,
    // and the ties (made when the command ends). No UI work here.
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

    function fractionOfTicks(ticks) {
        var g = gcd(ticks, ticksPerWhole);
        return fraction(ticks / g, ticksPerWhole / g);
    }

    // The chord/rest of (staff, voice) that starts before `tick` and is still
    // sounding at it: {tick, end, el}, or null. Chords/rests never cross a
    // barline, so the search starts at the bar.
    function heldAt(staff, voice, tick) {
        var m = curScore.tick2measure(fractionFromTicks(tick));
        if (!m) return null;
        var c = makeCursor({ tick: m.tick.ticks, staff: staff, voice: voice });
        if (!c.segment) return null;
        if (!c.element) c.next();
        while (c.segment && c.tick < tick) {
            var el = c.element;
            var end = c.tick + el.actualDuration.ticks;
            if (end > tick) return { tick: c.tick, end: end, el: el };
            c.next();
        }
        return null;
    }

    // Shortens a note/rest held across t so that it ends at t. The part before
    // t stays: the same chord (with its lyrics and markings), continued by
    // tied notes if its new length isn't one note value. What followed
    // becomes rests, which the write then overwrites.
    // (It is only ever shortened to one note value: ChordRest.duration =
    // Score::changeCRlen, which for longer values fills a chord's head with
    // rests and overlaps a rest's fill rests.)
    function splitHeld(t, held) {
        var el = held.el;
        var head = t.tick - held.tick;
        var where = "staff " + t.staff + " voice " + t.voice;
        var isRest = el.type === Element.REST;
        touch(held.tick, held.end);
        var pitches = isRest ? [] : chordPitches(el);
        var spell = {};
        var notes = isRest ? [] : el.notes;
        for (var i = 0; i < notes.length; i++) spell[notes[i].pitch] = notes[i].tpc1;
        var firstLen = largestNoteValue(head);
        el.duration = fractionOfTicks(firstLen);
        if (firstLen < head) {
            writeEvents({ tick: held.tick + firstLen, staff: t.staff, voice: t.voice },
                        [{ kind: isRest ? "rest" : "note", rest: isRest, pitches: pitches, spell: spell, ticks: head - firstLen, tie: [], marks: {},
                           label: "held note" }], false);
            for (var p = 0; p < pitches.length; p++) queueTie(t, held.tick, pitches[p], held.tick + firstLen);
        }
        if (isRest) return [];      // shortening a rest to write after it is nothing to warn about
        return ["Split the note/chord [" + pitches.join(", ") + "] at tick " + held.tick + " (" + where + ", it lasted until tick " +
                held.end + "): the part before tick " + t.tick + " stays, the rest is overwritten"];
    }

    // Where voices 2-4 can start a rest that leads up to t: the latest point
    // at or before t where a chord/rest of any voice starts and this voice is
    // free up to t.
    function gapStart(t) {
        var m = curScore.tick2measure(fractionFromTicks(t.tick));
        var from = m.tick.ticks;
        var c0 = makeCursor({ tick: from, staff: t.staff, voice: 0 });
        while (c0.segment && c0.tick <= t.tick) {
            from = c0.tick;
            if (!c0.next()) break;
        }
        var c = makeCursor({ tick: m.tick.ticks, staff: t.staff, voice: t.voice });
        if (c.segment && !c.element) c.next();
        while (c.segment && c.tick < t.tick) {
            var end = c.tick + c.element.actualDuration.ticks;
            if (end > from) from = end;
            c.next();
        }
        if (from < t.tick && !curScore.findSegmentAtTick(Segment.ChordRest, fractionFromTicks(from))) {
            throw new Error("Voice " + t.voice + " of staff " + t.staff + " can't start at tick " + t.tick +
                            ": nothing starts at tick " + from + " to lead up to it. Write from an earlier position.");
        }
        return from;
    }

    // Makes a chord/rest boundary at t in its voice so that writing can start
    // there: splits a note/rest held across t, or (voices 2-4) starts the
    // empty stretch before t with a rest. Returns warnings.
    function prepareStart(t) {
        var track = t.staff * 4 + t.voice;
        var seg = t.tick < scoreEndTick() ? curScore.findSegmentAtTick(Segment.ChordRest, fractionFromTicks(t.tick)) : null;
        if (seg && seg.elementAt(track)) return [];
        var held = heldAt(t.staff, t.voice, t.tick);
        if (held) {
            if (held.el.tuplet) {
                throw new Error("Tick " + t.tick + " is inside a tuplet of staff " + t.staff + " voice " + t.voice +
                                " (it starts at tick " + held.tick + "); writing into the middle of a tuplet isn't supported");
            }
            return splitHeld(t, held);
        }
        if (seg) return [];      // this voice is empty here; MuseScore fills it with rests
        if (t.voice === 0) throw new Error("Internal: nothing of voice 1 at tick " + t.tick + " on staff " + t.staff);
        var from = gapStart(t);
        if (from < t.tick) {
            writeEvents({ tick: from, staff: t.staff, voice: t.voice },
                        [{ kind: "rest", rest: true, pitches: [], spell: {}, ticks: t.tick - from, tie: [], marks: {} }], false);
        }
        return [];
    }

    // Checks that [t.tick, endTick) of the target voice can be overwritten
    // (no tuplet in it). Returns warnings about what the overwrite changes nearby.
    function checkWriteTarget(t, endTick) {
        var where = "staff " + t.staff + " voice " + t.voice;
        var warnings = [];
        var w = makeCursor(t);
        requireSegment(w, t);
        if (!w.element) w.next();
        var firstChecked = false;
        while (w.segment && w.tick < endTick) {
            var el = w.element;
            if (el.tuplet) {
                throw new Error("There is a tuplet at tick " + w.tick + " in " + where +
                                "; writing over tuplets isn't supported yet (fill tuplets with add_note, or clear_range first)");
            }
            if (!firstChecked && w.tick === t.tick && el.type === Element.CHORD && hasTie(el, true)) {
                warnings.push("The tie into the overwritten note at tick " + t.tick + " was removed");
            }
            firstChecked = true;
            var end = w.tick + el.actualDuration.ticks;
            if (end > endTick && el.type === Element.CHORD) {
                warnings.push("The note/chord at tick " + w.tick + " lasted until tick " + end + "; after tick " + endTick + " it is now a rest (not re-struck)");
            } else if (end === endTick && el.type === Element.CHORD && hasTie(el, false)) {
                warnings.push("The tie from the overwritten note at tick " + w.tick + " to tick " + endTick + " was removed");
            }
            w.next();
        }
        return warnings;
    }

    // Throws unless `el` (read back at `tick`) is exactly the requested piece.
    function checkWritten(el, ev, ticks, tick, t, inTuplet, actual) {
        var want = (ev.rest ? "rest" : "chord [" + ev.pitches.join(", ") + "]") + " of " + ticksText(ticks);
        var got;
        if (!el) {
            got = "nothing";
        } else {
            got = (el.type === Element.CHORD ? "chord [" + chordPitches(el).join(", ") + "]" : el.type === Element.REST ? "rest" : el.name) +
                  " of " + ticksText(el.duration.ticks) + (el.tuplet ? " in a tuplet" : "");
            var ok = el.type === (ev.rest ? Element.REST : Element.CHORD) && el.duration.ticks === ticks &&
                     (inTuplet ? !!el.tuplet : !el.tuplet) && el.actualDuration.ticks === (isSet(actual) ? actual : ticks);
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

    // Spells the notes of the chord at `tick` as named (pitch -> tpc). The
    // transposing (written) spelling moves along with the concert one.
    function applySpelling(track, tick, spell) {
        var el = elementAt(track, tick);
        if (!el || el.type !== Element.CHORD) return;
        var notes = el.notes;
        for (var i = 0; i < notes.length; i++) {
            var want = spell[notes[i].pitch];
            if (want === undefined || notes[i].tpc1 === want) continue;
            var shift = notes[i].tpc2 - notes[i].tpc1;
            var tpc2 = want + shift;
            while (tpc2 > 33) tpc2 -= 12;
            while (tpc2 < -1) tpc2 += 12;
            notes[i].tpc1 = want;
            notes[i].tpc2 = tpc2;
        }
    }

    // Markings hang on a chord/rest segment. If none starts at t, a rest of
    // voice 0 held across t is split there (a note is not: that would
    // change the music). Inside a command.
    function ensureSegment(t) {
        if (t.tick < scoreEndTick() && curScore.findSegmentAtTick(Segment.ChordRest, fractionFromTicks(t.tick))) return;
        var held = t.tick < scoreEndTick() ? heldAt(t.staff, 0, t.tick) : null;
        if (held && held.el.type === Element.REST && !held.el.tuplet) {
            splitHeld({ tick: t.tick, staff: t.staff, voice: 0 }, held);
            return;
        }
        throw new Error("No note or rest starts at tick " + t.tick + " on staff " + t.staff +
                        (held ? " (the note from tick " + held.tick + " is still sounding there)" : "") +
                        ". Put it where a note or rest starts.");
    }

    // Adds a dynamic under the chord/rest at t (replacing one there).
    function putDynamic(t, name) {
        ensureSegment(t);
        var c = makeCursor(t);
        requireSegment(c, t);
        var old = findAnnotation(c.segment, Element.DYNAMIC, t.staff * 4 + t.voice);
        if (old) removeElement(old);
        var dyn = newElement(Element.DYNAMIC);
        dyn.text = dynamicSymbols(name);
        dyn.dynamicType = dynamicTypeValue(name);
        c.add(dyn);
    }

    // Adds a text-like element (expression/staff/system text, chord symbol) at t.
    // A chord symbol gets its text only once it is in the score: setting the
    // text of a Harmony that has no parent yet crashes MuseScore 4.7.5
    // (Harmony::setProperty(TEXT) calls explicitParent()->isFretDiagram(), and
    // explicitParent() is null until the element is added).
    function putText(t, elementType, text) {
        ensureSegment(t);
        var c = makeCursor(t);
        requireSegment(c, t);
        var el = newElement(elementType);
        if (elementType === Element.HARMONY) {
            c.add(el);
            el.text = text;
            return;
        }
        el.text = text;
        c.add(el);
    }

    function putArticulations(t, names) {
        var c = makeCursor(t);
        requireSegment(c, t);
        for (var i = 0; i < names.length; i++) {
            if (names[i] === "fermata") {
                if (!findAnnotation(c.segment, Element.FERMATA, t.staff * 4 + t.voice)) c.add(newElement(Element.FERMATA));
                continue;
            }
            var orn = hasKey(ornamentSymbols, names[i]);
            var art = newElement(orn ? Element.ORNAMENT : Element.ARTICULATION);
            art.symbol = SymId[orn ? ornamentSymbols[names[i]] : articulationSymbols[names[i]]];
            c.add(art);
        }
    }

    function putLyric(t, text, syllabic, verse) {
        var c = makeCursor(t);
        requireSegment(c, t);
        var el = c.element;
        var existing = el.lyrics;
        for (var k = existing.length - 1; k >= 0; k--) {
            if (existing[k].verse === verse) removeElement(existing[k]);
        }
        var lyr = newElement(Element.LYRICS);
        lyr.text = text;
        lyr.syllabic = syllabic;
        lyr.verse = verse;
        c.add(lyr);
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
            if (!allowTuplet || events.length !== 1 || events[0].kind === "tuplet") {
                throw new Error("Tick " + t.tick + " is inside a tuplet; write_voice can't write into tuplets yet (fill them with add_note)");
            }
            return writeInTuplet(t, events[0]);
        }

        ensureRoom(t.tick, total);
        var warnings = prepareStart(t);
        var endTick = t.tick + total;
        warnings = warnings.concat(checkWriteTarget(t, endTick));
        var bars = barsCovering(t.tick, endTick);

        // Plan: each event becomes units: the pieces of a split note, or the
        // notes of a tuplet.
        var units = [];
        var splits = [];
        var pos = t.tick;
        for (i = 0; i < events.length; i++) {
            var ev = events[i];
            if (ev.kind === "tuplet") {
                var bar = null;
                for (var b = 0; b < bars.length; b++) {
                    if (bars[b].startTick <= pos && pos < bars[b].endTick) bar = bars[b];
                }
                if (!bar || pos + ev.ticks > bar.endTick) {
                    throw new Error("The " + ev.ratio.actual + ":" + ev.ratio.normal + " tuplet at tick " + pos + " would cross a barline");
                }
                var p = pos;
                for (var k = 0; k < ev.inner.length; k++) {
                    var inner = ev.inner[k];
                    inner.units = [{ ev: inner, tick: p, ticks: inner.actual, nominal: inner.ticks, tuplet: ev, firstOfTuplet: k === 0 }];
                    units.push(inner.units[0]);
                    p += inner.actual;
                }
            } else {
                var pieces = planPieces(pos, ev.ticks, bars);
                if (pieces.length > 1) {
                    splits.push({ event: i, duration: ticksText(ev.ticks),
                                  writtenAs: pieces.map(function(piece) { return ticksText(piece.ticks); }) });
                }
                ev.units = [];
                for (var q = 0; q < pieces.length; q++) {
                    var u = { ev: ev, tick: pieces[q].tick, ticks: pieces[q].ticks, nominal: pieces[q].ticks, tuplet: null };
                    ev.units.push(u);
                    units.push(u);
                }
            }
            pos += ev.ticks;
        }

        // Write through one cursor: after each piece MuseScore moves it to the
        // next position, which must be where the next piece starts.
        var c = makeCursor(t);
        for (i = 0; i < units.length; i++) {
            u = units[i];
            if (!c.segment || c.tick !== u.tick) {
                throw new Error("MuseScore moved the write position to tick " + (c.segment ? c.tick : "none") + " instead of " +
                                u.tick + " (staff " + t.staff + " voice " + t.voice + "). Nothing was written.");
            }
            if (u.firstOfTuplet) {
                c.addTuplet(fraction(u.tuplet.ratio.actual, u.tuplet.ratio.normal), fractionOfTicks(u.tuplet.ticks));
                if (!c.segment || c.tick !== u.tick) throw new Error("MuseScore did not create the tuplet at tick " + u.tick + ". Nothing was written.");
            }
            writePiece(c, u.ev, u.nominal);
        }

        // Read everything back once: MuseScore must not have changed a duration.
        for (i = 0; i < units.length; i++) {
            u = units[i];
            checkWritten(elementAt(track, u.tick), u.ev, u.nominal, u.tick, t, !!u.tuplet, u.ticks);
        }

        // Spelling, then markings on the first piece of each note/rest.
        var flat = flattenEvents(events);
        var inWord = false;
        for (i = 0; i < flat.length; i++) {
            ev = flat[i];
            var at = { tick: ev.units[0].tick, staff: t.staff, voice: t.voice };
            if (Object.keys(ev.spell).length) {
                for (q = 0; q < ev.units.length; q++) applySpelling(track, ev.units[q].tick, ev.spell);
            }
            var marks = ev.marks;
            if (marks.dynamic) putDynamic(at, marks.dynamic);
            if (marks.articulations) putArticulations(at, marks.articulations);
            if (marks.text) putText(at, Element.EXPRESSION, marks.text);
            if (marks.chord) putText(at, Element.HARMONY, marks.chord);
            if (marks.lyric) {
                var raw = marks.lyric;
                if (raw !== "_") {
                    var cont = raw.length > 1 && raw.charAt(raw.length - 1) === "-";
                    var text = cont ? raw.substring(0, raw.length - 1) : raw;
                    putLyric(at, text, inWord ? (cont ? Lyrics.MIDDLE : Lyrics.END) : (cont ? Lyrics.BEGIN : Lyrics.SINGLE), 0);
                    inWord = cont;
                }
            }
        }

        // Ties between the pieces of a split note, and where a note asks for one.
        var ties = 0;
        for (i = 0; i < flat.length; i++) {
            ev = flat[i];
            if (ev.rest) continue;
            for (q = 0; q + 1 < ev.units.length; q++) {
                for (var n = 0; n < ev.pitches.length; n++) {
                    queueTie(t, ev.units[q].tick, ev.pitches[n], ev.units[q + 1].tick);
                    ties++;
                }
            }
            var last = ev.units[ev.units.length - 1];
            for (n = 0; n < ev.tie.length; n++) {
                queueTie(t, last.tick, ev.tie[n], last.tick + last.ticks);
                ties++;
            }
        }

        touch(t.tick, endTick);
        var lastUnit = units[units.length - 1];
        commitProbe = function() { return contentSignature(track, lastUnit.tick); };
        var lastEvent = flat[flat.length - 1];
        return {
            startTick: t.tick, endTick: endTick, written: units.length, ties: ties, splits: splits, warnings: warnings,
            lastChord: lastEvent.rest || lastUnit.tuplet ? null : { tick: lastEvent.units[0].tick, staff: t.staff, voice: t.voice, pieces: lastEvent.units.length }
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
        checkWritten(el, ev, ev.ticks, t.tick, t, true, el ? el.actualDuration.ticks : null);
        var endTick = t.tick + el.actualDuration.ticks;
        if (Object.keys(ev.spell).length) applySpelling(track, t.tick, ev.spell);
        for (var p = 0; p < ev.tie.length; p++) queueTie(t, t.tick, ev.tie[p], endTick);
        touch(t.tick, endTick);
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
    // rests, tuplets, ties and markings, one after another from the start.
    function writeVoice(params) {
        if (!curScore) return { error: "No score open" };
        var events = parseVoiceEvents(params.events, false);

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
        var spell = {};
        var pitch;
        if (typeof params.pitch === "string") {
            pitch = checkPitchList([params.pitch], "pitch", spell)[0];
        } else {
            if (!(isInt(params.pitch) && params.pitch >= 0 && params.pitch <= 127)) return { error: "Pitch must be a MIDI value 0-127 or a note name like C4" };
            pitch = params.pitch;
        }
        var advance = boolParam(params, "advanceCursorAfterAction", true);
        var tie = boolParam(params, "tie", false);
        if (boolParam(params, "addToChord", false)) return addPitchToChord(params, pitch, spell, tie);
        if (!isSet(params.duration)) return { error: "Missing required parameters: duration" };
        var ev = { kind: "note", rest: false, pitches: [pitch], spell: spell, ticks: parseDuration(params.duration, "duration"),
                   tie: tie ? [pitch] : [], marks: {} };

        return mutate(function() {
            var t = resolveTarget(params);
            var info = writeEvents(t, [ev], true);
            cursorState = { tick: advance ? info.endTick : t.tick, staff: t.staff, voice: t.voice, lastChord: info.lastChord };
            return writeResult("Note " + pitch + " added at tick " + t.tick + " on staff " + t.staff + " voice " + t.voice +
                               (tie ? ", tied to the next note" : ""), info);
        });
    }

    // Adds a pitch to the chord just written (or the chord at tick/measure).
    function addPitchToChord(params, pitch, spell, tie) {
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
            note.pitch = pitch;
            chord.add(note);
            var track = t.staff * 4 + t.voice;
            if (Object.keys(spell).length) applySpelling(track, chordTick, spell);
            var end = chordTick + chord.actualDuration.ticks;
            if (tie) queueTie(t, chordTick, pitch, end);
            touch(chordTick, end);
            // The write position doesn't move; only staff/voice may change.
            cursorState = { tick: isSet(params.tick) || isSet(params.measure) ? t.tick : cursorState.tick,
                            staff: t.staff, voice: t.voice,
                            lastChord: { tick: chordTick, staff: t.staff, voice: t.voice, pieces: 1 } };
            return { message: "Added pitch " + pitch + " to chord at tick " + chordTick + (tie ? ", tied to the next note" : "") };
        });
    }

    function addRest(params) {
        if (!curScore) return { error: "No score open" };
        var validation = validateParams(params, ["duration"]);
        if (!validation.valid) return validation;
        var advance = boolParam(params, "advanceCursorAfterAction", true);
        var ev = { kind: "rest", rest: true, pitches: [], spell: {}, ticks: parseDuration(params.duration, "duration"), tie: [], marks: {} };

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

    // ========================================
    // RANGE OPERATIONS: transpose, clear, replace
    // Each is one command (one undo step) and can be part of an atomic batch.
    // ========================================

    // [startTick, endTick) from startMeasure/endMeasure (whole bars, both
    // inclusive) or from startTick/endTick.
    function tickRange(params) {
        if (isSet(params.startTick) || isSet(params.endTick)) {
            if (isSet(params.startMeasure) || isSet(params.endMeasure)) {
                throw new Error("Give the range as bars (startMeasure/endMeasure) or as ticks (startTick/endTick), not both");
            }
            var end = scoreEndTick();
            var st = checkInt(params.startTick, "startTick", 0, end - 1);
            var et = checkInt(params.endTick, "endTick", st + 1, end);
            return { startTick: st, endTick: et, label: "ticks " + st + "-" + et };
        }
        if (!isSet(params.startMeasure)) throw new Error("Give the range: startMeasure/endMeasure (bars) or startTick/endTick");
        var r = barRange(params);
        return { startTick: r.startTick, endTick: r.endTick, label: "bars " + r.first.number + "-" + r.last.number, bars: r };
    }

    function indexList(value, label, max) {
        if (!Array.isArray(value) || !value.length) throw new Error(label + " must be a non-empty list");
        var out = [];
        for (var i = 0; i < value.length; i++) {
            checkInt(value[i], label + " entry", 0, max);
            if (out.indexOf(value[i]) >= 0) throw new Error(label + " lists " + value[i] + " twice");
            out.push(value[i]);
        }
        return out.sort(function(a, b) { return a - b; });
    }

    // staves: a list of staff indices (default: all staves)
    function staffList(params) {
        if (isSet(params.staves)) return indexList(params.staves, "staves", curScore.nstaves - 1);
        var all = [];
        for (var s = 0; s < curScore.nstaves; s++) all.push(s);
        return all;
    }

    // voices: a list of voices 0-3 (default: all)
    function voiceList(params) {
        return isSet(params.voices) ? indexList(params.voices, "voices", 3) : [0, 1, 2, 3];
    }

    // The chords/rests of one voice that start in [startTick, endTick): [{tick, el}]
    function voiceElements(staff, voice, startTick, endTick) {
        var out = [];
        var m = curScore.tick2measure(fractionFromTicks(startTick));
        if (!m) return out;
        var c = makeCursor({ tick: m.tick.ticks, staff: staff, voice: voice });
        if (!c.segment) return out;
        if (!c.element) c.next();
        while (c.segment && c.tick < endTick) {
            if (c.tick >= startTick) out.push({ tick: c.tick, el: c.element });
            c.next();
        }
        return out;
    }

    // tpc change for a transposition by n semitones (C -> Db for +1, C -> D for +2, ...)
    property var tpcShiftBySemitone: [0, -5, 2, -3, 4, -1, 6, 1, -4, 3, -2, 5]

    // Concert key (fifths) of a staff at tick. Staff.key() is the written
    // key (KeySigEvent::key); for a transposing instrument the concert key is
    // that moved back by the instrument's interval: 7 * chromatic - 12 * diatonic
    // fifths (Bb clarinet: written D major = concert C major).
    function concertKeyAt(staffIdx, tick) {
        var st = staffObj(staffIdx);
        var f = fractionFromTicks(tick);
        var k = st.key(f);
        try {
            var iv = st.transpose(f);
            if (iv && (iv.chromatic || iv.diatonic)) k += 7 * iv.chromatic - 12 * iv.diatonic;
        } catch (e) {}
        return normalizeKey(k);
    }

    // A key in -7..7, preferring the spelling with at most 6 accidentals
    function normalizeKey(k) {
        while (k > 6) k -= 12;
        while (k < -6) k += 12;
        return k;
    }

    // Adds a key signature (concert fifths) to one staff at a bar start.
    // Setting the mode or transposing a key signature that isn't on a staff yet
    // crashes MuseScore: it is added first, then set again (so a transposing
    // instrument gets its written key).
    function addKeySig(staffIdx, tick, fifths, mode) {
        var ks = newElement(Element.KEYSIG);
        ks.concertKey = fifths;
        var c = makeCursor({ tick: tick, staff: staffIdx, voice: 0 });
        requireSegment(c, { tick: tick, staff: staffIdx });
        c.add(ks);
        ks.concertKey = fifths;
        if (mode === "minor") ks.keysig_mode = KeyMode.MINOR;
        else if (mode === "major") ks.keysig_mode = KeyMode.MAJOR;
    }

    // Chord symbol text moved by `shift` fifths: root and bass ("F#m7b5/A" +2
    // semitones -> "G#m7b5/B"). Roots are spelled Gb..A# (never Fb, Cb, E#, B#).
    // null if the text doesn't start with a note name.
    function transposeChordText(text, shift) {
        var letters = { F: 13, C: 14, G: 15, D: 16, A: 17, E: 18, B: 19 };
        function move(letter, acc) {
            var alter = { "": 0, "#": 1, "##": 2, "b": -1, "bb": -2, "\u266f": 1, "\u266d": -1 }[acc || ""];
            var tpc = letters[letter] + 7 * alter + shift;
            while (tpc > 24) tpc -= 12;
            while (tpc < 8) tpc += 12;
            return getTpcName(tpc);
        }
        var m = /^([A-G])(##|#|bb|b|\u266f|\u266d)?(.*)$/.exec(String(text));
        if (!m) return null;
        var rest = m[3];
        var bass = /\/([A-G])(##|#|bb|b|\u266f|\u266d)?$/.exec(rest);
        if (bass) rest = rest.substring(0, rest.length - bass[0].length) + "/" + move(bass[1], bass[2]);
        return move(m[1], m[2]) + rest;
    }

    // [{ann, tick}] of the given annotation types on the staves in [startTick, endTick)
    function annotationsWithTicks(startTick, endTick, staves, types) {
        var out = [];
        var m = curScore.tick2measure(fractionFromTicks(startTick));
        while (m && m.tick.ticks < endTick) {
            var seg = m.firstSegment;
            while (seg) {
                if (seg.tick >= startTick && seg.tick < endTick) {
                    var anns = seg.annotations;
                    for (var a = 0; a < anns.length; a++) {
                        var ann = anns[a];
                        if (types.indexOf(ann.type) >= 0 && ann.track >= 0 && staves.indexOf(Math.floor(ann.track / 4)) >= 0) {
                            out.push({ ann: ann, tick: seg.tick });
                        }
                    }
                }
                seg = seg.nextInMeasure;
            }
            m = m.nextMeasure;
        }
        return out;
    }

    // Transposes every note in the range (on the given staves and voices) by
    // `semitones`. Notes tied into or out of the range move with it.
    // Key signatures and chord symbols are left as they are.
    function transposeRange(params) {
        var validation = validateParams(params, ["semitones"]);
        if (!validation.valid) return validation;
        if (!curScore) return { error: "No score open" };
        var semis = checkInt(params.semitones, "semitones", -48, 48);
        if (semis === 0) return { error: "semitones must not be 0" };
        var range = tickRange(params);
        var staves = staffList(params);
        var voices = voiceList(params);
        var chords = boolParam(params, "chordSymbols", true);
        var keys = boolParam(params, "keySignatures", false);
        var shift = tpcShiftBySemitone[((semis % 12) + 12) % 12];
        if (keys) {
            var startBar = measureAtTick(range.startTick);
            var endBar = measureAtTick(Math.max(range.startTick, range.endTick - 1));
            if (startBar.startTick !== range.startTick || (range.endTick !== endBar.endTick)) {
                return { error: "keySignatures needs whole bars (startMeasure/endMeasure): key signatures sit at barlines" };
            }
        }

        return mutate(function() {
            var notes = [];
            var seen = {};
            var extendedTo = [];
            function take(n, inRange) {
                var key = n.track + ":" + elementTick(n) + ":" + n.pitch;
                if (seen[key]) return false;
                seen[key] = true;
                notes.push(n);
                if (!inRange) extendedTo.push(elementTick(n));
                return true;
            }
            for (var s = 0; s < staves.length; s++) {
                for (var v = 0; v < voices.length; v++) {
                    var items = voiceElements(staves[s], voices[v], range.startTick, range.endTick);
                    for (var i = 0; i < items.length; i++) {
                        if (items[i].el.type !== Element.CHORD) continue;
                        var chordNotes = items[i].el.notes;
                        for (var k = 0; k < chordNotes.length; k++) {
                            var n = chordNotes[k];
                            take(n, true);
                            // follow ties out of the range, both ways
                            var b = n;
                            while (b.tieBack && b.tieBack.startNote) { b = b.tieBack.startNote; if (!take(b, elementTick(b) >= range.startTick)) break; }
                            var f = n;
                            while (f.tieForward && f.tieForward.endNote) { f = f.tieForward.endNote; if (!take(f, elementTick(f) < range.endTick)) break; }
                        }
                    }
                }
            }
            for (i = 0; i < notes.length; i++) {
                var np = notes[i].pitch + semis;
                if (np < 0 || np > 127) throw new Error("Transposing pitch " + notes[i].pitch + " at tick " + elementTick(notes[i]) + " by " + semis + " leaves MIDI 0-127. Nothing was changed.");
            }
            var minTick = range.startTick, maxTick = range.endTick;
            for (i = 0; i < notes.length; i++) {
                n = notes[i];
                var tick = elementTick(n);
                minTick = Math.min(minTick, tick);
                maxTick = Math.max(maxTick, tick);
                var old1 = n.tpc1, old2 = n.tpc2;
                var new1 = old1 + tpcShiftBySemitone[((semis % 12) + 12) % 12];
                while (new1 > 26) new1 -= 12;
                while (new1 < 6) new1 += 12;
                var new2 = old2 + (new1 - old1);
                while (new2 > 33) new2 -= 12;
                while (new2 < -1) new2 += 12;
                n.pitch = n.pitch + semis;
                n.tpc1 = new1;
                n.tpc2 = new2;
            }
            touch(minTick, maxTick);
            var warnings = [];
            if (extendedTo.length) warnings.push("Tied notes outside the range moved too (ticks " + extendedTo.sort(function(a, b) { return a - b; }).join(", ") + ")");
            var harmonies = annotationsWithTicks(range.startTick, range.endTick, staves, [Element.HARMONY]);
            var movedChords = 0;
            if (chords) {
                for (i = 0; i < harmonies.length; i++) {
                    var h = harmonies[i];
                    var oldText = stripTags(h.ann.text);
                    var newText = transposeChordText(oldText, shift);
                    if (newText === null) {
                        warnings.push("Chord symbol '" + oldText + "' at tick " + h.tick + " was not transposed (no root note found)");
                        continue;
                    }
                    var hStaff = Math.floor(h.ann.track / 4);
                    removeElement(h.ann);
                    putText({ tick: h.tick, staff: hStaff, voice: 0 }, Element.HARMONY, newText);
                    movedChords++;
                }
            } else if (harmonies.length) {
                warnings.push(harmonies.length + " chord symbol(s) in the range were not transposed");
            }
            var keyChanges = 0;
            if (keys) {
                for (s = 0; s < staves.length; s++) {
                    // the keys in effect before anything changes
                    var bars = barsCovering(range.startTick, range.endTick);
                    var plan = [];
                    var prev = null;
                    for (var b = 0; b < bars.length; b++) {
                        var k = concertKeyAt(staves[s], bars[b].startTick);
                        if (k !== prev) plan.push({ tick: bars[b].startTick, key: normalizeKey(k + shift) });
                        prev = k;
                    }
                    var restore = range.endTick < scoreEndTick() ? concertKeyAt(staves[s], range.endTick) : null;
                    for (var q = 0; q < plan.length; q++) {
                        addKeySig(staves[s], plan[q].tick, plan[q].key);
                        keyChanges++;
                    }
                    if (restore !== null && restore !== plan[plan.length - 1].key) {
                        addKeySig(staves[s], range.endTick, restore);
                        warnings.push("Staff " + staves[s] + ": the key before transposing (" + keyName(restore) + ") is restored at tick " + range.endTick);
                    }
                }
                minTick = Math.min(minTick, range.startTick);
                maxTick = Math.max(maxTick, range.endTick);
                touch(minTick, maxTick);
            }
            var r = { message: "Transposed " + notes.length + " note(s) in " + range.label + " by " + semis + " semitone(s)" +
                               (movedChords ? ", " + movedChords + " chord symbol(s)" : "") +
                               (keys ? ", key signatures (" + keyChanges + ")" : ""), notes: notes.length };
            if (warnings.length) r.warnings = warnings;
            return r;
        });
    }

    // Staff-bound markings (dynamics, texts, chord symbols, fermatas) of the
    // given types on the staves in [startTick, endTick).
    function staffAnnotations(startTick, endTick, staves, types) {
        var out = [];
        var m = curScore.tick2measure(fractionFromTicks(startTick));
        while (m && m.tick.ticks < endTick) {
            var seg = m.firstSegment;
            while (seg) {
                if (seg.tick >= startTick && seg.tick < endTick) {
                    var anns = seg.annotations;
                    for (var a = 0; a < anns.length; a++) {
                        var ann = anns[a];
                        if (types.indexOf(ann.type) >= 0 && ann.track >= 0 && staves.indexOf(Math.floor(ann.track / 4)) >= 0) out.push(ann);
                    }
                }
                seg = seg.nextInMeasure;
            }
            m = m.nextMeasure;
        }
        return out;
    }

    function countAnnotations(startTick, endTick, staves, types) {
        return staffAnnotations(startTick, endTick, staves, types).length;
    }

    // Clears one voice in [startTick, endTick): a note held across the start
    // keeps its part before it; everything starting inside is removed (voice
    // 1 becomes rests, voices 2-4 become empty). A note that starts inside and
    // lasts past the end is removed entirely: its remainder becomes a rest.
    function clearVoice(staff, voice, startTick, endTick, warnings) {
        var track = staff * 4 + voice;
        var held = heldAt(staff, voice, startTick);
        if (held) {
            if (held.el.tuplet) {
                throw new Error("The range starts inside a tuplet (staff " + staff + " voice " + voice + ", tick " + held.tick + "); start it at the tuplet");
            }
            warnings.push.apply(warnings, splitHeld({ tick: startTick, staff: staff, voice: voice }, held));
        }
        var items = voiceElements(staff, voice, startTick, endTick);
        var removed = 0;
        for (var i = 0; i < items.length; i++) {
            var tick = items[i].tick;
            var el = elementAt(track, tick);
            if (!el) continue;
            if (el.type === Element.CHORD) {
                var end = tick + el.actualDuration.ticks;
                if (end > endTick) {
                    warnings.push("The note/chord at tick " + tick + " (staff " + staff + " voice " + voice + ") lasted until tick " + end +
                                  "; it was removed entirely, so after tick " + endTick + " there is a rest (not a re-struck note)");
                }
                removeElement(el);          // becomes a rest of the same length
                removed++;
                el = voice > 0 ? elementAt(track, tick) : null;
            }
            if (voice > 0 && el && el.type === Element.REST && el.gap !== true) removeElement(el);   // becomes a gap
        }
        return removed;
    }

    // Voice 1 of whole bars inside the range: one rest per bar instead of
    // the rests the removed notes left behind.
    function restWholeBars(staff, startTick, endTick) {
        var bars = barsCovering(startTick, endTick);
        for (var b = 0; b < bars.length; b++) {
            if (bars[b].startTick < startTick || bars[b].endTick > endTick) continue;
            var items = voiceElements(staff, 0, bars[b].startTick, bars[b].endTick);
            if (items.length <= 1) continue;
            writeEvents({ tick: bars[b].startTick, staff: staff, voice: 0 },
                        [{ kind: "rest", rest: true, pitches: [], spell: {}, ticks: bars[b].endTick - bars[b].startTick, tie: [], marks: {}, label: "rest" }], false);
        }
    }

    property var clearedMarkingTypes: [Element.DYNAMIC, Element.STAFF_TEXT, Element.EXPRESSION, Element.HARMONY, Element.FERMATA]

    function clearRange(params) {
        if (!curScore) return { error: "No score open" };
        var range = tickRange(params);
        var staves = staffList(params);
        var voices = voiceList(params);
        var markings = boolParam(params, "markings", true);

        return mutate(function() {
            var warnings = [];
            var removed = 0;
            for (var s = 0; s < staves.length; s++) {
                for (var v = 0; v < voices.length; v++) removed += clearVoice(staves[s], voices[v], range.startTick, range.endTick, warnings);
                if (voices.indexOf(0) >= 0) restWholeBars(staves[s], range.startTick, range.endTick);
            }
            var marks = 0;
            if (markings) {
                var anns = staffAnnotations(range.startTick, range.endTick, staves, clearedMarkingTypes);
                for (var a = 0; a < anns.length; a++) removeElement(anns[a]);
                marks = anns.length;
            }
            touch(range.startTick, range.endTick);
            var r = { message: "Cleared " + range.label + " on staves " + staves.join(", ") + " (voices " + voices.join(", ") +
                               "): " + removed + " note(s)/chord(s)" + (markings ? ", " + marks + " marking(s)" : "") };
            if (warnings.length) r.warnings = warnings;
            return r;
        });
    }

    // Replaces bars startMeasure..endMeasure with new music, in one command:
    // parts [{staff, voice, events}] each fill the whole section; the other
    // voices of those staves are cleared (unless clearOtherVoices is false).
    // Bars past the end of the score are appended (in the last bar's time
    // signature), so new music for several staves can be added at the end.
    function replaceSection(params) {
        if (!curScore) return { error: "No score open" };
        if (!isSet(params.startMeasure)) return { error: "Missing required parameters: startMeasure" };
        var count = listMeasures().length;
        var firstBar = checkInt(params.startMeasure, "startMeasure", 1, count + 1);
        var lastBar = isSet(params.endMeasure) ? checkInt(params.endMeasure, "endMeasure", firstBar, count + 1000) : firstBar;
        if (!Array.isArray(params.parts) || !params.parts.length) return { error: "parts must be a non-empty list of {staff, voice, events}" };
        var clearOthers = boolParam(params, "clearOtherVoices", true);
        var parts = [];
        var used = {};
        for (var i = 0; i < params.parts.length; i++) {
            var part = params.parts[i];
            var where = "Part " + i;
            if (!isPlainObject(part)) throw new Error(where + " must be an object {staff, voice, events}");
            checkKeys(part, ["staff", "voice", "events"], where);
            var staff = checkInt(part.staff, where + " staff", 0, curScore.nstaves - 1);
            var voice = isSet(part.voice) ? checkInt(part.voice, where + " voice", 0, 3) : 0;
            if (used[staff + ":" + voice]) throw new Error(where + ": staff " + staff + " voice " + voice + " is given twice");
            used[staff + ":" + voice] = true;
            var events = parseVoiceEvents(part.events, false);
            var total = 0;
            for (var e = 0; e < events.length; e++) total += events[e].ticks;
            parts.push({ staff: staff, voice: voice, events: events, total: total, where: where });
        }
        // voice 1 first: the other voices need its chords/rests to hang on
        parts.sort(function(a, b) { return a.voice - b.voice || a.staff - b.staff; });

        return mutate(function() {
            var appended = Math.max(0, lastBar - count);
            if (appended) curScore.appendMeasures(appended);
            var range = barRange({ startMeasure: firstBar, endMeasure: lastBar });
            var length = range.endTick - range.startTick;
            for (var q = 0; q < parts.length; q++) {
                if (parts[q].total !== length) {
                    throw new Error(parts[q].where + " (staff " + parts[q].staff + " voice " + parts[q].voice + ") lasts " + ticksText(parts[q].total) +
                                    ", but bars " + firstBar + "-" + lastBar + " last " + ticksText(length) + " (fill it exactly; use rests)");
                }
            }
            var warnings = [];
            var staves = [];
            for (var p = 0; p < parts.length; p++) if (staves.indexOf(parts[p].staff) < 0) staves.push(parts[p].staff);
            if (clearOthers) {
                for (var s = 0; s < staves.length; s++) {
                    for (var v = 0; v < 4; v++) {
                        if (used[staves[s] + ":" + v]) continue;
                        clearVoice(staves[s], v, range.startTick, range.endTick, warnings);
                        if (v === 0) restWholeBars(staves[s], range.startTick, range.endTick);
                    }
                }
            }
            var written = 0, ties = 0;
            for (p = 0; p < parts.length; p++) {
                var info = writeEvents({ tick: range.startTick, staff: parts[p].staff, voice: parts[p].voice }, parts[p].events, false);
                written += info.written;
                ties += info.ties;
                warnings = warnings.concat(info.warnings);
            }
            touch(range.startTick, range.endTick);
            cursorState = { tick: range.endTick, staff: parts[0].staff, voice: parts[0].voice, lastChord: null };
            var r = { message: (appended ? "Appended " + appended + " bar(s); w" : "W") + "rote bars " + firstBar + "-" + lastBar + " with " +
                               parts.length + " part(s): " + written + " notes/rests, " + ties + " tie(s), one undo step",
                      written: written, ties: ties, startMeasure: firstBar, endMeasure: lastBar };
            if (warnings.length) r.warnings = warnings;
            return r;
        });
    }

    // ========================================
    // TEXT, CHORD SYMBOLS, PEDAL, CLEFS, LAYOUT
    // ========================================

    property var textKinds: ({ "staff": Element.STAFF_TEXT, "system": Element.SYSTEM_TEXT, "expression": Element.EXPRESSION })

    function addText(params) {
        var validation = validateParams(params, ["text"]);
        if (!validation.valid) return validation;
        if (!curScore) return { error: "No score open" };
        if (typeof params.text !== "string" || !params.text.length) return { error: "text must be a non-empty string" };
        var kind = isSet(params.kind) ? params.kind : "staff";
        if (!hasKey(textKinds, kind)) return { error: "kind must be staff, system or expression" };
        return mutate(function() {
            var t = resolveTarget(params);
            putText(t, textKinds[kind], params.text);
            touch(t.tick, t.tick);
            return { message: kind + " text '" + params.text + "' added at tick " + t.tick + " on staff " + t.staff };
        });
    }

    function addChordSymbol(params) {
        var validation = validateParams(params, ["text"]);
        if (!validation.valid) return validation;
        if (!curScore) return { error: "No score open" };
        if (typeof params.text !== "string" || !params.text.length) return { error: "text must be a chord symbol like Cmaj7, F#m7b5, G/B" };
        return mutate(function() {
            var t = resolveTarget(params);
            ensureSegment(t);
            var c = makeCursor(t);
            requireSegment(c, t);
            var old = findAnnotation(c.segment, Element.HARMONY, t.staff * 4);
            if (old) removeElement(old);
            putText({ tick: t.tick, staff: t.staff, voice: 0 }, Element.HARMONY, params.text);
            touch(t.tick, t.tick);
            return { message: "Chord symbol " + params.text + " at tick " + t.tick + " on staff " + t.staff };
        });
    }

    // Pedal marks as symbols (Ped. ... *). MuseScore's plugin API can't add
    // real pedal lines, so these don't change playback.
    function addPedalMarks(params) {
        if (!curScore) return { error: "No score open" };
        var range = tickRange(params);
        var staff = isSet(params.staff) ? checkInt(params.staff, "staff", 0, curScore.nstaves - 1) : cursorState.staff;
        if (range.endTick >= scoreEndTick()) return { error: "The pedal release must be before the end of the score (add a bar, or end it earlier)" };
        return mutate(function() {
            putText({ tick: range.startTick, staff: staff, voice: 0 }, Element.STAFF_TEXT, "<sym>keyboardPedalPed</sym>");
            putText({ tick: range.endTick, staff: staff, voice: 0 }, Element.STAFF_TEXT, "<sym>keyboardPedalUp</sym>");
            touch(range.startTick, range.endTick);
            return { message: "Pedal marks (Ped. at tick " + range.startTick + ", release at tick " + range.endTick + ") on staff " + staff +
                              ". They are symbols only: MuseScore won't sustain on playback (use a pedal line from the palette for that)." };
        });
    }

    property var clefTypes: ({
        "treble": "G", "bass": "F", "alto": "C3", "tenor": "C4", "soprano": "C1", "mezzo-soprano": "C2", "baritone": "F_B",
        "treble 8vb": "G8_VB", "treble 8va": "G8_VA", "treble 15ma": "G15_MA", "bass 8vb": "F8_VB", "bass 8va": "F_8VA",
        "percussion": "PERC"
    })

    function addClef(params) {
        var validation = validateParams(params, ["type"]);
        if (!validation.valid) return validation;
        if (!curScore) return { error: "No score open" };
        if (!hasKey(clefTypes, params.type)) return { error: "Unknown clef '" + params.type + "' (use " + Object.keys(clefTypes).join(", ") + ")" };
        return mutate(function() {
            var t = resolveTarget(params);
            ensureSegment(t);
            var c = makeCursor({ tick: t.tick, staff: t.staff, voice: 0 });
            requireSegment(c, t);
            var clef = newElement(Element.CLEF);
            // Set the type before adding: the staff reads it when the clef is added
            clef.concertClefType = ClefType[clefTypes[params.type]];
            clef.transposingClefType = ClefType[clefTypes[params.type]];
            c.add(clef);
            touch(t.tick, t.tick);
            return { message: params.type + " clef at tick " + t.tick + " on staff " + t.staff };
        });
    }

    property var layoutBreakTypes: ({ "line": "LINE", "page": "PAGE", "section": "SECTION" })

    // A system/page/section break after a bar.
    function addLayoutBreak(params) {
        var validation = validateParams(params, ["type", "measure"]);
        if (!validation.valid) return validation;
        if (!curScore) return { error: "No score open" };
        if (!hasKey(layoutBreakTypes, params.type)) return { error: "type must be line, page or section" };
        var m = measureByNumber(params.measure);
        return mutate(function() {
            var els = m.obj.elements;
            for (var i = 0; i < els.length; i++) {
                if (els[i].type === Element.LAYOUT_BREAK && els[i].layoutBreakType === LayoutBreak[layoutBreakTypes[params.type]]) {
                    return { message: "Bar " + m.number + " already has a " + params.type + " break" };
                }
            }
            var lb = newElement(Element.LAYOUT_BREAK);
            lb.layoutBreakType = LayoutBreak[layoutBreakTypes[params.type]];
            var c = makeCursor({ tick: m.startTick, staff: 0, voice: 0 });
            requireSegment(c, { tick: m.startTick, staff: 0 });
            c.add(lb);
            touch(m.startTick, m.startTick);
            return { message: params.type + " break after bar " + m.number };
        });
    }

    // Makes every system hold `count` bars (0 removes the locks), with
    // MuseScore's system locks. EditSystemLocks::addRemoveSystemLocks works
    // on the selected bars, and only lock=false takes the bar count (it
    // removes the locks there, then locks every `count` bars), so the whole
    // score is selected first, outside the command.
    function setMeasuresPerSystem(params) {
        var validation = validateParams(params, ["count"]);
        if (!validation.valid) return validation;
        if (!curScore) return { error: "No score open" };
        var count = checkInt(params.count, "count", 0, 64);
        if (!selectRangeInclusive(0, scoreEndTick(), 0, curScore.nstaves - 1)) return { error: "Could not select the score" };
        return mutate(function() {
            curScore.addRemoveSystemLocks(count, false);
            return { message: count > 0 ? "Systems locked to " + count + " bar(s) each" : "System locks removed" };
        });
    }

    // ========================================
    // SCORE INFO, EXPORT, SAVE, REDO, SELECTION, CHECK
    // ========================================

    // [TextStyleType name for curScore.addText, Tid name of the text's
    // subStyle (Tid calls the lyricist style POET), meta tag]
    property var scoreTextFields: ({
        "title": ["TITLE", "TITLE", "workTitle"], "subtitle": ["SUBTITLE", "SUBTITLE", "subtitle"],
        "composer": ["COMPOSER", "COMPOSER", "composer"], "lyricist": ["LYRICIST", "POET", "lyricist"]
    })

    // The frame (VBox) above the first bar, where the title texts live.
    function titleFrame() {
        var m = curScore.firstMeasure;
        var mb = m ? m.prev : null;
        while (mb && mb.type !== Element.VBOX) mb = mb.prev;
        return mb;
    }

    // Sets title/subtitle/composer/lyricist: the text on the page (replacing
    // the old one) and the score property. An empty string removes it.
    function setScoreInfo(params) {
        if (!curScore) return { error: "No score open" };
        var keys = Object.keys(params);
        if (!keys.length) return { error: "Give at least one of title, subtitle, composer, lyricist" };
        for (var i = 0; i < keys.length; i++) {
            if (typeof params[keys[i]] !== "string") return { error: keys[i] + " must be a string" };
        }
        return mutate(function() {
            for (var i = 0; i < keys.length; i++) {
                var field = scoreTextFields[keys[i]];
                var frame = titleFrame();
                if (frame) {
                    var els = frame.elements;
                    for (var k = els.length - 1; k >= 0; k--) {
                        if (els[k].subStyle === Tid[field[1]]) removeElement(els[k]);
                    }
                }
                if (params[keys[i]].length) curScore.addText(field[0], params[keys[i]]);
                curScore.setMetaTag(field[2], params[keys[i]]);
            }
            return { message: "Set " + keys.join(", ") };
        });
    }

    // Exports the score to a file, e.g. format "pdf", "mid", "musicxml", "mxl", "png", "svg", "mp3", "wav".
    function exportScore(params) {
        var validation = validateParams(params, ["path", "format"]);
        if (!validation.valid) return validation;
        if (!curScore) return { error: "No score open" };
        if (typeof params.path !== "string" || !params.path.length) return { error: "path must be a file path" };
        var format = String(params.format).toLowerCase().replace(/^\./, "");
        if (!/^[a-z0-9]+$/.test(format)) return { error: "format must be an extension like pdf, mid, musicxml" };
        var ok = writeScore(curScore, params.path, format);
        if (!ok) return { error: "MuseScore could not export to " + params.path + " as " + format + " (unsupported format, or the folder doesn't exist)" };
        // EngravingPluginAPIHelper::writeScore adds "." + ext unless the name already ends with ext
        var path = params.path.slice(-format.length) === format ? params.path : params.path + "." + format;
        return { success: true, message: "Exported to " + path, path: path };
    }

    function saveScore(params) {
        if (!curScore) return { error: "No score open" };
        cmd("file-save");
        return { success: true, message: "Asked MuseScore to save the score (a score that was never saved opens the Save dialog)" };
    }

    property var redoCursorStack: []

    function redo(params) {
        if (!curScore) return { error: "No score open" };
        var steps = isSet(params.steps) ? checkInt(params.steps, "steps", 1, 1000) : 1;
        for (var i = 0; i < steps; i++) {
            cmd("action://notation/redo");
            undoCursorStack.push(copyCursor(cursorState));
            if (redoCursorStack.length > 0) cursorState = redoCursorStack.pop();
        }
        return navResult("Redid " + steps + " step(s)");
    }

    // What is selected in MuseScore: a range (bars, staves) or single elements.
    // fromUser: the user selected it (not the plugin showing its cursor).
    function getSelection(params) {
        if (!curScore) return { error: "No score open" };
        var sel = curScore.selection;
        var measures = listMeasures();
        function barOf(tick) {
            var m = measureAtTick(tick);
            return m ? m.number : null;
        }
        if (sel.isRange) {
            var st = sel.startSegment ? sel.startSegment.tick : 0;
            var et = sel.endSegment ? sel.endSegment.tick : scoreEndTick();
            return { success: true, kind: "range", fromUser: selectionFromUser, startTick: st, endTick: et,
                     startMeasure: barOf(st), endMeasure: barOf(Math.max(st, et - 1)),
                     startStaff: sel.startStaff, endStaff: sel.endStaff - 1 };
        }
        var els = sel.elements;
        var out = [];
        for (var i = 0; i < els.length && i < 200; i++) {
            var el = els[i];
            var item = { type: el.name, tick: elementTick(el), staff: el.track >= 0 ? Math.floor(el.track / 4) : null, voice: el.track >= 0 ? el.track % 4 : null };
            if (el.type === Element.NOTE) item.pitch = el.pitch;
            item.measure = item.tick >= 0 ? barOf(item.tick) : null;
            out.push(item);
        }
        return { success: true, kind: out.length ? "list" : "none", fromUser: selectionFromUser, elements: out, count: els.length };
    }

    // Opens a score file (mscz, mscx, MusicXML, MIDI, ...) when no score is
    // open. With a score open, MuseScore 4 would open the file in a new window
    // (another MuseScore process), out of this plugin's reach, so that is refused.
    function openScore(params) {
        var validation = validateParams(params, ["path"]);
        if (!validation.valid) return validation;
        if (typeof params.path !== "string" || !params.path.length) return { error: "path must be a file path" };
        if (curScore) {
            return { error: "A score is already open. MuseScore 4 opens another file in a new window, which this plugin can't reach. " +
                            "Close the score in MuseScore (File > Close) and ask again, or open the file in MuseScore yourself." };
        }
        var opened = readScore(params.path, false);
        if (!opened || !curScore) return { error: "MuseScore could not open " + params.path + " (does the file exist?)" };
        cursorState = { tick: 0, staff: 0, voice: 0, lastChord: null };
        return { success: true, message: "Opened " + params.path, title: curScore.title, numMeasures: listMeasures().length };
    }

    // Bars whose voices don't add up (MuseScore marks them corrupted).
    function checkScore(params) {
        if (!curScore) return { error: "No score open" };
        var measures = listMeasures();
        var problems = [];
        for (var i = 0; i < measures.length; i++) {
            for (var s = 0; s < curScore.nstaves; s++) {
                if (measures[i].obj.corrupted(s)) problems.push({ measure: measures[i].number, staff: s });
            }
        }
        return { success: true, ok: problems.length === 0, corrupted: problems,
                 message: problems.length ? problems.length + " corrupted bar/staff combination(s)" : "No corrupted bars" };
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
            putDynamic(t, name);
            touch(t.tick, t.tick);
            moveCursorTo(t);
            return { message: "Dynamic " + name + " added at tick " + t.tick + " on staff " + t.staff };
        });
    }

    function addFermata(params) {
        if (!curScore) return { error: "No score open" };
        return mutate(function() {
            var t = resolveTarget(params);
            ensureSegment(t);
            var c = makeCursor(t);
            requireSegment(c, t);
            if (findAnnotation(c.segment, Element.FERMATA, t.staff * 4 + t.voice)) {
                return { message: "Fermata already present at tick " + t.tick };
            }
            c.add(newElement(Element.FERMATA));
            touch(t.tick, t.tick);
            moveCursorTo(t);
            return { message: "Fermata added at tick " + t.tick + " on staff " + t.staff };
        });
    }

    // Metronome symbols for a beat unit given in ticks.
    property var beatUnitSymbols: ({
        "1920": "<sym>metNoteWhole</sym>", "960": "<sym>metNoteHalfUp</sym>", "480": "<sym>metNoteQuarterUp</sym>",
        "240": "<sym>metNote8thUp</sym>", "120": "<sym>metNote16thUp</sym>",
        "2880": "<sym>metNoteWhole</sym><sym>metAugmentationDot</sym>", "1440": "<sym>metNoteHalfUp</sym><sym>metAugmentationDot</sym>",
        "720": "<sym>metNoteQuarterUp</sym><sym>metAugmentationDot</sym>", "360": "<sym>metNote8thUp</sym><sym>metAugmentationDot</sym>"
    })

    // Tempo marks are system text: placed on the top staff at the target tick.
    // bpm counts beatUnit notes (default a quarter; "3/8" = dotted quarter).
    function setTempo(params) {
        var validation = validateParams(params, ["bpm"]);
        if (!validation.valid) return validation;
        if (!curScore) return { error: "No score open" };
        if (!(typeof params.bpm === "number" && params.bpm > 0 && params.bpm < 1000)) return { error: "bpm must be between 0 and 1000" };
        var unit = isSet(params.beatUnit) ? parseDuration(params.beatUnit, "beatUnit") : 480;
        if (!beatUnitSymbols[String(unit)]) return { error: "beatUnit must be a plain or dotted note from 1/16 to 3/2, got " + ticksText(unit) };
        if (isSet(params.text) && typeof params.text !== "string") return { error: "text must be a string" };

        return mutate(function() {
            var t = resolveTarget(params);
            ensureSegment({ tick: t.tick, staff: 0 });
            var c = makeCursor({ tick: t.tick, staff: 0, voice: 0 });
            requireSegment(c, { tick: t.tick, staff: 0 });
            var old = findAnnotation(c.segment, Element.TEMPO_TEXT);
            if (old) removeElement(old);
            var tempo = newElement(Element.TEMPO_TEXT);
            tempo.text = (params.text ? params.text + " " : "") + beatUnitSymbols[String(unit)] + " = " + params.bpm;
            tempo.tempo = params.bpm * (unit / 480) / 60.0;     // quarter notes per second
            c.add(tempo);
            touch(t.tick, t.tick);
            var quarterBpm = Math.round(params.bpm * unit / 480 * 100) / 100;
            return { message: "Tempo set to " + params.bpm + " BPM (" + ticksText(unit) + " beats; quarter = " + quarterBpm + ") at tick " + t.tick };
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
            ensureSegment({ tick: t.tick, staff: 0 });
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
            for (var s = staves.first; s <= staves.last; s++) addKeySig(s, m.startTick, params.fifths, params.mode);
            // Staff.key() is the written key: compare the concert key
            var got = concertKeyAt(staves.first, m.startTick);
            if (got !== normalizeKey(params.fifths)) throw new Error("MuseScore did not apply the key signature (staff reads " + got + ")");
            touch(m.startTick, m.startTick);
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
                // Rests of voices 2-4 may be pasted as visible rests or gaps: ignore them
                if (e.voice > 0 && e.name === "Rest") continue;
                var pitches = (e.notes || []).map(function(n) { return n.pitchMidi; }).join(".");
                out.push((st - s0) + ":" + e.voice + ":" + (e.startTick - startTick) + ":" + e.name + ":" + e.durationTicks + ":" + pitches);
            }
        }
        return out.join("|");
    }

    // Copies bars startMeasure..endMeasure to toMeasure. With insert (default)
    // the copy goes into new empty bars inserted before toMeasure (or appended
    // at the end); otherwise it overwrites the bars there. toStaff pastes onto
    // other staves (e.g. a melody from the left hand to the right hand);
    // transpose then moves the copy by that many semitones.
    function copyMeasures(params) {
        var validation = validateParams(params, ["startMeasure", "toMeasure"]);
        if (!validation.valid) return validation;
        if (!curScore) return { error: "No score open" };
        var src = barRange(params);
        var staves = staffRange(params);
        var insert = boolParam(params, "insert", true);
        var total = listMeasures().length;
        checkInt(params.toMeasure, "toMeasure", 1, total + 1);
        if (!insert && params.toMeasure + src.bars - 1 > total) return { error: "Not enough bars after bar " + params.toMeasure + " to paste over; use insert" };
        var toFirst = isSet(params.toStaff) ? checkInt(params.toStaff, "toStaff", 0, curScore.nstaves - 1) : staves.first;
        var toLast = toFirst + staves.last - staves.first;
        if (toLast >= curScore.nstaves) return { error: "Copying " + (staves.last - staves.first + 1) + " staves to staff " + toFirst + " needs staves up to " + toLast };
        var semis = isSet(params.transpose) ? checkInt(params.transpose, "transpose", -48, 48) : 0;
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
        if (!selectRangeInclusive(target.startTick, targetEnd.endTick, toFirst, toLast)) return { error: "Could not select the target bars" };
        var pasted = runSelectionCommand("action://notation/paste", function() {
            return rangeSignature(target.startTick, targetEnd.endTick, toFirst, toLast) === srcSig;
        });
        if (!pasted) {
            showCursor();
            return { error: "The pasted bars don't match the source (MuseScore may have refused the paste)", undoSteps: steps };
        }
        steps++;
        touch(target.startTick, targetEnd.endTick);
        var message = "Copied bars " + src.first.number + "-" + src.last.number + (toFirst !== staves.first || toLast !== staves.last ? " (staves " +
                      staves.first + "-" + staves.last + ")" : "") + " to bars " + target.number + "-" + targetEnd.number +
                      (toFirst !== staves.first ? " on staves " + toFirst + "-" + toLast : "") + (insert ? " (inserted)" : " (overwritten)");
        if (semis) {
            var staffIdx = [];
            for (var s = toFirst; s <= toLast; s++) staffIdx.push(s);
            var tr = transposeRange({ startTick: target.startTick, endTick: targetEnd.endTick, staves: staffIdx, semitones: semis });
            if (tr.error) {
                cmd("action://notation/cancel");
                return { error: message + ", but transposing the copy failed: " + tr.error, undoSteps: steps };
            }
            steps++;
            message += ", transposed by " + semis + " semitone(s)";
        }
        cursorState = { tick: target.startTick, staff: toFirst, voice: cursorState.voice, lastChord: null };
        return finishSelectionEdit({ message: message, undoSteps: steps });
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

        var count = curScore.parts.length;
        var position = isSet(params.position) ? checkInt(params.position, "position", 0, count) : count;
        return mutate(function() {
            if (position === count) {
                curScore.appendPart(params.instrumentId);
            } else {
                // insertPart doesn't fall back to a default instrument for an unknown id
                curScore.insertPart(params.instrumentId, position);
                if (curScore.parts.length !== count + 1) throw new Error("Unknown instrument id '" + params.instrumentId + "' (see list_instruments)");
            }
            var parts = curScore.parts;
            var added = partSummary(parts[position], position);
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

    // Renames an instrument (part): the name shown before the first system and
    // its short name on the following systems.
    function setInstrumentName(params) {
        if (!curScore) return { error: "No score open" };
        if (!isSet(params.name) && !isSet(params.shortName)) return { error: "Give name and/or shortName" };
        var part;
        if (isSet(params.part)) {
            part = curScore.parts[checkInt(params.part, "part", 0, curScore.parts.length - 1)];
        } else {
            part = staffObj(checkInt(isSet(params.staff) ? params.staff : cursorState.staff, "staff", 0, curScore.nstaves - 1)).part;
        }
        return mutate(function() {
            var tick0 = fractionFromTicks(0);
            if (isSet(params.name)) curScore.setInstrumentName(part, tick0, String(params.name));
            if (isSet(params.shortName)) curScore.setInstrumentAbbreviature(part, tick0, String(params.shortName));
            touch(0, 0);
            return { message: "Instrument " + partIndexOf(part) + " is now named " + (part.longName || params.name) +
                              (isSet(params.shortName) ? " (" + params.shortName + ")" : "") };
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

    // The score as data. startMeasure/endMeasure (1-based, inclusive) limit
    // the bars read; reading bars also notices edits made in MuseScore.
    function getScore(params) {
        if (!curScore) return { error: "No score open" };
        var measures = listMeasures();
        var first = isSet(params.startMeasure) ? checkInt(params.startMeasure, "startMeasure", 1, measures.length) : 1;
        var last = isSet(params.endMeasure) ? checkInt(params.endMeasure, "endMeasure", first, measures.length) : measures.length;
        var bars = readBars(measures, first, last);
        // Inside an edit request (a sequence that also edits), the edit's own
        // record re-reads these bars afterwards: comparing now would log its
        // changes as the user's.
        if (touchedTicks === null) absorbDigests(measures, first, bars, "user", "edited in MuseScore");
        var summary = scoreHeader(measures);
        summary.firstMeasure = first;
        summary.lastMeasure = last;
        summary.measures = bars;
        return { success: true, analysis: summary, cursor: cursorInfo(), version: scoreVersion };
    }

    function clefName(type) {
        // ClefType values of MuseScore 4.7 (engraving/types/types.h)
        var names = { 0: "treble", 1: "treble 15mb", 2: "treble 8vb", 3: "treble 8va", 4: "treble 15ma", 5: "treble 8vb",
                      6: "treble 8vb", 7: "french violin", 8: "soprano", 9: "mezzo-soprano", 10: "alto", 11: "tenor",
                      12: "baritone (C)", 13: "alto", 14: "soprano", 15: "alto", 16: "tenor", 17: "soprano", 18: "alto", 19: "tenor",
                      20: "bass", 21: "bass 15mb", 22: "bass 8vb", 23: "bass 8va", 24: "bass 15ma", 25: "baritone",
                      26: "subbass", 27: "bass", 28: "bass", 29: "percussion", 30: "percussion",
                      31: "tab", 32: "tab", 33: "tab", 34: "tab", 35: "tenor 8vb", 36: "treble 8vb" };
        return names[type] || ("clef " + type);
    }

    // Everything but the bars: title, staves, parts, tempo and time signatures.
    function scoreHeader(measures) {
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
            staves: []
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
            var info = {
                name: "staff" + i,
                index: i,
                instrument: part ? (part.longName || part.partName) : "",
                shortName: part ? part.shortName : "",
                instrumentId: part ? part.instrumentId : "",
                part: part ? partIndexOf(part) : -1,
                visible: part ? part.show : true,
                keySignature: { fifths: fifths, name: keyName(fifths) }
            };
            try {
                info.clef = clefName(st.clefType(tick0));
                var tr = st.transpose(tick0);
                if (tr && (tr.chromatic || tr.diatonic)) info.transposition = { chromatic: tr.chromatic, diatonic: tr.diatonic };
            } catch (e) {}
            // Key and clef changes, bar by bar
            var keys = [], clefs = [], lastKey = fifths, lastClef = info.clef;
            for (var m = 1; m < measures.length; m++) {
                var f = fractionFromTicks(measures[m].startTick);
                var k = st.key(f);
                if (k !== lastKey) { keys.push({ measure: m + 1, fifths: k, name: keyName(k) }); lastKey = k; }
                try {
                    var c = clefName(st.clefType(f));
                    if (c !== lastClef) { clefs.push({ measure: m + 1, clef: c }); lastClef = c; }
                } catch (e2) {}
            }
            if (keys.length) info.keyChanges = keys;
            if (clefs.length) info.clefChanges = clefs;
            score.staves.push(info);
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
            } catch (e3) {}
        }

        var prevTs = "";
        for (var mi = 0; mi < measures.length; mi++) {
            var md = measures[mi];
            var tsText = md.numerator + "/" + md.denominator;
            if (tsText !== prevTs) {
                score.timeSignatures.push({ measure: md.number, tick: md.startTick, numerator: md.numerator, denominator: md.denominator });
                prevTs = tsText;
            }
            // Tempo marks anywhere in the score
            var seg = md.obj.firstSegment;
            while (seg) {
                var anns = seg.annotations;
                for (var a = 0; a < anns.length; a++) {
                    if (anns[a].type === Element.TEMPO_TEXT) {
                        var ann = processAnnotation(anns[a]);
                        score.tempos.push({ measure: md.number, tick: seg.tick, bpm: ann.bpm, text: ann.text });
                    }
                }
                seg = seg.nextInMeasure;
            }
        }
        return score;
    }

    // The contents of bars first..last (1-based): per staff the chords/rests
    // of all voices, and the markings.
    function readBars(measures, first, last) {
        var nstaves = curScore.nstaves;
        var tc = curScore.newCursor();
        var out = [];
        for (var mi = first - 1; mi < last; mi++) {
            var md = measures[mi];
            var tsText = md.numerator + "/" + md.denominator;
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

            // Effective tempo (quarter-note BPM, includes rit./accel.) and
            // playback time at the start of the bar. Kept out of the digest:
            // they change when an earlier bar changes.
            tc.rewindToFraction(fractionFromTicks(md.startTick));
            if (tc.segment) {
                measure.tempoBpm = Math.round(tc.tempo * 60 * 100) / 100;
                measure.timeSeconds = Math.round(tc.time(false)) / 1000;
            }
            out.push(measure);
        }
        return out;
    }

    // ========================================
    // SCORE VERSIONS
    // ========================================

    // 32-bit FNV-1a hash of a string, as hex.
    function hashText(s) {
        var h = 0x811c9dc5;
        for (var i = 0; i < s.length; i++) {
            h ^= s.charCodeAt(i);
            h = (h + ((h << 1) + (h << 4) + (h << 7) + (h << 8) + (h << 24))) >>> 0;
        }
        return h.toString(16);
    }

    function barDigest(bar) {
        var copy = {};
        for (var key in bar) {
            if (key !== "tempoBpm" && key !== "timeSeconds") copy[key] = bar[key];
        }
        return hashText(JSON.stringify(copy));
    }

    // [[first, last], ...] from a sorted list of bar numbers.
    function barRanges(numbers) {
        var out = [];
        for (var i = 0; i < numbers.length; i++) {
            var n = numbers[i];
            if (out.length && out[out.length - 1][1] === n - 1) out[out.length - 1][1] = n;
            else out.push([n, n]);
        }
        return out;
    }

    function bumpVersion(source, action, bars) {
        scoreVersion++;
        versionLog.push({ version: scoreVersion, source: source, action: action, bars: bars });
        if (versionLog.length > maxLogEntries) versionLog.shift();
    }

    // Compares freshly read bars first.. with the stored digests and stores
    // theirs. Returns the changed bar numbers. With no baseline (or a changed
    // number of bars) every bar is read, and null is returned when the change
    // can't be attributed to bars.
    function compareDigests(measures, first, bars) {
        if (barDigests === null || barDigests.length !== measures.length) {
            var hadBaseline = barDigests !== null;
            var oldDigests = barDigests;
            var all = first === 1 && bars.length === measures.length ? bars : readBars(measures, 1, measures.length);
            barDigests = all.map(barDigest);
            if (!hadBaseline) return [];
            // Bars were inserted or deleted: everything from the first difference on
            var changed = [];
            for (var i = 0; i < barDigests.length; i++) {
                if (changed.length || i >= oldDigests.length || oldDigests[i] !== barDigests[i]) changed.push(i + 1);
            }
            if (!changed.length) changed.push(measures.length);
            return changed;
        }
        var out = [];
        for (var b = 0; b < bars.length; b++) {
            var d = barDigest(bars[b]);
            var idx = first - 1 + b;
            if (barDigests[idx] !== d) {
                out.push(idx + 1);
                barDigests[idx] = d;
            }
        }
        return out;
    }

    function absorbDigests(measures, first, bars, source, action) {
        var changed = compareDigests(measures, first, bars);
        if (changed.length) bumpVersion(source, action, barRanges(changed));
        return changed;
    }

    // Reads the whole score and logs edits made in MuseScore since the last read.
    function detectUserChanges() {
        var measures = listMeasures();
        absorbDigests(measures, 1, readBars(measures, 1, measures.length), "user", "edited in MuseScore");
    }

    // Declares that the running request edits [startTick, endTick]: only
    // those bars are re-read afterwards. Without a declaration the whole
    // score is re-read.
    function touch(startTick, endTick) {
        if (touchedTicks !== null) touchedTicks.push([startTick, Math.max(startTick, endTick)]);
    }

    // After an edit request: re-read the bars it touched and log the change.
    function recordChange(action, ok) {
        var measures = listMeasures();
        var changed;
        if (barDigests === null || barDigests.length !== measures.length || !touchedTicks || !touchedTicks.length) {
            var all = readBars(measures, 1, measures.length);
            changed = compareDigests(measures, 1, all);
        } else {
            var wanted = {};
            for (var r = 0; r < touchedTicks.length; r++) {
                for (var m = 0; m < measures.length; m++) {
                    if (measures[m].endTick > touchedTicks[r][0] && measures[m].startTick <= touchedTicks[r][1]) wanted[m] = true;
                }
            }
            changed = [];
            for (var idx in wanted) {
                var i = parseInt(idx, 10);
                changed = changed.concat(compareDigests(measures, i + 1, readBars(measures, i + 1, i + 1)));
            }
            changed.sort(function(a, b) { return a - b; });
        }
        if (ok || changed.length) bumpVersion("mcp", action, changed.length ? barRanges(changed) : []);
    }

    // A different score is open than before: its versions start over.
    function noteScoreSwitch() {
        if (lastScore !== null && curScore.is(lastScore)) return;
        var switched = lastScore !== null;
        lastScore = curScore;
        barDigests = null;
        if (switched) {
            cursorState = { tick: 0, staff: 0, voice: 0, lastChord: null };
            undoCursorStack = [];
            bumpVersion("user", "another score was opened", null);
        }
    }

    function getVersion(params) {
        detectUserChanges();
        return { success: true, version: scoreVersion };
    }

    // The log entries after `version`, with the changed bars merged.
    // complete is false when the log can't say (too old, or another score).
    function getChangesSince(params) {
        var validation = validateParams(params, ["version"]);
        if (!validation.valid) return validation;
        checkInt(params.version, "version", 0, null);
        detectUserChanges();
        var since = params.version;
        var changes = versionLog.filter(function(c) { return c.version > since; });
        var complete = since <= scoreVersion &&
                       (since === scoreVersion || (versionLog.length > 0 && versionLog[0].version <= since + 1));
        var bars = [];
        for (var i = 0; i < changes.length; i++) {
            if (changes[i].bars === null) complete = false;
            else bars = bars.concat(changes[i].bars);
        }
        // merge the ranges
        bars.sort(function(a, b) { return a[0] - b[0]; });
        var merged = [];
        for (var j = 0; j < bars.length; j++) {
            var last = merged[merged.length - 1];
            if (last && bars[j][0] <= last[1] + 1) last[1] = Math.max(last[1], bars[j][1]);
            else merged.push([bars[j][0], bars[j][1]]);
        }
        var n = listMeasures().length;
        merged = merged.filter(function(r) { return r[0] <= n; }).map(function(r) { return [r[0], Math.min(r[1], n)]; });
        return { success: true, version: scoreVersion, since: since, complete: complete, changes: changes, changedBars: merged,
                 numMeasures: n };
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

        // Versions start at a random number, so a version from an earlier run
        // of the plugin can't be mistaken for a current one.
        scoreVersion = (Math.floor(Math.random() * 9000) + 1000) * 100;
        if (curScore) {
            cursorState = { tick: 0, staff: 0, voice: 0, lastChord: null };
            lastScore = curScore;
            try {
                detectUserChanges();   // the baseline digests
            } catch (e) {
                console.log("Could not read the score: " + e.toString());
            }
            showCursor();
        }
    }
}
