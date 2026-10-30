"""Chat actions keep ownership, explicit confirmation and current-plan bindings."""

from sqlalchemy import select
from test_owner_workspace_integration import workspace_clients  # noqa: F401
from test_sage_experience_integration import current_plans

from app.database import transaction
from app.models import CurrentPlans, Member


def test_feedback_preview_confirm_replay_and_stale_edit(workspace_clients):  # noqa: F811
    owner, member = workspace_clients
    current_plans(member)
    sid = member.post('/api/conversations').json()['id']
    details = dict(plan_kind='Training', notes='A comfortable session.', completion='Completed',
                   difficulty='Manageable', discomfort=False)
    response = member.post(f'/api/conversations/{sid}/member-actions', json={'kind': 'Feedback', 'details': details})
    assert response.status_code == 201, response.text
    mid = response.json()['message_id']
    path = f'/api/conversations/{sid}/member-actions/{mid}'
    assert owner.post(path, json={'action': 'confirm', 'revision': 1}).status_code == 403
    saved = member.post(path, json={'action': 'confirm', 'revision': 1})
    assert saved.status_code == 200, saved.text
    assert saved.json()['state'] == 'Completed'
    assert member.post(path, json={'action': 'confirm', 'revision': 1}).json() == saved.json()
    assert member.post(path, json={'action': 'edit', 'revision': 1, 'details': details}).status_code == 409
    assert len(member.get('/api/me/observations').json()) == 1
    history = member.get(f'/api/conversations/{sid}').json()
    assert next(m for m in history['messages'] if m['id'] == mid)['cards'][0]['state'] == 'Completed'


def test_missing_details_supersession_and_changed_baseline(workspace_clients):  # noqa: F811
    _, member = workspace_clients
    current_plans(member)
    sid = member.post('/api/conversations').json()['id']
    prefix = f'/api/conversations/{sid}/member-actions'
    first = member.post(prefix, json={'kind': 'Feedback', 'details': {'plan_kind': 'Training'}}).json()
    assert member.post(prefix+'/'+first['message_id'], json={'action': 'confirm', 'revision': 1}).status_code == 422
    second = member.post(prefix, json={'kind': 'Feedback', 'details': {'notes': 'Explicit new detail'}}).json()
    assert second['card']['details']['plan_kind'] == 'Training'
    assert member.post(prefix+'/'+first['message_id'], json={'action': 'confirm', 'revision': 1}).status_code == 409
    uid = member.get('/api/auth/me').json()['user']['id']
    with transaction() as db:
        person = db.scalar(select(Member).where(Member.user_id == uid))
        db.get(CurrentPlans, person.id).version += 1
    assert member.post(prefix+'/'+second['message_id'], json={'action': 'confirm', 'revision': 1}).status_code == 409
    assert member.get('/api/me/observations').json() == []


def test_foreign_conversation_and_revision_guard(workspace_clients):  # noqa: F811
    owner, member = workspace_clients
    foreign = owner.post('/api/conversations').json()['id']
    assert member.post(f'/api/conversations/{foreign}/member-actions', json={'kind':'Feedback'}).status_code == 404
    current_plans(member)
    sid = member.post('/api/conversations').json()['id']
    prefix = f'/api/conversations/{sid}/member-actions'
    started = member.post(prefix, json={'kind':'CycleReview'}).json()
    path = prefix+'/'+started['message_id']
    assert member.post(path, json={'action':'edit','revision':2,'details':{'notes':'Updated'}}).status_code == 409
    assert member.post(path, json={'action':'dismiss','revision':1}).json()['state'] == 'Dismissed'


def test_food_replacement_stays_in_original_conversation_and_preserves_baseline(workspace_clients):  # noqa: F811
    from app.activation import plan_fingerprint
    from app.models import PlanVersion, Request
    _, member = workspace_clients
    current_plans(member)
    uid = member.get('/api/auth/me').json()['user']['id']
    with transaction() as db:
        person = db.scalar(select(Member).where(Member.user_id == uid))
        current = db.get(CurrentPlans, person.id)
        meal = db.get(PlanVersion, current.nutrition_id)
        meal.training_input_id = current.training_id
        meal.content = {'title':'Breakfast','meals':[{'name':'Breakfast','portions':[{'food_id':'apple','grams':'100'}]}]}
        meal.fingerprint = plan_fingerprint(meal)
        baseline = current.nutrition_id
    sid = member.post('/api/conversations').json()['id']
    prefix = f'/api/conversations/{sid}/member-actions'
    details = {'selections':[{'meal_index':0,'portion_index':0}], 'preference':'Replace apple with banana.'}
    preview = member.post(prefix, json={'kind':'Substitution','details':details}).json()
    response = member.post(prefix+'/'+preview['message_id'], json={'action':'confirm','revision':1})
    assert response.status_code == 200, response.text
    result = response.json()['result']
    assert result['session_id'] == sid
    with transaction() as db:
        request = db.get(Request, result['request_id'])
        assert request.session_id == sid
        assert request.payload['substitution']['selections'] == details['selections']
        person = db.scalar(select(Member).where(Member.user_id == uid))
        assert db.get(CurrentPlans, person.id).nutrition_id == baseline
    other = member.post(prefix, json={'kind':'Substitution','details':details}).json()
    conflict = member.post(prefix+'/'+other['message_id'], json={'action':'confirm','revision':1})
    assert conflict.status_code == 409
    assert 'Another plan update' in conflict.json()['detail']


def test_cycle_confirmation_stays_in_chat_and_invalid_dates_do_not_write(workspace_clients):  # noqa: F811
    _, member = workspace_clients
    current_plans(member)
    sid = member.post('/api/conversations').json()['id']
    prefix = f'/api/conversations/{sid}/member-actions'
    details = dict(period_start='2026-10-01',period_end='2026-10-07',planned_sessions=2,
                   completed_sessions=1,difficulty='Manageable',discomfort=False,
                   adjustment='Maintenance',notes='One session was missed because of travel.')
    preview = member.post(prefix, json={'kind':'CycleReview','details':details}).json()
    response = member.post(prefix+'/'+preview['message_id'], json={'action':'confirm','revision':1})
    assert response.status_code == 200, response.text
    assert response.json()['result']['session_id'] == sid
