from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("contas/", include("apps.accounts.urls")),
    path("biblioteca/", include("apps.library.urls")),
    path("quizzes/", include("apps.quizzes.urls")),
    path("tempo/", include("apps.tracking.urls")),
    path("vocabulario/", include("apps.vocabulary.urls")),
    path("", include("apps.dashboard.urls")),
]
