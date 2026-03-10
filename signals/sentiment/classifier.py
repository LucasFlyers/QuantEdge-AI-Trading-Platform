"""
Transformer Sentiment Classifier — Phase 5

Uses ProsusAI/finbert — a BERT model fine-tuned on financial text.
Understands context, negation, and financial jargon far better than lexicons.

Falls back to LexiconSentimentClassifier automatically if torch/transformers
are unavailable or if the model fails to download.

Model: ProsusAI/finbert (~438MB, downloads once and caches)
Labels: positive, negative, neutral → mapped to bullish/bearish/neutral
"""
from __future__ import annotations

import os
from typing import Tuple

from utils.logging import get_logger

log = get_logger("signals.sentiment.classifier")


class TransformerSentimentClassifier:
    """
    FinBERT-powered sentiment classifier.

    Same interface as LexiconSentimentClassifier:
      classify(text) → (bullish_pct, neutral_pct, bearish_pct)

    Loads lazily on first call so startup is not blocked.
    """

    MODEL_NAME = os.getenv("SENTIMENT_MODEL", "ProsusAI/finbert")

    def __init__(self):
        self._pipeline = None
        self._loaded = False
        self._failed = False
        self._fallback = None  # set to LexiconSentimentClassifier on failure

    def _load(self) -> None:
        """Lazy-load the model. Called on first classify()."""
        if self._loaded or self._failed:
            return

        try:
            log.info("Loading FinBERT model", model=self.MODEL_NAME)
            from transformers import pipeline as hf_pipeline

            self._pipeline = hf_pipeline(
                task="text-classification",
                model=self.MODEL_NAME,
                tokenizer=self.MODEL_NAME,
                device=-1,          # CPU — no GPU needed
                top_k=None,         # return all 3 label scores
                truncation=True,
                max_length=512,
            )
            self._loaded = True
            log.info("FinBERT loaded successfully", model=self.MODEL_NAME)

        except Exception as e:
            self._failed = True
            log.warning(
                "FinBERT failed to load — falling back to lexicon classifier",
                error=str(e),
                model=self.MODEL_NAME,
            )
            # Import here to avoid circular dependency
            from signals.sentiment.engine import LexiconSentimentClassifier
            self._fallback = LexiconSentimentClassifier()

    def classify(self, text: str) -> Tuple[float, float, float]:
        """
        Returns (bullish_pct, neutral_pct, bearish_pct) summing to 1.0.
        Scores come from FinBERT's softmax output — well-calibrated probabilities.
        """
        self._load()

        # Fallback path
        if self._failed and self._fallback:
            return self._fallback.classify(text)

        if not self._pipeline:
            return 0.0, 1.0, 0.0  # safe neutral default

        try:
            # FinBERT returns list of dicts: [{"label": "positive", "score": 0.9}, ...]
            results = self._pipeline(text[:512])

            # hf pipeline with top_k=None wraps in a list
            if results and isinstance(results[0], list):
                results = results[0]

            scores = {r["label"].lower(): r["score"] for r in results}

            bullish_pct  = scores.get("positive", 0.0)
            bearish_pct  = scores.get("negative", 0.0)
            neutral_pct  = scores.get("neutral",  0.0)

            return bullish_pct, neutral_pct, bearish_pct

        except Exception as e:
            log.debug("FinBERT inference error, using neutral", error=str(e))
            return 0.0, 1.0, 0.0

    @property
    def is_transformer(self) -> bool:
        return self._loaded and not self._failed

    @property
    def model_name(self) -> str:
        return self.MODEL_NAME if self.is_transformer else "lexicon-fallback"
