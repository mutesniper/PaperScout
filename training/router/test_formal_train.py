from .formal_train import selection_key
from .compare_formal import gates


def test_checkpoint_selection_prioritizes_accuracy_then_f1_then_earliest():
    reports=[{'epoch':1,'acceptable_accuracy':.9,'macro_f1':.89},
             {'epoch':2,'acceptable_accuracy':.9,'macro_f1':.91},
             {'epoch':3,'acceptable_accuracy':.9,'macro_f1':.91},
             {'epoch':4,'acceptable_accuracy':.8,'macro_f1':.99}]
    assert max(reports,key=selection_key)['epoch']==2


def test_aggregate_accuracy_does_not_hide_boundary_or_class_failure():
    base={'acceptable_accuracy':.8,'per_class':{'clarify':{'recall':1.0}}}
    tuned={'acceptable_accuracy':.95,'macro_f1':.94,'schema_rate':1.0,
           'allowed_rate':.97,'premature_finish_rate':.03,'per_class':{'clarify':{'recall':.75}}}
    result=gates(base,tuned)
    assert result['gain_5_percentage_points']
    assert not result['every_class_recall']
    assert not result['no_class_recall_regression']
    assert not result['no_illegal_routes_on_validation']
    assert not result['premature_finish']
