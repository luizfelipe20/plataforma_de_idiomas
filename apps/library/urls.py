from django.urls import path

from . import views

app_name = "library"

urlpatterns = [
    path("", views.text_list, name="text_list"),
    path("textos/novo/", views.text_create, name="text_create"),
    path("textos/gerar/", views.text_generate, name="text_generate"),
    path("textos/<int:pk>/", views.text_detail, name="text_detail"),
    path("textos/<int:pk>/editar/", views.text_update, name="text_update"),
    path("textos/<int:pk>/excluir/", views.text_delete, name="text_delete"),
    path("textos/<int:pk>/traduzir/", views.text_translate, name="text_translate"),
    path("textos/<int:pk>/estudar/", views.study, name="study"),
    path("trechos/<int:pk>/audio.mp3", views.chunk_audio, name="chunk_audio"),
    path("tags/", views.tag_list, name="tag_list"),
    path("tags/<int:pk>/renomear/", views.tag_update, name="tag_update"),
    path("tags/<int:pk>/excluir/", views.tag_delete, name="tag_delete"),
]
