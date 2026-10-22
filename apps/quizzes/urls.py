from django.urls import path

from . import views

app_name = "quizzes"

urlpatterns = [
    path("texto/<int:text_pk>/gerar/", views.quiz_generate, name="quiz_generate"),
    path("texto/<int:text_pk>/novo/", views.quiz_new, name="quiz_new"),
    path("perguntas/<int:pk>/audio.mp3", views.question_audio, name="question_audio"),
    path("<int:pk>/", views.quiz_detail, name="quiz_detail"),
    path("<int:pk>/tentativas/nova/", views.attempt_start, name="attempt_start"),
    path("tentativas/", views.attempt_history, name="attempt_history"),
    path("tentativas/<int:pk>/", views.attempt_take, name="attempt_take"),
    path("tentativas/<int:pk>/resultado/", views.attempt_result, name="attempt_result"),
]
