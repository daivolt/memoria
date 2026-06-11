"""
grounded_cognition — Ground symbols in perception per Barsalou (2008).

Connects memoria's purely textual topics to visual representations via
DINOv2 vision backbone and ImageBind-style cross-modal alignment.

Architecture:
  1. Frozen DINOv2 ViT extracts visual embeddings from images/screens.
  2. ImageBind adapter (when available) maps between text concepts and
     visual regions (classify_region, search_by_text).
  3. VisualTopicEncoder stores per-concept visual embeddings alongside
     textual topic facts in /var/tmp/memoria/visual/<concept>/.
  4. Demonstration engine shows that a concept like 'red' has BOTH
     textual (topic facts) and visual (embedding) representations.

Reference: Barsalou, L. W. (2008). Grounded cognition. Annu. Rev. Psychol.
           DINOv2 (Oquab et al., 2023). ImageBind (Girdhar et al., 2023).
"""

import json
import logging
import time
from pathlib import Path
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

VISUAL_DIR = Path("/var/tmp/memoria/visual")
DEMO_EMBEDDING_DIM = 384


# ── Visual Embedding Store ──────────────────────────────────────


class VisualTopicEncoder:
    """Persist visual embeddings alongside textual topic facts.

    Storage layout:
      /var/tmp/memoria/visual/<concept>/
        index.json   — mapping from text label / source to embedding filename
        *.npy        — saved visual embeddings (384d for DINOv2 small)
        metadata.json — concept-level metadata (source, modality, creation time)

    This implements the core insight of grounded cognition: a concept like
    'red' exists both as a text symbol (topic facts) AND as a visual pattern
    (embedding). The two representations are linked through this store.
    """

    def __init__(self, visual_root: Path = VISUAL_DIR):
        self.root = visual_root
        self.root.mkdir(parents=True, exist_ok=True)
        self._embedding_cache: dict[str, np.ndarray] = {}

    def _concept_dir(self, concept: str) -> Path:
        sanitized = "".join(c if c.isalnum() or c in "-_" else "_" for c in concept)
        p = self.root / sanitized.lower()
        p.mkdir(parents=True, exist_ok=True)
        return p

    def _index_path(self, concept: str) -> Path:
        return self._concept_dir(concept) / "index.json"

    def _metadata_path(self, concept: str) -> Path:
        return self._concept_dir(concept) / "metadata.json"

    def store_embedding(
        self,
        concept: str,
        embedding: np.ndarray,
        label: str = "",
        source: str = "",
        modality: str = "visual",
    ):
        """Store a visual embedding for a given concept.

        Args:
            concept:  Concept name (e.g., 'red', 'button', 'error_message').
            embedding: 1-D numpy array from DINOv2 or ImageBind projection.
            label:     Human-readable label for this specific embedding.
            source:    Data source (e.g., 'screenshot', 'demo_generated').
            modality:  'visual' for DINOv2, 'imagebind' for cross-modal.
        """
        cdir = self._concept_dir(concept)
        ts = int(time.time())
        filename = f"{label or 'embedding'}_{ts}.npy"
        fpath = cdir / filename
        np.save(str(fpath), embedding)

        index = self._load_index(concept)
        index.append({
            "filename": filename,
            "label": label,
            "source": source,
            "modality": modality,
            "created_at": ts,
            "dim": int(embedding.shape[0]),
        })
        self._save_index(concept, index)

        meta = self._load_metadata(concept)
        meta["last_updated"] = ts
        meta["embedding_count"] = len(index)
        self._save_metadata(concept, meta)

        self._embedding_cache[str(fpath)] = embedding
        logger.info(
            "Stored %s embedding for concept '%s' (%s, dim=%d)",
            modality, concept, label or "unlabeled", embedding.shape[0],
        )

    def load_embeddings(self, concept: str) -> list[dict]:
        """Load all stored embeddings for a concept.

        Returns list of dicts with 'embedding' (np.ndarray) and metadata.
        """
        index = self._load_index(concept)
        cdir = self._concept_dir(concept)
        results = []
        for entry in index:
            fpath = cdir / entry["filename"]
            if not fpath.exists():
                continue
            cached = self._embedding_cache.get(str(fpath))
            if cached is not None:
                emb = cached
            else:
                emb = np.load(str(fpath))
                self._embedding_cache[str(fpath)] = emb
            results.append({**entry, "embedding": emb})
        return results

    def get_all_concepts(self) -> list[str]:
        """List all concepts that have visual embeddings stored."""
        if not self.root.exists():
            return []
        return sorted(
            d.name for d in self.root.iterdir()
            if d.is_dir() and (d / "index.json").exists()
        )

    def concept_summary(self, concept: str) -> dict:
        """Return summary metadata for a grounded concept."""
        meta = self._load_metadata(concept)
        index = self._load_index(concept)
        return {
            "concept": concept,
            "embedding_count": len(index),
            "last_updated": meta.get("last_updated", 0),
            "modalities": list({e.get("modality", "visual") for e in index}),
            "labels": [e.get("label", "") for e in index],
        }

    def delete_concept(self, concept: str) -> bool:
        """Remove all visual data for a concept."""
        cdir = self._concept_dir(concept)
        if not cdir.exists():
            return False
        import shutil
        shutil.rmtree(str(cdir))
        keys = [k for k in self._embedding_cache if concept in k]
        for k in keys:
            self._embedding_cache.pop(k, None)
        return True

    def _load_index(self, concept: str) -> list[dict]:
        ipath = self._index_path(concept)
        if not ipath.exists():
            return []
        try:
            return json.loads(ipath.read_text())
        except (json.JSONDecodeError, OSError):
            return []

    def _save_index(self, concept: str, index: list[dict]):
        self._index_path(concept).write_text(
            json.dumps(index, indent=2, default=str)
        )

    def _load_metadata(self, concept: str) -> dict:
        mpath = self._metadata_path(concept)
        if not mpath.exists():
            return {"concept": concept, "created_at": int(time.time())}
        try:
            return json.loads(mpath.read_text())
        except (json.JSONDecodeError, OSError):
            return {"concept": concept}

    def _save_metadata(self, concept: str, meta: dict):
        self._metadata_path(concept).write_text(json.dumps(meta, indent=2))


# ── Concept Grounder (Text ↔ Visual alignment) ──────────────────


class ConceptGrounder:
    """Align text concepts to visual representations.

    Uses ImageBind cross-modal space when available, falls back to
    DINOv2-only embeddings for visual similarity.
    """

    def __init__(
        self,
        visual_encoder: Optional[VisualTopicEncoder] = None,
    ):
        self.encoder = visual_encoder or VisualTopicEncoder()
        self._dinov2 = None
        self._imagebind = None

    def load_backbones(self, device: str = "cpu"):
        """Load DINOv2 and optionally ImageBind."""
        if self._dinov2 is not None:
            return
        try:
            from agent.vision_research import DINOv2Backbone
            self._dinov2 = DINOv2Backbone(device=device)
            self._dinov2.load()
            logger.info("DINOv2 backbone loaded for grounded cognition")
        except ImportError:
            logger.warning(
                "DINOv2 not available. Install: pip install torch transformers. "
                "Falling back to demo embeddings."
            )
        try:
            from agent.vision_research import ImageBindAdapter
            self._imagebind = ImageBindAdapter(backbone=self._dinov2)
            self._imagebind.load()
            logger.info("ImageBind adapter loaded for cross-modal grounding")
        except ImportError:
            logger.info(
                "ImageBind not available. Cross-modal grounding limited to "
                "visual similarity."
            )

    @property
    def is_loaded(self) -> bool:
        return self._dinov2 is not None

    def ground_from_image(
        self,
        concept: str,
        image: np.ndarray,
        label: str = "",
        source: str = "input",
    ):
        """Ground a concept by encoding an image into visual embedding.

        The concept's visual representation is stored and linked to the
        textual topic system. Future queries will return BOTH text facts
        and visual embeddings, demonstrating grounded cognition.
        """
        if self._dinov2 is None:
            self.load_backbones()
        if self._dinov2 is None:
            raise RuntimeError("DINOv2 backbone not available")

        embedding = self._dinov2.extract(image, feature_type="cls")
        self.encoder.store_embedding(
            concept=concept,
            embedding=embedding,
            label=label or concept,
            source=source,
            modality="visual",
        )

        if self._imagebind is not None and self._imagebind._imagebind_available:
            projected = self._imagebind._dinov2_to_imagebind(embedding)
            self.encoder.store_embedding(
                concept=concept,
                embedding=projected,
                label=f"{label or concept}_imagebind",
                source=source,
                modality="imagebind",
            )

    def ground_from_text_query(
        self,
        concept: str,
        screen: np.ndarray,
        regions: list[tuple],
        query: str,
    ):
        """Ground a concept by searching for it via text query on a screen.

        Uses ImageBind cross-modal search when available, DINOv2
        otherwise, to find the region that best matches the text query.
        """
        if self._imagebind is None or not self._imagebind._imagebind_available:
            logger.warning("ImageBind required for text-query grounding")
            return False

        if self._dinov2 is None:
            self.load_backbones()
        if self._dinov2 is None:
            return False

        matches = self._imagebind.search_by_text(query, screen, regions, top_k=1)
        if not matches:
            return False

        best = matches[0]
        region = screen[best.y:best.y + best.height, best.x:best.x + best.width]
        self.ground_from_image(concept, region, label=query, source="text_search")
        return True

    def ground_from_classification(
        self,
        region: np.ndarray,
        class_names: list[str],
    ) -> tuple[str, float]:
        """Zero-shot classify a region and auto-ground the best class.

        Returns (best_class, confidence).
        """
        if self._imagebind is None or not self._imagebind._imagebind_available:
            return ("unknown", 0.0)
        best_class, confidence = self._imagebind.classify_region(region, class_names)
        if confidence > 0.3 and best_class != "unknown":
            self.ground_from_image(
                best_class, region, label=f"classified_{best_class}",
                source="zero_shot_classification",
            )
        return best_class, confidence

    def get_cross_modal_similarity(
        self, concept: str, text: str
    ) -> float:
        """Compute similarity between a grounded concept's visual
        representation and a text query (requires ImageBind)."""
        if self._imagebind is None or not self._imagebind._imagebind_available:
            return 0.0
        embeddings = self.encoder.load_embeddings(concept)
        if not embeddings:
            return 0.0
        imagebind_embs = [
            e for e in embeddings if e.get("modality") == "imagebind"
        ]
        if not imagebind_embs:
            return 0.0
        visual_proj = imagebind_embs[0]["embedding"]
        text_emb = self._imagebind._encode_text(text)
        if text_emb is None:
            return 0.0
        return float(np.dot(visual_proj, text_emb))


# ── Grounded Cognition Demonstration ────────────────────────────


def _generate_concept_embedding(concept: str, seed: int = 42) -> np.ndarray:
    """Generate a deterministic synthetic embedding for demo purposes.

    Uses the concept name as a seed so the same concept always produces
    the same embedding — simulating what a real DINOv2 would do.
    """
    rng = np.random.RandomState(hash(concept) % (2 ** 31))
    emb = rng.randn(DEMO_EMBEDDING_DIM).astype(np.float32)
    norm = np.linalg.norm(emb)
    if norm > 0:
        emb = emb / norm
    return emb


def _compute_text_embedding(text: str) -> dict[str, float]:
    """Reuse TextEmbedder to get a textual embedding for comparison."""
    from cortex import TextEmbedder
    return TextEmbedder.embed(text)


class GroundedCognitionEngine:
    """Orchestrator for grounded cognition operations.

    Combines:
      - VisualTopicEncoder: persistent visual embedding store
      - ConceptGrounder: cross-modal text↔visual alignment
      - TextEmbedder: textual n-gram embedding (existing in cortex)
      - Memoria topics: textual topic facts

    Enables full concept grounding: retrieving BOTH textual facts and
    visual embeddings for any grounded concept.
    """

    def __init__(self):
        self.visual_encoder = VisualTopicEncoder()
        self.concept_grounder = ConceptGrounder(self.visual_encoder)
        self._loaded_backbones = False

    def ensure_backbones(self):
        if not self._loaded_backbones:
            self.concept_grounder.load_backbones()
            self._loaded_backbones = True

    def get_grounded_concept(
        self, concept: str
    ) -> Optional[dict]:
        """Retrieve BOTH textual and visual representations of a concept.

        This is the central grounded cognition operation:
        - Textual: topic facts from memoria's topic store (symbolic)
        - Visual: saved embeddings from the visual store (perceptual)

        Returns None if the concept has no visual grounding.
        """
        visual_data = self.visual_encoder.load_embeddings(concept)
        if not visual_data:
            return None

        textual_facts = self._load_textual_facts(concept)

        return {
            "concept": concept,
            "textual": {
                "facts": textual_facts,
                "embedding": _compute_text_embedding(" ".join(textual_facts)),
            },
            "visual": {
                "embeddings": [
                    {
                        "label": v.get("label", ""),
                        "source": v.get("source", ""),
                        "modality": v.get("modality", "visual"),
                        "dim": v.get("dim", int(v["embedding"].shape[0])),
                        "norm": float(np.linalg.norm(v["embedding"])),
                    }
                    for v in visual_data
                ],
                "count": len(visual_data),
            },
            "modalities": list(
                {v.get("modality", "visual") for v in visual_data}
            ),
            "summary": self.visual_encoder.concept_summary(concept),
        }

    def demonstrate_grounding(
        self, concept: str = "red", generate_visuals: bool = True
    ) -> dict:
        """Full grounded cognition demonstration.

        Shows that a concept exists as BOTH:
          1. A symbolic text representation (topic facts + n-gram embedding)
          2. A perceptual visual representation (DINOv2/ImageBind embedding)

        When DINOv2/ImageBind are not available, generates synthetic
        embeddings to demonstrate the architecture.
        """
        if generate_visuals:
            self.ensure_backbones()

        has_real_backbone = (
            self.concept_grounder._dinov2 is not None
        )

        textual_facts = self._load_textual_facts(concept)
        if not textual_facts:
            textual_facts = [
                f"{concept} is a fundamental perceptual category",
                f"{concept} is associated with specific wavelengths of light",
                f"{concept} appears frequently in UI elements and signals",
            ]

        if generate_visuals:
            existing = self.visual_encoder.load_embeddings(concept)
            if not existing:
                if has_real_backbone:
                    dummy_image = np.zeros((224, 224, 3), dtype=np.uint8)
                    dummy_image[:] = (
                        [255, 0, 0] if concept.lower() == "red" else [128, 128, 128]
                    )
                    try:
                        self.concept_grounder.ground_from_image(
                            concept, dummy_image, label=f"demo_{concept}",
                            source="demonstration",
                        )
                    except Exception:
                        pass
                else:
                    embedding = _generate_concept_embedding(concept)
                    self.visual_encoder.store_embedding(
                        concept=concept, embedding=embedding,
                        label=f"demo_{concept}", source="demonstration",
                        modality="visual",
                    )
                    imgbind_emb = _generate_concept_embedding(f"{concept}_imagebind")
                    self.visual_encoder.store_embedding(
                        concept=concept, embedding=imgbind_emb,
                        label=f"demo_{concept}_imagebind", source="demonstration",
                        modality="imagebind",
                    )
                    logger.info(
                        "Generated synthetic demo embeddings for '%s' "
                        "(DINOv2 not available)", concept
                    )

        text_emb = _compute_text_embedding(" ".join(textual_facts))

        visual_data = self.visual_encoder.load_embeddings(concept)

        cross_modal_score = 0.0
        if has_real_backbone and self.concept_grounder._imagebind and self.concept_grounder._imagebind._imagebind_available:
            try:
                cross_modal_score = self.concept_grounder.get_cross_modal_similarity(
                    concept, f"a {concept} object"
                )
            except Exception:
                pass
        elif not has_real_backbone:
            syn_vis = _generate_concept_embedding(concept)
            syn_txt = _generate_concept_embedding(f"{concept}_text")
            cross_modal_score = float(np.dot(syn_vis, syn_txt))

        cross_modal_score = max(0.0, min(1.0, cross_modal_score))

        return {
            "demonstration": "Grounded Cognition (Barsalou, 2008)",
            "concept": concept,
            "backbone_available": has_real_backbone,
            "textual_representation": {
                "modality": "symbolic / language",
                "facts": textual_facts,
                "n_gram_embedding_dim": len(text_emb),
                "n_gram_embedding_keys_sample": list(text_emb.keys())[:10],
            },
            "visual_representation": {
                "modality": "perceptual / vision",
                "backbone": "DINOv2" if has_real_backbone else "Synthetic (DINOv2 not available)",
                "embeddings": [
                    {
                        "label": v.get("label", ""),
                        "modality": v.get("modality", "visual"),
                        "dim": v.get("dim", int(v["embedding"].shape[0])),
                        "norm": float(np.linalg.norm(v["embedding"])),
                        "sample_values": v["embedding"][:5].tolist(),
                    }
                    for v in visual_data
                ],
                "count": len(visual_data),
            },
            "cross_modal_alignment": {
                "status": "active" if (has_real_backbone and cross_modal_score > 0) else "simulated",
                "text_to_visual_similarity": round(cross_modal_score, 4),
                "method": "ImageBind projection" if (
                    has_real_backbone and self.concept_grounder._imagebind
                ) else "Cosine similarity in embedding space",
            },
            "implications": [
                f"Concept '{concept}' exists as BOTH a symbolic text pattern "
                f"({len(textual_facts)} facts, trigram embedding) AND a perceptual "
                f"pattern ({len(visual_data)} visual embeddings)",
                "This dual representation enables grounded reasoning: "
                "the symbol 'red' is connected to visual experiences of redness",
                "Cross-modal alignment allows text queries to retrieve visual "
                "patterns and vice versa, enabling perceptual symbol systems",
            ],
        }

    def _load_textual_facts(self, concept: str) -> list[str]:
        topic_path = Path("/var/tmp/memoria/topics") / f"{concept}.md"
        if not topic_path.exists():
            return []
        return [
            e.strip()
            for e in topic_path.read_text().split("§")
            if e.strip()
        ]

    def status(self) -> dict:
        self.ensure_backbones()
        grounded = self.visual_encoder.get_all_concepts()
        dinov2_avail = self.concept_grounder._dinov2 is not None
        imagebind_avail = (
            self.concept_grounder._imagebind is not None
            and self.concept_grounder._imagebind._imagebind_available
        )

        topics_dir = Path("/var/tmp/memoria/topics")
        textual_topic_count = (
            len([f for f in topics_dir.iterdir() if f.suffix == ".md"])
            if topics_dir.exists() else 0
        )

        cross_modal = dinov2_avail and imagebind_avail

        return {
            "grounded_cognition": "active",
            "framework": "Barsalou (2008) — Perceptual Symbol Systems",
            "backbones": {
                "dinov2": {"available": dinov2_avail, "model": "facebook/dinov2-small" if dinov2_avail else None},
                "imagebind": {"available": imagebind_avail},
                "cross_modal_alignment": cross_modal,
            },
            "concepts": {
                "grounded_count": len(grounded),
                "grounded": grounded,
                "textual_topics_available": textual_topic_count,
            },
            "visual_store": str(self.visual_encoder.root),
            "embedding_dim": DEMO_EMBEDDING_DIM,
        }


_engine: Optional[GroundedCognitionEngine] = None


def get_grounded_engine() -> GroundedCognitionEngine:
    global _engine
    if _engine is None:
        _engine = GroundedCognitionEngine()
    return _engine
