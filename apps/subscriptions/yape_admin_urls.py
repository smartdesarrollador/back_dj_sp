from django.urls import path

from .yape_admin_views import YapeConfigView, YapeProofListView, YapeProofReviewView

urlpatterns = [
    path('config/', YapeConfigView.as_view(), name='yape-admin-config'),
    path('proofs/', YapeProofListView.as_view(), name='yape-proof-list'),
    path('proofs/<uuid:proof_id>/review/', YapeProofReviewView.as_view(), name='yape-proof-review'),
]
