"""Model Context Protocol (MCP) Server for The Hunger Games Canon & Rubric Knowledge."""

from app.mcp_server.canon_server import (
    server,
    lookup_hunger_games_canon,
    fetch_exam_rubric_criteria,
    get_exemplar_answer,
    get_canon_mcp_toolset,
)

__all__ = [
    "server",
    "lookup_hunger_games_canon",
    "fetch_exam_rubric_criteria",
    "get_exemplar_answer",
    "get_canon_mcp_toolset",
]
