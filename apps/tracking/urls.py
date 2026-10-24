from django.urls import path

from . import views

app_name = "tracking"

urlpatterns = [
    path("heartbeat/", views.heartbeat, name="heartbeat"),
    path("estudo/iniciar/", views.study_start, name="study_start"),
    path("estudo/<int:pk>/batida/", views.study_beat, name="study_beat"),
    path("sessoes/", views.session_history, name="session_history"),
    path("sessoes/<int:pk>/excluir/", views.session_delete, name="session_delete"),
    path("sessoes/limpar/", views.session_clear, name="session_clear"),
]
