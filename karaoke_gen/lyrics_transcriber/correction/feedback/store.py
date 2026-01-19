"""Storage backend for correction annotations."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional
from datetime import datetime
from collections import Counter, defaultdict

from .schemas import CorrectionAnnotation, AnnotationStatistics

logger = logging.getLogger(__name__)


class FeedbackStore:
    """Stores correction annotations in JSONL format."""
    
    def __init__(self, storage_dir: str = "cache"):
        """Initialize feedback store.
        
        Args:
            storage_dir: Directory to store annotations file
        """
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.annotations_file = self.storage_dir / "correction_annotations.jsonl"
        
        # Ensure file exists
        if not self.annotations_file.exists():
            self.annotations_file.touch()
            logger.info(f"Created annotations file: {self.annotations_file}")
    
    def save_annotation(self, annotation: CorrectionAnnotation) -> bool:
        """Save a single annotation to the JSONL file.
        
        Args:
            annotation: CorrectionAnnotation to save
        
        Returns:
            True if successful, False otherwise
        """
        try:
            # Convert to dict and handle datetime serialization
            data = annotation.model_dump()
            data['timestamp'] = data['timestamp'].isoformat()
            
            # Append to JSONL file
            with open(self.annotations_file, 'a', encoding='utf-8') as f:
                f.write(json.dumps(data, ensure_ascii=False) + '\n')
            
            logger.debug(f"Saved annotation {annotation.annotation_id}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to save annotation: {e}")
            return False
    
    def save_annotations(self, annotations: List[CorrectionAnnotation]) -> int:
        """Save multiple annotations.
        
        Args:
            annotations: List of annotations to save
        
        Returns:
            Number of annotations successfully saved
        """
        saved = 0
        for annotation in annotations:
            if self.save_annotation(annotation):
                saved += 1
        return saved
    
    def get_all_annotations(self) -> List[CorrectionAnnotation]:
        """Load all annotations from the JSONL file.
        
        Returns:
            List of CorrectionAnnotation objects
        """
        annotations = []
        
        if not self.annotations_file.exists():
            return annotations
        
        try:
            with open(self.annotations_file, 'r', encoding='utf-8') as f:
                for line_num, line in enumerate(f, 1):
                    line = line.strip()
                    if not line:
                        continue
                    
                    try:
                        data = json.loads(line)
                        # Parse timestamp if string
                        if isinstance(data.get('timestamp'), str):
                            data['timestamp'] = datetime.fromisoformat(data['timestamp'])
                        
                        annotation = CorrectionAnnotation.model_validate(data)
                        annotations.append(annotation)
                        
                    except Exception as e:
                        logger.warning(f"Failed to parse annotation on line {line_num}: {e}")
                        continue
            
            logger.debug(f"Loaded {len(annotations)} annotations")
            return annotations
            
        except Exception as e:
            logger.error(f"Failed to load annotations: {e}")
            return []
    
    def get_annotations_by_song(self, audio_hash: str) -> List[CorrectionAnnotation]:
        """Get all annotations for a specific song.
        
        Args:
            audio_hash: Hash of the audio file
        
        Returns:
            List of annotations for that song
        """
        all_annotations = self.get_all_annotations()
        return [a for a in all_annotations if a.audio_hash == audio_hash]
    
    def get_annotations_by_category(self, category: str) -> List[CorrectionAnnotation]:
        """Get all annotations of a specific type.
        
        Args:
            category: Annotation type category
        
        Returns:
            List of annotations of that type
        """
        all_annotations = self.get_all_annotations()
        return [a for a in all_annotations if a.annotation_type == category]
    
    def get_statistics(self) -> AnnotationStatistics:
        """Generate aggregated statistics from all annotations.
        
        Returns:
            AnnotationStatistics object with aggregated data
        """
        annotations = self.get_all_annotations()
        
        if not annotations:
            return AnnotationStatistics()
        
        # Count by type
        type_counts = Counter(a.annotation_type for a in annotations)
        
        # Count by action
        action_counts = Counter(a.action_taken for a in annotations)
        
        # Average confidence
        avg_confidence = sum(a.confidence for a in annotations) / len(annotations)
        
        # Agentic agreement rate
        agentic_proposals = [a for a in annotations if a.agentic_proposal is not None]
        if agentic_proposals:
            agentic_agreement_rate = sum(1 for a in agentic_proposals if a.agentic_agreed) / len(agentic_proposals)
        else:
            agentic_agreement_rate = 0.0
        
        # Most common error patterns
        error_patterns = defaultdict(list)
        for a in annotations:
            if a.action_taken != "NO_ACTION":
                pattern = f"{a.original_text} -> {a.corrected_text}"
                error_patterns[pattern].append(a)
        
        most_common = [
            {
                "pattern": pattern,
                "count": len(anns),
                "annotation_type": anns[0].annotation_type
            }
            for pattern, anns in sorted(error_patterns.items(), key=lambda x: len(x[1]), reverse=True)[:10]
        ]
        
        # Unique songs
        unique_hashes = set(a.audio_hash for a in annotations)
        
        return AnnotationStatistics(
            total_annotations=len(annotations),
            annotations_by_type={k: v for k, v in type_counts.items()},
            annotations_by_action={k: v for k, v in action_counts.items()},
            average_confidence=avg_confidence,
            agentic_agreement_rate=agentic_agreement_rate,
            most_common_errors=most_common,
            songs_annotated=len(unique_hashes)
        )
    
    def export_to_training_data(self, output_file: Optional[Path] = None) -> Path:
        """Export annotations in a format suitable for model fine-tuning.
        
        Args:
            output_file: Optional path for output file
        
        Returns:
            Path to the exported file
        """
        if output_file is None:
            output_file = self.storage_dir / "training_data.jsonl"
        
        annotations = self.get_all_annotations()
        
        # Filter to high-confidence annotations (4-5 rating)
        high_confidence = [a for a in annotations if a.confidence >= 4.0]
        
        with open(output_file, 'w', encoding='utf-8') as f:
            for annotation in high_confidence:
                # Create a training example with input/output format
                training_example = {
                    "input": {
                        "original_text": annotation.original_text,
                        "annotation_type": annotation.annotation_type,
                        "artist": annotation.artist,
                        "title": annotation.title,
                        "reference_sources": annotation.reference_sources_consulted
                    },
                    "output": {
                        "action": annotation.action_taken,
                        "corrected_text": annotation.corrected_text,
                        "reasoning": annotation.reasoning
                    },
                    "metadata": {
                        "confidence": annotation.confidence,
                        "annotation_id": annotation.annotation_id,
                        "timestamp": annotation.timestamp.isoformat()
                    }
                }
                f.write(json.dumps(training_example, ensure_ascii=False) + '\n')
        
        logger.info(f"Exported {len(high_confidence)} training examples to {output_file}")
        return output_file

