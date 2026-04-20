from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Dict, Any, List
import os
import traceback
import threading
import time

app = FastAPI(title="FlowerNet UniEval Service", version="1.0.0")


class UniEvalRequest(BaseModel):
    draft: str
    outline: str
    history: List[str] = []


class UniEvalService:
    def __init__(self) -> None:
        self.model_name = os.getenv("UNIEVAL_MODEL_NAME", "cross-encoder/nli-distilroberta-base")
        self.model_revision = os.getenv("UNIEVAL_MODEL_REVISION", "").strip() or None
        self.cache_dir = os.getenv("UNIEVAL_CACHE_DIR", "").strip() or None
        self.prefer_local_cache = os.getenv("UNIEVAL_PREFER_LOCAL_CACHE", "true").lower() in ("1", "true", "yes", "on")
        self.allow_online_fetch = os.getenv("UNIEVAL_ALLOW_ONLINE_FETCH", "true").lower() in ("1", "true", "yes", "on")
        self.model_load_retries = max(1, int(os.getenv("UNIEVAL_MODEL_LOAD_RETRIES", "3")))
        self.model_retry_backoff = float(os.getenv("UNIEVAL_MODEL_RETRY_BACKOFF", "2.0"))
        self.max_input_chars = int(os.getenv("UNIEVAL_MAX_INPUT_CHARS", "4000"))
        self.bool_threshold = float(os.getenv("UNIEVAL_BOOL_THRESHOLD", "0.5"))
        self.load_timeout_sec = int(os.getenv("UNIEVAL_LOAD_TIMEOUT_SEC", "180"))
        self.ready = False
        self.loading = False
        self.error = ""
        self.last_ready_at = 0.0
        self.last_error_at = 0.0
        self.load_attempts = 0
        self.tokenizer = None
        self.model = None
        self.id2label: Dict[int, str] = {}
        self._load_lock = threading.Lock()
        self._warmup_thread: threading.Thread | None = None
        self._warmup_started_at = 0.0

        if self.cache_dir:
            os.makedirs(self.cache_dir, exist_ok=True)
            os.environ.setdefault("HF_HOME", self.cache_dir)

    def _from_pretrained_kwargs(self, *, local_files_only: bool = False) -> Dict[str, Any]:
        kwargs: Dict[str, Any] = {}
        if self.model_revision:
            kwargs["revision"] = self.model_revision
        if self.cache_dir:
            kwargs["cache_dir"] = self.cache_dir
        if local_files_only:
            kwargs["local_files_only"] = True
        return kwargs

    def _attempt_model_load(self, *, local_files_only: bool) -> None:
        from transformers import AutoTokenizer, AutoModelForSequenceClassification  # type: ignore

        pretrained_kwargs = self._from_pretrained_kwargs(local_files_only=local_files_only)
        self.tokenizer = AutoTokenizer.from_pretrained(self.model_name, **pretrained_kwargs)
        self.model = AutoModelForSequenceClassification.from_pretrained(self.model_name, **pretrained_kwargs)
        config = getattr(self.model, "config", None)
        if config is not None and isinstance(getattr(config, "id2label", None), dict):
            self.id2label = {int(k): str(v) for k, v in config.id2label.items()}

    def ensure_model_loaded(self) -> None:
        if self.ready:
            return
        with self._load_lock:
            if self.ready:
                return
            self.loading = True
            self.error = ""
            print(f"[UniEval] loading model: {self.model_name}", flush=True)
            try:
                load_plans: List[tuple[str, bool]] = []
                if self.prefer_local_cache and self.cache_dir:
                    load_plans.append(("local-cache", True))
                if self.allow_online_fetch:
                    load_plans.append(("online-fetch", False))
                if not load_plans:
                    load_plans.append(("default", False))

                last_error = ""
                for plan_name, local_only in load_plans:
                    for attempt in range(1, self.model_load_retries + 1):
                        self.load_attempts += 1
                        try:
                            print(
                                f"[UniEval] load attempt mode={plan_name} local_only={local_only} retry={attempt}/{self.model_load_retries}",
                                flush=True,
                            )
                            self._attempt_model_load(local_files_only=local_only)
                            self.ready = True
                            self.error = ""
                            self.last_ready_at = time.time()
                            print("[UniEval] model ready", flush=True)
                            return
                        except Exception as e:  # pragma: no cover
                            last_error = str(e)
                            self.last_error_at = time.time()
                            print(
                                f"[UniEval] load failed mode={plan_name} retry={attempt}/{self.model_load_retries}: {last_error}",
                                flush=True,
                            )
                            if attempt < self.model_load_retries:
                                sleep_seconds = max(0.5, self.model_retry_backoff * attempt)
                                time.sleep(sleep_seconds)

                self.error = last_error or "model load failed"
                self.ready = False
                print(f"[UniEval] model load failed: {self.error}", flush=True)
            finally:
                self.loading = False

    def _refresh_loading_state(self) -> None:
        if self.ready:
            return
        if not self.loading:
            return
        if self._warmup_started_at <= 0:
            return
        elapsed = time.time() - self._warmup_started_at
        if elapsed <= self.load_timeout_sec:
            return

        # A blocked model download/init should not keep the API in loading forever.
        self.loading = False
        if not self.error:
            self.error = f"model warmup timeout after {self.load_timeout_sec}s"
        print(f"[UniEval] warmup timeout: {self.error}", flush=True)

    def warmup_async(self) -> None:
        self._refresh_loading_state()
        if self.ready or self.loading:
            return
        if self._warmup_thread is not None and self._warmup_thread.is_alive():
            return

        self.loading = True
        self.error = ""
        self._warmup_started_at = time.time()
        self._warmup_thread = threading.Thread(target=self.ensure_model_loaded, daemon=True)
        self._warmup_thread.start()
        print("[UniEval] async warmup started", flush=True)

    def _clip(self, text: str) -> str:
        text = (text or "").strip()
        if len(text) <= self.max_input_chars:
            return text
        keep = self.max_input_chars
        head = int(keep * 0.7)
        tail = keep - head
        return text[:head] + "\n...\n" + text[-tail:]

    def _softmax(self, logits: List[float]) -> List[float]:
        if not logits:
            return []
        max_logit = max(logits)
        exps = [pow(2.718281828, x - max_logit) for x in logits]
        denom = sum(exps) or 1.0
        return [v / denom for v in exps]

    def _entailment_probability(self, premise: str, hypothesis: str) -> float:
        if not self.ready or self.tokenizer is None or self.model is None:
            raise RuntimeError(f"UniEval model unavailable: {self.error or 'model not ready'}")

        inputs = self.tokenizer(
            premise,
            hypothesis,
            return_tensors="pt",
            truncation=True,
            max_length=512,
        )
        outputs = self.model(**inputs)
        logits_tensor = outputs.logits[0].detach().cpu().tolist()
        probs = self._softmax([float(x) for x in logits_tensor])

        entail_idx = None
        for idx, label in self.id2label.items():
            if "entail" in label.lower():
                entail_idx = idx
                break
        if entail_idx is None:
            entail_idx = len(probs) - 1
        entail_idx = max(0, min(entail_idx, len(probs) - 1))
        return max(0.0, min(1.0, float(probs[entail_idx])))

    def _boolean_dimension(self, question: str, source: str, output: str) -> Dict[str, Any]:
        premise = f"SOURCE:\n{source}\n\nOUTPUT:\n{output}"
        hypothesis = question
        score = self._entailment_probability(premise, hypothesis)
        decision = score >= self.bool_threshold
        return {
            "score": round(score, 4),
            "boolean": bool(decision),
            "question": question,
        }

    def score(self, draft: str, outline: str, history: List[str]) -> Dict[str, Any]:
        draft = self._clip(draft)
        outline = self._clip(outline)
        history_text = self._clip("\n".join(history[-3:]) if history else "")

        bool_questions = {
            "consistency": "The output is fully consistent with the source objective and key points.",
            "coherence": "The output is logically coherent and well connected across sentences.",
            "fluency": "The output is fluent and readable with clear expression.",
            "factuality": "The output is factually grounded in the source and does not invent unsupported claims.",
        }

        source_for_consistency = outline
        source_for_coherence = outline + ("\n" + history_text if history_text else "")
        source_for_factuality = outline + ("\n" + history_text if history_text else "")
        source_for_fluency = draft

        details = {
            "consistency": self._boolean_dimension(bool_questions["consistency"], source_for_consistency, draft),
            "coherence": self._boolean_dimension(bool_questions["coherence"], source_for_coherence, draft),
            "fluency": self._boolean_dimension(bool_questions["fluency"], source_for_fluency, draft),
            "factuality": self._boolean_dimension(bool_questions["factuality"], source_for_factuality, draft),
        }

        return {
            "scores": {
                "consistency": details["consistency"]["score"],
                "coherence": details["coherence"]["score"],
                "fluency": details["fluency"]["score"],
                "factuality": details["factuality"]["score"],
            },
            "boolean": {
                "consistency": details["consistency"]["boolean"],
                "coherence": details["coherence"]["boolean"],
                "fluency": details["fluency"]["boolean"],
                "factuality": details["factuality"]["boolean"],
            },
            "questions": {
                "consistency": details["consistency"]["question"],
                "coherence": details["coherence"]["question"],
                "fluency": details["fluency"]["question"],
                "factuality": details["factuality"]["question"],
            },
        }


service = UniEvalService()
service.warmup_async()


@app.get("/")
def root() -> Dict[str, Any]:
    service._refresh_loading_state()
    return {
        "status": "online" if service.ready else ("loading" if service.loading else "degraded"),
        "service": "flowernet-unieval",
        "model": service.model_name,
        "model_revision": service.model_revision,
        "cache_dir": service.cache_dir,
        "bool_threshold": service.bool_threshold,
        "prefer_local_cache": service.prefer_local_cache,
        "allow_online_fetch": service.allow_online_fetch,
        "load_attempts": service.load_attempts,
        "last_ready_at": service.last_ready_at,
        "last_error_at": service.last_error_at,
        "ready": service.ready,
        "loading": service.loading,
        "error": service.error,
    }


@app.get("/health/live")
def health_live() -> Dict[str, str]:
    return {"status": "alive"}


@app.get("/health/ready")
def health_ready() -> Dict[str, Any]:
    service._refresh_loading_state()
    if not service.ready:
        raise HTTPException(status_code=503, detail=f"not ready: {service.error or 'loading'}")
    return {"status": "ready", "model": service.model_name, "model_revision": service.model_revision}


@app.post("/score")
def score(req: UniEvalRequest) -> Dict[str, Any]:
    service._refresh_loading_state()
    if not service.ready:
        service.warmup_async()
        raise HTTPException(status_code=503, detail=f"UniEval model not ready: {service.error or 'loading'}")

    try:
        payload = service.score(req.draft, req.outline, req.history)
        return payload
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", "8004"))
    uvicorn.run(app, host="0.0.0.0", port=port)
