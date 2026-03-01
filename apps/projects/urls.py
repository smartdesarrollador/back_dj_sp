from django.urls import path

from apps.projects import views

# All UUIDs use the built-in <uuid:pk> converter.
# Nested resource param names: pk=project, sk=section, ik=item, fk=field, mk=member

urlpatterns = [
    # ── Projects ────────────────────────────────────────────────────────────
    path('', views.ProjectListView.as_view(), name='project-list'),
    path('create/', views.ProjectCreateView.as_view(), name='project-create'),
    path('<uuid:pk>/', views.ProjectDetailView.as_view(), name='project-detail'),
    path('<uuid:pk>/update/', views.ProjectUpdateView.as_view(), name='project-update'),
    path('<uuid:pk>/delete/', views.ProjectDeleteView.as_view(), name='project-delete'),

    # ── Sections ────────────────────────────────────────────────────────────
    path('<uuid:pk>/sections/', views.SectionCreateView.as_view(), name='section-create'),
    path('<uuid:pk>/sections/reorder/', views.SectionReorderView.as_view(), name='section-reorder'),
    path('<uuid:pk>/sections/<uuid:sk>/', views.SectionUpdateView.as_view(), name='section-update'),
    path('<uuid:pk>/sections/<uuid:sk>/delete/', views.SectionDeleteView.as_view(), name='section-delete'),

    # ── Items ────────────────────────────────────────────────────────────────
    path(
        '<uuid:pk>/sections/<uuid:sk>/items/',
        views.ItemCreateView.as_view(),
        name='item-create',
    ),
    path(
        '<uuid:pk>/sections/<uuid:sk>/items/<uuid:ik>/',
        views.ItemUpdateView.as_view(),
        name='item-update',
    ),
    path(
        '<uuid:pk>/sections/<uuid:sk>/items/<uuid:ik>/delete/',
        views.ItemDeleteView.as_view(),
        name='item-delete',
    ),

    # ── Fields ───────────────────────────────────────────────────────────────
    path(
        '<uuid:pk>/sections/<uuid:sk>/items/<uuid:ik>/fields/',
        views.FieldCreateView.as_view(),
        name='field-create',
    ),
    path(
        '<uuid:pk>/sections/<uuid:sk>/items/<uuid:ik>/fields/<uuid:fk>/',
        views.FieldUpdateView.as_view(),
        name='field-update',
    ),
    path(
        '<uuid:pk>/sections/<uuid:sk>/items/<uuid:ik>/fields/<uuid:fk>/delete/',
        views.FieldDeleteView.as_view(),
        name='field-delete',
    ),

    # ── Reveal Password ───────────────────────────────────────────────────────
    path(
        '<uuid:pk>/sections/<uuid:sk>/items/<uuid:ik>/reveal/<uuid:fk>/',
        views.RevealPasswordView.as_view(),
        name='field-reveal',
    ),

    # ── Members ───────────────────────────────────────────────────────────────
    path('<uuid:pk>/members/', views.MemberListView.as_view(), name='member-list'),
    path('<uuid:pk>/members/add/', views.MemberAddView.as_view(), name='member-add'),
    path('<uuid:pk>/members/<uuid:mk>/', views.MemberRemoveView.as_view(), name='member-remove'),
]
