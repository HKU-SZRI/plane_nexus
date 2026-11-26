import pytest
from plane.db.models import (
    Cycle, CycleIssue, Issue, IssueComment, IssueRelation, Module, ModuleIssue,
    Project, ProjectMember, State,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def contexts(workspace, create_user):
    source=Project.objects.create(name="Source",identifier="SRC",workspace=workspace,created_by=create_user)
    target=Project.objects.create(name="Target",identifier="DST",workspace=workspace,created_by=create_user)
    for project in (source,target):
        ProjectMember.objects.create(project=project,member=create_user,role=20,is_active=True)
    state=State.objects.create(project=source,name="Meeting pending",group="unstarted")
    item=Issue.objects.create(project=source,workspace=workspace,name="Recurring meeting",state=state,created_by=create_user)
    return source,target,item


def path(workspace,source,item):
    return f"/api/v1/workspaces/{workspace.slug}/projects/{source.id}/work-items/{item.id}/transfer/"


def test_move_preserves_identity_history_parent_and_is_idempotent(api_key_client,workspace,create_user,contexts):
    source,target,item=contexts
    State.objects.create(project=target,name="Meeting pending",group="unstarted")
    parent=Issue.objects.create(project=source,workspace=workspace,name="Parent",created_by=create_user)
    item.parent=parent
    item.save()
    Issue.objects.filter(pk=item.pk).update(created_by=create_user)
    comment=IssueComment.objects.create(project=source,workspace=workspace,issue=item,actor=create_user,comment_html="<p>History</p>")
    original_id=item.id
    payload={"target_project_id":str(target.id),"preserve_identity":True}
    assert api_key_client.get(path(workspace,source,item)).json()["identity_preserving_move"]
    preview=api_key_client.post(path(workspace,source,item),payload|{"validate_only":True},format="json")
    assert preview.status_code==200,preview.data
    item.refresh_from_db()
    assert item.project_id==source.id
    response=api_key_client.post(path(workspace,source,item),payload,format="json")
    assert response.status_code==200,response.data
    item.refresh_from_db();comment.refresh_from_db()
    assert item.id==original_id and item.project_id==target.id
    assert item.parent_id==parent.id and item.created_by_id==create_user.id
    assert comment.issue_id==original_id and comment.project_id==target.id
    assert item.state.name=="Meeting pending"
    again=api_key_client.post(path(workspace,source,item),payload,format="json")
    assert again.status_code==200 and again.data["id"]==str(original_id)
    assert Issue.objects.filter(name="Recurring meeting").count()==1
    ProjectMember.objects.filter(project=source,member=create_user).update(is_active=False)
    detail=api_key_client.get(
        f"/api/v1/workspaces/{workspace.slug}/projects/{target.id}/work-items/{item.id}/?expand=parent")
    assert detail.status_code==200,detail.data
    assert detail.data.get("parent") is None


def test_move_rejects_missing_target_status_without_writes(api_key_client,workspace,contexts):
    source,target,item=contexts
    response=api_key_client.post(path(workspace,source,item),
        {"target_project_id":str(target.id),"preserve_identity":True},format="json")
    assert response.status_code==409
    item.refresh_from_db()
    assert item.project_id==source.id


def test_move_preserves_cycle_and_module_links_by_matching_target_name(api_key_client, workspace, create_user, contexts):
    source,target,item=contexts
    State.objects.create(project=target,name="Meeting pending",group="unstarted")
    Cycle.objects.create(project=source,workspace=workspace,name="Sprint 4",owned_by=create_user)
    target_cycle=Cycle.objects.create(project=target,workspace=workspace,name="Sprint 4",owned_by=create_user)
    Module.objects.create(project=source,workspace=workspace,name="Q4 planning")
    target_module=Module.objects.create(project=target,workspace=workspace,name="Q4 planning")
    cycle_link=CycleIssue.objects.create(
        project=source,workspace=workspace,issue=item,
        cycle=Cycle.objects.get(project=source,name="Sprint 4"),
    )
    module_link=ModuleIssue.objects.create(
        project=source,workspace=workspace,issue=item,
        module=Module.objects.get(project=source,name="Q4 planning"),
    )

    payload={"target_project_id":str(target.id),"preserve_identity":True}
    preview=api_key_client.post(path(workspace,source,item),payload|{"validate_only":True},format="json")
    assert preview.status_code==200,preview.data
    moved=api_key_client.post(path(workspace,source,item),payload,format="json")
    assert moved.status_code==200,moved.data
    cycle_link.refresh_from_db();module_link.refresh_from_db()
    assert cycle_link.cycle_id==target_cycle.id and cycle_link.project_id==target.id
    assert module_link.module_id==target_module.id and module_link.project_id==target.id
    assert CycleIssue.objects.filter(issue=item).count()==1
    assert ModuleIssue.objects.filter(issue=item).count()==1


def test_move_with_cycle_without_matching_target_is_rejected(api_key_client, workspace, create_user, contexts):
    source,target,item=contexts
    State.objects.create(project=target,name="Meeting pending",group="unstarted")
    cycle=Cycle.objects.create(project=source,workspace=workspace,name="No target sprint",owned_by=create_user)
    CycleIssue.objects.create(project=source,workspace=workspace,issue=item,cycle=cycle)
    response=api_key_client.post(path(workspace,source,item),
        {"target_project_id":str(target.id),"preserve_identity":True},format="json")
    assert response.status_code==409
    item.refresh_from_db()
    assert item.project_id==source.id
    assert CycleIssue.objects.filter(issue=item,cycle=cycle).count()==1


def test_related_issue_in_inaccessible_context_is_hidden(api_key_client,workspace,create_user,contexts):
    source,target,item=contexts
    related=Issue.objects.create(project=target,workspace=workspace,name="Hidden cross-context task")
    IssueRelation.objects.create(project=source,workspace=workspace,issue=item,related_issue=related,relation_type="relates_to")
    ProjectMember.objects.filter(project=target,member=create_user).delete()
    response=api_key_client.get(f"/api/v1/workspaces/{workspace.slug}/projects/{source.id}/work-items/{item.id}/relations/")
    assert response.status_code==200,response.data
    assert "Hidden cross-context task" not in str(response.data)
    assert str(related.id) not in str(response.data)
