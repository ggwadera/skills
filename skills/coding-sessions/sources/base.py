"""Base interface for session source adapters.

Each source (Claude Code, Codex, opencode) implements the SessionSource ABC.
The CLI builds typed dataclasses from each adapter and passes them to shared
formatters, so rendering is source-agnostic.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Timeline event kinds
# ---------------------------------------------------------------------------

class EventKind(str, Enum):
    USER_MESSAGE = "user_message"
    ASSISTANT_TEXT = "assistant_text"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    THINKING = "thinking"
    SLASH_COMMAND = "slash_command"
    COMPACT_BOUNDARY = "compact_boundary"
    AWAY_SUMMARY = "away_summary"
    TURN_DURATION = "turn_duration"
    AI_TITLE = "ai_title"
    REASONING = "reasoning"
    TOKEN_COUNT = "token_count"
    SESSION_META = "session_meta"
    TURN_CONTEXT = "turn_context"
    WORLD_STATE = "world_state"
    TASK_STARTED = "task_started"
    FILE_EDIT = "file_edit"
    COMPACTION = "compaction"


# ---------------------------------------------------------------------------
# Dataclasses returned by adapters
# ---------------------------------------------------------------------------

@dataclass
class SessionRow:
    source: str
    session_id: str
    project: str
    title: str
    lines: int
    user_prompts: int
    tool_calls: int
    assistant_texts: int
    compacts: int
    last_ts: str
    file_size: int = 0
    cost: float = 0.0
    tokens_input: int = 0
    tokens_output: int = 0
    duration_s: int = 0


@dataclass
class SessionMeta:
    source: str
    session_id: str
    title: str
    cwd: str
    model: str
    version: str
    git_branch: str
    line_count: int
    first_ts: str
    last_ts: str
    compact_count: int
    file_size: int
    cost: float = 0.0
    tokens_input: int = 0
    tokens_output: int = 0
    duration_s: int = 0
    agent: str = ""
    source_label: str = ""
    is_subagent: bool = False
    nickname: str = ""


@dataclass
class Prompt:
    text: str
    timestamp: str


@dataclass
class AssistantText:
    text: str
    timestamp: str


@dataclass
class ToolCall:
    name: str
    input: str
    timestamp: str
    status: str = ""
    tool_id: str = ""
    output: str = ""


@dataclass
class CompactBoundary:
    timestamp: str
    trigger: str
    pre_tokens: int
    post_tokens: int
    dropped_tokens: int
    duration_ms: int


@dataclass
class AwaySummary:
    timestamp: str
    content: str


@dataclass
class ReasoningEntry:
    text: str
    timestamp: str


@dataclass
class SubagentInfo:
    agent_type: str = ""
    description: str = ""
    agent_id: str = ""
    nickname: str = ""


@dataclass
class TodoEntry:
    content: str
    status: str
    priority: str
    position: int


@dataclass
class TimelineEvent:
    timestamp: str
    kind: EventKind
    text: str = ""
    tool_name: str = ""
    tool_input: str = ""
    tool_output: str = ""
    tool_status: str = ""
    tool_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Abstract base class
# ---------------------------------------------------------------------------

class SessionSource(ABC):
    """Interface that every session source adapter implements."""

    #: Registry name used in --source and auto-detection output
    name: str

    @abstractmethod
    def list_sessions(self, dir_path: str | None, all_dirs: bool,
                      limit: int = 200) -> list[SessionRow]:
        """List sessions, optionally filtered by directory."""

    @abstractmethod
    def find(self, session_id: str, dir_path: str | None) -> Any | None:
        """Locate a session by ID. Returns an opaque handle or None."""

    @abstractmethod
    def metadata(self, handle: Any, session_id: str) -> SessionMeta:
        """Extract session-level metadata."""

    @abstractmethod
    def user_prompts(self, handle: Any) -> list[Prompt]:
        """Extract all real user prompts."""

    @abstractmethod
    def assistant_texts(self, handle: Any) -> list[AssistantText]:
        """Extract all assistant text responses."""

    @abstractmethod
    def tool_calls(self, handle: Any) -> list[ToolCall]:
        """Extract all tool calls."""

    @abstractmethod
    def compact_boundaries(self, handle: Any) -> list[CompactBoundary]:
        """Extract compaction boundaries."""

    @abstractmethod
    def away_summaries(self, handle: Any) -> list[AwaySummary]:
        """Extract auto-generated away/recap summaries."""

    @abstractmethod
    def reasoning(self, handle: Any) -> list[ReasoningEntry]:
        """Extract reasoning summaries (empty for sources without reasoning)."""

    @abstractmethod
    def subagents(self, handle: Any) -> list[SubagentInfo]:
        """Extract subagent info."""

    @abstractmethod
    def timeline_events(self, handle: Any) -> list[TimelineEvent]:
        """Extract the full ordered timeline of events."""

    def files_edited(self, session_id: str) -> list[Path]:
        """Return file paths edited during the session (claude only)."""
        return []

    def todos(self, session_id: str) -> list[TodoEntry]:
        """Return todo list for the session (opencode only)."""
        return []

    def matches(self, session_id: str, dir_path: str | None, needle: str) -> bool:
        """Return True when the session title or a user prompt contains needle."""
        handle = self.find(session_id, dir_path)
        if handle is None:
            return False
        needle = needle.lower()
        meta = self.metadata(handle, session_id)
        if needle in (meta.title or "").lower():
            return True
        return any(needle in prompt.text.lower() for prompt in self.user_prompts(handle))
