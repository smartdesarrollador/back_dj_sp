from django.urls import path

from apps.env_vars.views import (
    EnvVariableDetailView,
    EnvVariableListCreateView,
    EnvVariableRevealView,
)

urlpatterns = [
    path('', EnvVariableListCreateView.as_view(), name='env-var-list-create'),
    path('<uuid:pk>/', EnvVariableDetailView.as_view(), name='env-var-detail'),
    path('<uuid:pk>/reveal/', EnvVariableRevealView.as_view(), name='env-var-reveal'),
]
