from rest_framework import serializers
from .models import LinkRegistry

class LinkRegistrySerializer(serializers.ModelSerializer):
    user = serializers.StringRelatedField(read_only=True)

    class Meta:
        model = LinkRegistry
        fields = "__all__"
        read_only_fields = ("created_at", "updated_at")
