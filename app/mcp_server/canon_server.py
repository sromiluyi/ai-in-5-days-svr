"""Hunger Games Canon & Exam Rubric MCP Server.

Provides tools conforming to the Model Context Protocol (MCP) to supply factual
ground truth from Suzanne Collins' *The Hunger Games* (Book 1) and official
Middle School ELA exam rubrics to ADK agents.
"""

from __future__ import annotations

import asyncio
import os
import sys
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from mcp.server.fastmcp import FastMCP
from google.adk.tools.mcp_tool import McpToolset
from google.adk.tools.mcp_tool.mcp_session_manager import StdioConnectionParams

from app.observability.logger import log_intent, log_outcome


# Canonical Knowledge Base: Ground truth facts strictly from Suzanne Collins' *The Hunger Games* (Book 1)
CANON_KNOWLEDGE_BASE: Dict[str, Dict[str, Any]] = {
    "reaping": {
        "chapter": 2,
        "title": "The Reaping & Katniss's Sacrifice",
        "facts": [
            "At the 74th annual Hunger Games reaping for District 12, Primrose (Prim) Everdeen's name is called from the glass bowl.",
            "Katniss Everdeen volunteers as tribute to take her twelve-year-old sister Prim's place, an extremely rare act in District 12.",
            "Peeta Mellark, the baker's son who once saved Katniss's starving family by intentionally burning bread and tossing it to her, is selected as the boy tribute.",
            "The crowd in District 12 touches the three middle fingers of their left hand to their lips and extends them toward Katniss as a silent symbol of respect and farewell.",
        ],
    },
    "district_12": {
        "chapter": 1,
        "title": "District 12 Setting & Life",
        "facts": [
            "District 12 is located in the Appalachian region, impoverished and dedicated to coal mining.",
            "Katniss lives in 'the Seam', the poorest section, with her mother and sister Prim after her father died in a coal mine explosion.",
            "Katniss illegal hunts beyond the perimeter fence in the woods with her friend Gale Hawthorne to keep their families alive.",
            "Madge Undersee, the mayor's daughter, gives Katniss the gold Mockingjay pin before she departs for the Capitol.",
        ],
    },
    "tracker_jackers": {
        "chapter": 14,
        "title": "Tracker Jacker Attack in the Arena",
        "facts": [
            "Tracker jackers are genetically engineered wasps (muttations) created by the Capitol with venom that induces violent hallucinations and severe swelling.",
            "While trapped in a tree by Career tributes and Peeta, Katniss notices Rue pointing toward a golden tracker jacker nest above.",
            "Katniss saws the branch during the anthem, dropping the nest onto the sleeping Career pack below.",
            "Glimmer (District 1) and the District 4 girl are killed by the stings; Katniss retrieves Glimmer's silver bow and arrows.",
            "Katniss suffers multiple stings, hallucinates, and Peeta urges her to run when Cato returns, saving her life.",
        ],
    },
    "rue_alliance": {
        "chapter": 18,
        "title": "Rue's Alliance, Death, and District 11 Bread",
        "facts": [
            "Rue (District 11) forms an alliance with Katniss, sharing knowledge of edible plants and healing Katniss's stings with chewed leaves.",
            "They communicate across the forest using Rue's four-note mockingjay melody to confirm safety.",
            "Katniss destroys the Careers' pyramid of supplies using the tracker jacker nest knowledge and mining explosions.",
            "Rue is speared by Marvel (District 1); Katniss kills Marvel immediately with an arrow.",
            "As Rue dies, Katniss sings to her and covers her body in wild flowers (marigolds, rue) as a visible rebuke to the Capitol.",
            "In response, the citizens of District 11 send Katniss a loaf of dark crescent bread rationed from their own district.",
        ],
    },
    "nightlock_climax": {
        "chapter": 27,
        "title": "The Nightlock Berries & Capitol Defiance",
        "facts": [
            "After defeating the wolf muttations and Cato at the Cornucopia, the Gamemakers revoke the rule allowing two victors from the same district.",
            "Peeta insists Katniss kill him so she can survive, but Katniss refuses to become a pawn of the Capitol.",
            "Katniss retrieves poisonous Nightlock berries (identified earlier by Peeta) and hands half to Peeta.",
            "On the count of three, they prepare to swallow the berries together so the Capitol has no victor.",
            "Claudius Templesmith panics and frantically announces that both Katniss and Peeta are declared victors of the 74th Hunger Games.",
        ],
    },
}

EXAM_RUBRIC: Dict[str, Dict[str, Any]] = {
    "Q1": {
        "prompt": "What act does Katniss Everdeen perform at the District 12 reaping that begins her journey in the Hunger Games, and whom does she protect?",
        "question_type": "short_answer",
        "max_correctness": 5.0,
        "max_quality": 5.0,
        "key_points": [
            "Katniss volunteers as tribute to replace her younger sister Primrose (Prim).",
            "Prim's name was drawn from the glass ball despite only having one slip.",
        ],
        "middle_school_standard": "Direct recall with explicit textual identification of character relationships.",
    },
    "Q2": {
        "prompt": "How does Katniss use the tracker jacker nest defensively when surrounded by the Career tributes in the tree?",
        "question_type": "short_answer",
        "max_correctness": 5.0,
        "max_quality": 5.0,
        "key_points": [
            "Rue points out the tracker jacker nest above Katniss in the tree.",
            "Katniss saws through the branch during the anthem so the sound masks the noise.",
            "The nest falls onto the Careers below, scattering them and killing Glimmer.",
            "Katniss secures the bow and arrows from Glimmer's body.",
        ],
        "middle_school_standard": "Sequential cause-and-effect explanation of tactical plot event.",
    },
    "Q3": {
        "prompt": "Explain the significance of the four-note Mockingjay song that Katniss and Rue use to communicate in the arena.",
        "question_type": "short_answer",
        "max_correctness": 5.0,
        "max_quality": 5.0,
        "key_points": [
            "The four-note melody is used by Rue in District 11 to signal the end of the workday.",
            "In the arena, they agree to whistle the tune to the mockingjays so the birds carry the song, confirming both are safe.",
            "It symbolizes communication beyond Capitol control and solidarity across districts.",
        ],
        "middle_school_standard": "Explains both literal plot mechanism and symbolic meaning.",
    },
    "Q4": {
        "prompt": "In an analytical response (1-2 paragraphs), analyze how the Capitol uses the Hunger Games as an instrument of psychological, political, and physical control over the twelve districts. Use evidence from the novel.",
        "question_type": "long_essay",
        "max_correctness": 10.0,
        "max_quality": 10.0,
        "key_points": [
            "Physical control: Annual reminder of Capitol victory in the Dark Days; taking children shows absolute ownership over citizens' lives.",
            "Psychological control: Breeds mutual suspicion between districts by forcing children to kill each other for Capitol entertainment; makes districts view each other as rivals rather than allies.",
            "Political control: The 'Treaty of Treason' law mandating mandatory viewing and celebrations in districts enforces submissiveness.",
            "Symbolism: The stark contrast between Capitol luxury and District poverty/starvation.",
        ],
        "middle_school_standard": "Clear central claim, two pieces of evidence, analytical reasoning connecting evidence to authoritarian control.",
    },
    "Q5": {
        "prompt": "In a well-developed essay (2-3 paragraphs), discuss how Katniss's honoring of Rue with flowers and the climax with the nightlock berries represent subversion against the Capitol. How do these actions demonstrate that 'they don't own me'?",
        "question_type": "long_essay",
        "max_correctness": 15.0,
        "max_quality": 15.0,
        "key_points": [
            "Flower memorial: By covering Rue in wildflowers, Katniss treats Rue as a human being rather than disposable Capitol entertainment; it explicitly denies the Capitol's dehumanization of tributes.",
            "District 11 bread: Demonstrates inter-district solidarity and gratitude that defies Capitol division.",
            "Nightlock berries: Connects to Peeta's wish before entering the arena ('I want to die as myself... to show the Capitol they don't own me'). Katniss and Peeta refuse to kill each other, holding the Capitol hostage with the threat of no victor.",
            "Defiance & Subversion: Both actions expose the Capitol's vulnerability and ignite the latent spark of rebellion.",
        ],
        "middle_school_standard": "Structured essay with thesis, thematic analysis, synthesis of Peeta's philosophy, and thorough textual citations.",
    },
}

EXEMPLAR_ANSWERS: Dict[str, str] = {
    "Q1": "At the District 12 reaping, twelve-year-old Primrose Everdeen's name is pulled from the glass bowl. In an immediate act of love and sacrifice, sixteen-year-old Katniss Everdeen pushes through the crowd and shouts that she volunteers as tribute to take her younger sister's place.",
    "Q2": "While pinned in a tree by Career tributes, Katniss notices Rue signaling toward a tracker jacker nest above. During the loud Capitol anthem, Katniss saws through the branch holding the nest, dropping it onto the sleeping Careers. The venomous stings kill Glimmer and scatter the others, allowing Katniss to retrieve Glimmer's silver bow and arrows.",
    "Q3": "Rue teaches Katniss a four-note melody that she used in District 11 to signal the end of the workday. In the arena, they agree to whistle this tune to the mockingjays so the birds repeat it across the forest, letting each girl know the other is safe. Symbolically, it represents human connection and unity defying Capitol surveillance.",
    "Q4": "The Capitol utilizes the Hunger Games as a comprehensive apparatus of authoritarian control over Panem. Physically, the Capitol proves its absolute sovereignty by forcibly selecting twenty-four children every year to murder each other on live television, reminding all citizens that resistance in the Dark Days was futile. Politically and psychologically, the Games divide the districts by fostering mutual distrust; citizens are forced to cheer for their own tributes rather than uniting against Capitol oppression. Furthermore, mandatory Capitol broadcasts force starving families in the districts to watch Capitol citizens treat their children's slaughter as mere entertainment, reinforcing total helplessness.",
    "Q5": "Throughout the novel, Katniss demonstrates that human dignity can overcome totalitarian oppression through acts of defiant subversion. After Rue is killed by Marvel, Katniss does not simply walk away as the Capitol expects. Instead, she sings Rue to sleep and weaves a wreath of colorful wildflowers around her body. By decorating Rue in flowers, Katniss humanizes her and shows the Capitol that tributes are not disposable pawns for television ratings. This prompts District 11 to send Katniss bread, proving that compassion can unite divided districts.\n\nThis philosophy culminates at the Cornucopia with the nightlock berries, directly fulfilling Peeta's desire to 'die as myself' and show the Capitol they do not own him. When Claudius Templesmith revokes the dual victor rule, Katniss and Peeta refuse to murder each other. By raising the poisonous berries to their lips, they force the Gamemakers to choose between accepting two victors or having no victor at all. This ultimate act of defiance shatters the Capitol's illusion of absolute power.",
}


# Initialize MCP Server instance
server = FastMCP(
    name="hunger_games_canon_server",
    instructions="Provides canonical book facts and official middle school ELA exam rubrics for The Hunger Games.",
)


@server.tool(
    name="lookup_hunger_games_canon",
    description="Look up verified canonical facts from Suzanne Collins' The Hunger Games (Book 1) to verify student factual accuracy.",
)
def lookup_hunger_games_canon(query: str, chapter: Optional[int] = None) -> Dict[str, Any]:
    """Look up verified canonical facts from Suzanne Collins' *The Hunger Games* (Book 1).

    Args:
        query: Search term or key concept (e.g., 'tracker jackers', 'reaping', 'nightlock', 'rue').
        chapter: Optional chapter number filter.

    Returns:
        Dictionary with matching canonical facts, chapter references, and context.
    """
    log_intent("MCPServer", "LOOKUP_CANON", query, {"chapter": chapter})

    clean_query = query.lower().strip()
    matches: List[Dict[str, Any]] = []

    for key, data in CANON_KNOWLEDGE_BASE.items():
        if chapter and data["chapter"] != chapter:
            continue

        if (
            key in clean_query
            or any(clean_query in f.lower() for f in data["facts"])
            or data["title"].lower() in clean_query
            or any(word in data["title"].lower() for word in clean_query.split())
        ):
            matches.append(
                {
                    "topic": key,
                    "title": data["title"],
                    "chapter": data["chapter"],
                    "canonical_facts": data["facts"],
                }
            )

    # Fallback to all facts if specific query broad
    if not matches and not chapter:
        for key, data in CANON_KNOWLEDGE_BASE.items():
            matches.append({"topic": key, "title": data["title"], "canonical_facts": data["facts"][:2]})

    result = {
        "status": "success",
        "query": query,
        "total_matches": len(matches),
        "results": matches,
    }

    log_outcome(
        "MCPServer",
        "LOOKUP_CANON",
        "SUCCESS",
        f"Found {len(matches)} canon matches for query '{query}'",
    )
    return result


@server.tool(
    name="fetch_exam_rubric_criteria",
    description="Retrieve official rubric benchmarks, point allocations, and key criteria for a specific exam question.",
)
def fetch_exam_rubric_criteria(question_id: str) -> Dict[str, Any]:
    """Retrieve official rubric benchmarks, point allocations, and key criteria for an exam question.

    Args:
        question_id: The question identifier (e.g., 'Q1', 'Q2', 'Q3', 'Q4', 'Q5').

    Returns:
        Dictionary containing prompt, question type, points breakdown, and key criteria.
    """
    qid = question_id.upper().strip()
    log_intent("MCPServer", "FETCH_RUBRIC", qid)

    if qid not in EXAM_RUBRIC:
        return {
            "status": "error",
            "error_code": "INVALID_QUESTION_ID",
            "message": f"Question ID '{question_id}' not found in official rubric.",
            "valid_questions": list(EXAM_RUBRIC.keys()),
            "recovery_guidance": "Please request one of the valid question IDs: ['Q1', 'Q2', 'Q3', 'Q4', 'Q5'].",
        }

    rubric_item = EXAM_RUBRIC[qid]
    result = {
        "status": "success",
        "question_id": qid,
        **rubric_item,
    }
    log_outcome("MCPServer", "FETCH_RUBRIC", "SUCCESS", f"Retrieved rubric for {qid}")
    return result


@server.tool(
    name="get_exemplar_answer",
    description="Retrieve a middle school exemplar answer for comparison and benchmark grading.",
)
def get_exemplar_answer(question_id: str) -> Dict[str, Any]:
    """Retrieve an exemplar middle school answer for benchmark grading.

    Args:
        question_id: The question identifier ('Q1' to 'Q5').

    Returns:
        Dictionary with exemplar text and pedagogical commentary.
    """
    qid = question_id.upper().strip()
    log_intent("MCPServer", "GET_EXEMPLAR", qid)

    if qid not in EXEMPLARY_ANSWERS:
        return {
            "status": "error",
            "error_code": "INVALID_QUESTION_ID",
            "message": f"Exemplar for '{question_id}' not found.",
            "valid_questions": list(EXEMPLARY_ANSWERS.keys()),
            "recovery_guidance": "Please request one of: ['Q1', 'Q2', 'Q3', 'Q4', 'Q5'].",
        }

    result = {
        "status": "success",
        "question_id": qid,
        "exemplar_answer": EXEMPLARY_ANSWERS[qid],
    }
    log_outcome("MCPServer", "GET_EXEMPLAR", "SUCCESS", f"Retrieved exemplar for {qid}")
    return result


EXEMPLARY_ANSWERS = EXEMPLAR_ANSWERS


def get_canon_mcp_toolset() -> McpToolset:
    """Create an ADK McpToolset connecting to this MCP server via stdio.

    Returns:
        Configured McpToolset ready to attach to ADK agents.
    """
    from mcp import StdioServerParameters

    script_path = os.path.abspath(__file__)
    python_exe = sys.executable

    connection_params = StdioConnectionParams(
        server_params=StdioServerParameters(
            command=python_exe,
            args=[script_path],
        ),
        timeout=30,
    )
    return McpToolset(
        connection_params=connection_params,
        tool_filter=[
            "lookup_hunger_games_canon",
            "fetch_exam_rubric_criteria",
            "get_exemplar_answer",
        ],
    )


if __name__ == "__main__":
    # Run the MCP server over stdio for external MCP clients or ADK
    asyncio.run(server.run_stdio_async())
