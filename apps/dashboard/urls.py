from django.urls import path

from . import views

app_name = "dashboard"

urlpatterns = [
    path("", views.index, name="index"),
    path("relatorio/texto/<int:pk>/", views.text_report, name="text_report"),
]
