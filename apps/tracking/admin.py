from django.contrib import admin

from .models import AccessSession, StudySession


@admin.register(AccessSession)
class AccessSessionAdmin(admin.ModelAdmin):
    list_display = ("user", "started_at", "ended_at", "duration_seconds", "active_seconds", "status", "end_reason")
    list_filter = ("status", "end_reason")


@admin.register(StudySession)
class StudySessionAdmin(admin.ModelAdmin):
    list_display = ("user", "activity", "text", "started_at", "ended_at", "duration_seconds")
    list_filter = ("activity",)
