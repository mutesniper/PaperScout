import json
from .task_suite import suite, World
from .layered_eval import run_task, aggregate
from .contract import State, model_messages


def test_reference_paths_all_complete_and_separate_control_tasks():
    reports=[]
    for task in suite():
        oracle=World(task)
        def decide(state):
            route=oracle.expected()
            oracle.execute(route)
            return {'raw':json.dumps({'route':route})}
        result=run_task(task,decide)
        assert result['task_success'],task.id
        reports.append(result)
    report=aggregate(reports)
    assert report['tasks']==24
    assert report['layer3']['scenario_success_rate']==1
    assert report['layer3']['control_task_count']==4
    assert report['layer3']['research_task_count']==20
    assert report['layer3']['answer_quality'] is None


def test_gold_never_appears_in_model_state_and_clarification_hides_selection():
    task=next(t for t in suite() if t.id=='clarify_document')
    payload=json.loads(model_messages(task.initial)[1]['content'])
    assert not {'targets','objective','expected','expected_blocked'} & payload.keys()
    world=World(task)
    assert world.execute('answer')['status']=='unresolved_user_selection'
    assert world.violation


def test_premature_finish_does_not_count_as_completion():
    result=run_task(suite()[1],lambda s:{'raw':'{"route":"finish"}'})
    assert not result['task_success']
    assert result['trace'][0]['premature_finish']


def test_wrong_target_tool_and_invalid_json_not_repaired():
    task=next(t for t in suite() if t.id=='ignore_extra')
    result=run_task(task,lambda s:{'raw':'{"route":"compare"}'})
    assert not result['task_success']
    assert result['trace'][0]['event']['status']=='missing_target_evidence'
    bad=run_task(task,lambda s:{'raw':'```json\n{"route":"answer"}\n```'})
    assert not bad['task_success'] and bad['termination']=='invalid_output'


def test_constraint_and_order_violations_cannot_be_hidden_by_later_completion():
    world=World(next(t for t in suite() if t.id=='ordered_delivery'))
    assert world.execute('compare')['status']=='wrong_order'
    assert world.violation
    world=World(next(t for t in suite() if t.id=='no_network'))
    assert world.execute('search')['status']=='blocked_user_constraint'
    assert world.violation


def test_mock_download_failure_is_not_retried_and_requires_honest_stop():
    world=World(next(t for t in suite() if t.id=='failed_target'))
    assert world.expected()=='download_pdf'
    assert world.execute('download_pdf')['status']=='injected_download_failure'
    assert world.expected()=='finish'
    assert not world.complete()


def test_executor_does_not_consult_gold_and_worlds_are_isolated():
    task=next(t for t in suite() if t.id=='known_link')
    first,second=World(task),World(task)
    def forbidden():
        raise AssertionError('Executor must not consult reference policy')
    first.expected=forbidden
    assert first.execute('download_pdf')['progress']
    assert len(first.state.available_pdfs)==1
    assert not second.state.available_pdfs and not task.initial.available_pdfs


def test_whole_run_limit_handles_repeated_unnecessary_clarification():
    result=run_task(suite()[0],lambda s:{'raw':'{"route":"clarify"}'})
    assert not result['task_success']
    assert len(result['trace'])<=12
    assert sum(r['event']['status']=='unnecessary_clarification' for r in result['trace'])==10
