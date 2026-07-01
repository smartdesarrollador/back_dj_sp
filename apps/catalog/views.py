from rest_framework import status
from rest_framework.generics import ListAPIView, ListCreateAPIView, RetrieveUpdateDestroyAPIView
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import CatalogItem
from .serializers import CatalogItemSerializer, CatalogItemWriteSerializer


def _require_staff(request: Request) -> Response | None:
    if not request.user.is_staff:
        return Response({'detail': 'Staff access required.'}, status=status.HTTP_403_FORBIDDEN)
    return None


class PublicCatalogListView(ListAPIView):
    """GET /api/v1/public/catalog/ — lista items activos, filtrable por ?app=<name>."""
    permission_classes = [AllowAny]
    serializer_class = CatalogItemSerializer
    pagination_class = None

    def get_queryset(self):
        qs = CatalogItem.objects.filter(is_active=True)
        app = self.request.query_params.get('app')
        if app:
            qs = qs.filter(target_apps__contains=[app])
        return qs


class AdminCatalogListCreateView(ListCreateAPIView):
    """
    GET  /api/v1/admin/catalog/ — lista todos los items (staff)
    POST /api/v1/admin/catalog/ — crear item con imagen (multipart/form-data, staff)
    """
    permission_classes = [IsAuthenticated]
    pagination_class = None

    def get_serializer_class(self):
        return CatalogItemWriteSerializer if self.request.method == 'POST' else CatalogItemSerializer

    def get_queryset(self):
        return CatalogItem.objects.all()

    def list(self, request: Request, *args, **kwargs) -> Response:
        if err := _require_staff(request):
            return err
        return super().list(request, *args, **kwargs)

    def create(self, request: Request, *args, **kwargs) -> Response:
        if err := _require_staff(request):
            return err
        return super().create(request, *args, **kwargs)


class AdminCatalogDetailView(RetrieveUpdateDestroyAPIView):
    """
    GET    /api/v1/admin/catalog/{id}/ — detalle (staff)
    PATCH  /api/v1/admin/catalog/{id}/ — editar con imagen opcional (staff)
    DELETE /api/v1/admin/catalog/{id}/ — eliminar + borrar imagen del disco (staff)
    """
    permission_classes = [IsAuthenticated]
    queryset = CatalogItem.objects.all()
    http_method_names = ['get', 'patch', 'delete', 'head', 'options']

    def get_serializer_class(self):
        return CatalogItemWriteSerializer if self.request.method == 'PATCH' else CatalogItemSerializer

    def _check_staff(self, request: Request) -> Response | None:
        return _require_staff(request)

    def retrieve(self, request: Request, *args, **kwargs) -> Response:
        if err := self._check_staff(request):
            return err
        return super().retrieve(request, *args, **kwargs)

    def partial_update(self, request: Request, *args, **kwargs) -> Response:
        if err := self._check_staff(request):
            return err
        kwargs['partial'] = True
        return super().update(request, *args, **kwargs)

    def destroy(self, request: Request, *args, **kwargs) -> Response:
        if err := self._check_staff(request):
            return err
        instance: CatalogItem = self.get_object()
        if instance.image:
            instance.image.delete(save=False)
        instance.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class AdminCatalogReorderView(APIView):
    """POST /api/v1/admin/catalog/{id}/reorder/ — actualiza solo el campo order (staff)."""
    permission_classes = [IsAuthenticated]

    def post(self, request: Request, pk: str) -> Response:
        if err := _require_staff(request):
            return err
        try:
            item = CatalogItem.objects.get(pk=pk)
        except CatalogItem.DoesNotExist:
            return Response({'detail': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)
        order = request.data.get('order')
        if order is None or not str(order).isdigit():
            return Response({'detail': 'order debe ser un entero positivo.'}, status=status.HTTP_400_BAD_REQUEST)
        item.order = int(order)
        item.save(update_fields=['order', 'updated_at'])
        return Response(CatalogItemSerializer(item, context={'request': request}).data)
