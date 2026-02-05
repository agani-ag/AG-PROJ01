# views.py
from rest_framework.viewsets import ModelViewSet
from ..models import LinkRegistry
from ..serializers import LinkRegistrySerializer
from ..permissions import IsAdminOrReadOnly
from django.shortcuts import render

class LinkRegistryViewSet(ModelViewSet):
    queryset = LinkRegistry.objects.filter(active=True)
    serializer_class = LinkRegistrySerializer
    permission_classes = [IsAdminOrReadOnly]

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)

def link_registry_view(request):
    return render(request, 'link_registry/link_registry.html')