from django.contrib import admin

from .models import AttemptAnswer, Option, Question, Quiz, QuizAttempt


class OptionInline(admin.TabularInline):
    model = Option
    extra = 0


@admin.register(Question)
class QuestionAdmin(admin.ModelAdmin):
    list_display = ("prompt", "kind", "quiz", "order")
    list_filter = ("kind",)
    inlines = [OptionInline]


class QuestionInline(admin.TabularInline):
    model = Question
    extra = 0
    fields = ("order", "kind", "prompt")
    show_change_link = True


@admin.register(Quiz)
class QuizAdmin(admin.ModelAdmin):
    list_display = ("__str__", "title", "language", "options_per_question", "ai_model", "created_at")
    inlines = [QuestionInline]


class AttemptAnswerInline(admin.TabularInline):
    model = AttemptAnswer
    extra = 0
    readonly_fields = ("question", "selected_option", "is_correct")


@admin.register(QuizAttempt)
class QuizAttemptAdmin(admin.ModelAdmin):
    list_display = ("quiz", "user", "status", "score", "mode", "completed_at")
    list_filter = ("status", "mode")
    inlines = [AttemptAnswerInline]
