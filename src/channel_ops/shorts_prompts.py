"""Transformation-toy prompt generation for the Unfoldables Shorts channel.

Each video is produced from a *pair* of prompts, because Google Flow works in
two stages: a text-to-image prompt fixes the closed toy resting in a hand, then
an image-to-video prompt animates the button press and the unfolding.

The wording that survived testing is deliberately kept out of this repository.
It is a working formula and the repository is public, so the skeleton lives in
the ``PROMPT_TEMPLATE`` secret and only the slot names are visible here. There
is no built-in fallback on purpose — a missing secret fails loudly rather than
silently publishing weaker prompts.

The template exposes these slots:

===================  ============================================
``shape``            closed silhouette (dome, oval, sphere, …)
``material``         shell material and finish
``internal_detail``  what is glimpsed inside while still closed
``button``           how the trigger looks, for the still frame
``button_short``     the same trigger, phrased for the video prompt
``creature``         what it unfolds into
``shell_mechanic``   how the shell opens
``emerging_parts``   which parts push out and lock into place
===================  ============================================
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from .config_loader import find_project_root
from .providers.base import AIProvider

logger = logging.getLogger(__name__)

HISTORY_FILE = "data/shorts_history.json"

# How many past creatures to keep out of new suggestions.
RECENT_MEMORY = 40

_SLOTS = (
    "shape",
    "material",
    "internal_detail",
    "button",
    "button_short",
    "creature",
    "shell_mechanic",
    "emerging_parts",
)

_CONCEPT_INSTRUCTIONS = """\
You invent concepts for a series of short videos. Every video shows a small
mechanical toy resting in a palm; a button is pressed and the toy unfolds into
a miniature mechanical creature or machine.

Return ONLY a JSON array of {count} objects, no prose and no code fences. Each
object must have exactly these string fields:
{slots}

Hard rules, in priority order:

1. PLAUSIBLE FOLDING. The closed shape must believably BE the creature's main
   body or shell, so nothing has to appear from nowhere and nothing grows. A
   dome is a turtle's shell; a sphere is an owl's body; a hexagonal puck is a
   beetle's wing cases. Never pair a shape with a creature that could not fold
   back into it.
2. MATERIAL MATCHES SUBJECT. Cast iron and copper pipes suit a steam engine;
   antique brass and clockwork suit an owl; transparent resin over glowing
   fibre optics suits something crystalline. The material should explain the
   creature, not fight it.
3. SIMPLE MECHANICS. One shell movement (splits, slides, folds outward) plus
   two or three parts pushing out. Resist elaborate multi-stage descriptions;
   video models lose coherence and the transformation stops looking physical.
4. `shell_mechanic` is a clause continuing "The unfolding process is logical
   and physically consistent." and must end ready for ", revealing the movement
   of internal mechanical components and gears." Example: "The hexagonal shell
   splits along its titanium seams and the two halves rotate outward like
   elytra".
5. `emerging_parts` is a noun phrase that reads naturally before "physically
   push out into place". Example: "Six folded segmented legs and a small
   antennaed head".
6. `button_short` is a short noun phrase for the same button, e.g. "the
   recessed titanium button at the centre of the obsidian puck".
7. Write every field in English, in plain descriptive prose, with no camera or
   lighting directions — the template already supplies those.
8. GRAMMAR. Every field is a fragment dropped into a sentence, never a sentence
   of its own, and none may end with a full stop. Articles differ per field:
   * `shape` — one to four words, no article, and never the word "shaped"
     since the template appends "-shaped": "dome", "rounded hexagonal puck".
   * `creature` — no article, and never the word "mechanical"; the template
     already says it. Write "scarab beetle", not "a mechanical scarab beetle".
   * `button` — KEEP the article, because the template does not supply one:
     "a single recessed titanium button ringed by amber light".
   * `button_short` — KEEP "the": "the recessed titanium button at the centre".
   * `material` and `internal_detail` — noun phrases, no article needed:
     "polished obsidian-black ceramic", not "It is made of ceramic."
   * `shell_mechanic` — a complete independent clause with its own subject and
     verb, because the template continues it with ", revealing the movement of
     internal mechanical components and gears". Start with the thing that
     moves: "The hexagonal shell splits along its titanium seams". NEVER open
     with "as", "when", "while" or "once" — that leaves the sentence unfinished.

Do not reuse any of these creatures: {avoid}
"""

# Slots dropped into the middle of a sentence; they must not start with a
# capital or an article. The rest begin a sentence and keep their capital.
_MID_SENTENCE = frozenset(
    {"shape", "material", "internal_detail", "button", "button_short", "creature"}
)

# Only these two follow an article the template already supplies ("a compact,
# {shape}-shaped object", "a miniature mechanical {creature}"). The button
# fields carry their own, so stripping it there would produce "features small,
# ruby button".
_ARTICLE_SUPPLIED = frozenset({"shape", "creature"})

# The template continues shell_mechanic with ", revealing …", so a subordinate
# opener leaves the sentence without a main clause.
_SUBORDINATOR = re.compile(r"^(?:as|when|while|once|after|then)\s+", re.IGNORECASE)
_DETERMINER = re.compile(r"^(?:the|a|an|its|two|three|four|six|eight)\b", re.IGNORECASE)
_ARTICLE = re.compile(r"^(?:a|an|the)\b", re.IGNORECASE)


def _indefinite(text: str) -> str:
    return "an" if text[:1].lower() in "aeiou" else "a"


def _clean_slot(value: object, slot: str) -> str:
    """Reshape a model-written field into the fragment the template expects.

    The instructions ask for fragments, but models drift back into whole
    sentences — which renders as "a compact, A sleek cylinder.-shaped object".
    Normalising here rather than trusting the prompt keeps that out of the
    published wording.
    """
    text = " ".join(str(value).split()).strip()
    text = text.rstrip(" .")  # the template supplies the punctuation
    if not text:
        return text

    if slot == "shell_mechanic":
        text = _SUBORDINATOR.sub("", text)
        if not _DETERMINER.match(text):
            text = f"The {text}"
    elif slot == "button" and not _ARTICLE.match(text):
        # "The toy features small copper rivet" — the template supplies no
        # article here, so a missing one has to be restored.
        text = f"{_indefinite(text)} {text}"
    elif slot == "button_short" and not _ARTICLE.match(text):
        text = f"the {text}"

    if slot in _MID_SENTENCE:
        if slot in _ARTICLE_SUPPLIED:
            text = re.sub(r"^(?:a|an|the)\s+", "", text, flags=re.IGNORECASE)
        if slot == "creature":
            # "mechanical dragonfly" would render as "mechanical mechanical …".
            text = re.sub(r"^mechanical\s+", "", text, flags=re.IGNORECASE)
        # Two leading capital *letters* mean an acronym such as LED or USB;
        # "A small ruby button" must not be mistaken for one.
        lead = text[:2]
        if not (lead.isalpha() and lead.isupper()):
            text = text[0].lower() + text[1:]
    else:
        text = text[0].upper() + text[1:]
    return text


class TemplateMissingError(RuntimeError):
    """Raised when the prompt skeleton has not been provided."""


@dataclass(frozen=True)
class Concept:
    """One video idea, filling every slot in the template."""

    shape: str
    material: str
    internal_detail: str
    button: str
    button_short: str
    creature: str
    shell_mechanic: str
    emerging_parts: str


@dataclass(frozen=True)
class PromptPair:
    """The two prompts Flow needs, plus the concept they came from."""

    concept: Concept
    text_to_image: str
    image_to_video: str


# -----------------------------------------------------------------------
# Template
# -----------------------------------------------------------------------

def load_template() -> tuple[str, str]:
    """Return the (text-to-image, image-to-video) skeletons from the secret.

    The secret holds ``{"t2i": "...", "i2v": "..."}``.
    """
    raw = os.getenv("PROMPT_TEMPLATE", "").strip()
    if not raw:
        raise TemplateMissingError(
            "PROMPT_TEMPLATE is not set. The prompt skeleton is kept in a secret rather "
            "than in this public repository. Add it under Settings > Secrets and "
            "variables > Actions as JSON: {\"t2i\": \"...\", \"i2v\": \"...\"}"
        )
    try:
        data = json.loads(raw)
        return data["t2i"], data["i2v"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise TemplateMissingError(
            f"PROMPT_TEMPLATE is malformed ({exc}). Expected JSON with 't2i' and 'i2v' keys."
        ) from exc


def template_version() -> str:
    """A short fingerprint of the current template wording.

    Template changes are evaluated by comparing videos made before and after
    them, which cannot be done reliably from memory once a few days have
    passed. Recording this against each published video makes the comparison
    exact. It is a hash rather than the text itself so the formula stays out of
    this public repository's data files.
    """
    try:
        t2i, i2v = load_template()
    except TemplateMissingError:
        return "unset"
    return hashlib.sha256(f"{t2i}\x00{i2v}".encode()).hexdigest()[:12]


def render(concept: Concept) -> PromptPair:
    """Fill the template with *concept*.

    Unused slots are tolerated so the secret can be reworded without a code
    change, but a slot the template asks for and the concept lacks is a real
    error and is reported by name.
    """
    t2i_template, i2v_template = load_template()
    values = asdict(concept)

    def fill(template: str, label: str) -> str:
        try:
            return template.format(**values)
        except KeyError as exc:
            raise RuntimeError(
                f"The {label} template references unknown slot {exc}. "
                f"Available slots: {', '.join(_SLOTS)}"
            ) from exc

    return PromptPair(
        concept=concept,
        text_to_image=fill(t2i_template, "text-to-image"),
        image_to_video=fill(i2v_template, "image-to-video"),
    )


# -----------------------------------------------------------------------
# History
# -----------------------------------------------------------------------

def _history_path(root: Path | None = None) -> Path:
    return (root or find_project_root()) / HISTORY_FILE


def load_used_creatures(root: Path | None = None) -> list[str]:
    """Return previously suggested creatures, so ideas stay fresh."""
    path = _history_path(root)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return [str(item) for item in data.get("creatures", [])]
    except (json.JSONDecodeError, AttributeError, TypeError) as exc:
        logger.warning("Unreadable history at %s (%s) — treating as empty", path, exc)
        return []


def remember_creatures(creatures: list[str], root: Path | None = None) -> Path:
    """Append *creatures* to the history, keeping only the recent window."""
    path = _history_path(root)
    combined = load_used_creatures(root) + [c.strip() for c in creatures if c.strip()]

    deduped: list[str] = []
    seen: set[str] = set()
    for creature in reversed(combined):  # keep the most recent of any duplicate
        key = creature.lower()
        if key not in seen:
            seen.add(key)
            deduped.append(creature)
    deduped = list(reversed(deduped))[-RECENT_MEMORY:]

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"creatures": deduped}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


# -----------------------------------------------------------------------
# Concept generation
# -----------------------------------------------------------------------

def _strip_code_fence(text: str) -> str:
    """Remove a ```json ... ``` wrapper if the model added one."""
    fenced = re.match(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", text, re.DOTALL)
    return fenced.group(1) if fenced else text.strip()


def parse_concepts(raw: str) -> list[Concept]:
    """Parse the model's JSON array into concepts, ignoring extra fields."""
    try:
        data = json.loads(_strip_code_fence(raw))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Concept response was not valid JSON: {exc}") from exc

    if not isinstance(data, list):
        raise RuntimeError(f"Expected a JSON array of concepts, got {type(data).__name__}.")

    concepts: list[Concept] = []
    for index, item in enumerate(data, start=1):
        if not isinstance(item, dict):
            raise RuntimeError(f"Concept {index} is not an object.")
        cleaned = {slot: _clean_slot(item.get(slot, ""), slot) for slot in _SLOTS}
        missing = [slot for slot, value in cleaned.items() if not value]
        if missing:
            raise RuntimeError(f"Concept {index} is missing: {', '.join(missing)}")
        concepts.append(Concept(**cleaned))
    return concepts


def generate_concepts(
    provider: AIProvider,
    *,
    count: int = 3,
    avoid: list[str] | None = None,
) -> list[Concept]:
    """Ask the provider for *count* fresh concepts."""
    avoid_list = avoid or []
    instructions = _CONCEPT_INSTRUCTIONS.format(
        count=count,
        slots="\n".join(f"  - {slot}" for slot in _SLOTS),
        avoid=", ".join(avoid_list) if avoid_list else "(none yet)",
    )
    raw = provider.generate(f"Invent {count} new concepts.", system_prompt=instructions)
    concepts = parse_concepts(raw)
    logger.info("Generated %d concept(s): %s", len(concepts), ", ".join(c.creature for c in concepts))
    return concepts


def generate_prompt_pairs(
    provider: AIProvider,
    *,
    count: int = 3,
    root: Path | None = None,
    remember: bool = True,
) -> list[PromptPair]:
    """Generate concepts and render them, skipping recently used creatures."""
    concepts = generate_concepts(provider, count=count, avoid=load_used_creatures(root))
    pairs = [render(concept) for concept in concepts]
    if remember:
        remember_creatures([c.creature for c in concepts], root)
    return pairs
