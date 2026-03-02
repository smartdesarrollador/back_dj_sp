"""
Management command: seed_faker_data

Fills all application tables with realistic fake data using Faker (es_ES).
Calls seed_dev_data first (idempotent) to ensure tenants + users exist.

Run: python manage.py seed_faker_data
Make alias: make seed-faker
"""
from __future__ import annotations

import base64
import random
import uuid
from datetime import date, timedelta
from typing import Any

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.utils import timezone
from django.utils.text import slugify
from faker import Faker

from apps.bookmarks.models import Bookmark, BookmarkCollection
from apps.calendar_app.models import CalendarEvent, EventAttendee
from apps.contacts.models import Contact, ContactGroup
from apps.digital_services.models import (
    CVDocument,
    DigitalCard,
    LandingTemplate,
    PortfolioItem,
    PublicProfile,
)
from apps.env_vars.models import EnvVariable
from apps.forms_app.models import Form, FormQuestion, FormResponse
from apps.notes.models import Note
from apps.projects.models import (
    Project,
    ProjectItem,
    ProjectItemField,
    ProjectMember,
    ProjectSection,
)
from apps.rbac.models import Role, UserRole
from apps.sharing.models import Share
from apps.snippets.models import CodeSnippet
from apps.ssh_keys.models import SSHKey
from apps.ssl_certs.models import SSLCertificate
from apps.subscriptions.models import Invoice, PaymentMethod, Subscription
from apps.support.models import SupportTicket, TicketComment
from apps.tasks.models import Task, TaskBoard, TaskComment
from apps.tenants.models import Tenant

User = get_user_model()
fake = Faker("es_ES")
Faker.seed(42)

RESERVED_USERNAMES = {
    "admin", "api", "www", "app", "dashboard", "login", "register",
    "help", "support", "public", "landing", "cv", "portafolio",
}

COLORS = ["#6366f1", "#10b981", "#f59e0b", "#ef4444", "#8b5cf6", "#3b82f6"]
COLOR_NAMES = ["blue", "green", "red", "purple", "yellow", "gray"]


class Command(BaseCommand):
    help = "Seeds all app tables with realistic fake data using Faker (idempotent)"

    def handle(self, *args: Any, **kwargs: Any) -> None:
        self.stdout.write("Ensuring base dev data exists…")
        call_command("seed_dev_data", verbosity=0)

        for tenant in Tenant.objects.all():
            self.stdout.write(f"  Seeding tenant: {tenant.name}")
            users = list(User.objects.filter(tenant=tenant))
            if not users:
                continue

            self._seed_subscriptions(tenant)
            self._seed_projects(tenant, users)
            self._seed_tasks(tenant, users)
            self._seed_calendar(tenant, users)
            self._seed_notes(tenant, users)
            self._seed_contacts(tenant, users)
            self._seed_bookmarks(tenant, users)
            self._seed_credentials(tenant, users)
            self._seed_snippets(tenant, users)
            self._seed_forms(tenant, users)
            self._seed_support(tenant, users)
            self._seed_digital_services(tenant, users)
            self._seed_sharing(tenant, users)

        self.stdout.write(self.style.SUCCESS("✓ Fake data seeded for all tenants."))

    # ── Phase 1: Subscriptions ────────────────────────────────────────────────

    def _seed_subscriptions(self, tenant: Any) -> None:
        Subscription.objects.get_or_create(
            tenant=tenant,
            defaults={
                "plan": tenant.plan,
                "status": "active",
                "billing_cycle": "monthly",
                "current_period_start": timezone.now() - timedelta(days=15),
                "current_period_end": timezone.now() + timedelta(days=15),
            },
        )

        PaymentMethod.objects.get_or_create(
            stripe_payment_method_id=f"pm_seed_{tenant.slug}",
            defaults={
                "tenant": tenant,
                "type": "card",
                "brand": "visa",
                "last4": str(random.randint(1000, 9999)),
                "exp_month": random.randint(1, 12),
                "exp_year": random.randint(2025, 2030),
                "is_default": True,
            },
        )

        for i, status in enumerate(["paid", "open", "draft"]):
            Invoice.objects.get_or_create(
                stripe_invoice_id=f"in_seed_{tenant.slug}_{i}",
                defaults={
                    "tenant": tenant,
                    "amount_cents": random.choice([1000, 2900, 4900, 9900]),
                    "currency": "usd",
                    "status": status,
                    "invoice_date": timezone.now() - timedelta(days=30 * i),
                    "due_date": timezone.now() - timedelta(days=30 * i - 15),
                },
            )

    # ── Phase 2: Projects ─────────────────────────────────────────────────────

    def _seed_projects(self, tenant: Any, users: list) -> None:
        section_names = ["Backlog", "En progreso", "Completado"]

        for i in range(3):
            creator = users[i % len(users)]
            project, _ = Project.objects.get_or_create(
                tenant=tenant,
                name=f"Proyecto {i + 1} — {tenant.slug}",
                defaults={
                    "created_by": creator,
                    "description": fake.paragraph(nb_sentences=2),
                    "color": COLORS[i % len(COLORS)],
                },
            )

            # Project members
            for user in users[:min(3, len(users))]:
                ProjectMember.objects.get_or_create(
                    project=project,
                    user=user,
                    defaults={"role": random.choice(["viewer", "editor", "admin"])},
                )

            for j, section_name in enumerate(section_names):
                section, _ = ProjectSection.objects.get_or_create(
                    project=project,
                    name=section_name,
                    defaults={"order": j},
                )

                for k in range(4):
                    item, _ = ProjectItem.objects.get_or_create(
                        section=section,
                        name=f"Item {k + 1}",
                        defaults={
                            "description": fake.sentence(),
                            "url": f"https://seed.example.com/{tenant.slug}/item-{k}",
                            "username": f"user{k}@seed.example.com",
                            "order": k,
                        },
                    )

                    ProjectItemField.objects.get_or_create(
                        item=item,
                        label="Nota",
                        defaults={"value": fake.sentence(), "field_type": "text"},
                    )
                    ProjectItemField.objects.get_or_create(
                        item=item,
                        label="Documentación",
                        defaults={
                            "value": f"https://docs.seed.example.com/{tenant.slug}",
                            "field_type": "url",
                        },
                    )

    # ── Phase 3: Tasks ────────────────────────────────────────────────────────

    def _seed_tasks(self, tenant: Any, users: list) -> None:
        statuses = ["todo", "in_progress", "review", "done"]
        priorities = ["low", "medium", "high", "urgent"]

        for i in range(2):
            board, _ = TaskBoard.objects.get_or_create(
                tenant=tenant,
                name=f"Board {i + 1} — {tenant.slug}",
                defaults={
                    "created_by": users[0],
                    "description": fake.sentence(),
                },
            )

            for j in range(6):
                assignee = users[j % len(users)]
                task, _ = Task.objects.get_or_create(
                    board=board,
                    title=f"Tarea {j + 1} — {board.name}",
                    defaults={
                        "tenant": tenant,
                        "description": fake.paragraph(nb_sentences=2),
                        "status": statuses[j % len(statuses)],
                        "priority": priorities[j % len(priorities)],
                        "assignee": assignee,
                        "created_by": users[0],
                        "due_date": date.today() + timedelta(days=random.randint(1, 60)),
                        "order": j,
                    },
                )

                for user in users[:min(2, len(users))]:
                    TaskComment.objects.get_or_create(
                        task=task,
                        user=user,
                        defaults={"content": f"Comentario de {user.name} en la tarea."},
                    )

    # ── Phase 4: Calendar ─────────────────────────────────────────────────────

    def _seed_calendar(self, tenant: Any, users: list) -> None:
        for user in users:
            for i in range(5):
                delta_days = random.randint(-30, 60)
                start = timezone.now() + timedelta(
                    days=delta_days, hours=random.randint(8, 18)
                )
                end = start + timedelta(hours=random.randint(1, 3))

                event, _ = CalendarEvent.objects.get_or_create(
                    tenant=tenant,
                    user=user,
                    title=f"Evento {i + 1} — {user.email[:20]}",
                    defaults={
                        "description": fake.sentence(),
                        "start_datetime": start,
                        "end_datetime": end,
                        "color": random.choice(COLOR_NAMES),
                        "location": fake.city(),
                    },
                )

                other_users = [u for u in users if u != user]
                for attendee_user in random.sample(other_users, min(2, len(other_users))):
                    EventAttendee.objects.get_or_create(
                        event=event,
                        user=attendee_user,
                        defaults={
                            "status": random.choice(["invited", "accepted", "declined", "maybe"])
                        },
                    )

    # ── Phase 5: Notes ────────────────────────────────────────────────────────

    def _seed_notes(self, tenant: Any, users: list) -> None:
        categories = ["work", "personal", "ideas", "archive"]

        for user in users:
            for i in range(5):
                Note.objects.get_or_create(
                    tenant=tenant,
                    user=user,
                    title=f"Nota {i + 1} — {user.name[:20]}",
                    defaults={
                        "content": fake.paragraph(nb_sentences=3),
                        "category": categories[i % len(categories)],
                        "is_pinned": i < 2,
                        "color": random.choice(COLOR_NAMES),
                    },
                )

    # ── Phase 6: Contacts ─────────────────────────────────────────────────────

    def _seed_contacts(self, tenant: Any, users: list) -> None:
        for user in users:
            user_hex = str(user.pk).replace("-", "")[:8]
            groups = []

            for group_name in ["Trabajo", "Personal"]:
                group, _ = ContactGroup.objects.get_or_create(
                    user=user,
                    name=group_name,
                    defaults={
                        "tenant": tenant,
                        "color": random.choice(COLOR_NAMES),
                    },
                )
                groups.append(group)

            for k in range(5):
                Contact.objects.get_or_create(
                    tenant=tenant,
                    user=user,
                    email=f"contact-{user_hex}-{k}@seed.example.com",
                    defaults={
                        "first_name": fake.first_name(),
                        "last_name": fake.last_name(),
                        "phone": fake.phone_number()[:30],
                        "company": fake.company(),
                        "job_title": fake.job(),
                        "group": groups[k % 2] if k < 4 else None,
                        "notes": fake.sentence() if k % 2 == 0 else "",
                    },
                )

    # ── Phase 7: Bookmarks ────────────────────────────────────────────────────

    def _seed_bookmarks(self, tenant: Any, users: list) -> None:
        for user in users:
            user_hex = str(user.pk).replace("-", "")[:8]
            collections = []

            for coll_name in ["Dev Resources", "Artículos"]:
                coll, _ = BookmarkCollection.objects.get_or_create(
                    user=user,
                    name=coll_name,
                    defaults={
                        "tenant": tenant,
                        "color": random.choice(COLOR_NAMES),
                    },
                )
                collections.append(coll)

            for k in range(5):
                Bookmark.objects.get_or_create(
                    tenant=tenant,
                    user=user,
                    url=f"https://seed.example.com/{tenant.slug}/{user_hex}/bm-{k}",
                    defaults={
                        "title": f"Recurso {k + 1} — {user.name[:20]}",
                        "description": fake.sentence(),
                        "tags": ["python", "django"] if k % 2 == 0 else ["api", "backend"],
                        "collection": collections[k % 2] if k < 4 else None,
                    },
                )

    # ── Phase 8: Credentials ──────────────────────────────────────────────────

    def _seed_credentials(self, tenant: Any, users: list) -> None:
        env_vars_data = [
            ("DATABASE_URL", "development"),
            ("SECRET_KEY", "production"),
            ("REDIS_URL", "staging"),
            ("API_KEY", "all"),
        ]

        for user in users:
            user_hex = str(user.pk).replace("-", "")[:8]

            # EnvVariables — unique: (tenant, user, key, environment)
            for key, env in env_vars_data:
                EnvVariable.objects.get_or_create(
                    tenant=tenant,
                    user=user,
                    key=key,
                    environment=env,
                    defaults={
                        "value": f"seed-value-{user_hex}-{fake.password(length=16)}",
                        "description": f"{key} para entorno {env}",
                    },
                )

            # SSH Keys — fingerprint is auto-calculated in save()
            for idx, algo in enumerate(["rsa", "ed25519"]):
                raw = (user_hex + f"{algo}{idx}").encode()
                key_b64 = base64.b64encode(raw * 4).decode()
                fake_pubkey = f"ssh-{algo} {key_b64} seed@fake"

                SSHKey.objects.get_or_create(
                    tenant=tenant,
                    user=user,
                    name=f"Clave {algo.upper()} {idx + 1} — {user.email[:20]}",
                    defaults={
                        "public_key": fake_pubkey,
                        "private_key": "",
                        "algorithm": algo,
                        "description": f"Clave {algo} de desarrollo",
                    },
                )

            # SSL Certificates
            for idx in range(2):
                valid_from = date.today() - timedelta(days=random.randint(10, 200))
                valid_until = valid_from + timedelta(days=365)
                SSLCertificate.objects.get_or_create(
                    tenant=tenant,
                    user=user,
                    domain=f"sub{idx}-{user_hex}.{tenant.subdomain}.seed.example.com",
                    defaults={
                        "issuer": random.choice(["Let's Encrypt", "DigiCert", "Comodo"]),
                        "valid_from": valid_from,
                        "valid_until": valid_until,
                    },
                )

    # ── Phase 9: Snippets ─────────────────────────────────────────────────────

    def _seed_snippets(self, tenant: Any, users: list) -> None:
        lang_codes = [
            ("python", "def hello():\n    return 'Hello, World!'\n"),
            ("javascript", "const greet = () => console.log('Hello!');\n"),
            ("bash", "#!/bin/bash\necho 'Hello World'\n"),
            ("sql", "SELECT * FROM users WHERE active = true;\n"),
        ]

        for user in users:
            for i, (lang, code) in enumerate(lang_codes):
                CodeSnippet.objects.get_or_create(
                    tenant=tenant,
                    user=user,
                    title=f"{lang.capitalize()} snippet {i + 1} — {user.email[:20]}",
                    defaults={
                        "description": fake.sentence(),
                        "code": code,
                        "language": lang,
                        "tags": ["backend", "api"],
                    },
                )

    # ── Phase 10: Forms ───────────────────────────────────────────────────────

    def _seed_forms(self, tenant: Any, users: list) -> None:
        for user in users:
            for i, status in enumerate(["active", "draft"]):
                form, _ = Form.objects.get_or_create(
                    tenant=tenant,
                    user=user,
                    title=f"Formulario {i + 1} — {user.name[:20]}",
                    defaults={
                        "description": fake.sentence(),
                        "status": status,
                        # public_url_slug auto-generated in save()
                    },
                )

                question_specs = [
                    ("text", []),
                    ("multiple_choice", ["Opción A", "Opción B", "Opción C"]),
                    ("checkbox", ["Item 1", "Item 2", "Item 3"]),
                ]

                for j, (qtype, options) in enumerate(question_specs):
                    FormQuestion.objects.get_or_create(
                        form=form,
                        order=j,
                        defaults={
                            "label": f"Pregunta {j + 1} de tipo {qtype}",
                            "question_type": qtype,
                            "options": options,
                            "required": j == 0,
                        },
                    )

                # FormResponses — no unique constraint; guard by existence
                if status == "active" and not FormResponse.objects.filter(form=form).exists():
                    questions = list(FormQuestion.objects.filter(form=form))
                    for _ in range(5):
                        response_data: dict = {}
                        for q in questions:
                            if q.question_type == "text":
                                response_data[str(q.id)] = fake.sentence()
                            elif q.question_type == "multiple_choice":
                                response_data[str(q.id)] = q.options[0] if q.options else ""
                            elif q.question_type == "checkbox":
                                response_data[str(q.id)] = q.options[:2] if q.options else []
                        FormResponse.objects.create(
                            form=form,
                            data=response_data,
                            respondent_ip=fake.ipv4(),
                        )

    # ── Phase 11: Support ─────────────────────────────────────────────────────

    def _seed_support(self, tenant: Any, users: list) -> None:
        categories = ["technical", "billing", "access", "feature_request", "other"]
        priorities = ["urgente", "alta", "media", "baja"]

        for i in range(5):
            client = users[i % len(users)]
            ticket, _ = SupportTicket.objects.get_or_create(
                tenant=tenant,
                subject=f"Ticket {i + 1} — {tenant.slug}",
                defaults={
                    "client": client,
                    "description": fake.paragraph(nb_sentences=3),
                    "category": categories[i % len(categories)],
                    "priority": priorities[i % len(priorities)],
                    "client_email": client.email,
                    # reference is auto-generated in save()
                },
            )

            for role in ["client", "agent"]:
                author = client.name if role == "client" else "Agente de Soporte"
                TicketComment.objects.get_or_create(
                    ticket=ticket,
                    role=role,
                    defaults={
                        "author": author,
                        "message": fake.paragraph(nb_sentences=2),
                    },
                )

    # ── Phase 12: Digital Services ────────────────────────────────────────────

    def _seed_digital_services(self, tenant: Any, users: list) -> None:
        owner_role = Role.objects.filter(name="Owner", is_system_role=True).first()
        if owner_role:
            owner_ids = set(
                UserRole.objects.filter(role=owner_role, user__tenant=tenant)
                .values_list("user_id", flat=True)
            )
            target_users = [u for u in users if u.id in owner_ids] or [users[0]]
        else:
            target_users = [users[0]]

        for user in target_users:
            user_hex = str(user.pk).replace("-", "")[:8]
            username = slugify(f"{tenant.slug[:12]}-{user_hex}")[:50]
            if username in RESERVED_USERNAMES:
                username = f"u-{username}"[:50]

            profile, _ = PublicProfile.objects.get_or_create(
                user=user,
                defaults={
                    "username": username,
                    "display_name": user.name,
                    "title": fake.job(),
                    "bio": fake.paragraph(nb_sentences=2)[:500],
                    "is_public": True,
                },
            )

            DigitalCard.objects.get_or_create(
                profile=profile,
                defaults={
                    "email": user.email,
                    "phone": fake.phone_number()[:20],
                    "location": fake.city(),
                    "linkedin_url": f"https://linkedin.com/in/{slugify(user.name)}",
                    "github_url": f"https://github.com/{slugify(user.name)}",
                    "website_url": f"https://{tenant.subdomain}.seed.example.com",
                },
            )

            LandingTemplate.objects.get_or_create(
                profile=profile,
                defaults={
                    "template_type": "basic",
                    "sections": [],
                    "contact_email": user.email,
                    "enable_contact_form": True,
                },
            )

            for idx in range(3):
                port_slug = f"proyecto-{idx + 1}-{user_hex}"
                PortfolioItem.objects.get_or_create(
                    profile=profile,
                    slug=port_slug,
                    defaults={
                        "title": f"Proyecto de portfolio {idx + 1}",
                        "description_short": fake.sentence()[:200],
                        "description_full": fake.paragraph(nb_sentences=3),
                        "cover_image_url": f"https://placehold.co/800x450?text=proyecto-{idx + 1}",
                        "tags": ["web", "django", "python"],
                        "project_date": date.today() - timedelta(days=random.randint(30, 730)),
                        "is_featured": idx == 0,
                        "order": idx,
                    },
                )

            CVDocument.objects.get_or_create(
                profile=profile,
                defaults={
                    "professional_summary": fake.paragraph(nb_sentences=2)[:500],
                    "experience": [
                        {
                            "company": fake.company(),
                            "position": fake.job(),
                            "start": "2021-01",
                            "end": "2023-12",
                            "description": fake.sentence(),
                        }
                    ],
                    "education": [
                        {
                            "institution": f"Universidad {fake.last_name()}",
                            "degree": "Grado",
                            "field": "Ingeniería Informática",
                            "start": "2016",
                            "end": "2020",
                        }
                    ],
                    "skills": ["Python", "Django", "PostgreSQL", "React", "Docker"],
                    "languages": [
                        {"language": "Español", "level": "Nativo"},
                        {"language": "Inglés", "level": "Avanzado"},
                    ],
                },
            )

    # ── Phase 13: Sharing ─────────────────────────────────────────────────────

    def _seed_sharing(self, tenant: Any, users: list) -> None:
        if len(users) < 2:
            return

        sharer = users[0]
        recipients = users[1:3]
        projects = list(Project.objects.filter(tenant=tenant)[:3])

        for project in projects:
            for recipient in recipients:
                if recipient == sharer:
                    continue
                Share.objects.get_or_create(
                    tenant=tenant,
                    resource_type="project",
                    resource_id=project.id,
                    shared_with=recipient,
                    defaults={
                        "shared_by": sharer,
                        "permission_level": random.choice(["viewer", "editor"]),
                    },
                )
