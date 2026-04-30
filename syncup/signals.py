"""Signal handlers for syncup app.

Currently: clean up Filebase S3 objects when a MediaUploadProgress row is
deleted (covers cascading deletes from Device or MediaDownloadRequest too).
"""
from django.db.models.signals import pre_delete
from django.dispatch import receiver

from .models import MediaUploadProgress


@receiver(pre_delete, sender=MediaUploadProgress)
def _cleanup_filebase_object(sender, instance: MediaUploadProgress, **kwargs):
    if instance.storage_backend != "filebase" or not instance.s3_key:
        return

    # Skip deletion if another row still references the same S3 key (dedup).
    sibling_exists = MediaUploadProgress.objects.filter(
        s3_key=instance.s3_key
    ).exclude(pk=instance.pk).exists()
    if sibling_exists:
        return

    from . import storage_filebase
    if storage_filebase.is_enabled():
        storage_filebase.delete_key(instance.s3_key)
