from django.urls import path

from apps.contact.views import AdminContactDetailView, AdminContactListView

urlpatterns = [
    path('', AdminContactListView.as_view(), name='admin-contact-list'),
    path('<uuid:pk>/', AdminContactDetailView.as_view(), name='admin-contact-detail'),
]
