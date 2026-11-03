"""Keep issue relations within contexts the requesting member can read."""

from uuid import UUID

from django.db.models import Q

from plane.db.models import Issue, ProjectMember, WorkspaceMember


def visible_relation_issues(user, slug):
    if not getattr(user, "is_authenticated", False) or not user.is_active:
        return Issue.issue_objects.none()
    if not WorkspaceMember.objects.filter(workspace__slug=slug, member=user, is_active=True).exists():
        return Issue.issue_objects.none()
    memberships = ProjectMember.objects.filter(
        workspace__slug=slug, member=user, is_active=True
    ).values_list("project_id", "role")
    regular, guest = [], []
    for project_id, role in memberships:
        if role == 5:
            guest.append(project_id)
        elif role in (15, 20):
            regular.append(project_id)
    return Issue.objects.filter(workspace__slug=slug, is_draft=False).exclude(
        state__group="triage"
    ).filter(
        Q(project_id__in=regular)
        | Q(project_id__in=guest, project__guest_view_all_features=True)
        | Q(project_id__in=guest, created_by_id=user.id)
    )


def relation_items_visible(user, slug, project_id, issue_id, targets):
    if not isinstance(targets, list) or not targets:
        return False
    try:
        ids = {UUID(str(value)) for value in targets}
        source_id = UUID(str(issue_id))
    except (ValueError, TypeError, AttributeError):
        return False
    visible = visible_relation_issues(user, slug)
    if not visible.filter(id=source_id, project_id=project_id).exists():
        return False
    return visible.filter(id__in=ids).count() == len(ids)
