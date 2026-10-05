import copy
import unittest
from tools.ops.bench27_review_effort import calculate


def fixture():
    def interval(actor, session, work, start, end):
        return dict(actor=actor, session=session, work_id=work, cohort='cap1', activity='review',
                    start=f'2026-10-05T12:{start}:00Z', end=f'2026-10-05T12:{end}:00Z', evidence='review.json')
    return {'schema': 'bench27-review-effort.v1',
            'cohorts': [dict(cohort='cap1', kind='capacity', complete=True, evidence='coverage.json')],
            'deliveries': [dict(work_id=w, cohort='cap1', status=s, evidence='verdict.json')
                           for w, s in [('a', 'accepted'), ('r', 'rejected'), ('f', 'failed')]],
            'intervals': [interval('codex','parent','a','00','04'), interval('codex','parent','r','02','06'),
                          interval('opus','grade-f','f','03','08')]}


class ReviewEffortTests(unittest.TestCase):
    def test_overlap_and_rejected_failed_numerator(self):
        row=calculate(fixture())['cohorts'][0]
        self.assertEqual(row['worker_sum_minutes'],11)
        self.assertEqual(row['union_elapsed_minutes'],8)
        self.assertEqual(row['actor_minutes'],{'codex':6,'opus':5})
        self.assertEqual(row['actor_minutes_per_accepted'],{'codex':6,'opus':5})
        self.assertIn('heterogeneous',row['worker_sum_basis'])
        self.assertEqual(row['worker_minutes_per_accepted'],11)
        self.assertEqual(row['human_review']['status'],'unknown')
        self.assertEqual(row['manual_baseline']['status'],'unknown')
        self.assertEqual(row['economic_benefit'],'not_assessed')

    def test_zero_accepted_and_partial(self):
        data=fixture();data['deliveries'][0]['status']='rejected';data['cohorts'][0]['complete']=False
        row=calculate(data)['cohorts'][0]
        self.assertEqual(row['actor_minutes_per_accepted'],{'codex':None,'opus':None})
        self.assertIsNone(row['worker_minutes_per_accepted']);self.assertEqual(row['coverage'],'partial')
        self.assertEqual(row['rate_status'],'undefined_zero_accepted')
        data['deliveries'][0]['status']='accepted'
        self.assertEqual(calculate(data)['cohorts'][0]['rate_status'],'partial_observation')

    def test_invalid_input(self):
        mutations=[lambda d:d['intervals'][0].update(end='2026-10-05T11:00:00Z'),
                   lambda d:d['intervals'][0].update(start='2026-10-05T12:00:00'),
                   lambda d:d['intervals'][0].update(evidence=''),
                   lambda d:d['intervals'][0].update(work_id='unknown'),
                   lambda d:d['deliveries'].append(copy.deepcopy(d['deliveries'][0])),
                   lambda d:d['cohorts'][0].pop('complete'),
                   lambda d:d['intervals'].pop(),
                   lambda d:d['cohorts'][0].update(manual_baseline={'minutes':70}),
                   lambda d:d['cohorts'][0].update(manual_baseline={'minutes':10**1000,'evidence':'x'}),
                   lambda d:d['cohorts'][0].update(human_review={'minutes':float('nan'),'evidence':'x'})]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                data=fixture();mutation(data)
                with self.assertRaises(ValueError):calculate(data)

    def test_distinct_cohorts_and_evidenced_manual_remain_unassessed(self):
        data=fixture()
        data['cohorts'].append(dict(cohort='qualification',kind='qualification',complete=False,evidence='pending'))
        data['cohorts'][0]['manual_baseline']={'minutes':3,'evidence':'actual-manual-log.json'}
        rows=calculate(data)['cohorts']
        self.assertEqual(len(rows),2);self.assertEqual(rows[1]['worker_sum_minutes'],0)
        self.assertEqual(rows[0]['manual_baseline']['status'],'evidenced')
        self.assertEqual(rows[0]['economic_benefit'],'not_assessed')
        data['deliveries'].append(dict(work_id='q',cohort='qualification',status='failed',evidence='q'))
        extra=copy.deepcopy(data['intervals'][0]);extra.update(work_id='q',cohort='qualification')
        data['intervals'].append(extra)
        with self.assertRaises(ValueError):calculate(data)


if __name__=='__main__':unittest.main()
