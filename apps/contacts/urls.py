from django.urls import path

from apps.contacts.views import (
    ContactDetailView,
    ContactExportView,
    ContactGroupDetailView,
    ContactGroupListCreateView,
    ContactListCreateView,
)

urlpatterns = [
    path('', ContactListCreateView.as_view(), name='contact-list-create'),
    path('export/', ContactExportView.as_view(), name='contact-export'),
    path('groups/', ContactGroupListCreateView.as_view(), name='contact-group-list-create'),
    path('groups/<uuid:pk>/', ContactGroupDetailView.as_view(), name='contact-group-detail'),
    path('<uuid:pk>/', ContactDetailView.as_view(), name='contact-detail'),
]
