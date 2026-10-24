from django.urls import path

from . import views

app_name = "vocabulary"

urlpatterns = [
    path("", views.term_list, name="term_list"),
    path("marcar/", views.term_mark, name="term_mark"),
    path("explicar/", views.term_explain, name="term_explain"),
    path("praticar/", views.practice_create, name="practice_create"),
    path("<int:pk>/", views.term_detail, name="term_detail"),
    path("<int:pk>/significado/", views.term_update, name="term_update"),
    path("<int:pk>/excluir/", views.term_delete, name="term_delete"),
]
