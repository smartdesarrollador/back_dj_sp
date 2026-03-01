from django.urls import path

from apps.support.views import (
    TicketCloseView,
    TicketCommentView,
    TicketDetailView,
    TicketExportView,
    TicketListCreateView,
)

urlpatterns = [
    path('tickets/', TicketListCreateView.as_view()),
    path('tickets/export/', TicketExportView.as_view()),  # before <uuid:pk>
    path('tickets/<uuid:pk>/', TicketDetailView.as_view()),
    path('tickets/<uuid:pk>/close/', TicketCloseView.as_view()),
    path('tickets/<uuid:pk>/comments/', TicketCommentView.as_view()),
]
