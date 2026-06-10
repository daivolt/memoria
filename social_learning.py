"""
social_learning — Tomasello (1999) cultural intelligence for multi-agent systems.

Three pillars:
  1. Teaching protocol — structured lessons from teacher → student agents
  2. Cross-generational knowledge accumulation — insights consolidated across
     agent generations and inherited by new agents
  3. Cultural evolution — topics/lessons mutate and are selected across
     generations via variation-and-selection

Data is stored in /var/tmp/memoria/culture/ alongside other memoria state.
"""

import copy
import json
import math
import random
import textwrap
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

CULTURE_DIR = Path("/var/tmp/memoria/culture")
CULTURE_DIR.mkdir(parents=True, exist_ok=True)

MAX_LESSON_FACTS = 20
MAX_CULTURAL_MEMORY = 500
GENERATION_MUTATION_RATE = 0.15
SELECTION_PRESSURE = 0.7  # fraction of top facts that survive selection


# ── Lesson Model ───────────────────────────────────────────────

class Lesson:
    """A structured teaching unit created by one agent for another.

    Lessons encode knowledge in a format suitable for cross-agent transfer:
      - title + topic:  what the lesson is about
      - prerequisites:  which lessons/lessons should come first
      - facts:          structured knowledge items (list of strings)
      - examples:       concrete worked examples
      - exercises:      practice items the student can attempt
      - generation:     cultural generation counter (starts at 0, increments with variation)
      - score:          aggregate outcome metric (0–1, higher = more effective)
      - n_students:     how many agents have completed this lesson
    """

    def __init__(
        self,
        lesson_id: str | None = None,
        title: str = "",
        topic: str = "",
        prerequisites: list[str] | None = None,
        facts: list[str] | None = None,
        examples: list[str] | None = None,
        exercises: list[str] | None = None,
        teacher_agent: str = "",
        creator_project: str = "",
        generation: int = 0,
        parent_id: str | None = None,
        score: float = 0.5,
        n_students: int = 0,
        created_at: float | None = None,
    ):
        self.lesson_id = lesson_id or f"lesson_{int(time.time())}_{random.randint(1000,9999)}"
        self.title = title[:200]
        self.topic = topic[:100]
        self.prerequisites = prerequisites or []
        self.facts = facts or []
        self.examples = examples or []
        self.exercises = exercises or []
        self.teacher_agent = teacher_agent[:64]
        self.creator_project = creator_project[:100]
        self.generation = generation
        self.parent_id = parent_id
        self.score = max(0.0, min(1.0, score))
        self.n_students = n_students
        self.created_at = created_at or time.time()

    def to_dict(self) -> dict:
        return {
            "lesson_id": self.lesson_id,
            "title": self.title,
            "topic": self.topic,
            "prerequisites": self.prerequisites,
            "facts": self.facts,
            "examples": self.examples,
            "exercises": self.exercises,
            "teacher_agent": self.teacher_agent,
            "creator_project": self.creator_project,
            "generation": self.generation,
            "parent_id": self.parent_id,
            "score": self.score,
            "n_students": self.n_students,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Lesson":
        return cls(
            lesson_id=d.get("lesson_id"),
            title=d.get("title", ""),
            topic=d.get("topic", ""),
            prerequisites=d.get("prerequisites", []),
            facts=d.get("facts", []),
            examples=d.get("examples", []),
            exercises=d.get("exercises", []),
            teacher_agent=d.get("teacher_agent", ""),
            creator_project=d.get("creator_project", ""),
            generation=d.get("generation", 0),
            parent_id=d.get("parent_id"),
            score=d.get("score", 0.5),
            n_students=d.get("n_students", 0),
            created_at=d.get("created_at"),
        )

    def mutate(self, rate: float = GENERATION_MUTATION_RATE) -> "Lesson":
        """Produce a child lesson with cultural variation.

        Applies random mutations to facts, examples, and exercises
        according to the given mutation rate.  Increments generation.
        """
        child = copy.deepcopy(self)
        child.generation += 1
        child.parent_id = self.lesson_id
        child.lesson_id = f"lesson_{int(time.time())}_{random.randint(1000,9999)}"
        child.n_students = 0
        child.score = 0.5  # reset until assessed

        def _mutate_list(items: list[str]) -> list[str]:
            out = []
            for item in items:
                if random.random() < rate:
                    words = item.split()
                    if words and random.random() < 0.5:
                        random.shuffle(words)
                        out.append(" ".join(words))
                    else:
                        pass
                else:
                    out.append(item)
            if random.random() < rate:
                out.append(f"[variation] mutated addition gen {child.generation}")
            return out

        child.facts = _mutate_list(self.facts)
        child.examples = _mutate_list(self.examples)
        child.exercises = _mutate_list(self.exercises)
        return child


# ── Lesson Store ────────────────────────────────────────────────

def _lesson_path(lesson_id: str) -> Path:
    return CULTURE_DIR / "lessons" / f"{lesson_id}.json"


def save_lesson(lesson: Lesson):
    (CULTURE_DIR / "lessons").mkdir(parents=True, exist_ok=True)
    _lesson_path(lesson.lesson_id).write_text(
        json.dumps(lesson.to_dict(), ensure_ascii=False, indent=2)
    )


def load_lesson(lesson_id: str) -> Lesson | None:
    p = _lesson_path(lesson_id)
    if not p.exists():
        return None
    try:
        return Lesson.from_dict(json.loads(p.read_text()))
    except (json.JSONDecodeError, OSError):
        return None


def delete_lesson(lesson_id: str):
    p = _lesson_path(lesson_id)
    if p.exists():
        p.unlink()


def list_lessons(topic: str | None = None, project: str | None = None,
                 min_score: float = 0.0) -> list[Lesson]:
    lessons_dir = CULTURE_DIR / "lessons"
    if not lessons_dir.exists():
        return []
    result = []
    for f in sorted(lessons_dir.iterdir()):
        if f.suffix != ".json":
            continue
        try:
            lesson = Lesson.from_dict(json.loads(f.read_text()))
        except (json.JSONDecodeError, OSError):
            continue
        if topic and lesson.topic.lower() != topic.lower():
            continue
        if project and lesson.creator_project != project:
            continue
        if lesson.score < min_score:
            continue
        result.append(lesson)
    return result


def find_lessons_for_topic(topic: str, top_k: int = 3) -> list[Lesson]:
    """Find the best-scoring lessons for a given topic."""
    candidates = list_lessons(topic=topic, min_score=0.2)
    candidates.sort(key=lambda l: (-l.score, -l.n_students))
    return candidates[:top_k]


# ── Teaching Protocol ───────────────────────────────────────────

def create_lesson_from_agent(
    teacher_agent: str,
    project: str,
    title: str,
    topic: str,
    facts: list[str],
    examples: list[str] | None = None,
    exercises: list[str] | None = None,
    prerequisites: list[str] | None = None,
) -> Lesson:
    """Create a structured lesson from an agent's knowledge."""
    lesson = Lesson(
        title=title,
        topic=topic,
        facts=facts[:MAX_LESSON_FACTS],
        examples=examples or [],
        exercises=exercises or [],
        teacher_agent=teacher_agent,
        creator_project=project,
        generation=0,
        prerequisites=prerequisites or [],
    )
    save_lesson(lesson)
    return lesson


def record_student_outcome(
    lesson_id: str,
    student_agent: str,
    success: bool,
    score_delta: float | None = None,
) -> dict:
    """Record a student's result for a lesson and update its score.

    Score is a running weighted average of student outcomes.
    Returns the updated lesson dict.
    """
    lesson = load_lesson(lesson_id)
    if lesson is None:
        return {"error": f"lesson {lesson_id} not found"}

    old_n = lesson.n_students
    outcome = 1.0 if success else (score_delta if score_delta is not None else 0.3)
    w = 1.0 / (old_n + 1)
    lesson.score = (1 - w) * lesson.score + w * outcome
    lesson.n_students += 1
    save_lesson(lesson)

    outcome_path = CULTURE_DIR / "outcomes.jsonl"
    with open(outcome_path, "a") as f:
        f.write(json.dumps({
            "lesson_id": lesson_id,
            "student_agent": student_agent,
            "success": success,
            "outcome": outcome,
            "score_after": round(lesson.score, 3),
            "timestamp": time.time(),
        }, ensure_ascii=False) + "\n")

    return {"lesson_id": lesson_id, "score": lesson.score, "n_students": lesson.n_students}


def get_student_outcomes(student_agent: str, limit: int = 20) -> list[dict]:
    """Return recent outcomes for a given student agent."""
    outcome_path = CULTURE_DIR / "outcomes.jsonl"
    if not outcome_path.exists():
        return []
    lines = outcome_path.read_text().strip().splitlines()
    matched = []
    for line in reversed(lines):
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if rec.get("student_agent") == student_agent:
            matched.append(rec)
            if len(matched) >= limit:
                break
    return matched


# ── Cultural Memory (cross-generational accumulation) ───────────

class CulturalMemory:
    """Accumulated knowledge across agent generations.

    Each generation of agents adds structured insights (facts with provenance).
    Selection retains only high-value insights.  New agents inherit the
    accumulated corpus when they start.
    """

    def __init__(self, project: str):
        self.project = project
        self.facts: list[dict] = []
        self.generation: int = 0
        self._load()

    def _path(self) -> Path:
        return CULTURE_DIR / f"cultural_memory_{self.project}.json"

    def _load(self):
        p = self._path()
        if p.exists():
            try:
                data = json.loads(p.read_text())
                self.facts = data.get("facts", [])
                self.generation = data.get("generation", 0)
            except (json.JSONDecodeError, OSError):
                pass

    def save(self):
        self._path().write_text(json.dumps({
            "facts": self.facts[-MAX_CULTURAL_MEMORY:],
            "generation": self.generation,
            "project": self.project,
        }, ensure_ascii=False, indent=2))

    def add_fact(self, text: str, source_agent: str = "", source_task: str = "",
                 topic: str = "", importance: float = 0.5):
        entry = {
            "text": text[:500],
            "source_agent": source_agent[:64],
            "source_task": source_task[:200],
            "topic": topic[:100],
            "importance": max(0.0, min(1.0, importance)),
            "generation": self.generation,
            "added_at": time.time(),
        }
        self.facts.append(entry)
        if len(self.facts) > MAX_CULTURAL_MEMORY:
            self._prune()
        self.save()

    def add_lesson_as_cultural_fact(self, lesson: Lesson):
        """Absorb a lesson's facts into the cultural memory."""
        for fact in lesson.facts:
            self.add_fact(
                text=fact,
                source_agent=lesson.teacher_agent,
                source_task=f"lesson:{lesson.lesson_id}",
                topic=lesson.topic,
                importance=lesson.score,
            )

    def _prune(self):
        """Selection: keep only the most important facts."""
        self.facts.sort(key=lambda f: (-f["importance"], -f.get("added_at", 0)))
        keep = max(50, int(len(self.facts) * SELECTION_PRESSURE))
        self.facts = self.facts[:keep]

    def inherit(self) -> list[str]:
        """Return the inherited knowledge for a new agent.

        New agents start with the highest-importance facts from cultural memory.
        """
        sorted_facts = sorted(self.facts, key=lambda f: -f["importance"])
        return [f["text"] for f in sorted_facts[:20]]

    def get_topic_summary(self, topic: str) -> list[str]:
        """Return facts filtered by topic."""
        return [f["text"] for f in self.facts
                if f.get("topic", "").lower() == topic.lower()]

    def next_generation(self):
        """Advance generation counter (called when a cohort of agents finishes)."""
        self.generation += 1
        self.save()


# ── Cultural Evolution Engine ───────────────────────────────────

class CulturalEvolution:
    """Drives variation and selection on lessons across generations.

    - Variation:  successful lessons are mutated to produce child lessons
    - Selection:  low-scoring lessons are pruned
    - Emergence:  new topics can arise from cross-topic combination
    """

    def __init__(self, project: str):
        self.project = project
        self.mutation_rate = GENERATION_MUTATION_RATE

    def evolve_generation(self, topic: str | None = None):
        """Run one generation cycle of variation + selection on lessons."""
        lessons = list_lessons(topic=topic, project=self.project)

        if not lessons:
            return {"variations": 0, "pruned": 0, "emerged": []}

        before = len(lessons)

        # 1. Selection: drop lessons below threshold
        surviving = [l for l in lessons if l.score >= 0.3 or l.n_students < 3]
        pruned = before - len(surviving)

        # 2. Variation: mutate the best lessons
        top_lessons = sorted(surviving, key=lambda l: (-l.score, -l.n_students))[:5]
        variations = []
        for lesson in top_lessons:
            if lesson.score >= 0.5 and lesson.n_students >= 1:
                child = lesson.mutate(rate=self.mutation_rate)
                child.creator_project = self.project
                save_lesson(child)
                variations.append(child.lesson_id)

        # 3. Emergence: combine facts from top-2 topics to form new lessons
        emerged = []
        topics_used = set(l.topic for l in top_lessons if l.topic)
        if len(topics_used) >= 2:
            topic_list = list(topics_used)
            for i in range(min(2, len(topic_list) - 1)):
                t1 = topic_list[i]
                t2 = topic_list[i + 1]
                cross_lessons = [l for l in top_lessons if l.topic in (t1, t2)]
                if len(cross_lessons) >= 2:
                    combined_facts = []
                    for cl in cross_lessons:
                        combined_facts.extend(cl.facts[:3])
                    if combined_facts:
                        emerged_topic = f"{t1}+{t2}"
                        merged = Lesson(
                            title=f"Merged: {t1} × {t2}",
                            topic=emerged_topic[:100],
                            facts=list(dict.fromkeys(combined_facts))[:MAX_LESSON_FACTS],
                            prerequisites=[l.lesson_id for l in cross_lessons[:2]],
                            teacher_agent="evolution",
                            creator_project=self.project,
                            generation=max(l.generation for l in cross_lessons) + 1,
                        )
                        save_lesson(merged)
                        emerged.append(merged.lesson_id)

        # 4. Clean up pruned lessons
        for lesson in lessons:
            if lesson.score < 0.3 and lesson.n_students >= 3:
                delete_lesson(lesson.lesson_id)

        return {
            "variations": len(variations),
            "pruned": pruned,
            "emerged": emerged,
        }

    def topic_diversity(self) -> dict:
        """Return diversity metrics for the current lesson corpus."""
        lessons = list_lessons(project=self.project)
        topics = Counter(l.topic for l in lessons if l.topic)
        return {
            "total_lessons": len(lessons),
            "unique_topics": len(topics),
            "topic_distribution": dict(topics.most_common(20)),
            "avg_score": round(
                sum(l.score for l in lessons) / len(lessons), 3
            ) if lessons else 0.0,
            "generation_max": max(l.generation for l in lessons) if lessons else 0,
        }


# ── Global registry ─────────────────────────────────────────────

_cultural_memories: dict[str, CulturalMemory] = {}
_evolution_engines: dict[str, CulturalEvolution] = {}


def get_cultural_memory(project: str) -> CulturalMemory:
    if project not in _cultural_memories:
        _cultural_memories[project] = CulturalMemory(project)
    return _cultural_memories[project]


def get_evolution_engine(project: str) -> CulturalEvolution:
    if project not in _evolution_engines:
        _evolution_engines[project] = CulturalEvolution(project)
    return _evolution_engines[project]


# ── Consolidation hook (called from memoria's multi-timescale loop) ──

def consolidate_cultural_knowledge(project: str):
    """Called periodically to consolidate task outcomes into cultural memory.

    Extracts high-reward episodes from CORTEX's hippocampus and adds them
    as cultural facts.  Then runs one generation of cultural evolution.
    """
    from cortex import get_engine

    engine = get_engine(project)
    memory = get_cultural_memory(project)
    evolution = get_evolution_engine(project)

    # Absorb high-reward hippocampal episodes as cultural facts
    for ep in engine.hippocampus.episodes:
        if ep.get("reward", 0) >= 0.6:
            meta = ep.get("meta", {}) or {}
            memory.add_fact(
                text=ep.get("task_title", "")[:300],
                source_agent=ep.get("agent_id", ""),
                source_task=ep.get("task_id", ""),
                topic=meta.get("type", "generic"),
                importance=ep.get("reward", 0.5),
            )

    # Run evolution
    result = evolution.evolve_generation()
    if result["variations"] or result["emerged"]:
        memory.next_generation()

    return {
        "facts_added": sum(1 for ep in engine.hippocampus.episodes if ep.get("reward", 0) >= 0.6),
        "variations": result["variations"],
        "pruned": result["pruned"],
        "emerged": result["emerged"],
        "generation": memory.generation,
    }


def get_agent_curriculum(project: str, agent_capabilities: list[str]) -> list[Lesson]:
    """Build a curriculum for a new agent based on cultural memory.

    Selects lessons in dependency order, filtering by agent capabilities
    and topic relevance.
    """
    memory = get_cultural_memory(project)
    inherited = memory.inherit()

    # Find relevant lessons, prioritizing high-score ones in dependency order
    all_lessons = list_lessons(project=project, min_score=0.2)
    capabilities_lower = [c.lower() for c in agent_capabilities]

    def topic_relevance(lesson: Lesson) -> float:
        tl = lesson.topic.lower()
        for cap in capabilities_lower:
            if cap in tl or tl in cap:
                return 1.0
        return 0.3

    scored = [(topic_relevance(l), l.score, l) for l in all_lessons]
    scored.sort(key=lambda x: (-x[0], -x[1]))

    # Build ordered list respecting prerequisites
    selected: list[Lesson] = []
    selected_ids: set[str] = set()
    for _, _, lesson in scored:
        if lesson.lesson_id in selected_ids:
            continue
        prereqs_met = all(p in selected_ids for p in lesson.prerequisites)
        if prereqs_met or not lesson.prerequisites:
            selected.append(lesson)
            selected_ids.add(lesson.lesson_id)
        if len(selected) >= 10:
            break

    return selected
