import logging
import secrets
from typing import Any, Dict, List, Optional, Tuple

from django.db import transaction
from django.db.models import QuerySet
from django_q.tasks import async_task

from genaigrader.models import (
    BatchEvaluation,
    Course,
    Evaluation,
    Exam,
    Question,
    QuestionOption,
)
from genaigrader.services.get_models_service import get_models_for_user
from genaigrader.services.stream_service import create_evaluation_stub
from genaigrader.tasks import EVALUATION_TASK_TIMEOUT, batch_orchestrator_task
from users.models import CustomUser

logger = logging.getLogger(__name__)

MAX_ITERATIONS = 100
MAX_PAYLOAD_SIZE = 5 * 1024 * 1024  # 5 MB


class OrchestratorEnqueueError(Exception):
    """Raised when the batch orchestrator task cannot be enqueued."""


def _generate_public_id() -> str:
    return f"eval-{secrets.token_urlsafe(8)}"


def _get_or_create_course(user, course_name: str) -> Course:
    normalized = course_name.strip()
    existing = Course.objects.filter(name__iexact=normalized, user=user).first()
    if existing:
        return existing
    return Course.objects.create(name=normalized, user=user)


def _get_or_create_exam(user, course: Course, external_id: str, title: str) -> Exam:
    exam, _created = Exam.objects.get_or_create(
        user=user,
        external_id=external_id,
        defaults={"course": course, "description": title},
    )
    return exam


def _create_questions_and_options(exam: Exam, questions_data: List[Dict]) -> None:
    for q_data in questions_data:
        question = Question.objects.create(
            exam=exam,
            statement=q_data["question_text"],
        )
        correct_option = None
        for idx, choice in enumerate(q_data["choices"]):
            letter = chr(ord("a") + idx)
            option = QuestionOption.objects.create(
                question=question,
                content=f"{letter}) {choice['choice_text']}",
            )
            if choice.get("isCorrect"):
                correct_option = option
        question.correct_option = correct_option
        question.save()


def _resolve_models(user: CustomUser, model_names: List[str]) -> List:
    """Look up models by description from the user's available models."""
    local_models, external_models = get_models_for_user(user)
    by_name = {}
    for m in list(local_models) + list(external_models):
        by_name.setdefault(m.description, m)

    resolved = []
    for name in model_names:
        model = by_name.get(name)
        if model is None:
            raise ValueError(f"Model '{name}' is not available for this user")
        resolved.append(model)
    return resolved


def _aggregate_status(
    evaluations: QuerySet,
) -> Tuple[str, int, int, int, int]:
    """Aggregate evaluation status across a batch.

    Returns (status, completed, failed, pending, total) where:
      - status: one of "completed", "failed", "processing", "pending"
      - completed/failed/pending/total: integer counts
    """
    total = evaluations.count()
    completed = evaluations.filter(status="completed").count()
    failed = evaluations.filter(status="failed").count()
    pending = total - completed - failed

    if total > 0 and completed == total:
        status = "completed"
    elif total > 0 and completed + failed == total:
        status = "failed"
    elif total > 0 and (completed + failed) > 0:
        status = "processing"
    elif evaluations.filter(status="running").exists():
        status = "processing"
    else:
        status = "pending"

    return status, completed, failed, pending, total


def create_batch_evaluation(
    user: CustomUser, payload: Dict[str, Any]
) -> Tuple[Any, int]:
    exam_data = payload["exam"]
    model_names = payload["models"]
    iterations = payload.get("iterations", 1)

    if iterations > MAX_ITERATIONS:
        raise ValueError(f"Iterations limit exceeded (max {MAX_ITERATIONS})")

    models = _resolve_models(user, model_names)

    with transaction.atomic():
        # Lock the user row to serialize concurrent batch creation for the
        # same user. Prevents race-induced duplicate courses/exams. On SQLite
        # this is a no-op; on PostgreSQL it acquires a row lock.
        CustomUser.objects.select_for_update().get(id=user.id)

        course = _get_or_create_course(user, exam_data["course"])
        exam = _get_or_create_exam(
            user, course, exam_data["external_id"], exam_data["title"]
        )

        if not exam.question_set.exists():
            _create_questions_and_options(exam, exam_data["questions"])

        batch = BatchEvaluation.objects.create(
            public_id=_generate_public_id(),
            user=user,
            exam=exam,
        )

        question_ids = list(exam.question_set.values_list("id", flat=True))
        exam_questions_map = {exam.id: question_ids}

        evaluations_meta = []
        evaluation_ids = []
        for model in models:
            for rep in range(1, iterations + 1):
                eval_stub = create_evaluation_stub(
                    exam_id=exam.id,
                    model_id=model.id,
                    user_prompt="",
                    notes=None,
                )
                eval_stub.batch = batch
                eval_stub.save(update_fields=["batch"])
                evaluation_ids.append(eval_stub.id)
                evaluations_meta.append(
                    {
                        "id": eval_stub.id,
                        "exam_id": exam.id,
                        "model_id": model.id,
                        "repetition": rep,
                        "total_repetitions": iterations,
                    }
                )

    total_tasks = len(evaluation_ids)

    try:
        async_task(
            batch_orchestrator_task,
            evaluations_meta,
            exam_questions_map,
            "",
            timeout=EVALUATION_TASK_TIMEOUT,
            group=f"batch:{batch.public_id}",
        )
    except Exception:
        Evaluation.objects.filter(
            id__in=evaluation_ids, status__in=("pending", "running")
        ).update(
            status="failed",
            failed_reason="Orchestrator enqueue failed",
        )
        logger.exception("Failed to enqueue orchestrator for batch %s", batch.public_id)
        raise OrchestratorEnqueueError("Failed to queue evaluation tasks")

    return batch, total_tasks


def get_batch_evaluation(user, evaluation_id: str) -> Optional[BatchEvaluation]:
    return BatchEvaluation.objects.filter(public_id=evaluation_id, user=user).first()


def get_batch_status(user, evaluation_id: str) -> Optional[Dict[str, Any]]:
    batch = get_batch_evaluation(user, evaluation_id)
    if not batch:
        return None

    status, completed, failed, pending, total = _aggregate_status(batch.evaluations)

    return {
        "evaluation_id": batch.public_id,
        "status": status,
        "progress": {
            "completed": completed,
            "failed": failed,
            "pending": pending,
            "total": total,
        },
    }


def get_batch_results(user, evaluation_id: str) -> Optional[Dict[str, Any]]:
    batch = get_batch_evaluation(user, evaluation_id)
    if not batch:
        return None

    evaluations = batch.evaluations.all()
    if evaluations.filter(status__in=["pending", "running"]).exists():
        return "not_ready"

    question_ids = list(
        batch.exam.question_set.order_by("id").values_list("id", flat=True)
    )
    question_index = {qid: idx + 1 for idx, qid in enumerate(question_ids)}

    option_letters = {}
    for qid in question_ids:
        option_ids = list(
            QuestionOption.objects.filter(question_id=qid)
            .order_by("id")
            .values_list("id", flat=True)
        )
        for idx, oid in enumerate(option_ids):
            option_letters[oid] = chr(ord("a") + idx)

    results: Dict[str, List[Dict[str, Any]]] = {}
    for eval_obj in evaluations.order_by("id"):
        model_name = eval_obj.model.description
        iteration = len(results.get(model_name, [])) + 1

        details = []
        qes = eval_obj.questionevaluation_set.select_related(
            "question", "question_option"
        ).order_by("question_id")
        for qe in qes:
            details.append(
                {
                    "question_id": f"q{question_index.get(qe.question_id, 0)}",
                    "selected_option": option_letters.get(qe.question_option_id),
                    "correct": "true" if qe.is_correct else "false",
                }
            )

        results.setdefault(model_name, []).append(
            {
                "iteration": iteration,
                "overall_score": eval_obj.grade,
                "details": details,
            }
        )

    return {
        "evaluation_id": batch.public_id,
        "results": results,
    }


def get_batch_history(user, limit: int = 50, offset: int = 0) -> Dict[str, Any]:
    batches = BatchEvaluation.objects.filter(user=user).order_by("-created_at")
    total = batches.count()
    page = list(batches[offset : offset + limit])

    results = []
    for batch in page:
        models_used = list(
            batch.evaluations.values_list("model__description", flat=True).distinct()
        )
        status, _, _, _, _ = _aggregate_status(batch.evaluations)
        results.append(
            {
                "evaluation_id": batch.public_id,
                "created_at": batch.created_at.isoformat().replace("+00:00", "Z"),
                "status": status,
                "models_used": models_used,
            }
        )

    next_offset = offset + limit
    prev_offset = max(0, offset - limit)
    return {
        "count": total,
        "next": (
            f"/api/v1/evaluations?limit={limit}&offset={next_offset}"
            if next_offset < total
            else None
        ),
        "previous": (
            f"/api/v1/evaluations?limit={limit}&offset={prev_offset}"
            if offset > 0
            else None
        ),
        "results": results,
    }
