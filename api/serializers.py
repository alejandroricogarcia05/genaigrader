from rest_framework import serializers


class ChoiceSerializer(serializers.Serializer):
    choice_text = serializers.CharField(required=True)
    isCorrect = serializers.BooleanField(required=True)


class QuestionSerializer(serializers.Serializer):
    question_text = serializers.CharField(required=True)
    choices = ChoiceSerializer(many=True, required=True)

    def validate_choices(self, value):
        if len(value) < 2:
            raise serializers.ValidationError("At least 2 choices are required")
        correct_count = sum(1 for c in value if c.get("isCorrect"))
        if correct_count != 1:
            raise serializers.ValidationError(
                "Exactly one choice must be marked as correct"
            )
        return value


class ExamSerializer(serializers.Serializer):
    external_id = serializers.CharField(required=True)
    course = serializers.CharField(required=True)
    title = serializers.CharField(required=True)
    questions = QuestionSerializer(many=True, required=True)

    def validate_questions(self, value):
        if len(value) == 0:
            raise serializers.ValidationError("At least one question is required")
        return value


class CreateEvaluationSerializer(serializers.Serializer):
    exam = ExamSerializer(required=True)
    models = serializers.ListField(
        child=serializers.CharField(), required=True, allow_empty=False
    )
    iterations = serializers.IntegerField(required=False, default=1)

    def validate_iterations(self, value):
        if value < 1:
            raise serializers.ValidationError("Iterations must be at least 1")
        return value
