import multiprocessing

from hearth.kernel.ledger import Ledger, new_event


def append_events(root, count):
    ledger = Ledger(root)
    for number in range(count):
        ledger.append(new_event({'id':'cpu-test','runner_class':'human','node':'test'}, 'test', args=number))


def test_independent_writers_have_correct_offsets(tmp_path):
    ctx = multiprocessing.get_context('spawn')
    processes = [ctx.Process(target=append_events, args=(str(tmp_path), 25)) for _ in range(3)]
    for process in processes:
        process.start()
    for process in processes:
        process.join(20)
        assert process.exitcode == 0
    result = Ledger(tmp_path).verify()
    assert result['ok'] and result['index_rows'] == 75
