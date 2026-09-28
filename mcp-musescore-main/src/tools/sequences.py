"""Sequence processing tools for MuseScore MCP."""

from typing import Any, Dict, List, Optional

from pydantic import ConfigDict, TypeAdapter, ValidationError, with_config
from typing_extensions import NotRequired, TypedDict

from ..client import MuseScoreClient
from ..notation import parse_pitch
from ..types import SEQUENCE_ACTIONS, ActionSequence
from ..utils.durations import DurationError, normalize_duration
from ..validation import voice_events
from .editing import section_parts
from .navigation import with_version


# The steps are checked against the full per-action types (ActionSequence), but
# the tool's own schema stays small: spelled out, the 53 action types would add
# ~40 KB to every conversation.
_SEQUENCE = TypeAdapter(ActionSequence)


@with_config(ConfigDict(extra="forbid"))
class Step(TypedDict):
    action: str
    params: NotRequired[Dict[str, Any]]


def action_list() -> str:
    """"addNote(pitch*, duration, ...)" for every sequence action (* = required)."""
    out = []
    for name, (params, _) in SEQUENCE_ACTIONS.items():
        req = set(params.__required_keys__)
        keys = [k + ("*" if k in req else "") for k in params.__annotations__]
        out.append(f"{name}({', '.join(keys)})")
    return "; ".join(out)


def check_sequence(sequence: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    try:
        return _SEQUENCE.validate_python(sequence)
    except ValidationError as e:
        raise ValueError(f"invalid sequence: {e}") from None


def normalize_sequence(sequence: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Check the parts of each step that the types can't (writable durations, write_voice
    events or notation, replaceSection parts) and return the steps in the plugin's wire format.
    Raises ValueError naming the step."""
    out = []
    for i, step in enumerate(sequence):
        step = dict(step)
        action = step["action"]
        params = dict(step.get("params") or {})
        try:
            if action in ("addNote", "addRest") and "duration" in params:
                params["duration"] = normalize_duration(params["duration"])
            if action == "addNote" and "pitch" in params:
                params["pitch"] = parse_pitch(params["pitch"], "pitch")
            elif action == "writeVoice":
                params["events"] = voice_events(params.get("events"), params.pop("notation", None))
            elif action == "replaceSection":
                params["parts"] = section_parts(params["parts"])
        except (ValueError, DurationError) as e:
            raise ValueError(f"step {i} ({action}): {e}") from None
        if "params" in step or params:
            step["params"] = params
        out.append(step)
    return out


def setup_sequence_tools(mcp, client: MuseScoreClient):
    """Setup sequence processing tools."""

    def with_actions(func):
        func.__doc__ = func.__doc__.replace("ACTIONS", action_list())
        return func

    @mcp.tool()
    @with_actions
    async def process_sequence(sequence: List[Step], atomic: bool = False, expected_version: Optional[int] = None):
        """Run several actions in one round trip, e.g. write two staves and add markings as ONE undo
        step (atomic=true).

        Actions use the plugin's camelCase names and params, e.g.
            {"action": "writeVoice", "params": {"staff": 0, "measure": 5, "notation": "C5:q D5 E5 F5"}}
            {"action": "writeVoice", "params": {"staff": 1, "measure": 5, "notation": "[C3 G3]:w"}}
            {"action": "addDynamic", "params": {"dynamic": "p", "measure": 5, "staff": 0}}
        Positions: staff, voice, measure, offset, tick. writeVoice takes the same events or notation as
        write_voice; replaceSection, transpose, clearRange, addText, addChordSymbol, addClef, setTempo
        (bpm, beatUnit) etc. take the params of their tools in camelCase. Unknown actions or params
        are errors.

        Without atomic, each step is its own undo step and the sequence stops at the first failing
        step (reporting which). With atomic=true the whole sequence is ONE undo step, and if any step
        fails nothing is kept; ties requested with addNote tie=true are made at the end, so the next
        note may be written by a later step. Atomic sequences can't contain actions that change the
        selection (undo, redo, deleteSelection, insertMeasure, select*, addSlur, addHairpin,
        addArticulation, deleteMeasures, copyMeasures, setMeasuresPerSystem).

        Args:
            sequence: The steps: {"action": name, "params": {...}}.
            atomic: One undo step, all or nothing.
            expected_version: Refuse the whole sequence if the score changed since this version.

        Actions and their params (* = required): ACTIONS
        """
        params = {"sequence": normalize_sequence(check_sequence(sequence)), "atomic": atomic}
        return await client.send_command("processSequence", with_version(params, expected_version))
