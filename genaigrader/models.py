from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

from genaigrader.services import model_service


class Course(models.Model):
    id = models.AutoField(primary_key=True)
    name = models.CharField(max_length=255)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)

    def __str__(self):
        return self.name


class Exam(models.Model):
    id = models.AutoField(primary_key=True)
    description = models.CharField(max_length=255)
    course = models.ForeignKey(Course, on_delete=models.CASCADE)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    external_id = models.CharField(max_length=255, null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "external_id"],
                condition=Q(external_id__isnull=False),
                name="uniq_exam_user_external_id",
            )
        ]

    def __str__(self):
        return self.description


class QuestionOption(models.Model):
    id = models.AutoField(primary_key=True)
    content = models.CharField(max_length=255)
    question = models.ForeignKey("Question", on_delete=models.CASCADE)

    def __str__(self):
        return self.content


class Question(models.Model):
    id = models.AutoField(primary_key=True)
    statement = models.TextField()
    correct_option = models.ForeignKey(
        QuestionOption,
        on_delete=models.CASCADE,
        null=True,
        related_name="correct_option_for",
    )
    exam = models.ForeignKey(Exam, on_delete=models.CASCADE)

    def __str__(self):
        return self.statement[:50]


class Family(models.Model):
    id = models.AutoField(primary_key=True)
    name = models.CharField(max_length=100, unique=True)
    base_color = models.CharField(max_length=7, null=True, blank=True)

    def __str__(self):
        return self.name


class Model(models.Model):
    id = models.AutoField(primary_key=True)
    description = models.CharField(max_length=255)

    family = models.ForeignKey(
        Family, on_delete=models.SET_NULL, null=True, blank=True, related_name="models"
    )
    color = models.CharField(
        max_length=7,
        null=True,
        blank=True,
        help_text="Hex color code for the model",
    )
    parameter_count = models.FloatField(
        null=True, blank=True, help_text="Model size in billions of parameters"
    )
    version = models.CharField(max_length=50, null=True, blank=True)

    api_url = models.URLField(max_length=500, null=True, blank=True)
    api_key = models.CharField(max_length=255, null=True, blank=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, null=True, blank=True
    )

    def clean(self):
        super().clean()
        if self.is_external and not self.user:
            raise ValidationError("The 'user' field is required for external models")
        if not self.is_external and self.user:
            raise ValidationError(
                "The 'user' field should only be used for external models"
            )

    def save(self, *args, **kwargs):
        needs_color_refresh = model_service.needs_model_color_refresh(self)
        missing_core_info = not self.family or needs_color_refresh

        if self.is_external:
            needs_classification = missing_core_info or not self.version
        else:
            needs_classification = missing_core_info or self.parameter_count is None

        if needs_classification:
            model_service.auto_classify_and_color(self)

        super().save(*args, **kwargs)

    def __str__(self):
        return self.description

    @property
    def family_name(self):
        return self.family.name if self.family else "Unknown"

    @property
    def is_external(self):
        return self.api_url is not None and self.api_key is not None

    def get_sort_key(self):
        """
        Generate a sort key for this model object.
        Local models are sorted first by family, then by size, then by variant.
        External models come last and are sorted alphabetically.
        """
        if self.is_external:
            # External models go last, sorted alphabetically
            return (1, self.description.lower())
        else:

            # Local models go first
            # Extract model information from name for local models
            family, size_value, size_unit, variant, _ = (
                model_service.extract_model_info(self.description, self.is_external)
            )
            # Sort by: family, size_value, size_unit (b < others), variant
            size_unit_priority = 0 if size_unit.lower() == "b" else 1
            return (
                0,
                family.lower(),
                size_value,
                size_unit_priority,
                size_unit.lower(),
                variant.lower(),
            )


class Evaluation(models.Model):
    id = models.AutoField(primary_key=True)
    prompt = models.TextField()
    ev_date = models.DateTimeField()
    grade = models.FloatField()
    time = models.FloatField()
    model = models.ForeignKey(Model, on_delete=models.CASCADE)
    exam = models.ForeignKey(Exam, on_delete=models.CASCADE)
    ollama_version = models.CharField(
        max_length=50,
        null=True,
        blank=True,
        help_text="Version of Ollama used (null for external models)",
    )
    notes = models.TextField(
        blank=True, null=True, help_text="Optional notes for the evaluation"
    )

    status = models.CharField(
        max_length=20,
        choices=[
            ("pending", "Pending"),
            ("running", "Running"),
            ("completed", "Completed"),
            ("failed", "Failed"),
        ],
        default="pending",
    )
    total_questions = models.PositiveIntegerField(default=0)
    failed_question_id = models.PositiveIntegerField(
        null=True, blank=True, help_text="ID of the question that caused the failure"
    )
    failed_reason = models.TextField(
        blank=True, null=True, help_text="Reason for evaluation failure"
    )
    batch = models.ForeignKey(
        "BatchEvaluation",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="evaluations",
    )

    def __str__(self):
        return f"{self.prompt} {self.grade}"


class BatchEvaluation(models.Model):
    id = models.AutoField(primary_key=True)
    public_id = models.CharField(max_length=64, unique=True, db_index=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    exam = models.ForeignKey(Exam, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Batch {self.public_id} by {self.user}"


class QuestionEvaluation(models.Model):
    id = models.AutoField(primary_key=True)
    evaluation = models.ForeignKey(Evaluation, on_delete=models.CASCADE)
    question = models.ForeignKey(Question, on_delete=models.CASCADE)
    question_option = models.ForeignKey(
        QuestionOption, on_delete=models.CASCADE, null=True, blank=True
    )
    response_text = models.TextField(blank=True, default="")
    is_correct = models.BooleanField(null=True, blank=True)
    question_time = models.FloatField(default=0.0)

    def __str__(self):
        option_id = (
            self.question_option_id if self.question_option_id is not None else "None"
        )
        return f"Evaluation {self.evaluation.id}, Question {self.question.id}, Option {option_id}"
