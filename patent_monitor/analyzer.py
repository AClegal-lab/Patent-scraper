"""AI-powered patent design analysis using Anthropic Claude API."""

import base64
import json
import logging
import re
import time
from datetime import datetime

import anthropic

from .config import AiConfig
from .models import AnalysisResult, Patent

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a design patent analysis specialist. You compare newly granted design patent drawings against reference product images to assess visual similarity and potential infringement risk.

You MUST respond with ONLY a valid JSON object in this exact format — no markdown, no explanation, no code fences:
{
  "similarity_score": <integer 0-100>,
  "risk_level": "<high|medium|low|none>",
  "recommendation": "<flag|monitor|dismiss>",
  "reasoning": "<2-3 sentence explanation>"
}

Scoring guidelines:
- 0-20: No meaningful visual similarity in overall design appearance
- 21-40: Minor surface-level similarities (common design elements shared by many products)
- 41-60: Moderate similarity in some distinctive design features
- 61-80: Significant similarity in overall appearance and distinctive elements
- 81-100: Near-identical or highly similar design that could cause confusion

Risk levels:
- none (score 0-20): No infringement concern
- low (score 21-40): Minimal concern, only common design elements overlap
- medium (score 41-70): Notable design similarities warrant monitoring
- high (score 71-100): Strong visual similarity, legal review recommended

Recommendations:
- dismiss: Score 0-30, no action needed
- monitor: Score 31-65, track for developments
- flag: Score 66-100, requires immediate legal attention

Focus on: overall shape/silhouette, proportions, distinctive ornamental features, surface treatments, and arrangement of design elements. Ignore functional or utilitarian aspects."""

TEXT_ONLY_NOTE = """
NOTE: No patent drawing image is available. Analyze based on the patent title, abstract, and classification codes compared to the product descriptions. Be conservative in scoring — without visual comparison, reduce confidence and score accordingly."""


class PatentAnalyzer:
    """Analyzes patent designs against product images using Claude API."""

    def __init__(self, config: AiConfig):
        self.config = config
        self.client = anthropic.Anthropic(api_key=config.api_key)
        self.min_interval = 60.0 / config.rate_limit_per_minute
        self._last_request_time = 0.0

    def analyze(
        self,
        patent: Patent,
        patent_image: bytes | None,
        product_images: list[tuple[str, bytes]],
    ) -> AnalysisResult:
        """Analyze a patent design against product images.

        Args:
            patent: The patent to analyze.
            patent_image: PNG bytes of the patent drawing, or None.
            product_images: List of (filename, bytes) for product reference images.

        Returns:
            AnalysisResult with similarity score, risk level, and recommendation.
        """
        try:
            messages = self._build_messages(patent, patent_image, product_images)
            system = SYSTEM_PROMPT
            if patent_image is None:
                system += TEXT_ONLY_NOTE

            response_text = self._call_api(system, messages)
            result = self._parse_response(response_text)

            result.patent_image_used = patent_image is not None
            result.product_images_used = [name for name, _ in product_images]
            result.model_used = self.config.model
            result.analyzed_at = datetime.now()

            return result

        except Exception as e:
            logger.error(f"Analysis failed for {patent.patent_number}: {e}")
            return AnalysisResult(
                similarity_score=0,
                risk_level="none",
                recommendation="monitor",
                reasoning=f"AI analysis failed: {e}",
                error=str(e),
                model_used=self.config.model,
                analyzed_at=datetime.now(),
            )

    def _build_messages(
        self,
        patent: Patent,
        patent_image: bytes | None,
        product_images: list[tuple[str, bytes]],
    ) -> list[dict]:
        """Build the Anthropic messages API payload."""
        content = []

        # Add patent drawing image if available
        if patent_image:
            content.append({
                "type": "text",
                "text": "PATENT DRAWING (the design being analyzed):",
            })
            content.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": self._guess_media_type(patent_image),
                    "data": base64.standard_b64encode(patent_image).decode("utf-8"),
                },
            })

        # Add product reference images
        for i, (filename, img_bytes) in enumerate(product_images):
            content.append({
                "type": "text",
                "text": f"PRODUCT REFERENCE IMAGE {i+1} ({filename}):",
            })
            content.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": self._guess_media_type(img_bytes, filename),
                    "data": base64.standard_b64encode(img_bytes).decode("utf-8"),
                },
            })

        # Add patent metadata text
        metadata = f"""
PATENT METADATA:
- Patent Number: {patent.patent_number}
- Title: {patent.title}
- Issue Date: {patent.issue_date.isoformat()}
- Assignee: {patent.assignee or 'Unknown'}
- US Classification: {patent.classification_us or 'N/A'}
- CPC Classification: {patent.classification_cpc or 'N/A'}
- Abstract: {patent.abstract or 'No abstract available'}

Compare the patent design against the product reference image(s) and provide your analysis as JSON."""

        content.append({"type": "text", "text": metadata})

        return [{"role": "user", "content": content}]

    def _call_api(self, system: str, messages: list[dict]) -> str:
        """Make the Anthropic API call with rate limiting and retries."""
        max_retries = 3

        for attempt in range(1, max_retries + 1):
            self._rate_limit()
            try:
                response = self.client.messages.create(
                    model=self.config.model,
                    max_tokens=self.config.max_tokens,
                    system=system,
                    messages=messages,
                )
                return response.content[0].text

            except anthropic.RateLimitError:
                wait = min(2 ** attempt * 5, 60)
                logger.warning(f"Anthropic rate limited. Waiting {wait}s (attempt {attempt}/{max_retries})")
                time.sleep(wait)

            except anthropic.APIStatusError as e:
                if e.status_code >= 500 and attempt < max_retries:
                    logger.warning(f"Anthropic server error {e.status_code} (attempt {attempt}/{max_retries})")
                    time.sleep(2 ** attempt)
                    continue
                raise

        raise RuntimeError("Anthropic API call failed after all retries")

    def _parse_response(self, response_text: str) -> AnalysisResult:
        """Parse Claude's response into an AnalysisResult."""
        # Strategy 1: Direct JSON parse
        try:
            data = json.loads(response_text.strip())
            return self._data_to_result(data)
        except json.JSONDecodeError:
            pass

        # Strategy 2: Extract JSON from markdown or surrounding text
        json_match = re.search(r'\{[^{}]*"similarity_score"[^{}]*\}', response_text, re.DOTALL)
        if json_match:
            try:
                data = json.loads(json_match.group(0))
                return self._data_to_result(data)
            except json.JSONDecodeError:
                pass

        # Strategy 3: Try to find any JSON object
        json_match = re.search(r'\{.*?\}', response_text, re.DOTALL)
        if json_match:
            try:
                data = json.loads(json_match.group(0))
                return self._data_to_result(data)
            except json.JSONDecodeError:
                pass

        # Fallback: Could not parse
        logger.error(f"Could not parse AI response: {response_text[:200]}")
        return AnalysisResult(
            similarity_score=0,
            risk_level="none",
            recommendation="monitor",
            reasoning=f"Could not parse AI response. Raw: {response_text[:200]}",
            error="parse_failure",
        )

    def _data_to_result(self, data: dict) -> AnalysisResult:
        """Convert parsed JSON dict to AnalysisResult."""
        score = int(data.get("similarity_score", 0))
        score = max(0, min(100, score))  # clamp to 0-100

        risk_level = data.get("risk_level", "none")
        if risk_level not in ("high", "medium", "low", "none"):
            risk_level = "none"

        recommendation = data.get("recommendation", "monitor")
        if recommendation not in ("flag", "monitor", "dismiss"):
            recommendation = "monitor"

        return AnalysisResult(
            similarity_score=score,
            risk_level=risk_level,
            recommendation=recommendation,
            reasoning=data.get("reasoning", ""),
        )

    def _rate_limit(self):
        """Enforce rate limiting between API requests."""
        elapsed = time.time() - self._last_request_time
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        self._last_request_time = time.time()

    def _guess_media_type(self, image_bytes: bytes, filename: str = "") -> str:
        """Guess the media type from image bytes or filename."""
        # Check magic bytes
        if image_bytes[:8] == b'\x89PNG\r\n\x1a\n':
            return "image/png"
        if image_bytes[:2] == b'\xff\xd8':
            return "image/jpeg"
        if image_bytes[:4] == b'RIFF' and image_bytes[8:12] == b'WEBP':
            return "image/webp"

        # Fallback to filename extension
        ext = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
        ext_map = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "webp": "image/webp"}
        return ext_map.get(ext, "image/png")
