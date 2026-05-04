"""Signal handlers for syncup app.

Currently: clean up Cloudinary objects when a MediaUploadProgress row is
deleted (covers cascading deletes from Device or MediaDownloadRequest too).
"""
from django.db.models.signals import pre_delete
from django.dispatch import receiver

from .models import MediaUploadProgress


@receiver(pre_delete, sender=MediaUploadProgress)
def _cleanup_cloud_object(sender, instance: MediaUploadProgress, **kwargs):
    if instance.storage_backend != "cloud" or not instance.s3_key:
        return

    # Skip deletion if another row still references the same key (dedup).
    sibling_exists = MediaUploadProgress.objects.filter(
        s3_key=instance.s3_key
    ).exclude(pk=instance.pk).exists()
    if sibling_exists:
        return

    from . import storage_cloud
    if storage_cloud.is_enabled():
        # Use the Cloudinary public_id (ipfs_cid field) for deletion
        storage_cloud.delete_key(instance.ipfs_cid or instance.s3_key)
