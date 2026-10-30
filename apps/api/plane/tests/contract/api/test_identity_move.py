import pytest
from plane.db.models import Project, ProjectMember, State, Issue, IssueComment, IssueRelation

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


def test_related_issue_in_inaccessible_context_is_hidden(api_key_client,workspace,create_user,contexts):
    source,target,item=contexts
    related=Issue.objects.create(project=target,workspace=workspace,name="Hidden cross-context task")
    IssueRelation.objects.create(project=source,workspace=workspace,issue=item,related_issue=related,relation_type="relates_to")
    ProjectMember.objects.filter(project=target,member=create_user).delete()
    response=api_key_client.get(f"/api/v1/workspaces/{workspace.slug}/projects/{source.id}/work-items/{item.id}/relations/")
    assert response.status_code==200,response.data
    assert "Hidden cross-context task" not in str(response.data)
    assert str(related.id) not in str(response.data)
