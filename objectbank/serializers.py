from .models import LinkRegistry
from rest_framework import serializers
from django.contrib.auth.models import User

class LinkRegistrySerializer(serializers.ModelSerializer):
    user = serializers.PrimaryKeyRelatedField(queryset=User.objects.all())
    username = serializers.CharField(source='user.username', read_only=True)

    class Meta:
        model = LinkRegistry
        fields = "__all__"
        read_only_fields = ("created_at", "updated_at")
