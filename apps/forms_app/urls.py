from django.urls import path

from apps.forms_app.views import (
    FormActivateView,
    FormDetailView,
    FormExportView,
    FormListCreateView,
    FormResponsesView,
    PublicFormSubmitView,
)

urlpatterns = [
    path('', FormListCreateView.as_view(), name='form-list-create'),
    path('<uuid:pk>/', FormDetailView.as_view(), name='form-detail'),
    path('<uuid:pk>/activate/', FormActivateView.as_view(), name='form-activate'),
    path('<uuid:pk>/responses/', FormResponsesView.as_view(), name='form-responses'),
    path('<uuid:pk>/export/', FormExportView.as_view(), name='form-export'),
    path('public/<slug:slug>/submit/', PublicFormSubmitView.as_view(), name='form-public-submit'),
]
