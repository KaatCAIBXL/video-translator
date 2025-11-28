"""
Service for generating images using Stable Diffusion WebUI (Automatic1111) API.
Supports Dreambooth models for custom character generation.
"""
import asyncio
import logging
import requests
import time
from pathlib import Path
from typing import List, Optional, Dict, Any
from PIL import Image
import io
import base64

logger = logging.getLogger(__name__)


class StableDiffusionService:
    """Service for interacting with Stable Diffusion WebUI API."""
    
    def __init__(self, api_url: str = "http://127.0.0.1:7860", timeout: int = 300):
        """
        Initialize Stable Diffusion service.
        
        Args:
            api_url: Base URL of Stable Diffusion WebUI API (default: http://127.0.0.1:7860)
            timeout: Request timeout in seconds (default: 300)
        """
        self.api_url = api_url.rstrip("/")
        self.timeout = timeout
        self.txt2img_endpoint = f"{self.api_url}/sdapi/v1/txt2img"
        self.progress_endpoint = f"{self.api_url}/sdapi/v1/progress"
        self.options_endpoint = f"{self.api_url}/sdapi/v1/options"
        
    def check_connection(self) -> bool:
        """Check if Stable Diffusion WebUI is accessible."""
        try:
            response = requests.get(f"{self.api_url}/", timeout=5)
            return response.status_code == 200
        except Exception as e:
            logger.warning(f"Stable Diffusion WebUI not accessible: {e}")
            return False
    
    def get_available_models(self) -> List[str]:
        """Get list of available models (including Dreambooth models)."""
        try:
            response = requests.get(f"{self.api_url}/sdapi/v1/sd-models", timeout=10)
            if response.status_code == 200:
                models = response.json()
                return [model.get("model_name", "") for model in models if model.get("model_name")]
            return []
        except Exception as e:
            logger.error(f"Error fetching models: {e}")
            return []
    
    def set_model(self, model_name: str) -> bool:
        """Set the active model (e.g., Dreambooth model)."""
        try:
            response = requests.post(
                f"{self.api_url}/sdapi/v1/options",
                json={"sd_model_checkpoint": model_name},
                timeout=10
            )
            return response.status_code == 200
        except Exception as e:
            logger.error(f"Error setting model: {e}")
            return False
    
    async def generate_image(
        self,
        prompt: str,
        negative_prompt: str = "",
        model_name: Optional[str] = None,
        width: int = 512,
        height: int = 512,
        steps: int = 20,
        cfg_scale: float = 7.0,
        seed: int = -1,
        sampler_name: str = "DPM++ 2M Karras",
        **kwargs
    ) -> Optional[Image.Image]:
        """
        Generate a single image from text prompt.
        
        Args:
            prompt: Text prompt for image generation
            negative_prompt: Negative prompt (things to avoid)
            model_name: Name of the model to use (if None, uses current model)
            width: Image width (default: 512)
            height: Image height (default: 512)
            steps: Number of sampling steps (default: 20)
            cfg_scale: CFG scale (default: 7.0)
            seed: Random seed (-1 for random)
            sampler_name: Sampler to use
            **kwargs: Additional parameters for txt2img API
        
        Returns:
            PIL Image object or None if generation failed
        """
        # Set model if specified
        if model_name:
            if not self.set_model(model_name):
                logger.error(f"Failed to set model: {model_name}")
                return None
        
        # Prepare request payload
        payload = {
            "prompt": prompt,
            "negative_prompt": negative_prompt,
            "width": width,
            "height": height,
            "steps": steps,
            "cfg_scale": cfg_scale,
            "seed": seed,
            "sampler_name": sampler_name,
            **kwargs
        }
        
        try:
            # Run in thread pool to avoid blocking
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                lambda: requests.post(self.txt2img_endpoint, json=payload, timeout=self.timeout)
            )
            
            if response.status_code != 200:
                logger.error(f"Stable Diffusion API error: {response.status_code} - {response.text}")
                return None
            
            result = response.json()
            images = result.get("images", [])
            
            if not images:
                logger.error("No images returned from Stable Diffusion API")
                return None
            
            # Decode base64 image
            image_data = base64.b64decode(images[0])
            image = Image.open(io.BytesIO(image_data))
            
            return image
            
        except Exception as e:
            logger.exception(f"Error generating image: {e}")
            return None
    
    async def generate_images_for_text(
        self,
        text: str,
        sentences: Optional[List[str]] = None,
        model_name: Optional[str] = None,
        image_per_sentence: bool = True,
        width: int = 512,
        height: int = 512,
        steps: int = 20,
        cfg_scale: float = 7.0,
        **kwargs
    ) -> List[Image.Image]:
        """
        Generate multiple images from text, optionally splitting by sentences.
        
        Args:
            text: Full text to generate images for
            sentences: Optional list of sentences (if None, will split text)
            model_name: Name of the model to use
            image_per_sentence: If True, generate one image per sentence
            width: Image width
            height: Image height
            steps: Number of sampling steps
            cfg_scale: CFG scale
            **kwargs: Additional parameters
        
        Returns:
            List of PIL Image objects
        """
        images = []
        
        if image_per_sentence and sentences:
            # Generate one image per sentence
            for i, sentence in enumerate(sentences):
                if not sentence.strip():
                    continue
                
                logger.info(f"Generating image {i+1}/{len(sentences)}: {sentence[:50]}...")
                image = await self.generate_image(
                    prompt=sentence,
                    model_name=model_name,
                    width=width,
                    height=height,
                    steps=steps,
                    cfg_scale=cfg_scale,
                    **kwargs
                )
                
                if image:
                    images.append(image)
                else:
                    logger.warning(f"Failed to generate image for sentence: {sentence}")
                
                # Small delay to avoid overwhelming the API
                await asyncio.sleep(0.5)
        else:
            # Generate single image for full text
            logger.info(f"Generating image for text: {text[:50]}...")
            image = await self.generate_image(
                prompt=text,
                model_name=model_name,
                width=width,
                height=height,
                steps=steps,
                cfg_scale=cfg_scale,
                **kwargs
            )
            
            if image:
                images.append(image)
        
        return images


def create_video_from_images(
    images: List[Image.Image],
    output_path: Path,
    fps: float = 2.0,
    duration_per_image: float = 2.0,
    transition_frames: int = 10
) -> bool:
    """
    Create a video from a list of images using ffmpeg.
    
    Args:
        images: List of PIL Image objects
        output_path: Path to save the output video
        fps: Frames per second for the video
        duration_per_image: Duration each image should be shown (seconds)
        transition_frames: Number of frames for crossfade transition between images
    
    Returns:
        True if successful, False otherwise
    """
    import subprocess
    import tempfile
    
    if not images:
        logger.error("No images provided for video creation")
        return False
    
    # Create temporary directory for images
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        
        # Save all images as temporary files
        image_paths = []
        for i, image in enumerate(images):
            # Resize images to same size (use first image's size)
            if i == 0:
                target_size = image.size
            else:
                image = image.resize(target_size, Image.Resampling.LANCZOS)
            
            image_path = temp_path / f"frame_{i:05d}.png"
            image.save(image_path, "PNG")
            image_paths.append(image_path)
        
        # Create ffmpeg command to create video with crossfades
        # This is a simplified version - you might want to use more advanced transitions
        try:
            # Calculate total frames needed
            frames_per_image = int(duration_per_image * fps)
            total_frames = len(images) * frames_per_image
            
            # Use ffmpeg to create video from images
            # Simple approach: concatenate images with specified duration
            ffmpeg_cmd = [
                "ffmpeg",
                "-y",  # Overwrite output file
                "-framerate", str(fps),
                "-i", str(temp_path / "frame_%05d.png"),
                "-vf", f"scale={target_size[0]}:{target_size[1]},fps={fps}",
                "-c:v", "libx264",
                "-pix_fmt", "yuv420p",
                "-t", str(len(images) * duration_per_image),
                str(output_path)
            ]
            
            result = subprocess.run(
                ffmpeg_cmd,
                capture_output=True,
                text=True,
                timeout=300
            )
            
            if result.returncode != 0:
                logger.error(f"ffmpeg error: {result.stderr}")
                return False
            
            logger.info(f"Video created successfully: {output_path}")
            return True
            
        except Exception as e:
            logger.exception(f"Error creating video from images: {e}")
            return False

