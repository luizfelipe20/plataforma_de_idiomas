from django.contrib import admin

from .models import MediaSegment, MediaSource, ProcessingStep


class ProcessingStepInline(admin.TabularInline):
    model = ProcessingStep
    extra = 0
    readonly_fields = ("name", "status", "attempts", "progress", "message", "started_at", "finished_at")


@admin.register(MediaSource)
class MediaSourceAdmin(admin.ModelAdmin):
    list_display = ("title", "owner", "language", "status", "current_step", "progress", "duration_seconds", "created_at")
    list_filter = ("status", "language")
    search_fields = ("title", "original_filename")
    inlines = [ProcessingStepInline]


@admin.register(MediaSegment)
class MediaSegmentAdmin(admin.ModelAdmin):
    list_display = ("media", "index", "start_seconds", "end_seconds", "transcript_status")
    list_filter = ("transcript_status",)
