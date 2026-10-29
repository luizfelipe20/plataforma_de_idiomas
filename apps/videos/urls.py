from django.urls import path

from . import views

app_name = "videos"

urlpatterns = [
    path("", views.media_list, name="media_list"),
    path("enviar/", views.upload_start, name="upload_start"),
    path("situacao/", views.media_status, name="media_status"),
    path("<int:pk>/", views.media_detail, name="media_detail"),
    path("<int:pk>/bloco/", views.upload_chunk, name="upload_chunk"),
    path("<int:pk>/repetir/", views.media_retry, name="media_retry"),
    path("<int:pk>/excluir/", views.media_delete, name="media_delete"),
    path("<int:pk>/arquivos/<slug:kind>/", views.media_file, name="media_file"),
    path("<int:pk>/legendas.<str:fmt>", views.media_subtitles, name="media_subtitles"),
    path("<int:pk>/quiz/", views.media_quiz_create, name="media_quiz_create"),
    path("<int:pk>/segmentos/<int:index>/", views.segment_detail, name="segment_detail"),
    path("<int:pk>/segmentos/<int:index>/audio.mp3", views.segment_audio, name="segment_audio"),
    path("<int:pk>/segmentos/<int:index>/legendas.<str:fmt>", views.segment_subtitles, name="segment_subtitles"),
    path("<int:pk>/segmentos/<int:index>/revisar/", views.segment_review, name="segment_review"),
    path("<int:pk>/segmentos/<int:index>/transcrever/", views.segment_retranscribe, name="segment_retranscribe"),
]
