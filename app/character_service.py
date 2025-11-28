"""
Service for managing characters and Dreambooth training.
"""
import asyncio
import json
import logging
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import List, Optional
from datetime import datetime

from .config import settings
from .models import Character

logger = logging.getLogger(__name__)


class CharacterService:
    """Service for managing characters and their Dreambooth models."""
    
    def __init__(self):
        self.characters_dir = settings.CHARACTERS_DIR
        self.characters_dir.mkdir(parents=True, exist_ok=True)
        self.dreambooth_path = settings.DREAMBOOTH_PATH
        
    def _get_character_dir(self, character_id: str) -> Path:
        """Get directory for a character."""
        return self.characters_dir / character_id
    
    def _get_character_json(self, character_id: str) -> Path:
        """Get JSON file path for character metadata."""
        return self._get_character_dir(character_id) / "character.json"
    
    def _get_training_images_dir(self, character_id: str) -> Path:
        """Get directory for training images."""
        return self._get_character_dir(character_id) / "training_images"
    
    def create_character(
        self,
        name: str,
        token: str,
        description: str,
        class_word: str = "person"
    ) -> Character:
        """Create a new character."""
        character_id = str(uuid.uuid4())
        character_dir = self._get_character_dir(character_id)
        character_dir.mkdir(parents=True, exist_ok=True)
        training_images_dir = self.get_training_images_dir(character_id)
        training_images_dir.mkdir(parents=True, exist_ok=True)
        
        character = Character(
            id=character_id,
            name=name,
            token=token,
            description=description,
            class_word=class_word,
            status="pending",
            training_images_count=0,
            created_at=datetime.now().isoformat()
        )
        
        # Save character metadata
        self._save_character(character)
        
        return character
    
    def _save_character(self, character: Character):
        """Save character metadata to JSON."""
        json_path = self._get_character_json(character.id)
        json_path.write_text(
            character.model_dump_json(indent=2),
            encoding="utf-8"
        )
    
    def get_character(self, character_id: str) -> Optional[Character]:
        """Get character by ID."""
        json_path = self._get_character_json(character_id)
        if not json_path.exists():
            return None
        
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
            return Character(**data)
        except Exception as e:
            logger.exception(f"Error loading character {character_id}: {e}")
            return None
    
    def list_characters(self) -> List[Character]:
        """List all characters."""
        characters = []
        if not self.characters_dir.exists():
            return characters
        
        for character_dir in self.characters_dir.iterdir():
            if not character_dir.is_dir():
                continue
            
            character = self.get_character(character_dir.name)
            if character:
                characters.append(character)
        
        return sorted(characters, key=lambda c: c.created_at, reverse=True)
    
    def delete_character(self, character_id: str) -> bool:
        """Delete a character and all its files."""
        character_dir = self._get_character_dir(character_id)
        if character_dir.exists():
            shutil.rmtree(character_dir)
            return True
        return False
    
    def add_training_images(self, character_id: str, image_files: List[Path]) -> int:
        """Add training images to a character."""
        training_images_dir = self.get_training_images_dir(character_id)
        count = 0
        
        for image_file in image_files:
            if not image_file.exists():
                continue
            
            # Copy image to training directory
            dest = training_images_dir / image_file.name
            shutil.copy2(image_file, dest)
            count += 1
        
        # Update character metadata
        character = self.get_character(character_id)
        if character:
            character.training_images_count = len(list(training_images_dir.glob("*")))
            self._save_character(character)
        
        return count
    
    async def train_character(self, character_id: str) -> bool:
        """
        Train a Dreambooth model for a character.
        Uses JoePenna's Dreambooth-Stable-Diffusion implementation.
        """
        character = self.get_character(character_id)
        if not character:
            logger.error(f"Character {character_id} not found")
            return False
        
        if not settings.DREAMBOOTH_ENABLED:
            logger.error("Dreambooth is not enabled")
            return False
        
        if not self.dreambooth_path.exists():
            logger.error(f"Dreambooth path does not exist: {self.dreambooth_path}")
            return False
        
        training_images_dir = self.get_training_images_dir(character_id)
        if not training_images_dir.exists() or len(list(training_images_dir.glob("*"))) == 0:
            logger.error(f"No training images found for character {character_id}")
            return False
        
        # Update status to training
        character.status = "training"
        self._save_character(character)
        
        try:
            # Prepare training command
            # Based on JoePenna's Dreambooth-Stable-Diffusion
            main_py = self.dreambooth_path / "main.py"
            if not main_py.exists():
                logger.error(f"main.py not found in {self.dreambooth_path}")
                character.status = "failed"
                character.error = "Dreambooth main.py not found"
                self._save_character(character)
                return False
            
            # Generate project name from character name
            project_name = f"{character.token}_{character.class_word}".replace(" ", "_")
            
            # Build command
            cmd = [
                "python",
                str(main_py),
                "--project_name", project_name,
                "--max_training_steps", str(settings.DREAMBOOTH_MAX_TRAINING_STEPS),
                "--token", character.token,
                "--training_model", settings.DREAMBOOTH_BASE_MODEL,
                "--training_images", str(training_images_dir),
                "--class_word", character.class_word,
                "--learning_rate", str(settings.DREAMBOOTH_LEARNING_RATE),
                "--save_every_x_steps", str(settings.DREAMBOOTH_SAVE_EVERY_X_STEPS),
            ]
            
            # Add regularization images if specified
            if settings.DREAMBOOTH_REGULARIZATION_IMAGES:
                cmd.extend(["--regularization_images", settings.DREAMBOOTH_REGULARIZATION_IMAGES])
            
            logger.info(f"Starting Dreambooth training for character {character_id}")
            logger.info(f"Command: {' '.join(cmd)}")
            
            # Run training in thread pool
            loop = asyncio.get_event_loop()
            process = await loop.run_in_executor(
                None,
                lambda: subprocess.Popen(
                    cmd,
                    cwd=str(self.dreambooth_path),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True
                )
            )
            
            # Wait for process to complete (with timeout)
            try:
                stdout, stderr = await asyncio.wait_for(
                    loop.run_in_executor(None, process.communicate),
                    timeout=3600 * 2  # 2 hours timeout
                )
                
                if process.returncode == 0:
                    # Training successful
                    # Find the generated model
                    # Models are typically saved in dreambooth_path/models/ or similar
                    model_path = self._find_trained_model(project_name)
                    
                    character.status = "completed"
                    character.model_path = str(model_path) if model_path else None
                    self._save_character(character)
                    
                    logger.info(f"Training completed for character {character_id}")
                    return True
                else:
                    character.status = "failed"
                    character.error = stderr or "Training failed"
                    self._save_character(character)
                    logger.error(f"Training failed for character {character_id}: {stderr}")
                    return False
                    
            except asyncio.TimeoutError:
                process.kill()
                character.status = "failed"
                character.error = "Training timeout (exceeded 2 hours)"
                self._save_character(character)
                logger.error(f"Training timeout for character {character_id}")
                return False
                
        except Exception as e:
            logger.exception(f"Error training character {character_id}: {e}")
            character.status = "failed"
            character.error = str(e)
            self._save_character(character)
            return False
    
    def _find_trained_model(self, project_name: str) -> Optional[Path]:
        """Find the trained model file for a project."""
        # Look in common locations
        possible_locations = [
            self.dreambooth_path / "models" / f"{project_name}.ckpt",
            self.dreambooth_path / "models" / f"{project_name}_final.ckpt",
            self.dreambooth_path / f"{project_name}.ckpt",
            self.dreambooth_path / f"{project_name}_final.ckpt",
        ]
        
        for location in possible_locations:
            if location.exists():
                return location
        
        # Also search in subdirectories
        models_dir = self.dreambooth_path / "models"
        if models_dir.exists():
            for model_file in models_dir.glob(f"*{project_name}*.ckpt"):
                return model_file
        
        return None
    
    def get_character_token_prompt(self, character: Character, base_prompt: str) -> str:
        """
        Generate a prompt using the character token.
        Format: {base_prompt}, {token} {class_word}, {description}
        """
        parts = [base_prompt]
        if character.token:
            parts.append(f"{character.token} {character.class_word}")
        if character.description:
            parts.append(character.description)
        return ", ".join(parts)


# Global instance
character_service = CharacterService()

